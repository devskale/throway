"""pics — the event gallery for throway (see RFQ.md).

A curated, long-lived image gallery in its own storage namespace
(ROOT/pics) with its own budget, fully separate from the throwaway
pool (which LRU-evicts at 100 MB):

  * free upload for visitors — no accounts, images are public instantly
  * fixed lifetime: 90 days, then gone (the main cleanup mechanism)
  * pool: 20 GB — full pool REJECTS uploads, never evicts existing images
  * one admin with a long-lived secret URL: hide / unhide / delete / reorder
  * hidden images: 404 for everyone except the admin view

Interface (the whole surface store.py needs to know):

    get(h, rest, query)        dispatch GET  /pics/…
    post(h, rest, qp)          dispatch POST /pics/…
    sweep(root, now)           delete expired images (called from sweep())
    api_endpoints()            entries for the /api contract
    HELP_TOPICS                entries for /help
    PICS_NS                    the namespace dir name ("pics")

Everything below that line is implementation: image pipeline, meta
sidecars, ordering, moderation, HTML. Tests drive it through HTTP.
"""
import hmac
import io
import json
import os
import re
import secrets
import time

# --- config (own env family, like THROWAWAY_*) ----------------------------

NS = "pics"
PICS_TTL = int(os.environ.get("THROWAWAY_PICS_TTL", "") or 90 * 24 * 3600)
PICS_POOL = int(os.environ.get("THROWAWAY_PICS_POOL_BYTES", "") or 20 * 1024**3)
PICS_MAX_FILE = int(os.environ.get("THROWAWAY_PICS_MAX_FILE_BYTES", "") or 30 * 1024**2)
PICS_EDGE = int(os.environ.get("THROWAWAY_PICS_EDGE_PX", "") or 2048)
PICS_QUALITY = int(os.environ.get("THROWAWAY_PICS_QUALITY", "") or 80)
PICS_ADMIN_TOKEN = os.environ.get("THROWAWAY_PICS_ADMIN_TOKEN", "")
PICS_PAGE = 60          # gallery thumbs per page
PICS_ADMIN_PAGE = 48    # admin thumbs per page
PICS_JSON_CAP = 2000    # max images in agent JSON listings

# HEIC/HEIF/AVIF brand codes — decode support depends on pillow-heif
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:                                # pragma: no cover
    pillow_heif = None

HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis",
               b"mif1", b"msf1", b"avif")

_HEX = re.compile(r"^[0-9a-f]{4,32}$")


class PicError(Exception):
    """A rejected upload: (http_code, message)."""
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


# --- domain: image pipeline (pure, no fs) ---------------------------------

def looks_heic(data):
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS


def process_image(data):
    """Validate + recompress one image (RFQ FR-5).

    Returns (bytes, ctype, w, h). Raster images are re-encoded as WebP at
    max PICS_EDGE px / PICS_QUALITY; if the re-encode does not shrink an
    already-small JPEG/WebP, the original bytes are kept. GIFs pass through
    untouched so animation survives. Raises PicError on anything else.
    """
    if not data:
        raise PicError(400, "empty body")
    from PIL import Image, ImageOps   # lazy: throway must start without Pillow
    if data[:3] == b"GIF":
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception:
            w = h = 0
        return data, "image/gif", w, h
    if looks_heic(data) and pillow_heif is None:
        raise PicError(400, "HEIC/AVIF upload not supported on this server "
                            "(missing pillow-heif)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if max(im.size) > PICS_EDGE:
                im.thumbnail((PICS_EDGE, PICS_EDGE))
            w, h = im.size
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=PICS_QUALITY, method=4)
            out = buf.getvalue()
    except PicError:
        raise
    except Exception:
        raise PicError(400, "not a decodable image (jpeg/png/webp/gif/heic)")
    if fmt in ("JPEG", "WEBP") and len(out) >= len(data):
        return data, ("image/jpeg" if fmt == "JPEG" else "image/webp"), w, h
    return out, "image/webp", w, h


# --- domain: storage (meta sidecars like throway) --------------------------

def _dir(root):
    return os.path.join(root, NS)


def _path(root, pid):
    return os.path.join(root, NS, pid)


def _meta_path(root, pid):
    return _path(root, pid) + ".meta"


def load_meta(root, pid):
    try:
        return json.load(open(_meta_path(root, pid)))
    except Exception:
        return None


def save_meta(root, pid, meta):
    with open(_meta_path(root, pid), "w") as f:
        json.dump(meta, f)


def pics_size(root):
    """Bytes used by stored images (excl. .meta/.thumb sidecars)."""
    total, d = 0, _dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for f in names:
        if _HEX.match(f):
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


def remove_pic(root, pid):
    """Delete one image + all its sidecars. Idempotent."""
    for p in (_path(root, pid), _meta_path(root, pid),
              _path(root, pid) + ".thumb"):
        try:
            os.remove(p)
        except OSError:
            pass


def sweep(root, now=None):
    """Delete expired images (fixed PICS_TTL from creation)."""
    now = now if now is not None else time.time()
    d = _dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return
    for f in names:
        if not _HEX.match(f):
            continue
        m = load_meta(root, f)
        expires = (m or {}).get("expires")
        if expires is None:
            try:
                expires = os.path.getmtime(_path(root, f)) + PICS_TTL
            except OSError:
                continue
        if expires < now:
            remove_pic(root, f)


def all_pics(root):
    """All live images as [(pid, meta)], sweeping expired ones first."""
    sweep(root)
    d, out = _dir(root), []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for f in names:
        if not _HEX.match(f):
            continue
        m = load_meta(root, f)
        if m:
            out.append((f, m))
    return out


def _sorted_visible(items):
    vis = [(pid, m) for pid, m in items if not m.get("hidden")]
    vis.sort(key=lambda t: (t[1].get("order", 0), -t[1].get("created", 0)))
    return vis


def store_pic(root, data, name, ip):
    """Process and store one upload. Returns (pid, meta)."""
    out, ctype, w, h = process_image(data)
    used = pics_size(root)
    if used + len(out) > PICS_POOL:
        raise PicError(507, "gallery pool full — admin must delete images "
                            f"or wait for expiry (used {_fmt(used)} of {_fmt(PICS_POOL)})")
    pid = secrets.token_hex(8)
    d = _dir(root)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, pid + ".part")
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, _path(root, pid))          # atomic: no half-written pic
    now = time.time()
    items = all_pics(root)
    order = min([m.get("order", 0) for _, m in items] or [1]) - 1  # newest first
    meta = {
        "created": now, "expires": now + PICS_TTL, "ip": ip,
        "name": (name or pid)[:128], "orig_size": len(data), "size": len(out),
        "ctype": ctype, "w": w, "h": h, "hidden": False, "order": order,
    }
    save_meta(root, pid, meta)
    return pid, meta


def moderate(root, pid, action):
    """hide | unhide | delete — the admin verbs (RFQ FR-13/14)."""
    if not _HEX.match(pid or ""):
        return False
    m = load_meta(root, pid)
    if not m or not os.path.isfile(_path(root, pid)):
        return False
    if action == "delete":
        remove_pic(root, pid)
        return True
    if action == "hide":
        m["hidden"] = True
    elif action == "unhide":
        m["hidden"] = False
    else:
        return False
    save_meta(root, pid, m)
    return True


def reorder(root, pid, delta):
    """Move one visible image up (-1) / down (+1), then renumber 0..n-1."""
    vis = _sorted_visible(all_pics(root))
    ids = [p for p, _ in vis]
    if pid not in ids:
        return False
    i = ids.index(pid)
    j = i + delta
    if j < 0 or j >= len(ids):
        return False
    ids[i], ids[j] = ids[j], ids[i]
    for n, p in enumerate(ids):
        m = load_meta(root, p)
        if m:
            m["order"] = n
            save_meta(root, p, m)
    return True


def admin_ok(secret):
    """Constant-time check; empty token disables admin entirely."""
    if not PICS_ADMIN_TOKEN or not secret:
        return False
    return hmac.compare_digest(secret.encode(), PICS_ADMIN_TOKEN.encode())


# --- presentation helpers ---------------------------------------------------

def _fmt(n):
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit != "MB" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def public_meta(store, pid, m):
    base = store.PUBLIC_BASE
    iso = _iso(m["expires"])
    return {
        "id": pid,
        "url": f"{base}/pics/i/{pid}",
        "thumb": f"{base}/pics/i/{pid}?thumb=1",
        "name": m.get("name", pid),
        "size": m.get("size"),
        "orig_size": m.get("orig_size"),
        "width": m.get("w"), "height": m.get("h"),
        "content_type": m.get("ctype", "image/webp"),
        "expires_in": int(m.get("expires", 0) - time.time()),
        "expires_at": iso,
        "persistence": {"type": "pics", "expires_at": iso,
                        "extendable_by": "none", "max_age": PICS_TTL},
    }


# --- HTML pages (pure functions over data) ---------------------------------

_GALLERY_CSS = (
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin:1rem 0}"
    ".grid a{display:block;background:var(--card2);border-radius:8px;overflow:hidden}"
    ".grid img{width:100%;aspect-ratio:1;object-fit:cover;display:block}"
    ".pgn{display:flex;align-items:center;gap:.8rem;margin:1.2rem 0;flex-wrap:wrap}"
    ".up{background:var(--card);border:1px dashed var(--line);border-radius:8px;"
    "padding:1rem;margin:1rem 0;display:flex;flex-direction:column;gap:.5rem}"
    ".up input{min-height:44px}"
    ".meta{color:var(--muted);font-size:.85rem}"
)

_UP_JS = (
    "var q=[],done=0,fail=0,busy=false;"
    "var inp=document.getElementById('f'),st=document.getElementById('upstat');"
    "function upd(){st.textContent=done+' hochgeladen'"
    "+(fail?', '+fail+' fehlgeschlagen':'')+(q.length?', '+q.length+' ausstehend':'');}"
    "function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}"
    "inp.addEventListener('change',function(){"
    "for(var i=0;i<this.files.length;i++)q.push(this.files[i]);this.value='';upd();pump();});"
    "async function one(f){"
    "for(var a=0;a<3;a++){"
    "var r=await fetch('__PREFIX__/pics?name='+encodeURIComponent(f.name),"
    "{method:'POST',body:f});"
    "if(r.ok)return true;"
    "if(r.status===429){await sleep(61000);continue;}"
    "await sleep(1500);}"
    "return false;}"
    "async function pump(){if(busy)return;busy=true;"
    "while(q.length){var f=q.shift();if(await one(f))done++;else fail++;upd();}"
    "busy=false;if(done&&!fail){location.reload();}}".replace("__PREFIX__", "__P__")
)


def gallery_html(store, items, page, pages, total):
    """Public gallery page: grid, uploader, pagination."""
    e = store._html_escape
    cells = "".join(
        f"<a href='{store.PREFIX}/pics/i/{pid}'>"
        f"<img loading=lazy decoding=async alt='' "
        f"src='{store.PREFIX}/pics/i/{pid}?thumb=1'></a>"
        for pid, m in items)
    pgn = []
    if page > 1:
        pgn.append(f"<a class=btn href='?p={page-1}'>&#8249; neuer</a>")
    pgn.append(f"<span class=meta>Seite {page} / {pages}</span>")
    if page < pages:
        pgn.append(f"<a class=btn href='?p={page+1}'>&#228;lter &#8250;</a>")
    days = max(1, PICS_TTL // 86400)
    js = _UP_JS.replace("__P__", store.PREFIX)
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        + store._META_MOBILE
        + "<title>pics — event gallery</title>"
        + f"<style>{store._BASE_CSS}{_GALLERY_CSS}</style></head><body><main>"
        + "<h1>pics</h1>"
        + f"<p class=meta>{total} Bilder &#183; l&#228;uft nach {days} Tagen ab"
        + f" &#183; max {_fmt(PICS_MAX_FILE)} pro Bild</p>"
        + "<div class=up><input id=f type=file accept='image/*' multiple>"
        + "<div class=meta id=upstat>Bilder w&#228;hlen — sie werden der Reihe nach "
        + "hochgeladen (auch 1000 auf einmal).</div></div>"
        + f"<div class=grid>{cells}</div>"
        + f"<div class=pgn>{''.join(pgn)}</div>"
        + store._agent_hint(
            f"curl {store.PUBLIC_BASE}/pics?name=photo.jpg --data-binary @photo.jpg  # upload",
            f"curl -A curl {store.PUBLIC_BASE}/pics                                  # listing as JSON",
        )
        + f"<script>{js}</script>"
        + "</main></body></html>"
    )


_ADMIN_CSS = (
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:1rem 0}"
    ".card{background:var(--card);border:1px solid var(--line);border-radius:8px;"
    "padding:.4rem;display:flex;flex-direction:column;gap:.35rem}"
    ".card img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;background:var(--card2)}"
    ".ops{display:flex;gap:.3rem;flex-wrap:wrap}"
    ".ops button{min-height:36px;font-size:.8rem;padding:.2rem .55rem;border:1px solid var(--line);"
    "border-radius:6px;background:#fff;cursor:pointer}"
    ".ops button:hover{border-color:var(--accent)}"
    ".ops .danger{color:#b91c1c}"
    ".bar{background:var(--card2);border-radius:6px;height:10px;overflow:hidden;margin:.3rem 0 .6rem}"
    ".bar i{display:block;height:100%;background:var(--accent)}"
    "h2{font-size:1.05rem;margin-top:2rem}"
    ".hidden-sec .card{opacity:.6}"
    ".pgn{display:flex;align-items:center;gap:.8rem;margin:1.2rem 0}"
)


def _admin_card(store, secret, pid, m, page, ops):
    btns = "".join(
        f"<button name=action value={a}>{lab}</button>" for a, lab in ops)
    return (
        f"<div class=card><a href='{store.PREFIX}/pics/{secret}/i/{pid}'>"
        f"<img loading=lazy decoding=async alt='' "
        f"src='{store.PREFIX}/pics/{secret}/i/{pid}?thumb=1'></a>"
        f"<form class=ops method=post action='{store.PREFIX}/pics/{secret}'>"
        f"<input type=hidden name=id value='{pid}'>"
        f"<input type=hidden name=p value='{page}'>{btns}</form></div>"
    )


def admin_html(store, secret, vis, hid, page, pages, used):
    """Admin page: pool bar, visible grid with ops, hidden section."""
    pct = min(100.0, used * 100.0 / PICS_POOL)
    cards = "".join(_admin_card(store, secret, pid, m, page,
                                [("up", "&#8593;"), ("down", "&#8595;"),
                                 ("hide", "verbergen"), ("delete", "l&#246;schen")])
                    for pid, m in vis)
    hcards = "".join(_admin_card(store, secret, pid, m, page,
                                 [("unhide", "einblenden"), ("delete", "l&#246;schen")])
                     for pid, m in hid)
    pgn = []
    if page > 1:
        pgn.append(f"<a class=btn href='?p={page-1}'>&#8249;</a>")
    pgn.append(f"<span class=meta>Seite {page} / {pages}</span>")
    if page < pages:
        pgn.append(f"<a class=btn href='?p={page+1}'>&#8250;</a>")
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        + store._META_MOBILE
        + "<title>pics — admin</title>"
        + f"<style>{store._BASE_CSS}{_ADMIN_CSS}</style></head><body><main>"
        + "<h1>pics &#183; admin</h1>"
        + f"<p class=meta>{_fmt(used)} von {_fmt(PICS_POOL)} belegt</p>"
        + f"<div class=bar><i style='width:{pct:.1f}%'></i></div>"
        + f"<h2>Sichtbar ({len(vis)}{('…' if page < pages else '')})</h2>"
        + f"<div class=grid>{cards}</div>"
        + f"<div class=pgn>{''.join(pgn)}</div>"
        + f"<h2 class=hidden-sec>Verborgen ({len(hid)})</h2>"
        + f"<div class='grid hidden-sec'>{hcards}</div>"
        + "<a class=back href='" + store.PREFIX + "/pics'>&#8592; &#246;ffentliche Galerie</a>"
        + "</main></body></html>"
    )


# --- HTTP adapters (thin glue over the domain) ------------------------------

def get(h, rest, query):
    """Dispatch GET /pics/… — rest is the path parts after /pics."""
    from urllib.parse import unquote
    import store
    root = store.ROOT
    if not rest or rest == [""]:
        return _get_gallery(h, store, root, query)
    if rest[0] == "i" and len(rest) == 2 and rest[1]:
        return _serve(h, store, root, unquote(rest[1]), admin=False, query=query)
    secret = unquote(rest[0])
    if len(rest) == 1:
        if admin_ok(secret):
            return _get_admin(h, store, root, secret, query)
        return h._send(404, "not found\n")
    if len(rest) == 2 and rest[1] == "json":
        if admin_ok(secret):
            return _admin_json(h, store, root)
        return h._send(404, "not found\n")
    if len(rest) == 3 and rest[1] == "i" and rest[2]:
        if admin_ok(secret):
            return _serve(h, store, root, unquote(rest[2]), admin=True, query=query)
        return h._send(404, "not found\n")
    return h._send(404, "not found\n")


def post(h, rest, qp):
    """Dispatch POST /pics/… — upload (public) and admin actions."""
    from urllib.parse import unquote
    import store
    if not rest or rest == [""]:
        return _upload(h, store, qp)
    if len(rest) == 1:
        secret = unquote(rest[0])
        if admin_ok(secret):
            return _admin_action(h, store, store.ROOT, secret)
        return h._send(404, "not found\n")
    return h._send(404, "not found\n")


def _page_of(query, default=1):
    try:
        return max(1, int(dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
                          .get("p", default)))
    except Exception:
        return default


def _get_gallery(h, store, root, query):
    items = _sorted_visible(all_pics(root))
    if h._is_agent() or "json=1" in query:
        cap = items[:PICS_JSON_CAP]
        return h._send(200, json.dumps({
            "gallery": True,
            "count": len(items),
            "images": [public_meta(store, pid, m) for pid, m in cap],
            "pool": {"used": pics_size(root), "bytes": PICS_POOL},
            "limits": {"max_file_bytes": PICS_MAX_FILE, "ttl_seconds": PICS_TTL,
                       "edge_px": PICS_EDGE},
        }, indent=2), "application/json")
    total = len(items)
    pages = max(1, (total + PICS_PAGE - 1) // PICS_PAGE)
    page = min(_page_of(query), pages)
    chunk = items[(page - 1) * PICS_PAGE: page * PICS_PAGE]
    h._send(200, gallery_html(store, chunk, page, pages, total), "text/html")


def _get_admin(h, store, root, secret, query):
    items = all_pics(root)
    vis = _sorted_visible(items)
    hid = sorted([t for t in items if t[1].get("hidden")],
                 key=lambda t: -t[1].get("created", 0))[:500]
    if h._is_agent():
        return _admin_json(h, store, root)
    pages = max(1, (len(vis) + PICS_ADMIN_PAGE - 1) // PICS_ADMIN_PAGE)
    page = min(_page_of(query), pages)
    chunk = vis[(page - 1) * PICS_ADMIN_PAGE: page * PICS_ADMIN_PAGE]
    h._send(200, admin_html(store, secret, chunk, hid, page, pages,
                            pics_size(root)), "text/html")


def _admin_json(h, store, root):
    items = all_pics(root)
    vis = _sorted_visible(items)
    hid = [t for t in items if t[1].get("hidden")]
    h._send(200, json.dumps({
        "gallery": True, "admin": True,
        "visible": [public_meta(store, pid, m) for pid, m in vis[:PICS_JSON_CAP]],
        "hidden": [public_meta(store, pid, m) for pid, m in hid[:PICS_JSON_CAP]],
        "pool": {"used": pics_size(root), "bytes": PICS_POOL},
    }, indent=2), "application/json")


def _serve(h, store, root, pid, admin, query):
    """Serve one image (or its thumb). Hidden -> 404 unless admin."""
    import store
    if not _HEX.match(pid or ""):
        return h._send(404, "not found\n")
    sweep(root)
    fp = _path(root, pid)
    m = load_meta(root, pid)
    if not os.path.isfile(fp) or not m:
        return h._send(404, "not found\n")
    if m.get("expires", 0) < time.time():
        remove_pic(root, pid)
        return h._send(404, "not found\n")
    if m.get("hidden") and not admin:
        return h._send(404, "not found\n")
    ctype = m.get("ctype") or "image/webp"
    if "thumb=1" in query:
        return h._serve_thumb(fp, ctype)
    h._serve_file(fp, ctype, m.get("name"), "download=1" in query, pid)


def _upload(h, store, qp):
    """Public upload: raw body (agents, JS queue) or multipart (form)."""
    root, ip = store.ROOT, h._client_ip()
    name = ((qp.get("name") or [""])[0] or "").strip() or "image"
    ctype = h.headers.get("Content-Type", "")
    if ctype.startswith("multipart/form-data"):
        length = h.headers.get("Content-Length")
        if length is None:
            return h._send(411, json.dumps({"error": "length required"}), "application/json")
        if int(length) > 200 * 1024 * 1024:
            return h._send(413, json.dumps({"error": "batch too large"}), "application/json")
        payload = h._read_body()
        if payload is None:
            return h._send(400, json.dumps({"error": "bad body"}), "application/json")
        files = [t for t in store._parse_multipart(payload, ctype) if t[0]]
        if not files:
            return h._send(400, json.dumps({"error": "no file part"}), "application/json")
        added, errors = [], []
        for n, d, _c in files:
            try:
                pid, m = store_pic(root, d, store._safe_name(n)[:128], ip)
                added.append(public_meta(store, pid, m))
            except PicError as ex:
                errors.append(f"{store._safe_name(n)[:64]}: {ex.msg}")
        if h._is_agent():
            code = 200 if not errors else (507 if all(
                "pool" in e for e in errors) and not added else 207)
            return h._send(code, json.dumps(
                {"added": added, "errors": errors}, indent=2), "application/json")
        return h._send(303, b"", extra={"Location": f"{store.PREFIX}/pics"})
    # raw body: one image per request (JS queue + agents)
    length = h.headers.get("Content-Length")
    if length is None:
        return h._send(411, json.dumps({"error": "length required"}), "application/json")
    length = int(length)
    if length > PICS_MAX_FILE:
        # drain the body so keep-alive stays usable for browsers
        left = length
        while left > 0:
            chunk = h.rfile.read(min(1024 * 1024, left))
            if not chunk:
                break
            left -= len(chunk)
        return h._send(413, json.dumps(
            {"error": f"too large (max {_fmt(PICS_MAX_FILE)})"}), "application/json")
    data = h.rfile.read(length)
    try:
        pid, m = store_pic(root, data, store._safe_name(name)[:128], ip)
    except PicError as ex:
        return h._send(ex.code, json.dumps({"error": ex.msg}), "application/json")
    h._send(200, json.dumps(public_meta(store, pid, m), indent=2), "application/json")


def _admin_action(h, store, root, secret):
    """POST /pics/<secret> with urlencoded form: id, action, p."""
    from urllib.parse import parse_qs
    length = h.headers.get("Content-Length")
    body = h.rfile.read(int(length)) if length else b""
    form = parse_qs(body.decode("utf-8", "replace"))
    pid = (form.get("id") or [""])[0]
    action = (form.get("action") or [""])[0]
    page = (form.get("p") or ["1"])[0]
    if action in ("up", "down"):
        reorder(root, pid, -1 if action == "up" else 1)
    elif action in ("hide", "unhide", "delete"):
        moderate(root, pid, action)
    else:
        return h._send(400, "unknown action\n")
    h._send(303, b"", extra={"Location": f"{store.PREFIX}/pics/{secret}?p={page}"})


# --- self-service surfaces (merged into store's /api and /help) -------------

def api_endpoints(store_base):
    """Entries for the /api contract. store_base = PUBLIC_BASE."""
    return {
        "pics_upload": {
            "method": "POST",
            "url": store_base + "/pics?name=<filename>",
            "body": "raw image bytes (or multipart/form-data for batches)",
            "note": f"event gallery: free upload, public instantly. Recompressed "
                    f"server-side to max {PICS_EDGE}px WebP q{PICS_QUALITY} "
                    f"(GIFs pass through); original discarded. Fixed lifetime "
                    f"{PICS_TTL // 86400}d, own pool "
                    f"({PICS_POOL // 1024**3} GB — full pool rejects with 507, "
                    f"never evicts). Max {PICS_MAX_FILE // 1024**2} MB per upload. "
                    f"Accepts jpeg/png/webp/gif"
                    + ("/heic." if pillow_heif else " (heic needs pillow-heif on the server)."),
            "response": {"id": "str", "url": "str", "thumb": "str", "size": "int",
                         "width": "int", "height": "int", "expires_at": "str",
                         "persistence": {"type": "pics", "extendable_by": "none"}},
        },
        "pics_gallery": {
            "method": "GET",
            "url": store_base + "/pics",
            "note": "public gallery: HTML grid for browsers (paginated ?p=N), "
                    "JSON listing for agents (count, images[], pool, limits). "
                    "Hidden images never appear.",
        },
        "pics_image": {
            "method": "GET",
            "url": store_base + "/pics/i/<id>",
            "note": "serve one image inline; ?thumb=1 for a small cached WebP "
                    "preview. Hidden or expired images -> 404.",
        },
        "pics_admin": {
            "method": "GET/POST",
            "url": store_base + "/pics/<secret>",
            "note": "admin surface; the secret is a long-lived token from the "
                    "server env (THROWAWAY_PICS_ADMIN_TOKEN), passed as a path "
                    "segment (never a query param). GET: admin page (or /json "
                    "listing incl. hidden). POST form (id, action): hide | "
                    "unhide | delete | up | down. Wrong secret -> 404.",
        },
    }


HELP_TOPICS = {
    "pics": {
        "title": "Pics — event gallery",
        "summary": "Curated image gallery: free upload, 90-day lifetime, admin curation",
        "body": """WHAT IT IS
A long-lived image gallery inside throway, under {PUBLIC_BASE}/pics,
for event photos: visitors upload freely, everyone can view, one admin
curates (hide / delete / reorder).

DIFFERENCES FROM THROWAY FILES
- Fixed lifetime: {PICS_DAYS} days (no &ttl=). Expiry is the cleanup.
- Own pool: {PICS_GB} GB. When full, uploads are REJECTED (507) —
  existing images are never evicted to make room.
- Uploads are recompressed to max {PICS_EDGE}px WebP q{PICS_QUALITY}
  (GIFs pass through; originals are discarded).
- Images are only removed by expiry or the admin — anyone-with-URL
  cannot delete gallery images (unlike throway files).

UPLOAD
   POST {PUBLIC_BASE}/pics?name=photo.jpg    (raw bytes)
   POST {PUBLIC_BASE}/pics                   (multipart, batch)

VIEW
   GET {PUBLIC_BASE}/pics            gallery (HTML browsers, JSON agents)
   GET {PUBLIC_BASE}/pics/i/<id>     one image (?thumb=1 for preview)

ADMIN (secret from server env, path segment)
   GET  {PUBLIC_BASE}/pics/<secret>          admin page
   GET  {PUBLIC_BASE}/pics/<secret>/json     listing incl. hidden
   POST {PUBLIC_BASE}/pics/<secret>          form: id, action=hide|unhide|delete|up|down

Hidden images: 404 for everyone but the admin. Deleted images: gone
for good (bytes + meta).""",
    },
}
