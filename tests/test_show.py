"""Behavior tests for Show-Dirs (1.45.0): retained + publicly writable.

Token creates/flips; everyone with the URL can add/edit/delete files;
whole-dir delete stays token-gated; write_token (if set) beats open.
"""
import json

import pytest

from conftest import Server, multipart

AGENT = {"User-Agent": "curl/8.0"}
TOK = "rt-test-abc123"
AUTH = {"Authorization": f"Bearer {TOK}"}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "show", env_extra={"THROWAWAY_RETAIN_TOKEN": TOK})
    yield s
    s.stop()


def _add_file(srv, key, fname="n.txt", data=b"hi", headers=None):
    body, ctype = multipart([(fname, data, "text/plain")])
    return srv.post(f"/d/{key}", data=body,
                    headers={**AGENT, "Content-Type": ctype, **(headers or {})})


def test_create_show_dir(srv):
    st, _, raw = srv.post("/?dir=1&show=1&name=brett", data=b"",
                          headers={**AGENT, **AUTH})
    assert st == 200
    d = json.loads(raw)
    assert d["dir"] is True
    assert d["open"] is True
    assert d["expires_at"] is None
    assert d["persistence"]["retention"] == "indefinite"
    # write_token note must NOT appear (open dir, no token needed)
    assert "write_token" not in d


def test_create_show_without_token_401(srv):
    st, _, raw = srv.post("/?dir=1&show=1&name=brett", data=b"", headers=AGENT)
    assert st == 401
    assert "retain token required" in json.loads(raw)["error"]


def test_public_add_edit_delete(srv):
    srv.post("/?dir=1&show=1&name=brett", data=b"", headers={**AGENT, **AUTH})
    # add without token
    st, _, _ = _add_file(srv, "brett")
    assert st == 200
    # read without token
    st, _, body = srv.get("/d/brett/n.txt", headers=AGENT)
    assert st == 200 and body == b"hi"
    # edit without token
    st, _, raw = srv.put("/d/brett/n.txt", data=b"changed", headers=AGENT)
    assert st == 200
    assert json.loads(raw)["expires_at"] is None
    # delete file without token
    st, _, _ = srv.delete("/d/brett/n.txt", headers=AGENT)
    assert st == 200
    st, _, _ = srv.get("/d/brett/n.txt", headers=AGENT)
    assert st == 404


def test_whole_dir_delete_token_only(srv):
    srv.post("/?dir=1&show=1&name=brett", data=b"", headers={**AGENT, **AUTH})
    st, _, raw = srv.delete("/d/brett", headers=AGENT)
    assert st == 401
    assert "whole-dir delete" in json.loads(raw)["error"]
    st, _, _ = srv.delete("/d/brett", headers={**AGENT, **AUTH})
    assert st == 200


def test_flip_existing_dir_to_show(srv):
    srv.post("/?dir=1&name=norm", data=b"", headers=AGENT)  # normal, expiring
    st, _, raw = srv.post("/d/norm?show=1", data=b"", headers={**AGENT, **AUTH})
    assert st == 200
    d = json.loads(raw)
    assert d["open"] is True and d["expires_at"] is None
    # now publicly writable
    st, _, _ = _add_file(srv, "norm")
    assert st == 200


def test_retained_dir_without_show_still_gated(srv):
    """open darf nicht auf normale retained Dirs ueberschwappen."""
    srv.post("/?dir=1&name=gated", data=b"", headers={**AGENT, **AUTH})
    st, _, raw = srv.post("/d/gated", data=b"")  # kein UA/Token
    assert st == 401
    st, _, _ = _add_file(srv, "gated")
    assert st == 401


def test_write_token_beats_open(srv):
    st, _, raw = srv.post("/?dir=1&show=1&write=1&name=locked", data=b"",
                          headers={**AGENT, **AUTH})
    assert st == 200
    wt = json.loads(raw)["write_token"]
    # ohne write-token: 401 (write_token greift trotz open)
    st, _, _ = _add_file(srv, "locked")
    assert st == 401
    # mit write-token: 200
    st, _, _ = _add_file(srv, "locked", headers={"X-Throway-Write": wt})
    assert st == 200


def test_show_flag_on_single_file_400(srv):
    _, up = srv.jpost("/?name=f.txt", data=b"x", headers=AGENT)
    st, _, raw = srv.post(f"/{up['id']}?show=1", data=b"", headers={**AGENT, **AUTH})
    assert st == 400
    assert "dirs only" in json.loads(raw)["error"]


def test_api_surface(srv):
    st, _, raw = srv.get("/api", headers=AGENT)
    spec = json.loads(raw)
    assert "show_dir" in spec["endpoints"]
    assert "show=1" in spec["endpoints"]["create_dir"]["note"]
    st, _, raw = srv.get("/help/retention", headers=AGENT)
    assert "SHOW-DIRS" in raw.decode()


def test_show_dir_survives_sweep_and_is_listed(srv):
    srv.post("/?dir=1&show=1&name=brett&listed=1", data=b"",
             headers={**AGENT, **AUTH})
    st, _, raw = srv.get("/d", headers=AGENT)
    d = json.loads(raw)
    entry = [e for e in d.get("dirs", d.get("entries", [])) if e["name"] == "brett"]
    assert entry, d
    assert entry[0]["expires_at"] is None


# --- 1.45.2 hard-validate: Concurrency + Fall-through ----------------------

def test_parallel_adds_no_loss(srv):
    """12 parallele Adds muessen 12 Files, 12 Meta-Eintraege und >=12
    History-Eintraege hinterlassen (Retro hard-validate: Race verlor
    Files + _dir_add-None fiel in den Einzel-Upload-Pfad)."""
    import threading
    srv.post("/?dir=1&show=1&name=hammer", data=b"", headers={**AGENT, **AUTH})
    codes, bodies = [], []

    def add(i):
        b, ct = multipart([(f"f{i}.txt", f"c{i}".encode(), "text/plain")])
        st, _, raw = srv.post("/d/hammer", data=b,
                              headers={**AGENT, "Content-Type": ct})
        codes.append(st)
        bodies.append(raw)

    ts = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes == [200] * 12, codes
    # jede Antwort muss eine DIR-Antwort sein — kein stiller Einzel-Upload
    for raw in bodies:
        assert json.loads(raw).get("dir") is True
    st, _, raw = srv.get("/d/hammer", headers=AGENT)
    d = json.loads(raw)
    assert len(d["files"]) == 12, d["files"]
    st, _, raw = srv.get("/d/hammer/history", headers=AGENT)
    assert json.loads(raw)["total"] >= 12


def test_dir_add_missing_dir_404(srv):
    """POST /d/gibtsnicht (multipart) muss 404 sein — vorher fiel es
    durch do_POST in den Raw-Upload und erzeugte still eine Datei."""
    b, ct = multipart([("x.txt", b"x", "text/plain")])
    st, _, raw = srv.post("/d/gibtsnicht", data=b,
                          headers={**AGENT, "Content-Type": ct})
    assert st == 404
    # und es wurde kein Einzel-File angelegt
    st, _, raw = srv.get("/browse", headers=AGENT)
    assert not any(e["name"] == "x.txt"
                   for e in json.loads(raw).get("files", []))
