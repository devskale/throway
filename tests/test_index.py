"""Unit tests for throway/index.page() — pure render (1.51.0).

mdrender precedent: no server, no HTTP — page() is deterministic over the
stats dict. The behavior surface stays covered by the HTTP suite; these
pin the presentation contract (markers, substitution, escaping, JS/CSS
sanity — the things a string-typo would otherwise only break in the
browser).
"""
import re
import shutil
import subprocess

import pytest

from throway import index


STATS = {
    "files_now": 3,
    "bytes_now": 2 * 1024 * 1024 + 512,
    "pool_size": 100 * 1024 * 1024,
    "since_files": 7,
    "since_bytes": 10 * 1024 * 1024,
    "version": "9.9.9",
    "ttl_hours": 4,
    "prefix": "/throway",
    "max_mb": 5,
    "agent_description": "AGENT <COPY> & so on",
}


def test_markers_present():
    h = index.page(STATS)
    for m in (
        "dropzone.min.css", "dropzone.min.js",
        "id=tab-files", "id=tab-text", "id=tab-link", "id=tab-gallery",
        "v9.9.9", "4h (default)",
        "'/throway/pics'", "'/throway/api'", "'/throway/help'",
        "Drop files here", "since start",
    ):
        assert m in h, m


def test_stats_rendered():
    h = index.page(STATS)
    assert "<b>3</b>" in h                      # files_now
    assert "<b>7</b>" in h                      # since_files
    assert "pool: 2.0 MB of 100.0 MB" in h      # fmt via module formatter
    assert "width:2%" in h                      # pct 2.1 -> 2
    assert "since start" in h


def test_max_mb_substituted_not_leaked():
    h = index.page(STATS)
    assert "__MAX_MB__" not in h
    assert "var MAX_MB = 5;" in h


def test_agent_description_escaped():
    h = index.page(STATS)
    assert "&lt;COPY&gt;" in h
    assert "<COPY>" not in h


def test_js_constants_are_str():
    """Retro 1.43.2 (a) für die Homepage nachgezogen: ein Trailing-Comma
    macht die JS-Konstante zum Tupel und crasht erst beim Rendern."""
    assert isinstance(index._INDEX_JS, str)
    assert isinstance(index._INDEX_CSS, str)


def test_rendered_scripts_node_check():
    """Retro 1.43.2 (b): jedes gerenderte Inline-<script> parst via
    node --check — ungültiges JS in der String-Konstante fällt hier,
    nicht erst im Browser."""
    if not shutil.which("node"):
        pytest.skip("node nicht installiert")
    h = index.page(STATS)
    scripts = re.findall(r"<script>(.*?)</script>", h, re.S)
    assert scripts, "kein Inline-Script gerendert"
    for i, js in enumerate(scripts):
        r = subprocess.run(["node", "--check", "/dev/stdin"], input=js,
                           capture_output=True, text=True)
        assert r.returncode == 0, f"script {i} parst nicht: {r.stderr[:300]}"


def test_index_css_brace_balance():
    """Retro 2026-10-02: verwaiste/doppelte } legen die FOLGENDE Regel
    still weg — Release-Check-Spiegelung als Test."""
    css = index._INDEX_CSS
    assert css.count("{") == css.count("}"), \
        f"unbalanced: {css.count('{')} {{ vs {css.count('}')} }}"
