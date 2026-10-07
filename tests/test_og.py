"""Social preview cards (1.54.0): og:/twitter: meta on shared objects.

Unit level: throway/og.py (escaping, injection, extraction, md_brief,
canonical). Behavior level: the real server as subprocess — HTML files
get card meta only for card-crawler UAs (browsers/agents byte-identical),
md docs / dir pages / bundle index pages carry meta for everyone.
"""
import json
import time

import pytest

from conftest import Server, multipart

from throway import og

AGENT = {"User-Agent": "curl/8.0"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "text/html"}
TWBOT = {"User-Agent": "Mozilla/5.0 (compatible; Twitterbot/1.0; "
                       "+https://developer.twitter.com/)"}


# --- unit: og.py -------------------------------------------------------------

def test_meta_escapes_user_content():
    s = og.meta('a"b<c>', "d&f<geb>", "https://x/y?z=1")
    assert 'content="a&quot;b&lt;c&gt;"' in s
    assert 'content="d&amp;f&lt;geb&gt;"' in s
    assert 'content="https://x/y?z=1"' in s
    assert '<meta name="twitter:card" content="summary_large_image">' in s
    assert 'og:image' in s and "og-throway.png" in s


def test_meta_omits_empty_description_and_url():
    s = og.meta("t", "", "")
    assert "og:description" not in s
    assert "og:url" not in s


def test_is_crawler():
    assert og.is_crawler(TWBOT["User-Agent"])
    assert og.is_crawler("Twitterbot/1.0")          # bare UA also matches
    assert og.is_crawler("facebookexternalhit/1.1")
    assert og.is_crawler("Mozilla/5.0 (compatible; Discordbot/2.0)")
    assert not og.is_crawler("Mozilla/5.0 (Macintosh) Chrome/126")
    assert not og.is_crawler("curl/8.0")
    assert not og.is_crawler("")
    assert not og.is_crawler(None)


def test_has_card():
    assert og.has_card(b'<meta property="og:title" content="mine">')
    assert og.has_card(b"<meta name=twitter:card content=summary>")
    assert og.has_card(b'<meta property=\'og:image\' content="x">')
    assert not og.has_card(b"<title>plain page</title>")
    assert not og.has_card(b'<meta name="description" content="x">')


def test_inject_after_head_and_prepend_without():
    html = b"<html><HEAD lang=en><title>t</title></HEAD><body></body></html>"
    out = og.inject(html, "<meta x=1>")
    assert out.startswith(b"<html><HEAD lang=en><meta x=1><title>")
    out2 = og.inject(b"<html><body>no head</body></html>", "<meta x=1>")
    assert out2.startswith(b"<html><head><meta x=1></head><body>")
    out3 = og.inject(b"<body>not even html</body>", "<meta x=1>")
    assert out3.startswith(b"<head><meta x=1></head><body>")


def test_page_title_extracts_and_falls_back():
    assert og.page_title(b"<title>  Hello\n World </title>") == "Hello World"
    assert og.page_title(b"<TITLE>x &amp; y</TITLE>") == "x &amp; y"
    assert og.page_title(b"<html><body>none</body>", fallback="f.md") == "f.md"
    assert og.page_title(b"<title></title>", fallback="f") == "f"


def test_page_description():
    h = b'<meta name="description" content="first  para\nsecond">'
    assert og.page_description(h) == "first para second"
    assert og.page_description(b"<p>none</p>") == ""


def test_md_brief():
    md = "# Head\n\n```code\n```\n\n> quote\n\n| a | b |\n\n- **bold** item\n\nPlain [link](http://x) text.\n"
    assert og.md_brief(md) == "bold item"          # first prose line is the list item
    md2 = "# Head\n\nReal *first* paragraph here.\n"
    assert og.md_brief(md2) == "Real first paragraph here."
    assert og.md_brief("") == ""
    assert og.md_brief("# only a heading") == ""
    long = "x" * 300
    t = og.md_brief(long)
    assert len(t) == 200 and t.endswith("…")


def test_canonical():
    assert og.canonical("https://skale.dev/throway", "/throway",
                        "/throway/abc") == "https://skale.dev/throway/abc"
    assert og.canonical("https://skale.dev/throway", "/throway",
                        "/abc") == "https://skale.dev/throway/abc"
    assert og.canonical("http://127.0.0.1:8000", "",
                        "/d/k/f.html") == "http://127.0.0.1:8000/d/k/f.html"


# --- behavior: real server ---------------------------------------------------

@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    s = Server(tmp_path_factory.mktemp("og"))
    yield s
    s.stop()


def _upload(srv, name, data, ctype):
    st, h, raw = srv.post(f"/?name={name}", data=data, headers=AGENT)
    assert st == 200, raw
    return json.loads(raw)


HTML = (b"<!doctype html><html lang=en><head><meta charset=utf-8>"
        b"<title>What is Codemode \xe2\x80\x94 visualized</title></head>"
        b"<body><h1>hi</h1></body></html>")


def test_html_browser_bytes_untouched(srv):
    f = _upload(srv, "page.html", HTML, "text/html")
    st, h, body = srv.get(f"/{f['id']}", headers=BROWSER)
    assert st == 200
    assert body == HTML                              # cmp-grade identity
    assert b"og:" not in body and b"twitter:" not in body


def test_html_crawler_gets_card_from_own_title(srv):
    f = _upload(srv, "page.html", HTML, "text/html")
    st, h, body = srv.get(f"/{f['id']}", headers=TWBOT)
    assert st == 200
    assert b"What is Codemode \xe2\x80\x94 visualized" in body      # original kept
    assert b'property="og:title" content="What is Codemode' in body
    assert b'property="og:description" content="HTML page hosted on throway' in body
    assert b'property="og:image" content="https://skale.dev/og-throway.png"' in body
    assert b'name="twitter:card" content="summary_large_image"' in body
    assert b'property="og:url" content="https://skale.dev/throway/' in body
    assert h.get("Vary", "").lower() == "user-agent"


def test_html_crawler_own_card_wins(srv):
    own = HTML.replace(b"<title>", b'<meta property="og:image" content="https://me.example/p.png"><title>', 1)
    f = _upload(srv, "mine.html", own, "text/html")
    st, h, body = srv.get(f"/{f['id']}", headers=TWBOT)
    assert st == 200
    assert b"throway</span>" not in body
    assert b'og:site_name' not in body               # our snippet absent
    assert b'content="https://me.example/p.png"' in body
    assert b'og:image' in body                       # uploader's own card


def test_html_agent_bytes_untouched(srv):
    f = _upload(srv, "page.html", HTML, "text/html")
    st, h, body = srv.get(f"/{f['id']}", headers=AGENT)
    assert st == 200 and body == HTML


def test_html_raw_escape_beats_crawler(srv):
    # 1.54.1: ?raw=1 is the byte-exact escape — even card crawlers get raw
    f = _upload(srv, "page.html", HTML, "text/html")
    st, h, body = srv.get(f"/{f['id']}?raw=1", headers=TWBOT)
    assert st == 200 and body == HTML
    assert b"og:" not in body


def test_md_doc_carries_card_for_browsers(srv):
    md = b"# My Report\n\nFirst paragraph with **bold** prose.\n"
    f = _upload(srv, "report.md", md, "text/markdown")
    st, h, body = srv.get(f"/{f['id']}", headers=BROWSER)
    assert st == 200 and b"<h1>My Report</h1>" in body
    assert b'property="og:title" content="My Report"' in body
    assert b'content="First paragraph with bold prose."' in body
    assert b'name="twitter:card"' in body
    # raw escape: no card, exact bytes
    st, h, raw = srv.get(f"/{f['id']}?raw=1", headers=BROWSER)
    assert st == 200 and raw == md and b"og:" not in raw


def test_bundle_index_crawler_card(srv):
    files = [("index.html", HTML, "text/html"),
             ("app.js", b"console.log(1)", "text/javascript")]
    body, ctype = multipart(files)
    st, h, raw = srv.post("/", data=body, headers={**AGENT, "Content-Type": ctype})
    assert st == 200
    fid = json.loads(raw)["id"]
    st, h, page = srv.get(f"/{fid}/", headers=BROWSER)
    assert st == 200 and b"<base href=" in page and b"og:site_name" not in page
    st, h, page = srv.get(f"/{fid}/", headers=TWBOT)
    assert st == 200
    assert b'property="og:title" content="What is Codemode' in page
    assert b"name=\"twitter:card\"" in page


def test_dir_page_carries_card(srv):
    st, h, raw = srv.post("/?dir=1&name=ogcard-" + str(__import__("time").time()).replace(".", "-"),
                          data=b"", headers=AGENT)
    assert st == 200
    key = json.loads(raw)["id"]
    body, ctype = multipart([("n.txt", b"hi", "text/plain")])
    srv.post(f"/d/{key}", data=body, headers={**AGENT, "Content-Type": ctype})
    st, h, page = srv.get(f"/d/{key}", headers=BROWSER)
    assert st == 200
    assert b'property="og:title" content="Dir ' in page
    assert b"content=\"1 file in a throway dir" in page
    assert b'property="og:url" content="https://skale.dev/throway/d/' in page


def test_dir_html_file_crawler_card(srv):
    # the canonical share case: /d/<key>/<file>.html on X
    st, h, raw = srv.post("/?dir=1", data=b"", headers=AGENT)
    key = json.loads(raw)["id"]
    body, ctype = multipart([("doc.html", HTML, "text/html")])
    srv.post(f"/d/{key}", data=body, headers={**AGENT, "Content-Type": ctype})
    st, h, page = srv.get(f"/d/{key}/doc.html", headers=TWBOT)
    assert st == 200
    assert b'property="og:title" content="What is Codemode' in page
    assert b'property="og:url" content="https://skale.dev/throway/d/' in page
    # browser twin: byte-identical
    st, h, page = srv.get(f"/d/{key}/doc.html", headers=BROWSER)
    assert page == HTML
    srv.delete(f"/d/{key}", headers=AGENT)
