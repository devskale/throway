"""Behavior tests for the existing throway surface (pre-pics regression net).

These describe observable HTTP behavior — they must survive the
modularization (package split) unchanged.
"""
import json
import time

import pytest

from conftest import Server, multipart

AGENT = {"User-Agent": "curl/8.0"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/120.0"}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "main")
    yield s
    s.stop()


@pytest.fixture
def fast_srv(tmp_path):
    s = Server(tmp_path / "fast", env_extra={"THROWAWAY_TTL_HOURS": "0"})
    yield s
    s.stop()


@pytest.fixture
def tight_srv(tmp_path):
    s = Server(tmp_path / "tight", env_extra={"THROWAWAY_RATE_LIMIT": "5"})
    yield s
    s.stop()


# --- core contract -------------------------------------------------------

def test_api_contract(srv):
    st, hd, body = srv.get("/api", headers=AGENT)
    assert st == 200
    spec = json.loads(body)
    assert spec["base_url"].endswith("/throway")
    for ep in ("upload", "download", "create_dir", "get_dir", "browse_files"):
        assert ep in spec["endpoints"]
    assert hd["Content-Type"].startswith("application/json")


def test_agent_homepage_is_help(srv):
    st, _, body = srv.get("/", headers=AGENT)
    assert st == 200
    text = body.decode()
    assert "throway" in text.lower()
    assert "/api" in text


def test_errors_are_structured(srv, tmp_path):
    """1.41.2 (P1): every JSON error carries a stable machine `code`; 429
    and 507 add Retry-After (header + field) so an agent waits instead of
    guessing."""
    # 404: not found -> code
    st, _, body = srv.get("/deadbeefdeadbeef", headers=AGENT)
    assert st == 404
    d = json.loads(body)
    assert d["code"] == "not_found" and d["error"]
    # 413: eigener Server mit kleinem MAX_FILE, damit der Body die
    # Content-Length-Grenze wirklich ueberschreitet, ohne dass der Client
    # beim Senden abbricht (Connection reset bei 5MB+).
    small = Server(tmp_path / "small",
                   env_extra={"THROWAWAY_MAX_FILE_BYTES": "1000"})
    try:
        st, _, body = small.post("/?name=big.bin", data=b"x" * 2000,
                                 headers=AGENT)
        assert st == 413
        assert json.loads(body)["code"] == "too_large"
    finally:
        small.stop()
    # 429: rate limit -> code + Retry-After header + retry_after field
    hits = 0
    for _ in range(140):
        st, hd, body = srv.get("/api", headers=AGENT)
        if st == 429:
            d = json.loads(body)
            assert d["code"] == "rate_limited"
            assert hd.get("Retry-After") == "60"
            assert d["retry_after"] == 60
            hits += 1
            break
    assert hits == 1, "rate limit not reached / structured as expected"


def test_help_topics_all_served(srv):
    st, _, body = srv.get("/help", headers=AGENT)
    assert st == 200
    idx = json.loads(body)
    topics = [t["id"] for t in idx["help"]]
    for t in topics:
        st2, _, b2 = srv.get(f"/help/{t}", headers=AGENT)
        assert st2 == 200, f"topic {t} -> {st2}"


def test_markdown_help_discoverable(srv):
    """1.38.3: markdown publishing must be discoverable (session feedback:
    an agent could not find how to publish a living .md doc and mistook an
    uploaded kanban HTML for a throway feature)."""
    st, _, body = srv.get("/help", headers=AGENT)
    assert "markdown" in [t["id"] for t in json.loads(body)["help"]]
    st, _, body = srv.get("/help/markdown", headers=AGENT)
    assert st == 200
    p = body.decode()
    for marker in ("?name=notes.md", "?raw=1", "?dir=1&name=", "PUT",
                   "history", "uploaded HTML file"):
        assert marker in p, f"/help/markdown misses {marker!r}"
    # agent homepage names markdown + points at the topic
    st, _, body = srv.get("/", headers=AGENT)
    home = body.decode()
    assert "doc.md" in home and "/help/markdown" in home
    # overview carries the rendering line + the not-an-app guard
    st, _, body = srv.get("/help/overview", headers=AGENT)
    ov = body.decode()
    assert ".md" in ov and "uploaded HTML file" in ov


# --- single files --------------------------------------------------------

def test_upload_raw_and_download_roundtrip(srv):
    data = b"hello throway\n" * 10
    st, meta = srv.upload_raw(data, name="note.txt")
    assert st == 200, meta
    assert meta["name"] == "note.txt"
    assert meta["url"].endswith(meta["id"])
    st, hd, body = srv.get("/" + meta["id"])
    assert st == 200
    assert body == data
    assert hd["Content-Type"].startswith("text/plain")


def test_upload_multipart(srv):
    body, ctype = multipart([("a.txt", b"aaa", "text/plain")])
    st, meta = srv.jpost("/", data=body, headers={"Content-Type": ctype})
    assert st == 200
    st, _, got = srv.get("/" + meta["id"])
    assert got == b"aaa"


def test_upload_too_large_rejected(srv):
    import urllib.error
    try:
        st, meta = srv.upload_raw(b"x" * (5 * 1024 * 1024 + 1), name="big.bin")
    except (urllib.error.URLError, OSError):
        # server rejects before draining the body -> connection reset; that is
        # also a rejection, not a silent accept
        return
    assert st == 413


def test_upload_and_edit_text(srv):
    st, meta = srv.upload_raw(b"v1", name="n.txt")
    st, _, _ = srv.put("/" + meta["id"], data=b"v2")
    assert st == 200
    st, _, body = srv.get("/" + meta["id"])
    assert body == b"v2"
    st, _, _ = srv.patch("/" + meta["id"], data=b"+more")
    assert st == 200
    st, _, body = srv.get("/" + meta["id"])
    assert body == b"v2+more"


def test_once_burn_after_reading(srv):
    st, meta = srv.upload_raw(b"secret", name="s.txt", qs="once=1")
    assert st == 200
    st, _, body = srv.get("/" + meta["id"])
    assert st == 200 and body == b"secret"
    st, _, _ = srv.get("/" + meta["id"])
    assert st == 404


def test_delete_file(srv):
    st, meta = srv.upload_raw(b"bye", name="b.txt")
    st, _, _ = srv.delete("/" + meta["id"])
    assert st == 200
    st, _, _ = srv.get("/" + meta["id"])
    assert st == 404


def test_expiry_removes_file(fast_srv):
    st, meta = fast_srv.upload_raw(b"temp", name="t.txt")
    assert st == 200
    time.sleep(1.2)
    st, _, _ = fast_srv.get("/" + meta["id"])
    assert st == 404


def test_rate_limit(tight_srv):
    seen200 = 0
    st = None
    for _ in range(10):
        st, _, _ = tight_srv.get("/api")
        if st == 429:
            break
        seen200 += 1
    assert seen200 >= 3, "limit never kicked in"
    assert st == 429


def test_tags_and_browse(srv):
    st, meta = srv.upload_raw(b"paper", name="p.pdf", qs="tag=papers&tag=2026")
    assert st == 200
    st, _, body = srv.get("/browse?tag=papers", headers=AGENT)
    assert st == 200
    listing = json.loads(body)
    entries = listing if isinstance(listing, list) else listing.get("files") or listing.get("entries") or []
    assert any(e.get("id") == meta["id"] for e in entries)


# --- share names & dirs --------------------------------------------------

def test_share_named_dir(srv):
    st, meta = srv.upload_raw(b"shared note", name="n.txt", qs="share=team-notes")
    assert st == 200
    assert meta["url"].endswith("/d/team-notes")
    st, _, body = srv.get("/d/team-notes", headers=AGENT)
    assert st == 200
    d = json.loads(body)
    files = d.get("files", [])
    assert any(f["name"] == "n.txt" for f in files)


def test_dir_create_add_edit_delete(srv):
    st, _, body = srv.post("/?dir=1&name=team7&listed=1")
    assert st == 200
    body, ctype = multipart([("slide.txt", b"one", "text/plain")])
    st, _, _ = srv.post("/d/team7", data=body, headers={"Content-Type": ctype})
    assert st == 200
    st, _, got = srv.get("/d/team7/slide.txt")
    assert st == 200 and got == b"one"
    st, _, _ = srv.put("/d/team7/slide.txt", data=b"two")
    assert st == 200
    st, _, got = srv.get("/d/team7/slide.txt")
    assert got == b"two"
    st, _, _ = srv.delete("/d/team7/slide.txt")
    assert st == 200
    st, _, _ = srv.get("/d/team7/slide.txt")
    assert st == 404
    st, _, lb = srv.get("/d", headers=AGENT)
    assert st == 200 and b"team7" in lb


def test_dir_history(srv):
    srv.post("/?dir=1&name=hist1")
    body, ctype = multipart([("h.txt", b"abc", "text/plain")])
    srv.post("/d/hist1", data=body, headers={"Content-Type": ctype})
    st, _, hb = srv.get("/d/hist1/history", headers=AGENT)
    assert st == 200
    h = json.loads(hb)
    assert h.get("total", 0) >= 1


# --- markdown rendering in browsers (issue: throway-md-render-browser) -----

MD = """# Quartalsreport

Fett **wichtig** und `code` plus [link](https://example.com).

| Kennzahl | Wert |
|---|---:|
| Umsatz | 42 |

- punkt eins
- punkt zwei

> zitiert

```python
print("hi")
```
"""


def test_md_rendered_for_browsers_raw_for_agents(srv):
    from conftest import multipart
    body, ctype = multipart([("report.md", MD.encode(), "text/markdown")])
    srv.post("/?dir=1&name=md-test")
    srv.post("/d/md-test", data=body, headers={"Content-Type": ctype})
    path = "/d/md-test/report.md"
    HTML = {"User-Agent": BROWSER["User-Agent"], "Accept": "text/html,*/*"}
    # browser: rendered HTML
    st, hd, page = srv.get(path, headers=HTML)
    assert st == 200 and hd["Content-Type"].startswith("text/html")
    assert "<h1>Quartalsreport</h1>" in page.decode()
    assert "<td>42</td>" in page.decode()
    assert "<strong>wichtig</strong>" in page.decode()
    assert "<title>Quartalsreport</title>" in page.decode()
    # browser + ?raw=1: raw markdown
    st, hd, raw = srv.get(path + "?raw=1", headers=HTML)
    assert st == 200 and b"# Quartalsreport" in raw
    assert not hd["Content-Type"].startswith("text/html")
    # agent (curl UA): raw
    st, hd, raw = srv.get(path, headers=AGENT)
    assert b"# Quartalsreport" in raw
    assert not hd["Content-Type"].startswith("text/html")
    # single-file .md upload renders for browsers too
    st, meta = srv.upload_raw(MD.encode(), name="single.md")
    st, hd, page = srv.get("/" + meta["id"], headers=HTML)
    assert st == 200 and "<h1>Quartalsreport</h1>" in page.decode()
    st, _, raw = srv.get("/" + meta["id"], headers=AGENT)
    assert b"# Quartalsreport" in raw


def test_dir_index_landing(srv):
    """Issue throway-dir-index-landing: DIR root serves index.html inline
    to browsers (bundle parity); ?listing=1 forces the listing; agents
    keep getting JSON; without index.html nothing changes."""
    from conftest import multipart
    srv.post("/?dir=1&name=landing")
    idx = b"""<!doctype html><html><head><title>Landing</title></head>
<body><h1>Report</h1><a href="data.json">daten</a></body></html>"""
    body, ctype = multipart([("index.html", idx, "text/html"),
                             ("data.json", b"{}", "application/json")])
    srv.post("/d/landing", data=body, headers={"Content-Type": ctype})
    # browser at DIR root -> rendered index.html (+ base tag + footer)
    st, hd, page = srv.get("/d/landing", headers=BROWSER)
    assert st == 200 and hd["Content-Type"].startswith("text/html")
    p = page.decode()
    assert "<h1>Report</h1>" in p
    assert '<base href="/throway/d/landing/">' in p
    assert "?listing=1" in p                      # footer link to the listing
    # ?listing=1 forces the plain listing
    st, _, page = srv.get("/d/landing?listing=1", headers=BROWSER)
    assert b"index.html" in page and b"<h1>Report</h1>" not in page
    # agents keep getting JSON
    st, _, body2 = srv.get("/d/landing", headers=AGENT)
    assert b'"files"' in body2
    # dir without index.html: unchanged listing
    srv.post("/?dir=1&name=no-landing")
    st, _, page = srv.get("/d/no-landing", headers=BROWSER)
    assert st == 200 and b"\u2190 throway" in page or b"throway" in page
    assert b"<h1>Report</h1>" not in page


def test_dir_write_token(srv):
    """Issue throway-dir-write-token: opt-in write protection for dirs."""
    from conftest import multipart
    # create with write=1 -> token in response
    st, _, body = srv.post("/?dir=1&name=protected&write=1")
    d = json.loads(body)
    tok = d.get("write_token")
    assert st == 200 and tok and d.get("write_protected") is True
    # writes WITHOUT token -> 401
    body_mp, ctype = multipart([("x.txt", b"nope", "text/plain")])
    st, _, _ = srv.post("/d/protected", data=body_mp, headers={"Content-Type": ctype})
    assert st == 401
    st, _, _ = srv.put("/d/protected/x.txt", data=b"evil")
    assert st == 401
    st, _, _ = srv.patch("/d/protected/x.txt", data=b"!")
    assert st == 401
    st, _, _ = srv.delete("/d/protected")
    assert st == 401
    # wrong token -> 401
    st, _, _ = srv.post("/d/protected", data=body_mp,
                        headers={"Content-Type": ctype, "X-Throway-Write": "wrong" * 8})
    assert st == 401
    # the share= hole is closed too
    st, _, _ = srv.post("/?share=protected", data=b"evil")
    assert st == 401
    # reads stay open: listing, file, history, zip
    st, _, _ = srv.get("/d/protected", headers=AGENT)
    assert st == 200
    st, _, _ = srv.get("/d/protected/history", headers=AGENT)
    assert st == 200
    st, _, _ = srv.get("/d/protected?zip=1")
    assert st == 200
    # write WITH token (header) -> 200
    st, _, _ = srv.post("/d/protected", data=body_mp,
                        headers={"Content-Type": ctype, "X-Throway-Write": tok})
    assert st == 200
    # write WITH token (query) -> 200
    st, _, _ = srv.put("/d/protected/x.txt?write=" + tok, data=b"v2")
    assert st == 200, "query token"
    st, _, body = srv.get("/d/protected/x.txt")
    assert body == b"v2"
    st, _, _ = srv.delete("/d/protected?write=" + tok)
    assert st == 200

def test_dir_write_token_backward_compatible(srv):
    """Dirs without write=1 behave exactly as before; re-creation of a
    protected dir never re-reveals the token."""
    from conftest import multipart
    st, _, body = srv.post("/?dir=1&name=open-dir")
    d = json.loads(body)
    assert "write_token" not in d and "write_protected" not in d
    body_mp, ctype = multipart([("f.txt", b"ok", "text/plain")])
    st, _, _ = srv.post("/d/open-dir", data=body_mp, headers={"Content-Type": ctype})
    assert st == 200                       # no token needed, as ever
    # create-or-get on a protected dir: no token leak
    srv.post("/?dir=1&name=leaky&write=1")
    st, _, body = srv.post("/?dir=1&name=leaky&write=1")
    d2 = json.loads(body)
    assert "write_token" not in d2 and d2.get("write_protected") is True
    # custom token via write=<token>
    st, _, body = srv.post("/?dir=1&name=custom&write=my-own-token-123")
    d3 = json.loads(body)
    assert d3.get("write_token") == "my-own-token-123"
    st, _, _ = srv.delete("/d/custom", headers={"X-Throway-Write": "my-own-token-123"})
    assert st == 200



# --- idempotency (1.44.0 regression: hashlib/_read_json NameError ate the
# feature silently since 1.42.x — replay never worked in production) ------

def test_idempotency_replay(srv):
    hdrs = {**AGENT, "Idempotency-Key": "idem-fixed-1"}
    st1, b1 = srv.jpost("/?name=i.txt", data=b"v1", headers=hdrs)
    assert st1 == 200
    st2, b2 = srv.jpost("/?name=i.txt", data=b"v1", headers=hdrs)
    assert st2 == 200
    assert b1["id"] == b2["id"]
    assert b2.get("idempotent_replay") is True


def test_once_and_share_rejected(srv):
    """1.45.8: once=1 gilt nur fuer Einzeldateien — /api und AGENTS.md
    sagen 'not with &share=', der Code nahm es vorher still an und
    ignorierte once (Luecken-Batterie: Vertragsbruch)."""
    st, _, raw = srv.post("/?once=1&share=oncekey", data=b"x", headers=AGENT)
    assert st == 400
    assert "mutually exclusive" in raw.decode()


def test_help_errors_topic(srv):
    """P3 (sota): Fehler-Codes + Retry-Strategie als abrufbares Topic."""
    st, _, raw = srv.get("/help/errors", headers=AGENT)
    assert st == 200
    body = raw.decode()
    for token in ("rate_limited", "pool_full", "Retry-After", "100/min", "do not retry"):
        assert token in body, token
    st, _, raw = srv.get("/help", headers=AGENT)
    assert b'"errors"' in raw


def test_json_html_override(srv):
    """P6 (sota): ?json=1 erzwingt JSON auch mit Browser-UA, ?html=1
    erzwingt HTML auch mit curl-UA (Custom-UA-Parsing-Falle)."""
    import json as _j
    srv.post("/?dir=1&name=ovr&listed=1", data=b"", headers=AGENT)
    browser = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/120.0"}
    st, _, raw = srv.get("/d/ovr?json=1", headers=browser)
    assert st == 200
    j = _j.loads(raw)
    assert j.get("dir") is True or "files" in j
    st2, _, raw2 = srv.get("/d/ovr?html=1", headers=AGENT)
    assert st2 == 200
    assert b"<html" in raw2.lower() or b"<!doctype" in raw2.lower()


def test_dir_create_multipart_adds_to_existing(srv):
    """P5 (sota): create-or-get mit Multipart-Parts fuegt die Parts hinzu
    (wie POST /d/<key>) statt sie still zu verwerfen — Retry-Loop konvergiert."""
    import json as _j
    mp1, ct1 = multipart([
        ("a.txt", b"A", "text/plain"), ("b.txt", b"B", "text/plain"),
        ("c.txt", b"C", "text/plain")])
    st, _, raw = srv.post("/?dir=1&name=bridge", data=mp1, headers={**AGENT,
                          "Content-Type": ct1})
    assert st == 200
    names = {f["name"] for f in _j.loads(raw)["files"]}
    assert names == {"a.txt", "b.txt", "c.txt"}
    mp2, ct2 = multipart([("d.txt", b"D", "text/plain"), ("e.txt", b"E", "text/plain")])
    st, _, raw = srv.post("/?dir=1&name=bridge", data=mp2, headers={**AGENT,
                          "Content-Type": ct2})
    assert st == 200
    names = {f["name"] for f in _j.loads(raw)["files"]}
    assert names == {"a.txt", "b.txt", "c.txt", "d.txt", "e.txt"}
    st, _, raw = srv.get("/d/bridge/history", headers=AGENT)
    hist = _j.loads(raw)["history"]
    assert sum(1 for h in hist if h["action"] == "add") == 5
