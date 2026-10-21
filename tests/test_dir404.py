"""Issue dir-404-hint — Upload auf unbestehendem Dir: fail-lauter 404
mit Create-Hinweis statt stiller 404; zusaetzlich ?create=1 als
create-or-get fuer Skript-Flows (1.57.0).

Quelle: .work/issues/active/dir-404-hint.md (ubugrill@klark0, 2026-10-21).
"""
import json

import pytest

from conftest import Server, multipart

AGENT = {"User-Agent": "curl/8.0"}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "main")
    yield s
    s.stop()


def test_dir_upload_404_carries_create_hint(srv):
    """Repro aus dem Issue: POST /d/<unbekannt> muss fail-laut antworten —
    der 404-Body nennt den Create-Endpoint (hint-Feld), damit Skripte
    keine Schein-Erfolgsketten bauen."""
    body, ctype = multipart([("report.html", b"<h1>x</h1>", "text/html")])
    st, _, raw = srv.post("/d/gibts-nicht", data=body,
                          headers={"Content-Type": ctype})
    assert st == 404
    err = json.loads(raw)
    assert err["code"] == "not_found"
    assert "gibts-nicht" in err["hint"]
    assert "dir=1" in err["hint"]          # der Create-Endpoint steht im Hint
    assert "create=1" in err["hint"]       # die create-or-get-Alternative auch
    assert "help/dirs" in err["hint"]      # Doku-Verweis


def test_dir_upload_create_flag_creates_and_adds(srv):
    """?create=1 am Upload-POST: fehlender benannter Dir wird angelegt,
    die Dateien landen im selben Call (create-or-get fuer Skripte)."""
    body, ctype = multipart([("a.txt", b"alpha", "text/plain"),
                             ("b.txt", b"beta", "text/plain")])
    st, _, raw = srv.post("/d/neu-dir-42?create=1", data=body,
                          headers={"Content-Type": ctype})
    assert st == 200
    d = json.loads(raw)
    assert d.get("dir") is True
    st, _, got = srv.get("/d/neu-dir-42/a.txt")
    assert st == 200 and got == b"alpha"
    st, _, got = srv.get("/d/neu-dir-42/b.txt")
    assert st == 200 and got == b"beta"


def test_dir_upload_create_flag_existing_dir_adds(srv):
    """?create=1 auf einem existierenden Dir = gewoehnliches Add
    (create-OR-GET): kein Fehler, Dateien kommen dazu."""
    srv.post("/?dir=1&name=besteht-1")
    body, ctype = multipart([("c.txt", b"gamma", "text/plain")])
    st, _, raw = srv.post("/d/besteht-1?create=1", data=body,
                          headers={"Content-Type": ctype})
    assert st == 200
    st, _, got = srv.get("/d/besteht-1/c.txt")
    assert st == 200 and got == b"gamma"


def test_dir_upload_create_flag_rejects_bad_names(srv):
    """?create=1 validiert den Namen wie der Create-Call (reservierte/
    ungueltige Namen 400, kein stiller Fallback)."""
    body, ctype = multipart([("x.txt", b"x", "text/plain")])
    st, _, raw = srv.post("/d/ab?create=1", data=body,
                          headers={"Content-Type": ctype})
    assert st == 400
    assert "invalid dir name" in json.loads(raw)["error"]
    # reservierter Name darf keinen Dir erzeugen
    st, _, _ = srv.post("/d/pics?create=1", data=body,
                        headers={"Content-Type": ctype})
    assert st == 400


def test_dir_upload_create_flag_rejects_flag_combinations(srv):
    """create=1 kollidiert nicht mit anderen POST-Modifikatoren
    (Retro hard-validate: neue Pfad-Flags muessen abgewiesen werden)."""
    body, ctype = multipart([("x.txt", b"x", "text/plain")])
    # retain=1 ohne Token scheitert vorher an der Retain-Gate (401) —
    # auch das ist eine Abweisung, kein stiller Create-Pfad
    st, _, _ = srv.post("/d/kombi-1?create=1&retain=1", data=body,
                        headers={"Content-Type": ctype})
    assert st in (400, 401)
    st, _, _ = srv.post("/d/kombi-2?create=1&share=name1", data=body,
                        headers={"Content-Type": ctype})
    assert st == 400
    st, _, _ = srv.post("/?dir=1&name=kombi-3&create=1", data=body,
                        headers={"Content-Type": ctype})
    assert st == 400


def test_help_dirs_mentions_404_trap(srv):
    """Acceptance: /help/dirs erwaehnt die 404-Falle explizit."""
    st, _, body = srv.get("/help/dirs", headers=AGENT)
    assert st == 200
    text = body.decode()
    assert "404 TRAP" in text
    assert "create=1" in text
