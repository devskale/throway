"""1.56.1 — /assets/: statische Homepage-Assets (throway/static),
Whitelist, Content-Type, Cache-Header."""
import json

from conftest import Server

AGENT = {"User-Agent": "curl/8.0"}


def _srv(tmp_path):
    s = Server(tmp_path / "main")
    yield s
    s.stop()


def test_assets_served_with_type_and_cache(tmp_path):
    s = next(_srv(tmp_path))
    st, h, b = s.get("/assets/index.js", AGENT)
    assert st == 200 and "javascript" in h.get("Content-Type", "")
    assert "max-age" in h.get("Cache-Control", "")
    assert b"applyAuth" in b                       # das echte homepage-js
    st, h, b = s.get("/assets/index.css", AGENT)
    assert st == 200 and "css" in h.get("Content-Type", "")


def test_assets_whitelist(tmp_path):
    s = next(_srv(tmp_path))
    for bad in ("/assets/evil.js", "/assets/../store.py", "/assets/"):
        st, _, _ = s.get(bad, AGENT)
        assert st == 404, bad
