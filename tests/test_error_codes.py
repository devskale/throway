"""1.53.4 — Error-Code-Disziplin: JEDER Fehler-Body trägt ein stabiles
`code`-Feld, und dokumentierte Validierungen werden tatsächlich vollzogen.

Live-Validierung 2026-10-05: drei Verletzungen des selbst dokumentierten
Vertrags ("Every error response is JSON with an error message and a
stable `code`"):
  1. ~25 rohe Error-Sends ohne `code` (dirs, pics, retain, store)
  2. PUT/PATCH auf ein Bundle → 404 not_found statt 403 forbidden
  3. invalide Dir-Namen / ttl= wurden still akzeptiert
"""
import json

import pytest

from conftest import Server

AGENT = {"User-Agent": "curl/8.0"}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "main")
    yield s
    s.stop()


def _body(body):
    d = json.loads(body)
    assert d.get("code"), f"error body without stable code: {d}"
    return d


# --- 1. jede Fehler-Antwort trägt code ------------------------------------

def test_invalid_share_name_has_code(srv):
    st, _, body = srv.post("/?share=ab", data=b"x", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"


def test_invalid_dir_name_has_code(srv):
    st, _, body = srv.post("/?dir=1&name=ab", data=b"x", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"


def test_retain_without_token_has_code(srv):
    st, _, body = srv.post("/?retain=1", data=b"x", headers=AGENT)
    assert st == 401
    assert _body(body)["code"] == "write_denied"


def test_invalid_ttl_has_code(srv):
    st, _, body = srv.post("/?name=x.txt&ttl=xyz", data=b"x", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"


def test_edit_bundle_forbidden_not_not_found(srv):
    """PUT auf ein existierendes Bundle ist 403 forbidden, nicht 404 —
    das Bundle existiert (GET → 200), der Edit ist nur nicht erlaubt."""
    # bundle bauen: 2 multipart parts
    boundary = "----throwaytest"
    parts = []
    for n, d in (("index.html", b"<html>hi</html>"), ("style.css", b"body{}")):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="file"; filename="{n}"\r\n'
            f"Content-Type: text/plain\r\n\r\n".encode() + d + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(parts)
    st, _, body = srv.post(
        "/", data=payload,
        headers={**AGENT,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert st == 200, body
    bid = json.loads(body)["id"]
    # GET zeigt: das Bundle existiert
    st, _, _ = srv.get(f"/{bid}", headers=AGENT)
    assert st == 200
    # PUT/PATCH darauf: 403 forbidden (nicht 404 not_found)
    st, _, body = srv.put(f"/{bid}", data=b"new", headers=AGENT)
    assert st == 403
    assert _body(body)["code"] == "forbidden"
    st, _, body = srv.patch(f"/{bid}", data=b"more", headers=AGENT)
    assert st == 403
    assert _body(body)["code"] == "forbidden"


# --- 2. dokumentierte Validierungen werden vollzogen ----------------------

def test_invalid_dir_name_rejected(srv):
    """Doku sagt 5-32 chars [a-z0-9-] — 'ab' muss 400 sein, kein Dir."""
    st, _, body = srv.post("/?dir=1&name=ab", data=b"x", headers=AGENT)
    assert st == 400
    assert "5-32" in _body(body)["error"]
    # kein Dir entstanden
    st, _, _ = srv.get("/d/ab", headers=AGENT)
    assert st == 404


def test_invalid_ttl_rejected_not_silently_ignored(srv):
    """ttl=xyz ist unparseable → 400 bad_request, kein stiller Default."""
    st, _, body = srv.post("/?name=x.txt&ttl=xyz", data=b"x", headers=AGENT)
    assert st == 400
    assert "ttl" in _body(body)["error"].lower()


# --- 3. Stichproben über weitere rohe Pfade --------------------------------

def test_edit_binary_file_has_code(srv):
    st, _, body = srv.post("/?name=x.bin", data=b"\x00\x01",
                           headers=AGENT)
    assert st == 200
    fid = json.loads(body)["id"]
    st, _, body = srv.put(f"/{fid}", data=b"nope", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"
    st, _, body = srv.patch(f"/{fid}", data=b"nope", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"


def test_dir_add_requires_multipart_has_code(srv):
    st, _, body = srv.post("/?dir=1", data=b"", headers=AGENT)
    assert st == 200
    key = json.loads(body)["id"]
    # non-multipart add to dir → 400 mit code
    st, _, body = srv.post(f"/d/{key}", data=b"raw", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"


def test_pics_url_required_has_code(srv):
    st, _, body = srv.post("/pics?import=1", data=b"", headers=AGENT)
    assert st == 400
    assert _body(body)["code"] == "bad_request"


def test_unknown_help_topic_has_code(srv):
    st, _, body = srv.get("/help/nosuchtopic", headers=AGENT)
    assert st == 404
    assert _body(body)["code"] == "not_found"


def test_gallery_not_found_has_code(srv):
    st, _, body = srv.get("/pics/no-such-gallery-xyz", headers=AGENT)
    assert st == 404
    assert _body(body)["code"] == "not_found"


# --- 4. Guardrail: die Bugklasse ist mechanisch — kein roher Error-Send ----

def test_no_raw_error_sends_anywhere():
    """1.53.4-Retro P1: jede Fehler-JSON entsteht in _err()/kit.err()
    (trägt `code`). Rohe json.dumps({"error": ...})-Sends sind die
    Bugklasse, die live 36+7 Stellen ohne code produzierte — der Scan
    fängt sie beim Commit, nicht erst im adversarialen Live-Pass.
    Ausnahmen: der _err-Körper selbst (er BAUT das code-Feld)."""
    import glob
    import os
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = [os.path.join(repo, "store.py")] + \
        sorted(glob.glob(os.path.join(repo, "throway", "*.py")))
    assert len(files) > 1, "Quellbaum nicht gefunden"
    for path in files:
        src = open(path, encoding="utf-8").read()
        assert 'json.dumps({"error"' not in src, (
            f"{path}: roher Error-Send gefunden — _err()/kit.err() "
            "nutzen (trägt code), siehe 1.53.4")
