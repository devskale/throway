"""throway/mdrender.py — minimal Markdown-to-HTML renderer for browser
views of .md files (issue: throway-md-render-browser).

Self-contained: stdlib only, no JS, no external CSS. Output is fully
HTML-escaped before inline formatting is applied, link schemes are
restricted (http/https/mailto/relative/anchor), and code spans are
tokenized so inline formatting never fires inside them.

Scope (what the firmenindex reports need — deliberately not full GFM):
headings, paragraphs, bullet/ordered lists (nested), GFM tables, fenced
code blocks, block quotes, horizontal rules, links + bare autolinks,
bold/italic, inline code.

Interface: render(text, title=None, raw_url=None) -> full HTML page.
"""

import html as _html
import re

_CSS = (
    ":root{--bg:#fff;--card:#fafafa;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}"
    "*{box-sizing:border-box}"
    "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "background:var(--bg);color:var(--ink);line-height:1.6;-webkit-text-size-adjust:100%}"
    "main{max-width:760px;margin:0 auto;padding:2.5rem 1.5rem 4rem}"
    "h1,h2,h3,h4,h5,h6{line-height:1.25;margin:1.5em 0 .5em}"
    "h1{font-size:1.7rem;margin-top:0}"
    "h2{font-size:1.35rem}h3{font-size:1.15rem}"
    "p{margin:.8em 0}"
    "code{background:var(--card);border:1px solid var(--line);border-radius:4px;"
    "padding:.08rem .35rem;font-size:.9em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}"
    "pre{background:var(--card);border:1px solid var(--line);border-radius:8px;"
    "padding:.8rem 1rem;overflow-x:auto}"
    "pre code{border:0;padding:0;background:none}"
    "blockquote{border-left:3px solid var(--line);margin:1em 0;padding:.2em 1em;color:var(--muted)}"
    "table{border-collapse:collapse;margin:1em 0;width:100%;font-size:.92rem;display:block;overflow-x:auto}"
    "th,td{border:1px solid var(--line);padding:.45rem .6rem;text-align:left;vertical-align:top}"
    "th{background:var(--card)}"
    "a{color:var(--accent)}"
    "hr{border:0;border-top:1px solid var(--line);margin:2em 0}"
    "ul,ol{padding-left:1.4rem;margin:.8em 0}"
    "li{margin:.25em 0}"
    "p.raw{margin-top:2.5rem;font-size:.85rem}"
    "p.raw a{color:var(--muted)}"
    "@media(max-width:560px){main{padding:1.5rem 1rem 3rem}}"
)

_FENCE_RE = re.compile(r"^```(\w*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})\s*$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def _inline(s):
    """Inline formatting on an escaped string: code spans, links, autolinks,
    bold, italic. Code-span content is protected via placeholders."""
    s = _html.escape(s, quote=False)
    codes = []

    def _code(m):
        codes.append("<code>%s</code>" % m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    s = re.sub(r"`([^`]+)`", _code, s)

    def _link(m):
        text, url = m.group(1), m.group(2).strip()
        if not re.match(r"^(https?://|mailto:|#|/)", url, re.I):
            return m.group(0)                     # unknown scheme: keep literal
        return '<a href="%s">%s</a>' % (_html.escape(url, quote=True), text)

    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, s)
    s = re.sub(r"(?<![\"'=\w])(https?://[^\s<>\x00]+[^\s<>\x00.,;:!?])",
               lambda m: '<a href="%s">%s</a>' % (m.group(1), m.group(1)), s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"(?<!_)__([^_]+)__(?!_)", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!_)_([^_\s][^_]*?)_(?!_)", r"<em>\1</em>", s)
    for i, c in enumerate(codes):
        s = s.replace("\x00%d\x00" % i, c)
    return s


def _is_table_start(line, nxt):
    return ("|" in line and nxt is not None and "|" in nxt
            and "-" in nxt and _SEP_RE.match(nxt) is not None
            and set(nxt.strip()) <= set("|-: "))


def _blocks(lines):
    """Parse lines into block-level HTML. Returns (html, first_h1)."""
    out = []
    first_h1 = None
    i, n = 0, len(lines)

    def _nxt(k):
        return lines[k] if k < n else None

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = _FENCE_RE.match(stripped)
        if m:
            buf = []
            i += 1
            while i < n and not _FENCE_RE.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            i += 1                              # closing fence (or EOF)
            out.append("<pre><code>%s</code></pre>" % _html.escape("\n".join(buf)))
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            lvl = len(m.group(1))
            txt = m.group(2).strip()
            if lvl == 1 and first_h1 is None:
                first_h1 = txt
            out.append("<h%d>%s</h%d>" % (lvl, _inline(txt), lvl))
            i += 1
            continue
        if _HR_RE.match(stripped):
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>"
                       % _inline(" ".join(b for b in buf if b)))
            continue
        if _is_table_start(stripped, _nxt(i + 1)):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1

            def _tr(cells, tag):
                cells = (cells + [""] * len(header))[:len(header)]
                return "<tr>" + "".join(
                    "<%s>%s</%s>" % (tag, _inline(c), tag) for c in cells) + "</tr>"

            out.append("<table><thead>%s</thead><tbody>%s</tbody></table>"
                       % (_tr(header, "th"), "".join(_tr(r, "td") for r in rows)))
            continue
        m = _LIST_RE.match(line)
        if m:
            items = []                          # (indent, ordered, text)
            while i < n:
                mm = _LIST_RE.match(lines[i])
                if mm:
                    items.append((len(mm.group(1).replace("\t", "  ")),
                                  bool(re.match(r"\d", mm.group(2))),
                                  mm.group(3)))
                    i += 1
                elif (lines[i].strip()
                      and lines[i][:1] in (" ", "\t") and items):
                    items[-1] = items[-1][:2] + (items[-1][2] + " " + lines[i].strip(),)
                    i += 1
                elif not lines[i].strip():
                    if i + 1 < n and _LIST_RE.match(lines[i + 1]):
                        i += 1
                        continue
                    break
                else:
                    break

            def build(pos, indent):
                ordered = items[pos][1]
                tag = "ol" if ordered else "ul"
                acc = ["<%s>" % tag]
                while pos < len(items):
                    ind, o, txt = items[pos]
                    if ind < indent - 1:
                        break
                    if ind >= indent + 2:
                        sub, pos = build(pos, ind)
                        acc[-1] = acc[-1][:-5] + sub + "</li>"
                        continue
                    if o != ordered:
                        break                       # list type changes → close
                    acc.append("<li>%s</li>" % _inline(txt))
                    pos += 1
                acc.append("</%s>" % tag)
                return "".join(acc), pos

            pos = 0
            parts = []
            while pos < len(items):
                part_html, pos = build(pos, items[pos][0])
                parts.append(part_html)
            out.append("".join(parts))
            continue
        # paragraph: accumulate until a blank line or the next block start
        buf = [stripped]
        i += 1
        while (i < n and lines[i].strip()
               and not _FENCE_RE.match(lines[i].strip())
               and not _HEADING_RE.match(lines[i].strip())
               and not _HR_RE.match(lines[i].strip())
               and not lines[i].strip().startswith(">")
               and not _LIST_RE.match(lines[i])
               and not _is_table_start(lines[i].strip(), _nxt(i + 1))):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % "<br>".join(_inline(b) for b in buf))
    return "".join(out), first_h1


def render(text, title=None, raw_url=None):
    """Render markdown text as a full, self-contained HTML page.
    title: filename (page <title> fallback if no `# ` heading exists).
    raw_url: if given, a small footer links to the raw source."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body, first_h1 = _blocks(lines)
    page_title = _html.escape(first_h1 or title or "markdown")
    raw = ""
    if raw_url:
        raw = ('<p class=raw>raw: <a href="%s">markdown</a></p>'
               % _html.escape(raw_url, quote=True))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body><main>"
            "%s%s</main></body></html>" % (page_title, _CSS, body, raw))
