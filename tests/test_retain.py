"""Behavior tests for token-gated indefinite retention (1.44.0).

Covers: token upload (header + query), 401s, flip routes, write gates,
dir retention, bundle retention, share retention, sweep/evict immunity
(in-process against the real store module), /api + /help surfaces.
"""
import importlib
import json
import os
import sys
import time

import pytest

from conftest import REPO, Server, multipart

AGENT = {"User-Agent": "curl/8.0"}
TOK = "rt-test-abc123"
AUTH = {"Authorization": f"Bearer {TOK}"}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "rt", env_extra={"THROWAWAY_RETAIN_TOKEN": TOK})
    yield s
    s.stop()


@pytest.fixture
def srv_off(tmp_path):
    """Server WITHOUT a retain token — feature off."""
    s = Server(tmp_path / "off")
    yield s
    s.stop()


def _store_module(root):
    """Import the real store module in-process against `root` (for
    sweep()/evict() checks — deterministic, no sleep races)."""
    os.environ["THROWAWAY_ROOT"] = str(root)
    sys.path.insert(0, REPO)
    import store
    importlib.reload(store)
    # 1.49.0: throway.dirs reads ROOT at import — reload in place so
    # store.dirs sweeps the SAME root (reload mutates the module object)
    import throway.dirs
    importlib.reload(throway.dirs)
    return store


# --- uploads -------------------------------------------------------------

def test_upload_with_bearer_token_retained(srv):
    st, hd, raw = srv.post("/?name=keep.txt", data=b"keep me",
                           headers={**AGENT, **AUTH})
    body = json.loads(raw)
    assert st == 200
    assert body["expires_at"] is None
    assert body["expires_in"] is None
    assert body["persistence"]["expires_at"] is None
    assert body["persistence"]["retention"] == "indefinite"
    assert "X-Expires" not in hd
    # public read stays open
    st, _, _ = srv.get(f"/{body['id']}")
    assert st == 200


def test_upload_with_query_token_retained(srv):
    st, body = srv.jpost(f"/?name=q.txt&token={TOK}", data=b"x", headers=AGENT)
    assert st == 200
    assert body["expires_at"] is None
    assert body["persistence"]["retention"] == "indefinite"


def test_upload_bad_token_401(srv):
    st, body = srv.jpost("/?name=x", data=b"x",
                            headers={**AGENT, "Authorization": "Bearer nope"})
    assert st == 401
    assert "invalid retain token" in body["error"]


def test_retain_flag_without_token_401(srv):
    st, body = srv.jpost("/?retain=1", data=b"x", headers=AGENT)
    assert st == 401
    assert "retain token required" in body["error"]


def test_retention_disabled_server(srv_off):
    st, body = srv_off.jpost("/?retain=1", data=b"x", headers=AGENT)
    assert st == 401
    assert "not enabled" in body["error"]
    st, body = srv_off.jpost("/", data=b"x",
                                headers={**AGENT, "Authorization": f"Bearer {TOK}"})
    assert st == 401


def test_upload_without_token_expires_as_before(srv):
    st, hd, raw = srv.post("/?name=n.txt", data=b"x", headers=AGENT)
    body = json.loads(raw)
    assert st == 200
    assert body["expires_at"] is not None
    assert body["expires_in"] == 14400
    assert hd.get("X-Expires") == "14400"
    assert "retention" not in body["persistence"]


def test_once_and_retention_rejected(srv):
    st, body = srv.jpost("/?name=s.txt&once=1", data=b"x",
                            headers={**AGENT, **AUTH})
    assert st == 400
    assert "mutually exclusive" in body["error"]


def test_bundle_with_token_retained(srv):
    body, ctype = multipart([("a.txt", b"aaa", "text/plain"),
                             ("b.css", b"b{}", "text/css")])
    st, resp = srv.jpost("/", data=body, headers={**AGENT, **AUTH,
                                                     "Content-Type": ctype})
    assert st == 200
    assert resp["bundle"] is True
    assert resp["expires_at"] is None
    assert resp["persistence"]["retention"] == "indefinite"
    st, _, _ = srv.get(f"/{resp['id']}/a.txt")
    assert st == 200


def test_share_with_token_retained(srv):
    st, body = srv.jpost("/?share=skillz&name=s.txt&token=" + TOK,
                            data=b"skill", headers=AGENT)
    assert st == 200
    assert body["dir"] is True
    assert body["expires_at"] is None
    assert body["persistence"]["retention"] == "indefinite"


# --- flip routes ----------------------------------------------------------

def test_flip_existing_file(srv):
    _, up = srv.jpost("/?name=f.txt", data=b"flip", headers=AGENT)
    fid = up["id"]
    assert up["expires_at"] is not None
    st, body = srv.jpost(f"/{fid}?retain=1", data=b"", headers={**AGENT, **AUTH})
    assert st == 200
    assert body["retention"] == "indefinite"
    assert body["expires_at"] is None
    # write-protected now
    st, _, _ = srv.put(f"/{fid}", data=b"hack", headers=AGENT)
    assert st == 401
    st, _, _ = srv.put(f"/{fid}", data=b"legit", headers={**AGENT, **AUTH})
    assert st == 200
    # flip is idempotent
    st, _ = srv.jpost(f"/{fid}?retain=1", data=b"", headers={**AGENT, **AUTH})
    assert st == 200


def test_flip_dir_and_write_gate(srv):
    srv.jpost("/?dir=1&name=team7", data=b"", headers=AGENT)
    st, body = srv.jpost("/d/team7?retain=1", data=b"", headers={**AGENT, **AUTH})
    assert st == 200
    assert body["retention"] == "indefinite"
    # add without token -> 401
    body_mp, ctype = multipart([("n.txt", b"hi", "text/plain")])
    st, _, _ = srv.post("/d/team7", data=body_mp,
                        headers={**AGENT, "Content-Type": ctype})
    assert st == 401
    # add with token -> 200
    st, _, _ = srv.post("/d/team7", data=body_mp,
                        headers={**AGENT, **AUTH, "Content-Type": ctype})
    assert st == 200
    st, _, _ = srv.get("/d/team7", headers=AGENT)
    assert st == 200


def test_token_put_retains_normal_file(srv):
    """write implies retention"""
    _, up = srv.jpost("/?name=w.txt", data=b"v1", headers=AGENT)
    fid = up["id"]
    assert up["expires_at"] is not None
    st, _, raw = srv.put(f"/{fid}", data=b"v2", headers={**AGENT, **AUTH})
    assert st == 200
    body = json.loads(raw)
    assert body["expires_at"] is None
    assert body["persistence"]["retention"] == "indefinite"
    # and now write-protected without token
    st, _, _ = srv.put(f"/{fid}", data=b"v3", headers=AGENT)
    assert st == 401


def test_delete_retained_requires_token(srv):
    _, up = srv.jpost("/?name=d.txt", data=b"x", headers={**AGENT, **AUTH})
    fid = up["id"]
    st, _, _ = srv.delete(f"/{fid}", headers=AGENT)
    assert st == 401
    st, _, _ = srv.delete(f"/{fid}", headers={**AGENT, **AUTH})
    assert st == 200


def test_patch_gate_and_retain(srv):
    _, up = srv.jpost("/?name=p.txt", data=b"a", headers=AGENT)
    fid = up["id"]
    st, _, raw = srv.patch(f"/{fid}", data=b"b", headers={**AGENT, **AUTH})
    body = json.loads(raw)
    assert st == 200
    assert body["expires_at"] is None
    st, _, _ = srv.patch(f"/{fid}", data=b"c", headers=AGENT)
    assert st == 401


# --- sweep / evict immunity (in-process, real module) ---------------------

def test_sweep_spares_retained(srv, tmp_path):
    _, up = srv.jpost("/?name=r.txt", data=b"keep", headers={**AGENT, **AUTH})
    fid = up["id"]
    # plant a stale disposable file (expired) next to it
    stale = os.path.join(srv.root, "deadbeef00000000")
    with open(stale, "wb") as f:
        f.write(b"stale")
    with open(stale + ".meta", "w") as f:
        json.dump({"expires": time.time() - 3600, "ctype": "text/plain",
                   "name": "stale.txt", "created": time.time() - 7200}, f)
    store = _store_module(srv.root)
    store.sweep()
    assert not os.path.exists(stale)          # expired disposable swept
    assert os.path.exists(os.path.join(srv.root, fid))   # retained stays
    st, _, _ = srv.get(f"/{fid}")
    assert st == 200


def test_evict_spares_retained(srv):
    _, up = srv.jpost("/?name=r.txt", data=b"keep", headers={**AGENT, **AUTH})
    fid = up["id"]
    _, up2 = srv.jpost("/?name=n.txt", data=b"drop", headers=AGENT)
    fid2 = up2["id"]
    store = _store_module(srv.root)
    store.evict(0)                             # evict everything evictable
    assert not os.path.exists(os.path.join(srv.root, fid2))
    assert os.path.exists(os.path.join(srv.root, fid))
    st, _, _ = srv.get(f"/{fid}")
    assert st == 200


def test_retained_dir_survives_sweep(srv):
    srv.jpost("/?dir=1&name=keepd", data=b"", headers={**AGENT, **AUTH})
    # force dir expiry in the past is impossible (no expires) — instead
    # plant an expired normal dir and check only it disappears
    nd = os.path.join(srv.root, "d")
    gone = os.path.join(nd, "gonedir")
    os.makedirs(gone, exist_ok=True)
    with open(os.path.join(gone, "gonedir.meta"), "w") as f:
        json.dump({"type": "dir", "created": time.time() - 9e5,
                   "updated": time.time() - 9e5, "expires": time.time() - 3600,
                   "max_age": 604800, "listed": False, "tags": [], "files": {}}, f)
    store = _store_module(srv.root)
    store.dirs.sweep(time.time())
    assert not os.path.exists(gone)
    assert os.path.isdir(os.path.join(nd, "keepd"))


# --- surfaces -------------------------------------------------------------

def test_api_and_help_surface(srv):
    st, _, body = srv.get("/api", headers=AGENT)
    spec = json.loads(body)
    assert spec["retention_token"] is True
    assert spec["endpoints"]["retain"]["method"] == "POST"
    st, _, body = srv.get("/help/retention", headers=AGENT)
    assert st == 200
    assert "Bearer" in body.decode()
    assert spec["endpoints"]["upload"]["response"]["persistence"]["expires_at"] == "str|null"


def test_api_retention_flag_off(srv_off):
    st, _, body = srv_off.get("/api", headers=AGENT)
    assert json.loads(body)["retention_token"] is False


# --- 1.45.1 hard-validate fixes -------------------------------------------

def test_bundle_deletable_and_retained_gate(srv):
    """Retro hard-validate: kein Create ohne Delete-Pfad. Normale Bundles
    waren per Doku loeschbar, code-seitig kam DELETE nur bei Files an."""
    body, ctype = multipart([("a.txt", b"aaa", "text/plain"),
                             ("b.css", b"b{}", "text/css")])
    # normales Bundle: ohne Token loeschbar
    st, resp = srv.jpost("/", data=body, headers={**AGENT, "Content-Type": ctype})
    assert st == 200
    st, _, _ = srv.delete(f"/{resp['id']}", headers=AGENT)
    assert st == 200
    st, _, _ = srv.get(f"/{resp['id']}", headers=AGENT)
    assert st == 404
    # retained Bundle: ohne Token 401, mit Token 200
    body, ctype = multipart([("a.txt", b"aaa", "text/plain"),
                             ("b.css", b"b{}", "text/css")])
    st, resp = srv.jpost("/", data=body, headers={**AGENT, **AUTH,
                                                    "Content-Type": ctype})
    assert st == 200 and resp["expires_at"] is None
    st, _, _ = srv.delete(f"/{resp['id']}", headers=AGENT)
    assert st == 401
    st, _, _ = srv.delete(f"/{resp['id']}", headers={**AGENT, **AUTH})
    assert st == 200


def test_share_token_add_implies_retention(srv):
    """Token-Add auf bestehendem Normal-Share-Dir muss es retained machen
    (write implies retention — ging ueber _share_store frueher verloren)."""
    st, body = srv.jpost("/?share=hfixdir&name=one.txt", data=b"1", headers=AGENT)
    assert st == 200 and body["expires_at"] is not None
    st, body = srv.jpost("/?share=hfixdir&name=two.txt", data=b"2",
                          headers={**AGENT, **AUTH})
    assert st == 200
    assert body["expires_at"] is None, body
    assert body["persistence"]["retention"] == "indefinite"
    # und jetzt write-gated
    st, _, raw = srv.post("/?share=hfixdir&name=three.txt", data=b"3", headers=AGENT)
    assert st == 401


def test_flip_on_reserved_names_404(srv):
    """POST /d?retain=1 (+ Token) darf KEINE stille (retained) Leer-Datei
    erzeugen — reservierte Namespaces enden mit 404 (Retro hard-validate)."""
    for reserved in ("d", "pics"):
        st, _, raw = srv.post(f"/{reserved}?retain=1", data=b"",
                              headers={**AGENT, **AUTH})
        assert st in (400, 404), (reserved, st)
    # Root ohne id faellt weiter durch (legitimer Retain-Upload per Flag)
    st, body = srv.jpost("/?retain=1", data=b"flag-upload",
                         headers={**AGENT, **AUTH})
    assert st == 200 and body["expires_at"] is None
    srv.delete(f"/{body['id']}", headers={**AGENT, **AUTH})
