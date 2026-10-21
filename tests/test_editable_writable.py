"""Behavior tests for the editable vs writable distinction (1.58.0).

editable = CONTENT question (text payload? PUT/PATCH work) — per file.
writable = PERMISSION question (writes accepted without a token?) —
object-level. Agents misread editable:false on dir containers as
"read-only" (smoke-test 2026-10-21); writable answers the question they
were actually asking.
"""
import json

import pytest

from conftest import Server, multipart

AGENT = {"User-Agent": "curl/8.0"}
TOK = "rt-ew-abc123"
AUTH = {"Authorization": f"Bearer {TOK}"}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "ew", env_extra={"THROWAWAY_RETAIN_TOKEN": TOK})
    yield s
    s.stop()


def _mk_dir(srv, qs="", headers=None, fname="a.txt"):
    body, ctype = multipart([(fname, b"hello", "text/plain")])
    st, _, raw = srv.post(f"/?dir=1{qs}", data=body,
                          headers={**AGENT, "Content-Type": ctype,
                                   **(headers or {})})
    return st, json.loads(raw)


def test_open_dir_writable_true_editable_false(srv):
    """The confusion case: open dir, no token — container says
    editable:false but writes succeed without any token."""
    st, d = _mk_dir(srv, "&name=ew-open")
    assert st == 200
    assert d["editable"] is False          # container is not a text payload
    assert d["writable"] is True           # permission: anyone may write
    # prove it: PUT without any token succeeds
    st, _, raw = srv.put(f"/d/ew-open/a.txt", data=b"changed",
                         headers={**AGENT, "Content-Type": "text/plain"})
    assert st == 200
    assert srv.get("/d/ew-open/a.txt", headers=AGENT)[2] == b"changed"


def test_write_protected_dir_writable_false(srv):
    st, d = _mk_dir(srv, "&name=ew-prot&write=1")
    assert st == 200
    assert d["writable"] is False
    assert d["write_protected"] is True
    assert "write_token" in d               # shown exactly once
    # write without token: 401
    body, ctype = multipart([("b.txt", b"x", "text/plain")])
    st, _, raw = srv.post("/d/ew-prot", data=body,
                          headers={**AGENT, "Content-Type": ctype})
    assert st == 401
    assert json.loads(raw)["code"] == "write_denied"


def test_show_dir_writable_true(srv):
    st, d = _mk_dir(srv, "&name=ew-show&show=1", headers=AUTH)
    assert st == 200
    assert d["open"] is True
    assert d["writable"] is True            # show-dir: public write
    assert d["persistence"]["retention"] == "indefinite"


def test_retained_dir_without_show_writable_false(srv):
    st, d = _mk_dir(srv, "&name=ew-ret", headers=AUTH)
    assert st == 200
    assert d["persistence"]["retention"] == "indefinite"
    assert d["writable"] is False           # retain token gates writes


def test_single_file_writable_true(srv):
    st, d = srv.jpost("/?name=ew.txt", data=b"plain",
                      headers={**AGENT, "Content-Type": "text/plain"})
    assert st == 200
    assert d["editable"] is True             # text payload
    assert d["writable"] is True             # no write gate on plain files


def test_retained_single_file_writable_false(srv):
    st, d = srv.jpost("/?name=ewr.txt", data=b"kept",
                      headers={**AGENT, "Content-Type": "text/plain", **AUTH})
    assert st == 200
    assert d["writable"] is False            # retain token gates writes
    assert d["persistence"]["retention"] == "indefinite"


def test_bundle_writable_false_immutable(srv):
    body, ctype = multipart([("x.txt", b"one", "text/plain"),
                             ("y.txt", b"two", "text/plain")])
    st, d = srv.jpost("/", data=body,
                      headers={**AGENT, "Content-Type": ctype})
    assert st == 200
    assert d["bundle"] is True
    assert d["editable"] is False
    assert d["writable"] is False            # bundles are immutable snapshots


def test_help_edit_explains_the_distinction(srv):
    st, _, raw = srv.get("/help/edit", headers=AGENT)
    assert st == 200
    body = raw.decode()
    assert "writable" in body
    assert "CONTENT question" in body
    st, _, raw = srv.get("/help/dirs", headers=AGENT)
    body = raw.decode()
    assert "editable vs writable" in body
