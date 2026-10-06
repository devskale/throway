"""throway/og.py — social preview cards (og:/twitter: meta), 1.54.0.

Shared throway links (HTML pages, rendered markdown docs, dirs, bundle
index pages) used to carry no card metadata: X/Twitter, Slack, Discord
& Co. showed a bare link. This module builds the og:*/twitter:* snippet
and the small amount of crawler machinery around it.

Representation rule (mirrors the Agent-vs-Browser split): social-card
crawlers (Twitterbot, facebookexternalhit, …) send Mozilla-prefixed UAs,
so the existing UA heuristic treats them as browsers — they get HTML.
On raw *user* HTML they additionally get our meta injected into <head>;
uploaders whose pages carry their own og:/twitter: tags win untouched.
Real browsers and agents always get byte-identical responses.

Interface: meta(), is_crawler(), has_card(), inject(), page_title(),
page_description(), md_brief(), canonical(). OG_IMAGE is env-tunable
(THROWAWAY_OG_IMAGE) — 1200×630 brand card, summary_large_image-safe.
"""

import html as _html
import os
import re

OG_IMAGE = os.environ.get("THROWAWAY_OG_IMAGE", "https://skale.dev/og-throway.png")

# UA substrings of social/link-preview crawlers. Kept to the well-known
# set — a false positive here swaps real bytes for injected ones.
CRAWLER_UAS = ("twitterbot", "facebookexternalhit", "slackbot", "discordbot",
               "whatsapp", "telegrambot", "linkedinbot", "embedly", "vkshare")

_HEAD_RE = re.compile(rb"<head[^>]*>", re.I)
_HTML_RE = re.compile(rb"<html[^>]*>", re.I)
_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_META_RE = re.compile(
    rb"<meta[^>]*name=[\"']?description[\"']?[^>]*>", re.I)
_CONTENT_RE = re.compile(rb"content\s*=\s*[\"']([^\"']*)[\"']", re.I)
_HAS_CARD_RE = re.compile(
    rb"<meta[^>]*(property|name)=[\"']?(og:title|og:image|twitter:card)", re.I)


def is_crawler(ua):
    """True for social-card crawlers (Twitterbot, facebookexternalhit, …)."""
    ua = (ua or "").lower()
    return any(c in ua for c in CRAWLER_UAS)


def _esc(s):
    return _html.escape(str(s or ""), quote=True)


def meta(title, description="", url="", image=None, card="summary_large_image"):
    """One og:*/twitter:* head snippet, all values attribute-escaped.
    image defaults to the brand card (OG_IMAGE)."""
    parts = [
        '<meta property="og:site_name" content="throway">',
        '<meta property="og:type" content="article">',
        '<meta property="og:title" content="%s">' % _esc(title),
    ]
    if description:
        parts.append('<meta property="og:description" content="%s">'
                     % _esc(description))
    if url:
        parts.append('<meta property="og:url" content="%s">' % _esc(url))
    parts.append('<meta property="og:image" content="%s">' % _esc(image or OG_IMAGE))
    parts.append('<meta name="twitter:card" content="%s">' % _esc(card))
    return "".join(parts)


def has_card(html):
    """True when the HTML already carries its own card tags (og:title,
    og:image or twitter:card) — the uploader's card beats ours."""
    return bool(_HAS_CARD_RE.search(html))


def inject(html, snippet):
    """Insert the meta snippet right after <head> (case-insensitive), or
    build one after <html …> when the page has no head, or prepend when
    it has neither. Bytes in, bytes out."""
    s = snippet.encode("utf-8") if isinstance(snippet, str) else snippet
    m = _HEAD_RE.search(html)
    if m:
        return html[:m.end()] + s + html[m.end():]
    m = _HTML_RE.search(html)
    if m:
        return html[:m.end()] + b"<head>" + s + b"</head>" + html[m.end():]
    return b"<head>" + s + b"</head>" + html


def page_title(html, fallback=""):
    """The page's own <title> text (whitespace-collapsed, ≤200 chars) or
    the fallback. Raw text — meta() escapes at render time."""
    m = _TITLE_RE.search(html)
    if not m:
        return fallback
    t = re.sub(r"\s+", " ", m.group(1).decode("utf-8", "replace")).strip()
    return t[:200] or fallback


def page_description(html):
    """The page's own <meta name=description> content, or ''. Same deal:
    raw text, escaped later."""
    m = _DESC_META_RE.search(html)
    if not m:
        return ""
    c = _CONTENT_RE.search(m.group(0))
    if not c:
        return ""
    return re.sub(r"\s+", " ", c.group(1).decode("utf-8", "replace")).strip()[:300]


def md_brief(text, n=200):
    """Short plain-text description for a markdown doc: first line that
    carries prose (headings/fences/quotes/tables/rules skipped), inline
    markers stripped, whitespace-collapsed, truncated with …."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "```", "~~~", ">", "|")):
            continue
        if re.match(r"^(---|\*\*\*|___)\s*$", line):
            continue
        line = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)   # list markers
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)  # images
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # links → text
        line = re.sub(r"[*_`]+", "", line).strip()
        if line:
            return _truncate(line, n)
    return ""


def _truncate(s, n):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def canonical(public_base, prefix, path):
    """Absolute URL for a request path: strip the mount prefix (self.path
    carries it, PUBLIC_BASE already ends with it) and re-attach the base."""
    if prefix and path.startswith(prefix):
        path = path[len(prefix):] or "/"
    return public_base.rstrip("/") + path
