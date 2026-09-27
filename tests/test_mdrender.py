"""Tests for throway/mdrender.py (issue: throway-md-render-browser)."""
from throway import mdrender


def r(text, **kw):
    return mdrender.render(text, **kw)


# --- inline ---------------------------------------------------------------

def test_headings_and_title_from_first_h1():
    page = r("# Mein Report\n\nText")
    assert "<h1>Mein Report</h1>" in page
    assert "<title>Mein Report</title>" in page


def test_title_falls_back_to_filename():
    page = r("nur text", title="report.md")
    assert "<title>report.md</title>" in page


def test_bold_italic_code():
    body = r("x **fett** und *kursiv* und `code *hier*` und __dick__")
    assert "<strong>fett</strong>" in body
    assert "<em>kursiv</em>" in body
    assert "<code>code *hier*</code>" in body          # protected
    assert "<strong>dick</strong>" in body


def test_links_and_autolink_and_scheme_guard():
    body = r("[klick](https://example.com) und https://foo.bar/baz und [evil](javascript:alert(1))")
    assert '<a href="https://example.com">klick</a>' in body
    assert '<a href="https://foo.bar/baz">' in body
    assert 'href="javascript:' not in body             # scheme guard
    assert "[evil](javascript:alert(1))" in body       # kept literal


def test_html_escaped():
    body = r("<script>alert(1)</script> & <b>bold</b>")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "&amp;" in body


# --- blocks ---------------------------------------------------------------

def test_lists_nested_and_ordered():
    body = r("- a\n- b\n  - b1\n  - b2\n1. eins\n2. zwei")
    assert "<ul>" in body and "<li>a</li>" in body
    assert "<ul><li>b1</li><li>b2</li></ul>" in body   # nested inside b
    assert "<ol><li>eins</li><li>zwei</li></ol>" in body


def test_table():
    body = r("| Name | Zahl |\n|---|---:|\n| capri | 3 |\n| Extra | Spalte |")
    assert "<table>" in body and "<th>Name</th>" in body and "<th>Zahl</th>" in body
    assert "<td>capri</td>" in body
    assert "<td>Extra</td><td>Spalte</td>" in body     # ragged row padded


def test_fence_escapes_html():
    body = r("```\n<b>not html</b>\n```")
    assert "<pre><code>" in body
    assert "&lt;b&gt;not html&lt;/b&gt;" in body


def test_blockquote_and_hr():
    body = r("> zitiert\n\n---\n\nweiter")
    assert "<blockquote><p>zitiert</p></blockquote>" in body
    assert "<hr>" in body


def test_raw_url_footer():
    page = r("text", raw_url="/throway/x.md?raw=1")
    assert 'href="/throway/x.md?raw=1"' in page
    assert "markdown" in page
