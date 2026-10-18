"""index — the throway homepage (upload UI under /).

Pure presentation module (mdrender precedent, 1.51.0): page(stats)
assembles the whole homepage HTML from a flat stats dict — no I/O, no
handler, no kit. store.py gathers the engine state (pool fill, session
counters, agent description, config) and sends the result.

stats contract (all keys required):
    files_now, bytes_now     current single files in the throwaway pool
    pool_size                THROW_POOL_SIZE (bytes) — pool meter denominator
    since_files, since_bytes session counters (since server start)
    version, ttl_hours       live config values (badge + ttl defaults)
    prefix                   route prefix (PUBLIC_BASE-relative links)
    max_mb                   MAX_FILE in MB — substituted into the JS
    agent_description        plain text (the HELP-assembled agent copy);
                             page() HTML-escapes it

Everything below the interface is implementation; unit tests drive
page() directly (tests/test_index.py), behavior tests stay on HTTP.
"""
import html as _html

DROPZONE_VERSION = "5.9.3"
DROPZONE_CSS = f"https://cdn.jsdelivr.net/npm/dropzone@{DROPZONE_VERSION}/dist/min/dropzone.min.css"
DROPZONE_JS = f"https://cdn.jsdelivr.net/npm/dropzone@{DROPZONE_VERSION}/dist/min/dropzone.min.js"

# same mobile/tint metas as store._META_MOBILE (shared presentation
# constant; joins the shared-mechanics theme when candidate 4 lands)
_META_MOBILE = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<meta name=theme-color content='#2563eb'>")

_INDEX_JS_URL = "index.js"

_INDEX_CSS_URL = "index.css"

def _fmt_size(n):
    """Format bytes with a sensible unit: B, kB, MB, GB (own copy, like
    pics._fmt — presentation formatter per module)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} kB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def page(stats):
    """Render the homepage as one HTML string (pure — see module docstring)."""
    files_now = stats["files_now"]
    bytes_now = stats["bytes_now"]
    pool_size = stats["pool_size"]
    since_files = stats["since_files"]
    since_bytes = stats["since_bytes"]
    version = stats["version"]
    ttl_hours = stats["ttl_hours"]
    prefix = stats["prefix"]
    pct = 100.0 * bytes_now / pool_size
    h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
         f"{_META_MOBILE}"
         "<title>throway — share files, gone in 4 hours | skale.dev</title>"
         "<meta name=description content='Disposable file sharing: upload a file or bundle, "
         "get a short URL, everything auto-expires after 4 hours. No signup, no tracking. "
         "With a plain API for agents.'>"
         "<link rel=canonical href='https://skale.dev/throway/'>"
         "<meta property=og:type content=website>"
         "<meta property=og:site_name content='skale.dev Apps'>"
         "<meta property=og:title content='throway — share files, gone in 4 hours'>"
         "<meta property=og:description content='No signup, no tracking — files and bundles "
         "auto-expire after 4 hours. Plain API for agents.'>"
         "<meta property=og:url content='https://skale.dev/throway/'>"
         "<meta property=og:image content='https://skale.dev/og-throway.png'>"
         "<meta name=twitter:card content=summary_large_image>"
         f"<link rel=stylesheet href='{DROPZONE_CSS}'>"
         f"<link rel=stylesheet href='{prefix}/assets/{_INDEX_CSS_URL}?v={version}'>"
         f"</head><body><main data-max-mb={stats['max_mb']}>"
         f"<header><div class='brand'><h1>throway<span class='dot'>.</span></h1>"
         f"<span class='version'>v{version}</span></div>"
         "<div class='hauth'>"
         "<div id='hauthAnon' class='hrow'>"
         "<button type=button class='hbtn ghost' id='hLogin'>Log in</button>"
         "<button type=button class='hbtn pill' id='hReg'>Register</button>"
         "</div>"
         "<button type=button class='chip' id='hauthUser' hidden>"
         "<span class='avatar' id='hAvatar'>?</span><span id='hWho'></span>"
         "<span class='chev'>&#9662;</span></button>"
         "<div class='pop' id='authPop' hidden>"
         "<div class='pophead'><b id='popTitle'>Log in</b>"
         "<button type=button class='popx' id='popClose' aria-label='close'>&#10005;</button></div>"
         "<div class='popmenu' id='chipMenu' hidden>"
         "<div class='who'><span class='avatar big' id='mAvatar'></span><div><div id='mWho'></div>"
         "<small>ttl up to 14d unlocked</small></div></div>"
         "<button type=button class='menubtn' id='hLogout'>Log out</button>"
         "</div>"
         "<div class='popform' id='popForm'>"
         "<input id='acctEmail' type='email' placeholder='you@example.org' autocomplete='email'>"
         "<input id='acctPass' type='password' placeholder='passphrase (8+ chars)' autocomplete='current-password'>"
         "<button type=button class='hbtn pill go' id='popGo'>Log in</button>"
         "<div class='hint' id='acctMsg'></div>"
         "<button type=button class='switch' id='popSwitch'>no account? register</button>"
         "</div>"
         "</div>"
         "</div></header>"
         "<p class='lede'>A disposable file store for agents and humans. Upload a file, a"
         " bundle, or a dir — share a short-lived URL. No accounts, no setup,"
         " nothing permanent.</p>"
         "<ul class='feats'>"
         "<li><b>Files</b> — one URL per upload<small>inline for images &amp; text, download otherwise</small></li>"
         "<li><b>Bundles</b> — a whole mini-website<small>index.html renders inline; zip for agents</small></li>"
         "<li><b>Dirs</b> — keep adding files over days<small>sliding lifetime (ttl= up to 7d, 14d with a free account); edit history</small></li>"
         "<li><b>Pics</b> — galleries for events<small>free upload, images live 90 days, per-gallery admin link · <a href='" + prefix + "/pics'>browse</a></small></li>"
         "</ul>"
         "<div class='uploader'>"
         "<div class='tabs' role='tablist' aria-label='Upload type'>"
         "<button type=button role=tab id=tab-files data-mode=files aria-controls=panel-files aria-selected=true tabindex=0>Files</button>"
         "<button type=button role=tab id=tab-text data-mode=text aria-controls=panel-text aria-selected=false tabindex=-1>Text</button>"
         "<button type=button role=tab id=tab-link data-mode=link aria-controls=panel-link aria-selected=false tabindex=-1>Link</button>"
         "<button type=button role=tab id=tab-gallery data-mode=gallery aria-controls=panel-gallery aria-selected=false tabindex=-1>Gallery</button>"
         "</div>"
         "<div class='panel' id='panel-files' role='tabpanel' aria-labelledby='tab-files'>"
         "<div id='drop' class='dropzone'>"
         "<div class='dz-message'>"
         "<div class='big'>Drop files here, or click to choose</div>"
         "<div class='sub'>Select one or many files — or just paste an image (⌘/Ctrl+V)</div>"
         "</div></div>"
         "<div class='controls'>"
         "<button id='up'>Upload</button>"
         "<label class='mode'><input type='checkbox' id='dirMode'>create a <b>dir</b></label>"
         "<label class='mode'><input type='checkbox' id='onceSel'>download <b>once</b></label>"
         "<label class='mode'>live <select id='ttlSel'>"
         "<option value=''>" + str(ttl_hours) + "h (default)</option>"
         "<option value='24h'>24h</option>"
         "<option value='7d'>7d</option>"
         "<option value='14d'>14d (max)</option>"
         "</select></label>"
         "</div>"
         "<div class='shareline'><label class='mode'>share name</label>"
         "<input id='shareSel' placeholder='optional, e.g. my-note' maxlength=32>"
         "<span class='hint'>a chosen, memorable URL (5-32 chars: a-z, 0-9, -)</span>"
         "</div>"
         "<div class='limits'>max 5 MB per file &#183; lives 4h (anon ttl up to 4h — account unlocks 14d) &#183; one file = share URL, many = bundle, dir checkbox = dir</div>"
         "</div>"
         "<div class='panel' id='panel-text' role='tabpanel' aria-labelledby='tab-text' hidden>"
         "<textarea id='createText' placeholder='Paste or type text to share…' rows=8></textarea>"
         "<div class='createbar'>"
         "<input id='createName' placeholder='filename (optional, e.g. note.txt)' style='flex:1;min-width:180px'>"
         "<label class='mode' title='the file auto-deletes after the first download'><input type='checkbox' id='createOnce'>download once</label>"
         "<label class='mode'>live <select id='createTtl'>"
         "<option value=''>" + str(ttl_hours) + "h (default)</option>"
         "<option value='24h'>24h</option>"
         "<option value='7d'>7d</option>"
         "<option value='14d'>14d (max)</option>"
         "</select></label>"
         "<button id='create'>Create</button>"
         "</div>"
         "<div class='shareline'><label class='mode'>share name</label>"
         "<input id='createShare' placeholder='optional, e.g. my-note' maxlength=32>"
         "<span class='hint'>a chosen, memorable URL (5-32 chars: a-z, 0-9, -)</span>"
         "</div>"
         "<div class='limits'>becomes a text file with its own URL &#183; lives 4h (ttl up to 14d)</div>"
         "</div>"
         "<div class='panel' id='panel-link' role='tabpanel' aria-labelledby='tab-link' hidden>"
         "<div class='linkrow'>"
         "<input id='linkUrl' type='url' placeholder='https://example.com/page-or-file'>"
         "<input id='linkName' placeholder='filename (optional)'>"
         "<button id='linkGo'>Import</button>"
         "</div>"
         "<div class='limits'>the server fetches the URL and hosts it here &#183; &#8804; 5 MB, public http(s) URLs only &#183; lives 4h</div>"
         "</div>"
         "<div class='panel' id='panel-gallery' role='tabpanel' aria-labelledby='tab-gallery' hidden>"
         "<div class='galbar'>"
         "<input id='galName' type='text' placeholder='gallery name, e.g. hochzeit-2026' maxlength=80>"
         "<label class='mode'><input type='checkbox' id='galListed'> listed</label>"
         "<button type=button id='galCreate' title='create the gallery now — you can add images any time'>Create gallery</button>"
         "<a class='mode' href='" + prefix + "/pics' style='color:var(--accent);text-decoration:none'>all galleries &#8594;</a>"
         "</div>"
         "<div id='galDrop' class='minidrop'>"
         "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true' style='width:32px;height:32px;display:block;margin:0 auto .35rem;color:var(--accent)'>"
         "<rect x='3' y='3' width='18' height='18' rx='2'/><circle cx='8.5' cy='8.5' r='1.5'/>"
         "<path d='M21 15l-5-5L5 21'/></svg>"
         "<div class=big>Drop images here, or click to choose</div>"
         "<div class=sub>JPG · PNG · WebP · GIF · HEIC — max 30 MB, downscaled to 2048 px · or just paste (⌘/Ctrl+V)</div>"
         "</div>"
         "<input id='galFile' type='file' accept='image/*' multiple class=srinput>"
         "<div id='galRows'></div>"
         "<div id='galResult'></div>"
         "<div class='limits'>max 30 MB per image, downscaled in your browser to &#8804; 2048 px &#183; images live 90 days &#183; you get a private admin link (hide / delete / reorder)</div>"
         "</div>"
         "</div>"
         "<div id='status'></div>"
         "<div id='result'></div>"
         "<div class='stats'>"
         f"<div class='stat'><b>{files_now}</b><span>{'file' if files_now == 1 else 'files'}</span>"
         f"<div class='sub'>{_fmt_size(bytes_now)}</div>"
         f"<div class='meter' title='pool: {_fmt_size(bytes_now)} of {_fmt_size(pool_size)}'><i style='width:{min(100, pct) if pct >= 2 else (2 if bytes_now > 0 else 0):.0f}%'></i></div>"
         f"<div class='when'>now</div></div>"
         f"<div class='stat'><b>{since_files}</b><span>{'file' if since_files == 1 else 'files'}</span>"
         f"<div class='sub'>{_fmt_size(since_bytes)}</div>"
         f"<div class='when'>since start</div></div>"
         "</div>"
         "<nav class='links'>"
         f"<a href='{prefix}/pics'>pics</a>"
         f"<a href='{prefix}/api'>API</a>"
         f"<a href='{prefix}/help'>help</a>"
         f"<a href='{prefix}/write_for_agents'>for agents</a>"
         f"<a href='{prefix}/releases'>releases</a>"
         "</nav>"
         "<details class='agents'><summary>Agent info — this page is machine-readable</summary><pre>" + _html.escape(stats["agent_description"]) + "</pre></details>"
         f"<script src='{DROPZONE_JS}'></script>"
         f"<script src='{prefix}/assets/{_INDEX_JS_URL}?v={version}' defer></script>"
         "</main></body></html>")
    return h
