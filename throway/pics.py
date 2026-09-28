"""pics — event galleries for throway (see RFQ.md + 1.20.0 in RELEASES.md).

Multi-gallery model (since 1.20.0): anyone can create a gallery and becomes
its admin via a per-gallery secret token. One env token
(THROWAWAY_PICS_ADMIN_TOKEN) acts as superadmin across all galleries
(operator moderation duty).

Layout (all inside ROOT/pics/ — one namespace, one 20 GB pool, fixed
90-day lifetime, reject-when-full, never evicted):

    ROOT/pics/<pid>            image bytes (+ <pid>.meta, <pid>.thumb)
    ROOT/pics/g/<gid>.json     gallery meta: {id, token, name, listed, ip,
                               created, expires}
    ROOT/pics/g/<gid>.likes.json     image like counters (+ visitor fps)
    ROOT/pics/g/<gid>.comments.json  guestbook comments (+ their likes)

Interface (the whole surface store.py needs to know):

    get(h, rest, query)        dispatch GET  /pics/…
    post(h, rest, qp)          dispatch POST /pics/…
    sweep(root, now)           delete expired images + galleries
    api_endpoints()            entries for the /api contract
    HELP_TOPICS                entries for /help
    NS                         the namespace dir name ("pics")

Routes:

    POST /pics?create=1[&name=][&listed=1]     create gallery
    GET  /pics                                 gallery index (listed only)
    GET  /pics/g/<gid>                         public gallery (+ upload box)
    POST /pics/g/<gid>?name=                   upload (raw / multipart batch)
    GET  /pics/g/<gid>/<secret>                admin page
    GET  /pics/g/<gid>/<secret>/json           admin listing incl. hidden
    POST /pics/g/<gid>/<secret>                actions: hide|unhide|delete|up|down
    GET  /pics/g/<gid>/<secret>/i/<pid>        admin view of hidden images
    GET  /pics/i/<pid>                         public image (?thumb=1)
    POST /pics/i/<pid>?like=1                  toggle image like (pseudonymous)
    POST /pics/g/<gid>?comment=1               add comment (form/JSON: name, text)
    POST /pics/g/<gid>?clike=<cid>             toggle comment like
    GET  /pics/g/<gid>?likes=1                 cheap counts (poll for live ranking)
    GET  /pics/g/<gid>[&sort=likes]            gallery view (sort=likes: ranking)

Hidden images: 404 for everyone except via the owning gallery's admin route.
Everything below the interface line is implementation; tests drive it
through HTTP.
"""
import hashlib
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
PICS_QUALITY = int(os.environ.get("THROWAWAY_PICS_QUALITY", "") or 90)
PICS_QUALITY_FLOOR = int(os.environ.get("THROWAWAY_PICS_QUALITY_FLOOR", "") or 65)
PICS_TARGET = int(os.environ.get("THROWAWAY_PICS_TARGET_BYTES", "") or 1024 * 1024)  # HQ cap
PICS_ADMIN_TOKEN = os.environ.get("THROWAWAY_PICS_ADMIN_TOKEN", "")  # superadmin
PICS_PAGE = 60          # gallery thumbs per page
PICS_ADMIN_PAGE = 48    # admin thumbs per page
PICS_JSON_CAP = 2000    # max images in agent JSON listings
PICS_MAX_COMMENTS = int(os.environ.get("THROWAWAY_PICS_MAX_COMMENTS", "") or 500)
PICS_COMMENT_COOLDOWN = int(os.environ.get("THROWAWAY_PICS_COMMENT_COOLDOWN", "") or 20)
PICS_COMMENT_MAX_LEN = 500            # chars after strip
PICS_NAME_MAX_LEN = 40                # display name cap
PICS_LIKE_FP_CAP = 500                # visitor fingerprints kept per object

# HEIC/HEIF/AVIF brand codes — decode support depends on pillow-heif
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:                                # pragma: no cover
    pillow_heif = None

HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis",
               b"mif1", b"msf1", b"avif")

_HEX = re.compile(r"^[0-9a-f]{4,32}$")
# gallery ids: hex (unnamed) OR dir-style memorable names (create-or-get):
# 5-32 chars [a-z0-9-], >=1 letter, not a reserved word (validated via
# store._valid_name at creation). Images (pids) stay hex-only.
_GID = re.compile(r"^[a-z0-9][a-z0-9-]{3,31}$")


def _valid_gid(gid):
    if not _GID.match(gid or ""):
        return False
    return bool(re.search(r"[a-z]", gid))


class PicError(Exception):
    """A rejected upload: (http_code, message)."""
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


# --- domain: image pipeline (pure, no fs) ---------------------------------

def looks_heic(data):
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS


def strip_jpeg_exif(data):
    """Losslessly remove metadata segments (APP1 = EXIF/XMP incl. GPS,
    APP13 = Photoshop/IPTC, COM = comments) from a JPEG. Pixels stay
    byte-identical — only metadata chunks are cut out of the container.
    JFIF (APP0) and ICC colour profile (APP2) are kept. Returns the input
    unchanged on any structural surprise (play safe over clever)."""
    if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        return data
    out = bytearray(b"\xff\xd8")
    i, n = 2, len(data)
    try:
        while i + 4 <= n:
            if data[i] != 0xFF:
                return data
            marker = data[i + 1]
            if marker == 0xFF:                       # padding
                i += 1
                continue
            if marker == 0xD9:                       # EOI
                out += data[i:i + 2]
                return bytes(out)
            if marker == 0xDA:                       # SOS: entropy data follows
                out += data[i:]
                return bytes(out)
            if 0xD0 <= marker <= 0xD7 or marker == 0x01:   # standalone
                out += data[i:i + 2]
                i += 2
                continue
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            if seg_len < 2 or i + 2 + seg_len > n:
                return data
            if marker not in (0xE1, 0xED, 0xFE):     # drop EXIF/XMP/PS/COM
                out += data[i:i + 2 + seg_len]
            i += 2 + seg_len
    except Exception:
        return data
    return data


_KEEP_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def process_image(data):
    """Validate + recompress one image (RFQ FR-5, 1.26.0). Returns
    (bytes, ctype, w, h).

    Rule (Johann): **nur komprimieren, wenn die Pixel zu gross sind.**
      * max(w, h) <= PICS_EDGE (2048px) and the format is web-friendly
        (JPEG/PNG/WebP) -> the ORIGINAL bytes are kept byte-identical.
        For JPEGs, EXIF/GPS/Photoshop/comment segments are removed
        losslessly (container surgery, zero pixel change) so event
        photos never leak coordinates.
      * larger images are downscaled to PICS_EDGE and encoded as WebP,
        starting at PICS_QUALITY (90) and stepping the quality down in
        5-point steps only while the result exceeds PICS_TARGET
        (default 1 MB), floor PICS_QUALITY_FLOOR. Alpha is preserved.
    GIFs always pass through untouched (animation survives). HEIC/AVIF
    must always transcode (browsers cannot display them); the re-encode
    drops their metadata. Raises PicError on anything else.
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
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise PicError(400, "not a decodable image (jpeg/png/webp/gif/heic)")
    try:
        w, h = im.size
        fmt = im.format
        if fmt in _KEEP_FORMATS and max(w, h) <= PICS_EDGE:
            im.close()
            if fmt == "JPEG":
                data = strip_jpeg_exif(data)     # lossless metadata strip
            return data, _KEEP_FORMATS[fmt], w, h
        im = ImageOps.exif_transpose(im)
        if im.mode in ("P", "PA", "LA", "RGBA"):
            im = im.convert("RGBA")              # keep transparency in WebP
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if max(im.size) > PICS_EDGE:
            im.thumbnail((PICS_EDGE, PICS_EDGE))
        w, h = im.size
        best = None
        q = PICS_QUALITY
        while True:
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=q, method=4)
            out = buf.getvalue()
            if best is None or len(out) < len(best):
                best = out
            if len(out) <= PICS_TARGET or q <= PICS_QUALITY_FLOOR:
                break
            q = max(PICS_QUALITY_FLOOR, q - 5)
        return best, "image/webp", w, h
    except PicError:
        raise
    except Exception:
        raise PicError(400, "not a decodable image (jpeg/png/webp/gif/heic)")
    finally:
        try:
            im.close()
        except Exception:
            pass


# --- domain: image storage (meta sidecars like throway) ---------------------

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
    """Delete expired images AND expired galleries (fixed PICS_TTL)."""
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
    gallery_sweep(root, now)


def all_pics(root, gid=None):
    """All live images as [(pid, meta)] — optionally scoped to one gallery.
    Sweeps expired ones first."""
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
        if m and (gid is None or m.get("gid") == gid):
            out.append((f, m))
    return out


def _sorted_visible(items, likes=None):
    vis = [(pid, m) for pid, m in items if not m.get("hidden")]
    if likes is None:
        vis.sort(key=lambda t: (t[1].get("order", 0), -t[1].get("created", 0)))
    else:          # &sort=likes — most liked first, curated order ties
        vis.sort(key=lambda t: (-likes.get(t[0], 0), t[1].get("order", 0),
                                -t[1].get("created", 0)))
    return vis


def store_pic(root, data, name, ip, gid, dedupe=True):
    """Process and store one upload into gallery gid. Returns (pid, meta,
    duplicate). With dedupe, an identical image (sha256 of the stored
    bytes) already in THIS gallery is returned instead of stored twice —
    idempotent uploads for event walls and re-imports."""
    g = load_gallery(root, gid)
    if not g:
        raise PicError(404, "gallery not found")
    out, ctype, w, h = process_image(data)
    used = pics_size(root)
    if used + len(out) > PICS_POOL:
        raise PicError(507, "gallery pool full — admin must delete images "
                            f"or wait for expiry (used {_fmt(used)} of {_fmt(PICS_POOL)})")
    sha = hashlib.sha256(out).hexdigest()
    if dedupe:
        for pid_e, m_e in all_pics(root, gid):
            if m_e.get("sha256") == sha:
                return pid_e, m_e, True        # idempotent: existing wins
    pid = secrets.token_hex(8)
    d = _dir(root)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, pid + ".part")
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, _path(root, pid))          # atomic: no half-written pic
    now = time.time()
    items = [t for t in all_pics(root, gid)]
    order = min([m.get("order", 0) for _, m in items] or [1]) - 1  # newest first
    meta = {
        "created": now, "expires": now + PICS_TTL, "ip": ip, "gid": gid,
        "name": (name or pid)[:128], "orig_size": len(data), "size": len(out),
        "ctype": ctype, "w": w, "h": h, "hidden": False, "order": order,
        "sha256": sha,
    }
    save_meta(root, pid, meta)
    touch_gallery(root, gid, now)              # sliding gallery lifetime
    try:                                        # count into the shared stats
        import store
        s = store._load_stats()
        s["files"] += 1
        s["bytes"] += len(out)
        store._save_stats(s)
        store._bump_since_start(1, len(out))
    except Exception:
        pass                                    # stats are cosmetic — never fail an upload
    return pid, meta, False


def moderate(root, pid, action, gid=None):
    """hide | unhide | delete — scoped to one gallery when gid is given."""
    if not _HEX.match(pid or ""):
        return False
    m = load_meta(root, pid)
    if not m or not os.path.isfile(_path(root, pid)):
        return False
    if gid is not None and m.get("gid") != gid:
        return False                            # foreign gallery's image
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


def reorder(root, pid, delta, gid=None):
    """Move one visible image up (-1) / down (+1), then renumber 0..n-1."""
    vis = _sorted_visible(all_pics(root, gid))
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


# --- domain: galleries ------------------------------------------------------

# files in g/ that are engagement sidecars, never gallery metas
_SIDECAR_SUFFIXES = (".likes.json", ".comments.json")


def g_dir(root):
    return os.path.join(root, NS, "g")


def g_meta_path(root, gid):
    return os.path.join(g_dir(root), gid + ".json")


def load_gallery(root, gid):
    """Gallery meta or None (also None when expired — sweeps it)."""
    if not _valid_gid(gid):
        return None
    try:
        g = json.load(open(g_meta_path(root, gid)))
    except Exception:
        return None
    if g.get("expires", 0) < time.time():
        remove_gallery(root, gid)
        return None
    return g


def save_gallery(root, g):
    os.makedirs(g_dir(root), exist_ok=True)
    with open(g_meta_path(root, g["id"]), "w") as f:
        json.dump(g, f)


def remove_gallery(root, gid):
    for p in (g_meta_path(root, gid),
              _likes_path(root, gid), _likes_path(root, gid) + ".part",
              _comments_path(root, gid), _comments_path(root, gid) + ".part"):
        try:
            os.remove(p)
        except OSError:
            pass


def create_gallery(root, name, listed, ip):
    """Create (or get, for key-shaped names) a gallery with its own admin
    token. Mirrors dirs: a valid dir-style name ([a-z0-9-], 5-32, >=1
    letter, not reserved) becomes the gallery's key under /pics/g/<name>
    (create-or-get, idempotent); anything else is a display name on a
    fresh hex id. Returns (gid, meta, existed). The token is only
    meaningful for the creator: an existing named gallery is returned
    WITHOUT its token (never leak it to someone who just knows the name)."""
    import store
    now = time.time()
    if name and _GID.match(name) and store._valid_name(name)[0]:
        existing = load_gallery(root, name)
        if existing:
            return name, existing, True
        gid = name
    else:
        gid = secrets.token_hex(8)
    g = {
        "id": gid, "token": secrets.token_hex(24),
        "name": (name or "")[:80], "listed": bool(listed), "ip": ip,
        "created": now, "expires": now + PICS_TTL,   # slides on upload
    }
    save_gallery(root, g)
    return gid, g, False


# --- domain: likes & comments (sidecars per gallery) ------------------------

def _likes_path(root, gid):
    return os.path.join(g_dir(root), gid + ".likes.json")


def _comments_path(root, gid):
    return os.path.join(g_dir(root), gid + ".comments.json")


def load_likes(root, gid):
    """Image like counters + visitor fingerprints, one sidecar per gallery."""
    try:
        lk = json.load(open(_likes_path(root, gid)))
    except Exception:
        return {"v": 1, "imgs": {}}
    if not isinstance(lk.get("imgs"), dict):
        return {"v": 1, "imgs": {}}
    return lk


def save_likes(root, gid, lk):
    os.makedirs(g_dir(root), exist_ok=True)
    tmp = _likes_path(root, gid) + ".part"
    with open(tmp, "w") as f:
        json.dump(lk, f)
    os.replace(tmp, _likes_path(root, gid))


def load_comments(root, gid):
    try:
        cl = json.load(open(_comments_path(root, gid)))
    except Exception:
        return {"v": 1, "list": []}
    if not isinstance(cl.get("list"), list):
        return {"v": 1, "list": []}
    return cl


def save_comments(root, gid, cl):
    os.makedirs(g_dir(root), exist_ok=True)
    tmp = _comments_path(root, gid) + ".part"
    with open(tmp, "w") as f:
        json.dump(cl, f)
    os.replace(tmp, _comments_path(root, gid))


def likes_map(root, gid):
    """{pid: n} for one gallery (public counters only)."""
    return {p: e.get("n", 0) for p, e in load_likes(root, gid)["imgs"].items()}


def _fp(g, ip, ua):
    """Pseudonymous visitor fingerprint — keyed by the gallery's own secret
    token, never exposed in any response."""
    basis = "|".join((g.get("id", ""), g.get("token", ""), ip or "",
                      (ua or "")[:120]))
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _toggle_obj_like(entry, fp):
    """Flip one like on an {n, fp} entry; returns (likes, liked) after."""
    fps = entry.get("fp") or []
    if fp in fps:
        fps.remove(fp)
        entry["n"] = max(0, entry.get("n", 0) - 1)
        liked = False
    else:
        fps.append(fp)
        entry["n"] = entry.get("n", 0) + 1
        liked = True
    entry["fp"] = fps[-PICS_LIKE_FP_CAP:]      # keep the newest for dedup
    return entry["n"], liked


def toggle_image_like(root, pid, ip, ua):
    """Like/unlike one image (toggle per visitor fingerprint). Same
    visibility rules as _serve: hidden/expired/gone -> None."""
    if not _HEX.match(pid or ""):
        return None
    sweep(root)
    m = load_meta(root, pid)
    if not m or not os.path.isfile(_path(root, pid)):
        return None
    if m.get("expires", 0) < time.time():
        remove_pic(root, pid)
        return None
    if m.get("hidden"):
        return None
    gid = m.get("gid")
    g = load_gallery(root, gid) if gid else None
    if not g:
        return None
    lk = load_likes(root, gid)
    entry = lk["imgs"].setdefault(pid, {"n": 0, "fp": []})
    n, liked = _toggle_obj_like(entry, _fp(g, ip, ua))
    save_likes(root, gid, lk)
    return n, liked


def toggle_comment_like(root, gid, cid, ip, ua):
    g = load_gallery(root, gid)
    if not g or not _HEX.match(cid or ""):
        return None
    cl = load_comments(root, gid)
    for c in cl["list"]:
        if c.get("id") == cid:
            n, liked = _toggle_obj_like(c, _fp(g, ip, ua))
            save_comments(root, gid, cl)
            return n, liked
    return None


_cmt_last = {}                       # ip -> ts (spam cooldown, RAM only)


def add_comment(root, gid, name, text, ip):
    """Append a guestbook comment. Returns (comment, None) on success or
    (None, (http_code, message)). The cooldown lives in process RAM — a
    restart simply clears it, nothing personal is persisted with the
    comment (no IP, no fingerprint)."""
    g = load_gallery(root, gid)
    if not g:
        return None, (404, "gallery not found")
    name = " ".join((name or "").split())[:PICS_NAME_MAX_LEN]
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(ln.strip() for ln in text.split("\n")).strip()
    text = text[:PICS_COMMENT_MAX_LEN]
    if not text:
        return None, (400, "comment text required")
    now = time.time()
    if now - _cmt_last.get(ip or "", 0) < PICS_COMMENT_COOLDOWN:
        return None, (429, f"one comment per {PICS_COMMENT_COOLDOWN}s per "
                           "visitor — gleich nochmal versuchen")
    cl = load_comments(root, gid)
    if len(cl["list"]) >= PICS_MAX_COMMENTS:
        return None, (400, f"comment limit reached ({PICS_MAX_COMMENTS})")
    c = {"id": secrets.token_hex(4), "name": name or "Gast", "text": text,
         "ts": now, "n": 0, "fp": []}
    cl["list"].append(c)
    save_comments(root, gid, cl)
    _cmt_last[ip or ""] = now
    return c, None


def del_comment(root, gid, cid):
    cl = load_comments(root, gid)
    keep = [c for c in cl["list"] if c.get("id") != cid]
    if len(keep) == len(cl["list"]):
        return False
    cl["list"] = keep
    save_comments(root, gid, cl)
    return True


def public_comment(c):
    return {"id": c["id"], "name": c.get("name", "Gast"),
            "text": c.get("text", ""), "ts": c.get("ts"),
            "likes": c.get("n", 0)}


def touch_gallery(root, gid, now=None):
    """Slide the gallery lifetime forward on uploads."""
    g = load_gallery(root, gid)
    if not g:
        return
    g["expires"] = (now or time.time()) + PICS_TTL
    save_gallery(root, g)


def gallery_sweep(root, now=None):
    """Remove expired gallery metas (their images expired with them)."""
    now = now if now is not None else time.time()
    d = g_dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return
    for f in names:
        if not f.endswith(".json") or f.endswith(_SIDECAR_SUFFIXES):
            continue    # likes/comments sidecars are not gallery metas
        try:
            g = json.load(open(os.path.join(d, f)))
            if g.get("expires", 0) < now:
                os.remove(os.path.join(d, f))
        except Exception:
            pass


def all_galleries(root, listed_only=False):
    """Live galleries as [(gid, g)], newest first. Sweeps first."""
    gallery_sweep(root)
    d, out = g_dir(root), []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for f in names:
        if not f.endswith(".json") or f.endswith(_SIDECAR_SUFFIXES):
            continue
        gid = f[:-len(".json")]
        g = load_gallery(root, gid)
        if g and (not listed_only or g.get("listed")):
            out.append((gid, g))
    out.sort(key=lambda t: -t[1].get("created", 0))
    return out


def gallery_admin_ok(root, gid, secret):
    """Gallery meta if secret is this gallery's token OR the superadmin
    token; None otherwise (constant-time compares, no user enumeration)."""
    g = load_gallery(root, gid)
    if not g or not secret:
        return None
    s = secret.encode()
    if hmac.compare_digest(s, g["token"].encode()):
        return g
    if PICS_ADMIN_TOKEN and hmac.compare_digest(s, PICS_ADMIN_TOKEN.encode()):
        return g
    return None


# --- presentation helpers ---------------------------------------------------

def _fmt(n):
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit != "MB" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def public_meta(store, pid, m, likes=0):
    base = store.PUBLIC_BASE
    iso = _iso(m["expires"])
    return {
        "id": pid,
        "gid": m.get("gid"),
        "url": f"{base}/pics/i/{pid}",
        "thumb": f"{base}/pics/i/{pid}?thumb=1",
        "name": m.get("name", pid),
        "likes": likes,
        "size": m.get("size"),
        "orig_size": m.get("orig_size"),
        "width": m.get("w"), "height": m.get("h"),
        "content_type": m.get("ctype", "image/webp"),
        "expires_in": int(m.get("expires", 0) - time.time()),
        "expires_at": iso,
        "persistence": {"type": "pics", "expires_at": iso,
                        "extendable_by": "none", "max_age": PICS_TTL},
    }


def gallery_public_meta(store, gid, g, count):
    return {
        "id": gid,
        "url": f"{store.PUBLIC_BASE}/pics/g/{gid}",
        "name": g.get("name") or gid,
        "listed": bool(g.get("listed")),
        "images": count,
        "created_at": _iso(g.get("created", 0)),
        "expires_at": _iso(g.get("expires", 0)),
        "persistence": {"type": "pics-gallery", "expires_at": _iso(g.get("expires", 0)),
                        "extendable_by": "activity", "max_age": PICS_TTL},
    }


# --- HTML pages (pure functions over data) ---------------------------------

_GALLERY_CSS = (
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin:1rem 0}"
    ".grid a{display:block;background:var(--card2);border-radius:8px;overflow:hidden}"
    ".grid img{width:100%;aspect-ratio:1;object-fit:cover;display:block}"
    ".pgn{display:flex;align-items:center;gap:.8rem;margin:1.2rem 0;flex-wrap:wrap}"
    ".drop{border:2px dashed #d1d5db;border-radius:12px;padding:1.5rem 1rem;text-align:center;"
    "cursor:pointer;background:var(--card);display:block;transition:border-color .15s,background .15s;margin:0 0 1rem;position:relative}"
    ".drop:hover,.drop.hover{border-color:var(--accent);background:#eff6ff}"
    ".drop:focus-within{outline:2px solid var(--accent);outline-offset:2px}"
    ".drop svg{width:34px;height:34px;color:var(--accent);display:block;margin:0 auto .4rem}"
    ".drop .big{font-weight:600;font-size:1.02rem}"
    ".drop .sub{color:var(--muted);font-size:.83rem;margin-top:.2rem}"
    ".drop .meta{margin-top:.45rem}"
    ".srinput{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip:rect(0 0 0 0)}"
    ".meta{color:var(--muted);font-size:.85rem}"
    "details.embedbox{margin:.8rem 0 0;font-size:.85rem}"
    "details.embedbox summary{cursor:pointer;color:var(--muted)}"
    "details.embedbox input{width:100%;margin-top:.4rem;background:var(--card);"
    "border:1px solid var(--line);border-radius:8px;padding:.45rem .6rem;"
    "font:.75rem ui-monospace,monospace;color:var(--ink)}"
    "form.cnew{background:var(--card);border:1px solid var(--line);border-radius:8px;"
    "padding:1rem;margin:1rem 0;display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}"
    "form.cnew input[type=text]{flex:1;min-height:44px;border:1px solid var(--line);"
    "border-radius:8px;padding:.4rem .8rem;font-size:1rem}"
    "form.cnew label{display:flex;gap:.4rem;align-items:center;color:var(--muted);font-size:.9rem}"
    "button{min-height:44px;background:var(--accent);color:#fff;border:0;border-radius:8px;"
    "font-weight:600;padding:.5rem 1.2rem;cursor:pointer}"
    ".gl{background:var(--card);border:1px solid var(--line);border-radius:8px;"
    "padding:.7rem .9rem;margin:.4rem 0;display:flex;justify-content:space-between;"
    "align-items:center;gap:.6rem;flex-wrap:wrap}"
    ".gl a{color:var(--ink);font-weight:500;text-decoration:none;overflow-wrap:anywhere}"
    ".gl .meta{white-space:nowrap}"
    ".secret{background:#fffbe6;border:1px solid #eab308;border-radius:8px;"
    "padding:1rem;margin:1rem 0;overflow-wrap:anywhere}"
    ".secret code{background:var(--card);padding:.1rem .4rem;border-radius:4px}"
)

_UP_JS = (
    "var q=[],done=0,fail=0,busy=false;"
    "var inp=document.getElementById('f'),st=document.getElementById('upstat'),"
    "dzEl=document.getElementById('upDrop');"
    "function upd(){st.textContent=(done||fail||q.length)"
    "? done+' hochgeladen'+(dupcount?', '+dupcount+' duplikate \u00fcbersprungen':'')+(fail?', '+fail+' fehlgeschlagen':'')+(q.length?', '+q.length+' ausstehend':'')"
    ": '';}"
    "function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}"
    "inp.addEventListener('change',function(){"
    "var n=this.files.length;if(!n)return;"
    "for(var i=0;i<n;i++)q.push(this.files[i]);this.value='';"
    "st.textContent=n+' ausgew\u00e4hlt \u2014 Upload startet\u2026';upd();pump();});"
    "['dragover','dragenter'].forEach(function(ev){"
    "dzEl.addEventListener(ev,function(e){e.preventDefault();dzEl.classList.add('hover');});});"
    "['dragleave','dragend'].forEach(function(ev){"
    "dzEl.addEventListener(ev,function(e){e.preventDefault();dzEl.classList.remove('hover');});});"
    "dzEl.addEventListener('drop',function(e){e.preventDefault();dzEl.classList.remove('hover');"
    "var fl=e.dataTransfer.files,n=0;"
    "for(var i=0;i<fl.length;i++){if(fl[i].type&&fl[i].type.indexOf('image/')!==0)continue;"
    "q.push(fl[i]);n++;}"
    "if(n){st.textContent=n+' ausgew\u00e4hlt \u2014 Upload startet\u2026';pump();return;}"
    "var u=(e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain')||'').trim();"
    "if(/^https?:\\/\\//i.test(u)){importUrl(u);}});"
    "document.addEventListener('paste',function(e){"
    "var t=e.target;"
    "if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'))return;"
    "var txt=e.clipboardData?e.clipboardData.getData('text/plain'):'';"
    "txt=(txt||'').trim();"
    "if(/^https?:\\/\\//i.test(txt)){e.preventDefault();importUrl(txt);}"
    "});"
    "function importUrl(u){"
    "st.textContent='lade bild von url\u2026';"
    "fetch('__P__/pics/g/__GID__?url='+encodeURIComponent(u),"
    "{method:'POST',headers:{'Accept':'application/json'}})"
    ".then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})"
    ".then(function(x){"
    "if(x.ok&&(x.d.id||x.d.imported!=null)){"
    "st.textContent='importiert \u2713 '+(x.d.imported!=null?x.d.imported+' bilder':'1 bild');"
    "setTimeout(function(){location.reload();},700);}"
    "else st.textContent='import fehlgeschlagen: '+((x.d&&x.d.error)||'unbekannt');})"
    ".catch(function(e){st.textContent='import fehlgeschlagen: '+e;});}"
    "var dupcount=0;"
    "async function one(f){"
    "for(var a=0;a<3;a++){"
    "var r=await fetch('__P__/pics/g/__GID__?name='+encodeURIComponent(f.name),"
    "{method:'POST',body:f});"
    "if(r.ok){var d=await r.json().catch(function(){return {};});"
    "if(d.duplicate){dupcount++;}return true;}"
    "if(r.status===429){await sleep(61000);continue;}"
    "await sleep(1500);}"
    "return false;}"
    "async function pump(){if(busy)return;busy=true;"
    "while(q.length){var f=q.shift();if(await one(f))done++;else fail++;upd();}"
    "busy=false;if(done&&!fail){location.reload();}}"
)


def _page(store, title, body, extra_css=""):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            + store._META_MOBILE
            + f"<title>{store._html_escape(title)}</title>"
            + f"<style>{store._BASE_CSS}{extra_css}</style></head><body><main>"
            + body + "</main></body></html>")


def index_html(store, gals, created=None):
    """Gallery index: create form + listed galleries."""
    e = store._html_escape
    rows = "".join(
        f"<div class=gl><a href='{store.PREFIX}/pics/g/{gid}'>{e(g.get('name') or gid)}</a>"
        f"<span class=meta>{n} Bilder</span></div>"
        for gid, g, n in gals)
    created_html = ""
    if created:
        gid, g = created
        pub = f"{store.PUBLIC_BASE}/pics/g/{gid}"
        adm = f"{pub}/{g['token']}"
        created_html = (
            "<div class=secret><b>Galerie erstellt!</b><br>"
            f"\xd6ffentliche URL:<br><code>{e(pub)}</code><br><br>"
            "<b>Dein Admin-Link (nur jetzt sichtbar — bitte jetzt speichern!):</b><br>"
            f"<code>{e(adm)}</code><br><br>"
            "<span class=meta>Mit dem Admin-Link verwaltest du die Galerie: "
            "verbergen, l\xf6schen, neu sortieren. Er wird nirgends angezeigt — "
            "weg ist weg.</span></div>")
    days = max(1, PICS_TTL // 86400)
    return _page(store, "pics — galerien",
                 "<h1>pics</h1>"
                 + f"<p class=meta>Eigene Bildgalerie anlegen — Bilder laufen nach {days} Tagen ab, "
                 + f"max {_fmt(PICS_MAX_FILE)} pro Bild. Du bekommst einen Admin-Link f\xfcr "
                 + "verbergen / l\xf6schen / sortieren.</p>"
                 + "<form class=cnew method=post action='" + store.PREFIX + "/pics?create=1'>"
                 + "<input type=text name=name placeholder='Name der Galerie (z.B. Hochzeit M\u00fcnchen)'>"
                 + "<label><input type=checkbox name=listed value=1> \xf6ffentlich gelistet</label>"
                 + "<button>Galerie anlegen</button></form>"
                 + created_html
                 + f"<h2>Galerien</h2>{rows or '<p class=meta>Noch keine \xf6ffentlichen Galerien.</p>'}"
                 + store._agent_hint(
                     f"curl -X POST '{store.PUBLIC_BASE}/pics?create=1&name=party'  # new gallery (JSON incl. admin token)",
                     f"curl -A curl {store.PUBLIC_BASE}/pics                        # this index as JSON",
                 ),
                 _GALLERY_CSS)


_HEART_SVG = ("<svg viewBox='0 0 24 24' aria-hidden='true'>"
               "<path d='M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 "
               "5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 "
               "5.5 0 0 0 0-7.78z'/></svg>")

_LB_CSS = (
    "#lb{position:fixed;inset:0;background:rgba(17,24,39,.93);display:flex;flex-direction:column;"
    "align-items:center;justify-content:center;z-index:50;padding:2.5rem 3.2rem 1rem}"
    "#lb[hidden]{display:none}"
    "#lb img{max-width:100%;max-height:80vh;object-fit:contain;border-radius:6px}"
    "#lb .lbx{position:absolute;top:.5rem;right:.7rem;background:none;border:0;color:#e5e7eb;"
    "font-size:1.5rem;cursor:pointer;min-height:44px;min-width:44px}"
    "#lb .lbnav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(255,255,255,.08);"
    "border:0;color:#e5e7eb;font-size:1.9rem;cursor:pointer;border-radius:10px;min-height:56px;min-width:48px}"
    "#lb .lbprev{left:.4rem}"
    "#lb .lbnext{right:.4rem}"
    "#lb .lbnav:hover,#lb .lbx:hover{background:rgba(255,255,255,.22)}"
    "#lb .lbcap{color:#e5e7eb;font-size:.85rem;margin-top:.6rem;text-align:center;max-width:92vw;"
    "overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    "@media(max-width:560px){#lb{padding:1rem 2.8rem .8rem}}"
    "#lb .lblk{display:inline-flex;align-items:center;gap:6px;margin-top:.55rem;"
    "background:rgba(255,255,255,.12);color:#fff;border:0;border-radius:999px;"
    "padding:8px 15px;font:600 14px/1 system-ui,-apple-system,sans-serif;cursor:pointer}"
    "#lb .lblk svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2}"
    "#lb .lblk.on{background:#f43f5e}"
    "#lb .lblk.on svg{fill:#fff}"
)

_LB_HTML = (
    "<div id=lb hidden role=dialog aria-label='image viewer'>"
    "<button type=button class=lbx id=lbx aria-label='close'>&#10005;</button>"
    "<button type=button class='lbnav lbprev' id=lbprev aria-label='previous'>&#8249;</button>"
    "<img id=lbimg alt=''>"
    "<button type=button class='lbnav lbnext' id=lbnext aria-label='next'>&#8250;</button>"
    "<div class=lbcap id=lbcap></div>"
    "<button type=button class='lblk lk' id=lblk hidden aria-pressed=false aria-label='Bild liken'>" + _HEART_SVG + "<span class=n></span></button>"
    "</div>"
)


def _lb_script(imgs, admin_post=None):
    """Lightbox JS with the (page-local) image list baked in.
    imgs = [(url, name)] | [(url, name, pid, likes)] (public) or
    [(url, name, pid, likes, hidden)] (admin). Flip via on-screen buttons,
    arrow keys, or touch swipe; esc / backdrop click closes. Without JS the
    thumbs stay plain links (progressive enhancement).

    admin_post: when given (the gallery admin action URL), SPACE toggles
    the current image between visible (+) and hidden (\u2212): admins flip
    through with < > and curate without leaving the viewer. The card
    behind dims live; the caption shows the new state.

    public: a heart button (#lblk, class lk) mirrors the grid like state;
    it is kept in sync by the page's social script via window.__tyLbSync,
    which open() calls on every image change. Clicks bubble to the
    document-level delegation of the social script."""
    items = []
    for entry in imgs:
        it = {"u": entry[0], "n": entry[1]}
        if len(entry) > 2 and entry[2]:
            it["i"] = entry[2]
        if len(entry) > 3 and isinstance(entry[3], int):
            it["l"] = entry[3]                  # public: like count
        if len(entry) > 4:
            it["h"] = bool(entry[4])            # admin: hidden flag
        items.append(it)
    data = json.dumps(items).replace("</", "<\\/")
    admin_js = ""
    if admin_post:
        admin_js = (
            "var AP=" + json.dumps(admin_post) + ";"
            "var _lk=document.getElementById('lblk');if(_lk)_lk.hidden=true;"
            "function lbState(){return LB[lbi].h?'\u2212 hidden':'\u002b visible';}"
            "function lbCap(){lbcap.textContent=(lbi+1)+' / '+LB.length+' \u2014 '+(LB[lbi].n||'')+'  ['+lbState()+']';}"
            "lb.addEventListener('keydown',function(e){"
            "if(e.key===' '&&lbi>=0){e.preventDefault();"
            "var act=LB[lbi].h?'unhide':'hide',id=LB[lbi].i;"
            "fetch(AP,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},"
            "body:'id='+encodeURIComponent(id)+'&action='+act+'&p=1'})"
            ".then(function(r){if(!r.ok)throw 0;LB[lbi].h=!LB[lbi].h;lbCap();"
            "var card=document.querySelectorAll('.grid .card')[lbi];"
            "if(card)card.style.opacity=LB[lbi].h?'.45':'';})"
            ".catch(function(){});}"
            "if(e.key==='ArrowLeft'){e.preventDefault();nav(-1);}"
            "if(e.key==='ArrowRight'){e.preventDefault();nav(1);}"
            "if(e.key==='Escape')close();});"
            "lb.tabIndex=0;"
            "lb.addEventListener('focus',function(){},true);"
            "document.addEventListener('focusin',function(){if(!lb.hidden&&lbi>=0)lb.focus();});"
        )
    return (
        "<script>(function(){"
        "var LB=" + data + ";"
        "var lb=document.getElementById('lb'),lbimg=document.getElementById('lbimg'),"
        "lbcap=document.getElementById('lbcap'),lbi=-1;"
        "function open(i){if(!LB.length)return;lbi=(i%LB.length+LB.length)%LB.length;"
        "lb.hidden=false;document.body.style.overflow='hidden';"
        "lbimg.src=LB[lbi].u;"
        + ("lbCap();" if admin_post else
           "lbcap.textContent=(lbi+1)+' / '+LB.length+' \u2014 '+(LB[lbi].n||'');"
           "if(window.__tyLbSync)window.__tyLbSync(LB,lbi);") +
        "[lbi+1,lbi-1].forEach(function(j){var k=(j%LB.length+LB.length)%LB.length;"
        "var im=new Image();im.src=LB[k].u;});"
        + ("lb.focus();" if admin_post else "") + "}"
        "function close(){lb.hidden=true;document.body.style.overflow='';lbimg.src='';lbi=-1;"
        "var lk=document.getElementById('lblk');if(lk)lk.hidden=true;}"
        "function nav(d){if(lbi<0)return;open(lbi+d);}"
        "[].forEach.call(document.querySelectorAll('.grid a'),function(a,i){"
        "a.addEventListener('click',function(e){"
        "if(e.target&&e.target.closest&&e.target.closest('.lk'))return;"
        "e.preventDefault();open(i);});});"
        "document.getElementById('lbx').addEventListener('click',close);"
        "document.getElementById('lbprev').addEventListener('click',function(e){e.stopPropagation();nav(-1);});"
        "document.getElementById('lbnext').addEventListener('click',function(e){e.stopPropagation();nav(1);});"
        "lb.addEventListener('click',function(e){if(e.target===lb)close();});"
        "document.addEventListener('keydown',function(e){if(lbi<0||!lb.hidden&&e.target===lb)return;"
        "if(e.key==='Escape')close();"
        "if(e.key==='ArrowLeft')nav(-1);if(e.key==='ArrowRight')nav(1);});"
        + admin_js +
        "var tx=null;"
        "lb.addEventListener('touchstart',function(e){tx=e.touches[0].clientX;},{passive:true});"
        "lb.addEventListener('touchend',function(e){if(tx===null)return;"
        "var dx=e.changedTouches[0].clientX-tx;if(Math.abs(dx)>40)nav(dx<0?1:-1);tx=null;},{passive:true});"
        "})();</script>"
    )


_SOCIAL_CSS = (
    ".grid a{position:relative}"
    ".lk,.clk{display:inline-flex;align-items:center;gap:4px;border:0;border-radius:999px;"
    "padding:4px 9px 4px 7px;font:600 12px/1.1 system-ui,-apple-system,sans-serif;"
    "background:rgba(17,24,39,.62);color:#fff;cursor:pointer;backdrop-filter:blur(3px)}"
    ".lk svg,.clk svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:2}"
    ".lk.on,.clk.on{background:#f43f5e}"
    ".lk.on svg,.clk.on svg{fill:#fff}"
    ".grid .lk{position:absolute;left:6px;bottom:6px}"
    "@media(prefers-reduced-motion:no-preference){"
    "@keyframes typop{0%{transform:scale(1)}40%{transform:scale(1.28)}100%{transform:scale(1)}}"
    ".lk.on svg,.clk.on svg{animation:typop .35s}}"
    ".lk .n:empty,.clk .n:empty{display:none}"
    ".ccmts{max-width:760px;margin:1.1rem auto;padding:1rem 1.1rem;background:#fff;"
    "color:#111827;border-radius:12px;box-shadow:0 1px 10px rgba(0,0,0,.25)}"
    ".ccmts .ch2{font-size:1.05rem;margin:0 0 .6rem}"
    ".ccmts .cnum{color:#6b7280;font-weight:600;font-size:.85rem}"
    ".cform{display:flex;flex-direction:column;gap:.5rem}"
    ".cform input,.cform textarea{border:1px solid #d1d5db;border-radius:8px;"
    "padding:.55rem .7rem;font:inherit;width:100%;box-sizing:border-box;"
    "background:#fff;color:#111827}"
    ".cform textarea{min-height:72px;resize:vertical}"
    ".cform button{align-self:flex-end;min-height:44px;background:#2563eb;color:#fff;"
    "border:0;border-radius:8px;font-weight:600;padding:.5rem 1.2rem;cursor:pointer}"
    ".cstat{color:#b91c1c;font-size:.85rem;margin:.3rem 0 0}"
    ".clist{margin-top:.8rem}"
    ".cmt{border-top:1px solid #f0f0f2;padding:.65rem 0}"
    ".cmt:first-child{border-top:0}"
    ".cmt .ch{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}"
    ".cmt .ch b{font-size:.92rem}"
    ".cmt .ts{color:#6b7280;font-size:.78rem;margin-right:auto}"
    ".clk{background:#f3f4f6;color:#374151;padding:3px 8px 3px 6px}"
    ".clk.on{background:#f43f5e;color:#fff}"
    ".cmtx{margin:.35rem 0 0;font-size:.95rem;overflow-wrap:anywhere}"
    ".cfp{color:#6b7280;font-size:.78rem;margin:.8rem 0 0}"
)

_EMBED_CSS = (
    "*{box-sizing:border-box}"
    "html{scrollbar-width:none}"            # auto-height: kein Scrollen nötig -
    "::-webkit-scrollbar{display:none}"     # nur die helle Leiste im Dunkel-Host verstecken
    "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "background:transparent;color:#111827;line-height:1.5}"
    "main{padding:.6rem}"
    ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px}"
    ".grid a{display:block;background:#f4f4f5;border-radius:6px;overflow:hidden}"
    ".grid img{width:100%;aspect-ratio:1;object-fit:cover;display:block}"
    ".pgn{display:flex;align-items:center;justify-content:center;gap:.7rem;margin:.6rem 0 0;"
    "font-size:.8rem;color:#6b7280}"
    ".pgn a{color:#2563eb;text-decoration:none}"
) + _LB_CSS + _SOCIAL_CSS


EMBED_PAGE = 24   # initial images per embed page; more load on scroll


_SOCIAL_JS = (
    "<script>(function(){"
    "var P='__P__',GID='__GID__',SORT=__SORT__,HASOWN=Object.prototype.hasOwnProperty;"
    "function qsa(s){return [].slice.call(document.querySelectorAll(s))}"
    "function mine(){try{return JSON.parse(localStorage.getItem('ty_likes_'+GID))||{}}"
    "catch(e){return{}}}"
    "function setMine(id,v){var m=mine();if(v)m[id]=1;else delete m[id];"
    "try{localStorage.setItem('ty_likes_'+GID,JSON.stringify(m))}catch(e){}}"
    "function paintBtn(b,on){b.classList.toggle('on',on);"
    "b.setAttribute('aria-pressed',on?'true':'false')}"
    "function setN(b,n){var s=b.querySelector('.n');if(!s)return;"
    "s.setAttribute('data-n',n);s.textContent=n>0?n:''}"
    "function paintAll(){"
    "qsa('.lk').forEach(function(b){var pid=b.getAttribute('data-pid');"
    "if(pid)paintBtn(b,!!mine()[pid])});"
    "qsa('.clk').forEach(function(b){var cid=b.getAttribute('data-cid');"
    "if(cid)paintBtn(b,!!mine()[cid])});}"
    "function likePid(pid){if(!pid)return;"
    "fetch(P+'/pics/i/'+pid+'?like=1',{method:'POST',headers:{'Accept':'application/json'}})"
    ".then(function(r){return r.json()})"
    ".then(function(d){if(!d||d.id===undefined)return;"
    "setMine(pid,d.liked);"
    "qsa('.lk[data-pid=\"'+pid+'\"]').forEach(function(b){setN(b,d.likes);paintBtn(b,d.liked)});"
    "resort();})"
    ".catch(function(){});}"
    "function clike(cid){if(!cid)return;"
    "fetch(P+'/pics/g/'+GID+'?clike='+encodeURIComponent(cid),"
    "{method:'POST',headers:{'Accept':'application/json'}})"
    ".then(function(r){return r.json()})"
    ".then(function(d){if(!d||d.id===undefined)return;"
    "setMine(cid,d.liked);"
    "qsa('.clk[data-cid=\"'+cid+'\"]').forEach(function(b){setN(b,d.likes);paintBtn(b,d.liked)});})"
    ".catch(function(){});}"
    "document.addEventListener('click',function(e){"
    "if(!e.target||!e.target.closest)return;"
    "var lk=e.target.closest('.lk');"
    "if(lk){e.preventDefault();e.stopPropagation();likePid(lk.getAttribute('data-pid'));return;}"
    "var clk=e.target.closest('.clk');"
    "if(clk){e.preventDefault();e.stopPropagation();clike(clk.getAttribute('data-cid'));}});"
    "function cellN(a){var s=a.querySelector('.lk .n');if(!s)return 0;"
    "return parseInt(s.getAttribute('data-n')||'0',10)||0}"
    "function sortCells(){var grid=document.querySelector('.grid');if(!grid)return;"
    "var cs=[].slice.call(grid.children);"
    "cs.sort(function(a,b){return cellN(b)-cellN(a)});"
    "cs.forEach(function(c){grid.appendChild(c)});}"
    "function resort(){"
    "if(!SORT)return;"
    "var grid=document.querySelector('.grid');"
    "if(!grid||!grid.children.length)return;"
    "var rm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;"
    "var cs=[].slice.call(grid.children),pos=new Map();"
    "if(!rm){cs.forEach(function(c){pos.set(c,c.getBoundingClientRect())})}"
    "sortCells();"
    "if(rm)return;"
    "requestAnimationFrame(function(){cs.forEach(function(c){"
    "var p0=pos.get(c);if(!p0)return;var p1=c.getBoundingClientRect();"
    "var dx=p0.left-p1.left,dy=p0.top-p1.top;"
    "if(dx||dy){c.style.transition='none';c.style.transform='translate('+dx+'px,'+dy+'px)';"
    "requestAnimationFrame(function(){"
    "c.style.transition='transform .5s cubic-bezier(.22,.9,.26,1)';c.style.transform='';});}})})"
    "}"
    "function fmtTs(ts){if(!ts)return'';var d=new Date(ts*1000);"
    "function p(n){return (n<10?'0':'')+n}"
    "return p(d.getDate())+'.'+p(d.getMonth()+1)+'. '+p(d.getHours())+':'+p(d.getMinutes())}"
    "var HEART=\"" + _HEART_SVG.replace("</", "<\\/") + "\";"
    "function cmtNode(c){"
    "var d=document.createElement('div');d.className='cmt';d.setAttribute('data-cid',c.id);"
    "var h=document.createElement('div');h.className='ch';"
    "var b=document.createElement('b');b.textContent=c.name||'Gast';"
    "var ts=document.createElement('span');ts.className='ts';ts.textContent=fmtTs(c.ts);"
    "var bt=document.createElement('button');bt.type='button';bt.className='clk';"
    "bt.setAttribute('data-cid',c.id);bt.setAttribute('aria-pressed','false');"
    "bt.setAttribute('aria-label','Kommentar liken');bt.innerHTML=HEART;"
    "var sp=document.createElement('span');sp.className='n';bt.appendChild(sp);"
    "h.appendChild(b);h.appendChild(ts);h.appendChild(bt);"
    "var p=document.createElement('p');p.className='cmtx';p.textContent=c.text;"
    "d.appendChild(h);d.appendChild(p);return d;}"
    "var cf=document.getElementById('cform');"
    "if(cf){cf.addEventListener('submit',function(e){"
    "e.preventDefault();"
    "var fd=new FormData(cf),body=new URLSearchParams();"
    "body.set('name',fd.get('name')||'');body.set('text',fd.get('text')||'');"
    "var bt=cf.querySelector('button');if(bt)bt.disabled=true;"
    "fetch(P+'/pics/g/'+GID+'?comment=1',{method:'POST',"
    "headers:{'Accept':'application/json',"
    "'Content-Type':'application/x-www-form-urlencoded'},"
    "body:body.toString()})"
    ".then(function(r){return r.json().then(function(d){return{ok:r.ok,d:d}})})"
    ".then(function(x){"
    "if(bt)bt.disabled=false;"
    "if(x.ok&&x.d&&x.d.id){"
    "var list=document.querySelector('.clist');"
    "if(list){var em=list.querySelector('.cempty');if(em)em.remove();"
    "list.insertBefore(cmtNode(x.d),list.firstChild);}"
    "cf.reset();"
    "qsa('.cnum').forEach(function(s){"
    "var n=(parseInt(s.getAttribute('data-n')||'0',10)||0)+1;"
    "s.setAttribute('data-n',n);s.textContent=n;});"
    "cstat('');}"
    "else cstat((x.d&&x.d.error)||'konnte nicht gespeichert werden');})"
    ".catch(function(){if(bt)bt.disabled=false;cstat('Netzwerkfehler');});});}"
    "function cstat(t){var s=document.getElementById('cstat');"
    "if(!s){s=document.createElement('p');s.id='cstat';s.className='cstat';"
    "var f=document.getElementById('cform');if(!f)return;"
    "f.parentNode.insertBefore(s,f.nextSibling);}"
    "s.textContent=t;}"
    "window.__tyLbSync=function(LB,i){"
    "var lb=document.getElementById('lblk'),it=LB&&LB[i];"
    "if(!lb)return;"
    "if(!it||!it.i){lb.hidden=true;return;}"
    "lb.hidden=false;lb.setAttribute('data-pid',it.i);"
    "setN(lb,it.l||0);paintBtn(lb,!!mine()[it.i]);};"
    "function poll(){"
    "if(document.hidden)return;"
    "fetch(P+'/pics/g/'+GID+'?likes=1',{headers:{'Accept':'application/json'}})"
    ".then(function(r){return r.json()})"
    ".then(function(d){if(!d)return;"
    "var L=d.likes||{};"
    "qsa('.lk').forEach(function(b){var pid=b.getAttribute('data-pid');"
    "if(pid&&HASOWN.call(L,pid))setN(b,L[pid]);"
    "if(pid)paintBtn(b,!!mine()[pid]);});"
    "var C=d.cl||{};"
    "qsa('.clk').forEach(function(b){var cid=b.getAttribute('data-cid');"
    "if(cid&&HASOWN.call(C,cid))setN(b,C[cid]);"
    "if(cid)paintBtn(b,!!mine()[cid]);});"
    "if(d.comments!=null)qsa('.cnum').forEach(function(s){"
    "if(String(s.getAttribute('data-n')||'0')!==String(d.comments)){"
    "s.setAttribute('data-n',d.comments);s.textContent=d.comments;}});"
    "resort();})"
    ".catch(function(){});}"
    "paintAll();"
    "setInterval(poll,30000);setTimeout(poll,2000);"
    "})();</script>"
)


def _social_js(store, gid, sort_likes=False):
    return (_SOCIAL_JS.replace("__P__", store.PREFIX)
            .replace("__GID__", gid)
            .replace("__SORT__", "true" if sort_likes else "false"))


def _thumb_cell(store, pid, m, likes=0):
    """One grid cell: thumb + like button (delegated clicks, no-JS = link)."""
    n = f"<span class=n data-n={likes}>{likes or ''}</span>"
    return (f"<a href='{store.PREFIX}/pics/i/{pid}'>"
            f"<img loading=lazy decoding=async alt='' "
            f"src='{store.PREFIX}/pics/i/{pid}?thumb=1'>"
            f"<button type=button class=lk data-pid={pid} aria-pressed=false "
            f"aria-label='Bild liken'>{_HEART_SVG}{n}</button></a>")


def _cmt_date(ts):
    try:
        return time.strftime("%d.%m. %H:%M", time.localtime(ts))
    except Exception:
        return ""


def _comments_html(store, gid, cmts):
    """Guestbook section: form + newest-first list. Solid white card so it
    reads on any host page (embeds live on dark sites)."""
    e = store._html_escape
    rows = []
    for c in reversed(cmts):
        n = c.get("n", 0)
        rows.append(
            "<div class=cmt data-cid=" + c["id"] + ">"
            "<div class=ch><b>" + e(c.get("name", "Gast")) + "</b>"
            "<span class=ts>" + e(_cmt_date(c.get("ts"))) + "</span>"
            "<button type=button class=clk data-cid=" + c["id"] +
            " aria-pressed=false aria-label='Kommentar liken'>" + _HEART_SVG +
            "<span class=n data-n=" + str(n) + ">" + (str(n) if n else "") +
            "</span></button></div>"
            "<p class=cmtx>" + e(c.get("text", "")).replace("\n", "<br>") + "</p>"
            "</div>")
    return (
        "<section id=comments class=ccmts aria-label=Kommentare>"
        "<h2 class=ch2>Kommentare <span class=cnum data-n=" + str(len(cmts)) +
        ">" + str(len(cmts)) + "</span></h2>"
        "<form id=cform class=cform method=post action='" + store.PREFIX +
        "/pics/g/" + gid + "?comment=1'>"
        "<input type=text name=name maxlength=" + str(PICS_NAME_MAX_LEN) +
        " placeholder='Dein Name (optional)' autocomplete=name>"
        "<textarea name=text required maxlength=" + str(PICS_COMMENT_MAX_LEN) +
        " rows=3 placeholder='Danke, Lob, Erinnerung an den Abend &hellip;'>"
        "</textarea>"
        "<button type=submit>Abschicken</button></form>"
        "<div class=clist>" + ("".join(rows) or
        "<p class=cempty>Noch keine Kommentare &mdash; schreib den ersten!</p>") +
        "</div>"
        "<p class=cfp>Ohne Anmeldung. Name + Kommentar bleiben so lange online "
        "wie die Galerie. Seid nett zueinander.</p>"
        "</section>")


def _admin_comments_html(store, root, gid, secret, page):
    """Moderation list on the admin page: delete button per comment."""
    e = store._html_escape
    cmts = load_comments(root, gid)["list"]
    if not cmts:
        return "<h2>Kommentare (0)</h2><p class=meta>noch keine</p>"
    rows = "".join(
        "<div class=cmt><div class=ch><b>" + e(c.get("name", "Gast")) + "</b>"
        "<span class=ts>" + e(_cmt_date(c.get("ts"))) + "</span>"
        "<span class=meta>" + str(c.get("n", 0)) + " &#9829;</span>"
        "<form class=ops method=post action='" + store.PREFIX + "/pics/g/" +
        gid + "/" + secret + "'>"
        "<input type=hidden name=id value=" + c["id"] + ">"
        "<input type=hidden name=action value=cdel>"
        "<input type=hidden name=p value=" + str(page) + ">"
        "<button class=danger>l&#246;schen</button></form></div>"
        "<p class=cmtx>" + e(c.get("text", "")).replace("\n", "<br>") + "</p></div>"
        for c in reversed(cmts))
    return ("<h2>Kommentare (" + str(len(cmts)) + ")</h2>"
            "<div class=clist>" + rows + "</div>")


def embed_html(store, gid, g, items, page, pages, total,
               likes=None, comments=None, sort_likes=False):
    """Minimal, chrome-less gallery view for <iframe> embedding: grid with
    like buttons, guestbook comments, the lightbox and a scroll sentinel —
    no header, no uploader, transparent background so the host page shines
    through.

    Smart bits: (1) infinite scroll — interval polling fetches the next
    embed page and appends its grid (paging links stay as the no-JS
    fallback); (2) auto-height — the page reports its content height to
    the embedding parent via postMessage; (3) social — likes toggle
    instantly (optimistic, pseudonymous), with sort=likes liked images
    FLIP-climb and a 30 s poll keeps counts fresh without reload."""
    likes = likes or {}
    sq = "&sort=likes" if sort_likes else ""
    cells = "".join(_thumb_cell(store, pid, m, likes.get(pid, 0))
                    for pid, m in items)
    pgn = []
    if page > 1:
        pgn.append(f"<a href='?embed=1{sq}&p={page-1}'>&#8249;</a>")
    pgn.append(f"<span>{page} / {pages}</span>")
    if page < pages:
        pgn.append(f"<a href='?embed=1{sq}&p={page+1}'>&#8250;</a>")
    lb_imgs = [(f"{store.PREFIX}/pics/i/{pid}", m.get("name", pid), pid,
                likes.get(pid, 0)) for pid, m in items]
    inf = (
        "<div id=sent></div>"
        "<script>(function(){"
        "var page=" + str(page) + ",pages=" + str(pages) + ",busy=false,"
        "SQ=" + json.dumps(sq) + ","
        "grid=document.querySelector('.grid'),sent=document.getElementById('sent');"
        "var post=function(){try{parent.postMessage({type:'throway:pics:height',"
        "height:Math.max(280,document.body.scrollHeight)},'*');}catch(e){}};"
        "function more(){"
        "if(busy||page>=pages){if(page>=pages&&sent)sent.remove();return;}"
        "busy=true;if(sent)sent.textContent='\u2026';"
        "fetch('?embed=1'+SQ+'&p='+(page+1)).then(function(r){return r.text();})"
        ".then(function(html){"
        "var doc=new DOMParser().parseFromString(html,'text/html');"
        "var g=doc.querySelector('.grid');"
        "if(!g||!g.children.length){if(sent)sent.remove();busy=false;return;}"
        "while(g.firstChild)grid.appendChild(g.firstChild);"
        "page++;busy=false;if(sent)sent.textContent='';post();"
        "if(page>=pages&&sent)sent.remove();"
        "}).catch(function(){busy=false;});}"
        "function tick(){"
        "if(!sent||!sent.parentNode){clearInterval(iv);return;}"
        "if(sent.getBoundingClientRect().top<innerHeight+500)more();}"
        "var iv=setInterval(tick,400);setTimeout(tick,150);"
        "if('ResizeObserver' in window)new ResizeObserver(post).observe(document.body);"
        "window.addEventListener('load',post);setTimeout(post,300);"
        "})();</script>"
    )
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            + store._META_MOBILE
            + f"<title>{store._html_escape(g.get('name') or gid)}</title>"
            + f"<style>{_EMBED_CSS}</style></head><body><main>"
            + f"<div class=grid>{cells}</div>"
            + f"<div class=pgn>{''.join(pgn)}</div>"
            + _LB_HTML
            + _lb_script(lb_imgs)
            + _comments_html(store, gid, comments or [])
            + _social_js(store, gid, sort_likes)
            + inf
            + "</main></body></html>")


def gallery_html(store, gid, g, items, page, pages, total,
                 likes=None, comments=None, sort_likes=False):
    """One public gallery: grid (with like buttons), uploader, guestbook
    comments, pagination. sort=likes ranks by likes — liked images
    FLIP-climb in the browser."""
    e = store._html_escape
    likes = likes or {}
    sp = "&sort=likes" if sort_likes else ""
    title = g.get("name") or gid
    cells = "".join(_thumb_cell(store, pid, m, likes.get(pid, 0))
                    for pid, m in items)
    pgn = []
    if page > 1:
        pgn.append(f"<a class=btn href='?p={page-1}{sp}'>&#8249; neuer</a>")
    pgn.append(f"<span class=meta>Seite {page} / {pages}</span>")
    if page < pages:
        pgn.append(f"<a class=btn href='?p={page+1}{sp}'>&#228;lter &#8250;</a>")
    days = max(1, PICS_TTL // 86400)
    js = _UP_JS.replace("__P__", store.PREFIX).replace("__GID__", gid)
    lb_imgs = [(f"{store.PREFIX}/pics/i/{pid}", m.get("name", pid), pid,
                likes.get(pid, 0)) for pid, m in items]
    _DROP_ICON = ("<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6' "
                  "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>"
                  "<rect x='3' y='3' width='18' height='18' rx='2'/>"
                  "<circle cx='8.5' cy='8.5' r='1.5'/>"
                  "<path d='M21 15l-5-5L5 21'/></svg>")
    sort_note = ("sortiert nach <b>Likes</b> &#8212; die beliebtesten Bilder "
                 "stehen oben" if sort_likes else
                 f"kuratierte Reihenfolge &#8212; <a href='?sort=likes'>nach "
                 "Likes sortieren</a>")
    return _page(store, f"pics — {title}",
                 f"<h1>{e(title)}</h1>"
                 + f"<p class=meta>{total} Bilder &#183; {sort_note} &#183; l&auml;uft nach {days} Tagen ab"
                 + f" &#183; max {_fmt(PICS_MAX_FILE)} pro Bild &#183; "
                 + f"<a href='{store.PREFIX}/pics'>alle Galerien</a></p>"
                 + "<label class=drop id=upDrop for=f>"
                 + "<input id=f type=file accept='image/*' multiple class=srinput>"
                 + _DROP_ICON
                 + "<div class=big>Bilder hierher ziehen oder klicken</div>"
                 + "<div class=sub>JPG &#183; PNG &#183; WebP &#183; GIF &#183; HEIC — auch mehrere auf einmal (1000+), max "
                 + _fmt(PICS_MAX_FILE) + " pro Bild</div>"
                 + "<div class=meta id=upstat></div>"
                 + "</label>"
                 + f"<div class=grid>{cells}</div>"
                 + f"<div class=pgn>{''.join(pgn)}</div>"
                 + _comments_html(store, gid, comments or [])
                 + "<details class=embedbox><summary>diese Galerie einbetten (embed)</summary>"
                 + "<p class=meta>Auto-Höhe + Nachladen beim Scrollen — einfach beide Zeilen übernehmen:</p>"
                 + "<input readonly onclick='this.select()' value='"
                 + e(f'<iframe id="ty-{gid}" src="{store.PUBLIC_BASE}/pics/g/{gid}?embed=1&sort=likes" '
                     f'style="width:100%;height:640px;border:0;border-radius:8px" '
                     f'loading="lazy" title="{g.get("name") or gid}"></iframe>'
                     f'<script>window.addEventListener("message",function(e){{'
                     f'if(e.data&&e.data.type==="throway:pics:height")'
                     f'document.getElementById("ty-{gid}").style.height=e.data.height+"px";}});'
                     f'</script>')
                 + "'></details>"
                 + store._agent_hint(
                     f"curl {store.PUBLIC_BASE}/pics/g/{gid}?name=photo.jpg --data-binary @photo.jpg  # upload",
                     f"curl -A curl {store.PUBLIC_BASE}/pics/g/{gid}                        # listing as JSON",
                 )
                 + f"<script>{js}</script>"
                 + _LB_HTML
                 + _lb_script(lb_imgs)
                 + _social_js(store, gid, sort_likes),
                 _GALLERY_CSS + _LB_CSS + _SOCIAL_CSS)


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


def _admin_card(store, gid, secret, pid, page, ops):
    btns = "".join(
        f"<button name=action value={a}>{lab}</button>" for a, lab in ops)
    return (
        f"<div class=card><a href='{store.PREFIX}/pics/g/{gid}/{secret}/i/{pid}'>"
        f"<img loading=lazy decoding=async alt='' "
        f"src='{store.PREFIX}/pics/g/{gid}/{secret}/i/{pid}?thumb=1'></a>"
        f"<form class=ops method=post action='{store.PREFIX}/pics/g/{gid}/{secret}'>"
        f"<input type=hidden name=id value='{pid}'>"
        f"<input type=hidden name=p value='{page}'>{btns}</form></div>"
    )


def admin_html(store, gid, g, secret, vis, hid, page, pages, used):
    """Admin page of ONE gallery: pool bar, ops grid, hidden section."""
    e = store._html_escape
    title = g.get("name") or gid
    pct = min(100.0, used * 100.0 / PICS_POOL)
    cards = "".join(_admin_card(store, gid, secret, pid, page,
                                [("up", "&#8593;"), ("down", "&#8595;"),
                                 ("hide", "verbergen"), ("delete", "l&#246;schen")])
                    for pid, m in vis)
    hcards = "".join(_admin_card(store, gid, secret, pid, page,
                                 [("unhide", "einblenden"), ("delete", "l&#246;schen")])
                     for pid, m in hid)
    lb_imgs = [(f"{store.PREFIX}/pics/g/{gid}/{secret}/i/{pid}", m.get("name", pid),
                pid, bool(m.get("hidden")))
               for pid, m in list(vis) + list(hid)]
    pgn = []
    if page > 1:
        pgn.append(f"<a class=btn href='?p={page-1}'>&#8249;</a>")
    pgn.append(f"<span class=meta>Seite {page} / {pages}</span>")
    if page < pages:
        pgn.append(f"<a class=btn href='?p={page+1}'>&#8250;</a>")
    return _page(store, "pics — admin",
                 f"<h1>{e(title)} &#183; admin</h1>"
                 + f"<p class=meta>{_fmt(used)} von {_fmt(PICS_POOL)} belegt (pool geteilt zwischen allen Galerien)</p>"
                 + f"<div class=bar><i style='width:{pct:.1f}%'></i></div>"
                 + f"<h2>Sichtbar ({len(vis)}{('…' if page < pages else '')})</h2>"
                 + f"<div class=grid>{cards}</div>"
                 + f"<div class=pgn>{''.join(pgn)}</div>"
                 + f"<h2 class=hidden-sec>Verborgen ({len(hid)})</h2>"
                 + f"<div class='grid hidden-sec'>{hcards}</div>"
                 + _admin_comments_html(store, store.ROOT, gid, secret, page)
                 + f"<a class=back href='{store.PREFIX}/pics/g/{gid}'>&#8592; zur Galerie</a>"
                 + _LB_HTML
                 + _lb_script(lb_imgs,
                              admin_post=f"{store.PREFIX}/pics/g/{gid}/{secret}"),
                 _ADMIN_CSS + _LB_CSS + _SOCIAL_CSS)


# --- HTTP adapters (thin glue over the domain) ------------------------------

def get(h, rest, query):
    """Dispatch GET /pics/… — rest is the path parts after /pics."""
    from urllib.parse import unquote
    import store
    root = store.ROOT
    if not rest or rest == [""] or rest == ["create"]:
        return _get_index(h, store, root, query)
    if rest[0] == "i" and len(rest) == 2 and rest[1]:
        return _serve(h, store, root, unquote(rest[1]), admin=False, query=query)
    if rest[0] == "g":
        if len(rest) == 2 and rest[1]:
            return _get_gallery(h, store, root, unquote(rest[1]), query)
        if len(rest) >= 3:
            gid, secret = unquote(rest[1]), unquote(rest[2])
            if not gallery_admin_ok(root, gid, secret):
                return h._send(404, "not found\n")
            tail = rest[3:]
            if not tail:
                return _get_admin(h, store, root, gid, secret, query)
            if tail == ["json"]:
                return _admin_json(h, store, root, gid)
            if len(tail) == 2 and tail[0] == "i" and tail[1]:
                return _serve(h, store, root, unquote(tail[1]), admin=True,
                              query=query, gid=gid)
    return h._send(404, "not found\n")


def post(h, rest, qp):
    """Dispatch POST /pics/… — create / upload / admin actions."""
    from urllib.parse import unquote
    import store
    root = store.ROOT
    if rest and rest[0] == "i" and len(rest) == 2 and rest[1] and "like" in (qp or {}):
        return _like_image(h, store, root, unquote(rest[1]))
    if not rest or rest == [""]:
        if "create=1" in (qp or {}) or "create" in (qp or {}):
            return _create(h, store, root, qp)
        return h._send(400, json.dumps(
            {"error": "nothing to do — use ?create=1 to create a gallery, "
                      "or POST /pics/g/<gid> to upload"}), "application/json")
    if rest == ["create"]:
        return _create(h, store, root, qp)
    if rest[0] == "g":
        if len(rest) == 2 and rest[1]:
            if "clike" in (qp or {}):
                return _comment_like(h, store, root, unquote(rest[1]), qp)
            if "comment" in (qp or {}):
                return _comment_create(h, store, root, unquote(rest[1]))
            if "url" in (qp or {}):
                return _url_import(h, store, root, unquote(rest[1]), qp)
            return _upload(h, store, root, unquote(rest[1]), qp)
        if len(rest) == 3:
            gid, secret = unquote(rest[1]), unquote(rest[2])
            if not gallery_admin_ok(root, gid, secret):
                return h._send(404, "not found\n")
            return _admin_action(h, store, root, gid, secret)
    return h._send(404, "not found\n")


def _page_of(query, default=1):
    try:
        return max(1, int(dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
                          .get("p", default)))
    except Exception:
        return default


def _form_value(body_qp, key, default=""):
    return (body_qp.get(key) or [default])[0]


def _read_form(h):
    """Parse an urlencoded form body (admin actions, create fallback)."""
    from urllib.parse import parse_qs
    length = h.headers.get("Content-Length")
    body = h.rfile.read(int(length)) if length else b""
    return parse_qs(body.decode("utf-8", "replace"))


# --- GET views ---------------------------------------------------------------

def _get_index(h, store, root, query):
    gals = all_galleries(root, listed_only=True)
    if h._is_agent() and "html=1" not in query:
        out = []
        for gid, g in gals[:PICS_JSON_CAP]:
            n = len([1 for _, m in all_pics(root, gid) if not m.get("hidden")])
            out.append(gallery_public_meta(store, gid, g, n))
        return h._send(200, json.dumps({
            "galleries": out,
            "create": {"method": "POST",
                       "url": f"{store.PUBLIC_BASE}/pics?create=1",
                       "note": "optional &name=<display name> &listed=1; "
                               "response includes the one-time admin token"},
        }, indent=2), "application/json")
    gals_html = []
    for gid, g in gals[:200]:
        n = len([1 for _, m in all_pics(root, gid) if not m.get("hidden")])
        gals_html.append((gid, g, n))
    h._send(200, index_html(store, gals_html), "text/html")


def _create(h, store, root, qp):
    """POST /pics?create=1 — new gallery. Parameters come from the query
    string (agents, curl) or an urlencoded form body (browser form);
    query wins, body fills in the rest."""
    qp = qp or {}
    from urllib.parse import unquote
    name = unquote((qp.get("name") or [""])[0]).strip()
    listed = bool((qp.get("listed") or [""])[0])
    if not name and "name" not in qp and h.headers.get("Content-Type", "").startswith(
            "application/x-www-form-urlencoded"):
        form = _read_form(h)
        name = _form_value(form, "name").strip()
        listed = listed or bool(_form_value(form, "listed"))
    key = (store._safe_name(name) or "")[:80] or None
    gid, g, existed = create_gallery(root, key, listed, h._client_ip())
    pub = f"{store.PUBLIC_BASE}/pics/g/{gid}"
    wants_json = (h._is_agent()
                  or "application/json" in (h.headers.get("Accept") or ""))
    if existed:
        # create-or-get on an existing named gallery: public info only —
        # the admin token stays with whoever created it
        if wants_json:
            return h._send(200, json.dumps({
                "id": gid, "url": pub, "name": g.get("name") or gid,
                "existed": True,
                "note": "gallery already exists — the admin token was shown "
                        "only at creation and is not re-issued",
            }, indent=2), "application/json")
        return h._send(200, _page(store, "pics — galerie existiert",
            "<h1>Galerie existiert schon</h1>"
            + f"<p><a class=btn href='{store.PREFIX}/pics/g/{gid}'>Zur Galerie</a></p>"
            + "<p class=meta>Der Admin-Link wurde nur beim Anlegen gezeigt.",
            _GALLERY_CSS), "text/html")
    adm = f"{pub}/{g['token']}"
    if wants_json:
        return h._send(200, json.dumps({
            "id": gid, "url": pub, "admin_url": adm, "token": g["token"],
            "name": g["name"], "listed": g["listed"],
            "expires_at": _iso(g["expires"]),
            "note": "the admin token is shown exactly once — store it now",
        }, indent=2), "application/json")
    # browser: one-time page that shows the admin link
    h._send(200, index_html(store, [], created=(gid, g)), "text/html")


def _get_gallery(h, store, root, gid, query):
    g = load_gallery(root, gid)
    if not g:
        return h._send(404, "not found\n")
    if "likes=1" in query:                      # cheap counters for polling
        lk = load_likes(root, gid)
        cl = load_comments(root, gid)
        return h._send(200, json.dumps({
            "likes": {p: e.get("n", 0) for p, e in lk["imgs"].items()
                      if e.get("n")},
            "cl": {c["id"]: c.get("n", 0) for c in cl["list"] if c.get("n")},
            "comments": len(cl["list"]),
        }), "application/json")
    sort_likes = "sort=likes" in query
    lm = likes_map(root, gid)
    items = _sorted_visible(all_pics(root, gid), likes=lm if sort_likes else None)
    cmts = load_comments(root, gid)["list"]
    if h._is_agent() and "html=1" not in query:
        return h._send(200, json.dumps({
            "gallery": gallery_public_meta(store, gid, g, len(items)),
            "images": [public_meta(store, pid, m, lm.get(pid, 0))
                       for pid, m in items[:PICS_JSON_CAP]],
            "comments": [public_comment(c) for c in reversed(cmts)],
            "social": {
                "like_image": {
                    "method": "POST",
                    "url": f"{store.PUBLIC_BASE}/pics/i/<id>?like=1",
                    "note": "toggle per visitor (pseudonymous fingerprint); "
                            "response {id, likes, liked}"},
                "likes_poll": {
                    "method": "GET",
                    "url": f"{store.PUBLIC_BASE}/pics/g/<gid>?likes=1",
                    "note": "cheap counts for live ranking: "
                            "{likes: {id: n}, cl: {cid: n}, comments: m}"},
                "comment": {
                    "method": "POST",
                    "url": f"{store.PUBLIC_BASE}/pics/g/<gid>?comment=1",
                    "body": f"urlencoded form (or JSON): name (<={PICS_NAME_MAX_LEN} chars, "
                            f"optional), text (<={PICS_COMMENT_MAX_LEN} chars); "
                            f"cooldown {PICS_COMMENT_COOLDOWN}s per visitor, "
                            f"max {PICS_MAX_COMMENTS} per gallery"},
                "comment_like": {
                    "method": "POST",
                    "url": f"{store.PUBLIC_BASE}/pics/g/<gid>?clike=<cid>",
                    "note": "toggle — same fingerprint model as image likes"},
                "sort": "&sort=likes orders images by likes (ties keep the "
                        "curated order); browsers FLIP-climb live on sort pages",
            },
            "pool": {"used": pics_size(root), "bytes": PICS_POOL},
            "limits": {"max_file_bytes": PICS_MAX_FILE, "ttl_seconds": PICS_TTL,
                       "edge_px": PICS_EDGE},
            "upload": {"method": "POST",
                       "url": f"{store.PUBLIC_BASE}/pics/g/{gid}?name=<filename>"},
        }, indent=2), "application/json")
    if "embed=1" in query:
        total = len(items)
        pages = max(1, (total + EMBED_PAGE - 1) // EMBED_PAGE)
        page = min(_page_of(query), pages)
        chunk = items[(page - 1) * EMBED_PAGE: page * EMBED_PAGE]
        return h._send(200, embed_html(store, gid, g, chunk, page, pages, total,
                                       likes=lm, comments=cmts,
                                       sort_likes=sort_likes), "text/html")
    total = len(items)
    pages = max(1, (total + PICS_PAGE - 1) // PICS_PAGE)
    page = min(_page_of(query), pages)
    chunk = items[(page - 1) * PICS_PAGE: page * PICS_PAGE]
    h._send(200, gallery_html(store, gid, g, chunk, page, pages, total,
                              likes=lm, comments=cmts,
                              sort_likes=sort_likes), "text/html")


def _get_admin(h, store, root, gid, secret, query):
    items = all_pics(root, gid)
    vis = _sorted_visible(items)
    hid = sorted([t for t in items if t[1].get("hidden")],
                 key=lambda t: -t[1].get("created", 0))[:500]
    g = load_gallery(root, gid)
    if h._is_agent():
        return _admin_json(h, store, root, gid)
    pages = max(1, (len(vis) + PICS_ADMIN_PAGE - 1) // PICS_ADMIN_PAGE)
    page = min(_page_of(query), pages)
    chunk = vis[(page - 1) * PICS_ADMIN_PAGE: page * PICS_ADMIN_PAGE]
    h._send(200, admin_html(store, gid, g, secret, chunk, hid, page, pages,
                            pics_size(root)), "text/html")


def _admin_json(h, store, root, gid):
    items = all_pics(root, gid)
    vis = _sorted_visible(items)
    hid = [t for t in items if t[1].get("hidden")]
    g = load_gallery(root, gid)
    lm = likes_map(root, gid)
    h._send(200, json.dumps({
        "gallery": gallery_public_meta(store, gid, g, len(vis)) if g else None,
        "admin": True,
        "visible": [public_meta(store, pid, m, lm.get(pid, 0))
                    for pid, m in vis[:PICS_JSON_CAP]],
        "hidden": [public_meta(store, pid, m, lm.get(pid, 0))
                   for pid, m in hid[:PICS_JSON_CAP]],
        "comments": [public_comment(c)
                     for c in reversed(load_comments(root, gid)["list"])],
        "pool": {"used": pics_size(root), "bytes": PICS_POOL},
    }, indent=2), "application/json")


def _serve(h, store, root, pid, admin, query, gid=None):
    """Serve one image (or its thumb). Hidden -> 404 unless admin."""
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
    if admin and gid is not None and m.get("gid") != gid:
        return h._send(404, "not found\n")     # admin of another gallery
    ctype = m.get("ctype") or "image/webp"
    if "thumb=1" in query:
        return h._serve_thumb(fp, ctype)
    # image ids are immutable until expiry -> browsers may cache a day
    h._serve_file(fp, ctype, m.get("name"), "download=1" in query, pid,
                  cache=None if "download=1" in query else "public, max-age=86400")


# --- POST actions ------------------------------------------------------------

_LH3_RE = re.compile(r"https://lh3\.googleusercontent\.com/[\w\-./%?=&]+")
_LH3_SIZE_RE = re.compile(r"=(?:w|s)\d+(?:-h\d+)?[^=]*$")


_LH3_SIZE_RE2 = re.compile(r"=(?:w|s)(\d+)", re.I)


def _lh3_is_avatar_or_icon(u):
    """/ogw/ and /a/ are profile/avatar paths (the user icon in share
    pages); tiny w/s params (<=128px) are UI icons, not photos."""
    if "/ogw/" in u or re.search(r"googleusercontent\.com/a/", u):
        return True
    m = _LH3_SIZE_RE2.search(u)
    return bool(m and int(m.group(1)) <= 128)


def _scrape_lh3(html_text, cap=100):
    """Photo URLs from a Google Photos share page (server-rendered data
    blobs, no JS needed). The page carries SEVERAL size variants of every
    photo (96px thumbs, og-cover, full) plus the owner avatar — we keep
    exactly ONE variant per photo (the largest / param-less full version)
    and drop avatars and UI icons, so imports contain only real images."""
    best = {}                                  # base-path -> (rank, url)
    order = []                                 # stable output order
    for m in _LH3_RE.findall(html_text):
        u = m.rstrip(".,;:)")
        if not u or u.endswith("/") or _lh3_is_avatar_or_icon(u):
            continue
        base = u.split("=")[0]
        sm = _LH3_SIZE_RE2.search(u)
        rank = 10 ** 9 if not sm else int(sm.group(1))   # param-less = full
        if base not in best:
            order.append(base)
            best[base] = (rank, u)
        elif rank > best[base][0]:
            best[base] = (rank, u)
    return [best[b][1] for b in order][:cap]


def _lh3_hq(url):
    """Raise an lh3 URL's size bound to our 2048px budget. w/h are bounding
    box params (aspect preserved); the auth tail after them stays intact."""
    if _LH3_SIZE_RE.search(url):
        return _LH3_SIZE_RE.sub("=w2048-h2048-k-no", url)
    return url + "=w2048-h2048-k-no"


def _url_import(h, store, root, gid, qp):
    """POST /pics/g/<gid>?url=<url> — fetch a remote image server-side into
    the gallery (stage 1 of the google-photos-import plan). Same SSRF rules
    as the throway url import (public hosts only, redirect-checked), images
    only, capped at PICS_MAX_FILE, then through the normal pics pipeline
    (pixel rule, GPS strip, pool)."""
    from urllib.parse import unquote
    raw = unquote((qp.get("url") or [""])[0]).strip()
    if not raw:
        return h._send(400, json.dumps({"error": "url required"}), "application/json")
    g = load_gallery(root, gid)
    if not g:
        return h._send(404, json.dumps({"error": "gallery not found"}), "application/json")
    try:
        data, name, ctype = store._fetch_remote(raw, max_bytes=PICS_MAX_FILE)
    except store._FetchError as ex:
        code = getattr(ex, "code", None) or ex.args[0] if ex.args else 502
        msg = getattr(ex, "msg", None) or (ex.args[0] if ex.args else "fetch failed")
        return h._send(code if isinstance(code, int) else 502,
                       json.dumps({"error": f"fetch failed: {msg}"}),
                       "application/json")
    if not (ctype or "").startswith("image/"):
        # a PAGE, not an image: Google Photos share links land here — scrape
        # the embedded lh3 image URLs and import the whole album
        if (ctype or "").startswith("text/html"):
            urls = _scrape_lh3(data.decode("utf-8", "replace"))
            if not urls:
                return h._send(400, json.dumps(
                    {"error": "page contains no importable images (only direct "
                              "image URLs or Google Photos share links work)"}),
                    "application/json")
            imported, errors, dup_count = [], [], 0
            for u in urls:
                try:
                    idata, iname, ict = store._fetch_remote(_lh3_hq(u),
                                                            max_bytes=PICS_MAX_FILE)
                    if not (ict or "").startswith("image/"):
                        errors.append(f"{u[-24:]}: not an image")
                        continue
                    pid, m, dup = store_pic(root, idata, iname or "image", h._client_ip(), gid)
                    if dup:
                        dup_count += 1
                    else:
                        imported.append(public_meta(store, pid, m))
                except store._FetchError as ex:
                    errors.append(f"{u[-24:]}: {getattr(ex, 'msg', 'fetch failed')}")
                except PicError as ex:
                    errors.append(f"{u[-24:]}: {ex.msg}")
            return h._send(200, json.dumps({
                "gallery": store.PUBLIC_BASE + "/pics/g/" + gid,
                "imported": len(imported), "duplicates": dup_count,
                "failed": len(errors),
                "images": imported[:20], "errors": errors[:10],
            }, indent=2), "application/json")
        return h._send(400, json.dumps(
            {"error": f"not an image (content-type {ctype or 'unknown'})"}), "application/json")
    try:
        pid, m, dup = store_pic(root, data, name or "image", h._client_ip(), gid)
    except PicError as ex:
        return h._send(ex.code, json.dumps({"error": ex.msg}), "application/json")
    resp = dict(public_meta(store, pid, m))
    if dup:
        resp["duplicate"] = True
    h._send(200, json.dumps(resp, indent=2), "application/json")


def _upload(h, store, root, gid, qp):
    """Public upload into one gallery: raw body (agents, JS queue) or
    multipart (browser form / batch)."""
    from urllib.parse import unquote
    ip = h._client_ip()
    g = load_gallery(root, gid)
    if not g:
        return h._send(404, json.dumps({"error": "gallery not found"}),
                       "application/json")
    name = unquote((qp.get("name") or [""])[0] or "").strip() or "image"
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
        added, errors, dups = [], [], []
        for n, d, _c in files:
            try:
                pid, m, dup = store_pic(root, d, store._safe_name(n)[:128], ip, gid)
                if dup:
                    dups.append(public_meta(store, pid, m))
                else:
                    added.append(public_meta(store, pid, m))
            except PicError as ex:
                errors.append(f"{store._safe_name(n)[:64]}: {ex.msg}")
        if h._is_agent():
            code = 200 if not errors else (507 if all(
                "pool" in e for e in errors) and not added else 207)
            return h._send(code, json.dumps(
                {"added": added, "duplicates": len(dups), "errors": errors},
                indent=2), "application/json")
        return h._send(303, b"", extra={"Location": f"{store.PREFIX}/pics/g/{gid}"})
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
        pid, m, dup = store_pic(root, data, store._safe_name(name)[:128], ip, gid)
    except PicError as ex:
        return h._send(ex.code, json.dumps({"error": ex.msg}), "application/json")
    resp = dict(public_meta(store, pid, m))
    if dup:
        resp["duplicate"] = True
    h._send(200, json.dumps(resp, indent=2), "application/json")


def _admin_action(h, store, root, gid, secret):
    """POST /pics/g/<gid>/<secret> with urlencoded form: id, action, p."""
    form = _read_form(h)
    pid = _form_value(form, "id")
    action = _form_value(form, "action")
    page = _form_value(form, "p", "1")
    if action in ("up", "down"):
        reorder(root, pid, -1 if action == "up" else 1, gid=gid)
    elif action == "cdel":
        del_comment(root, gid, pid)            # pid holds the comment id here
    elif action in ("hide", "unhide", "delete"):
        moderate(root, pid, action, gid=gid)
    else:
        return h._send(400, "unknown action\n")
    h._send(303, b"", extra={
        "Location": f"{store.PREFIX}/pics/g/{gid}/{secret}?p={page}"})


def _like_image(h, store, root, pid):
    """POST /pics/i/<pid>?like=1 — toggle, JSON {id, likes, liked}."""
    res = toggle_image_like(root, pid, h._client_ip(),
                            h.headers.get("User-Agent", ""))
    if res is None:
        return h._send(404, json.dumps({"error": "image not found"}),
                       "application/json")
    n, liked = res
    h._send(200, json.dumps({"id": pid, "likes": n, "liked": liked}),
            "application/json")


def _comment_like(h, store, root, gid, qp):
    """POST /pics/g/<gid>?clike=<cid> — toggle, JSON {id, likes, liked}."""
    from urllib.parse import unquote
    cid = unquote((qp.get("clike") or [""])[0]).strip()
    res = toggle_comment_like(root, gid, cid, h._client_ip(),
                              h.headers.get("User-Agent", ""))
    if res is None:
        return h._send(404, json.dumps({"error": "comment not found"}),
                       "application/json")
    n, liked = res
    h._send(200, json.dumps({"id": cid, "likes": n, "liked": liked}),
            "application/json")


def _comment_create(h, store, root, gid):
    """POST /pics/g/<gid>?comment=1 — urlencoded form (browser) or JSON."""
    ctype = h.headers.get("Content-Type", "")
    if ctype.startswith("application/json"):
        try:
            length = int(h.headers.get("Content-Length") or 0)
            doc = json.loads(
                h.rfile.read(length).decode("utf-8", "replace") or "{}")
        except Exception:
            doc = {}
        name, text = str(doc.get("name", "")), str(doc.get("text", ""))
    else:
        form = _read_form(h)
        name, text = _form_value(form, "name"), _form_value(form, "text")
    c, err = add_comment(root, gid, name, text, h._client_ip())
    if err:
        return h._send(err[0], json.dumps({"error": err[1]}),
                       "application/json")
    if (h._is_agent()
            or "application/json" in (h.headers.get("Accept") or "")):
        return h._send(200, json.dumps(public_comment(c), indent=2),
                       "application/json")
    h._send(303, b"",
            extra={"Location": f"{store.PREFIX}/pics/g/{gid}#comments"})


# --- self-service surfaces (merged into store's /api and /help) -------------

def api_endpoints(store_base):
    """Entries for the /api contract. store_base = PUBLIC_BASE."""
    return {
        "pics_create": {
            "method": "POST",
            "url": store_base + "/pics?create=1[&name=<name>][&listed=1]",
            "note": "create a gallery: you become its admin via a per-gallery "
                    "token (returned once — store it!). A dir-style name "
                    "(5-32 chars [a-z0-9-], >=1 letter, not reserved) becomes "
                    "the gallery's key at /pics/g/<name> (create-or-get, "
                    "idempotent; an existing named gallery is returned WITHOUT "
                    "its token). Anything else is a display name on a fresh "
                    "hex id. Unlisted by default; &listed=1 puts it in GET /pics.",
            "response": {"id": "str", "url": "str", "admin_url": "str",
                         "token": "str", "existed?": "bool",
                         "expires_at": "str"},
        },
        "pics_index": {
            "method": "GET",
            "url": store_base + "/pics",
            "note": "gallery index: JSON for agents (listed galleries only), "
                    "HTML with create form for browsers",
        },
        "pics_gallery": {
            "method": "GET",
            "url": store_base + "/pics/g/<gid>",
            "note": "one gallery: HTML grid for browsers (paginated ?p=N), "
                    "JSON for agents (images[], pool, limits, upload how-to). "
                    "Hidden images never appear. ?embed=1 renders a minimal, "
                    "chrome-less view (transparent bg, no uploader) for "
                    "<iframe> embedding.",
        },
        "pics_upload": {
            "method": "POST",
            "url": store_base + "/pics/g/<gid>?name=<filename>",
            "body": "raw image bytes (or multipart/form-data for batches)",
            "note": f"free upload into a gallery, public instantly. Images at or "
                    f"under {PICS_EDGE}px are stored byte-identical (JPEG metadata "
                    f"stripped losslessly); only larger images are downscaled to "
                    f"{PICS_EDGE}px WebP starting at q{PICS_QUALITY}, quality stepped "
                    f"down only while the result exceeds ~{PICS_TARGET // 1024} kB "
                    f"(alpha preserved; GIFs pass through). Fixed lifetime "
                    f"{PICS_TTL // 86400}d (slides the gallery's lifetime), "
                    f"shared pool {PICS_POOL // 1024**3} GB — full pool rejects "
                    f"with 507, never evicts. Max {PICS_MAX_FILE // 1024**2} MB "
                    f"per upload. Accepts jpeg/png/webp/gif"
                    + ("/heic." if pillow_heif else " (heic needs pillow-heif on the server)."),
        },
        "pics_import_url": {
            "method": "POST",
            "url": store_base + "/pics/g/<gid>?url=<image-url>",
            "note": "server-side import of a remote image into a gallery "
                    "(drag a picture from another site into the dropzone, or "
                    "call directly). Public http(s) hosts only, images only, "
                    "max 30 MB — then the normal pics pipeline applies "
                    "(pixel rule, EXIF/GPS strip, pool).",
            "response": "same JSON as pics_upload",
        },
        "pics_image": {
            "method": "GET",
            "url": store_base + "/pics/i/<id>",
            "note": "serve one image inline; ?thumb=1 for a small cached WebP "
                    "preview. Hidden or expired images -> 404.",
        },
        "pics_like": {
            "method": "POST",
            "url": store_base + "/pics/i/<id>?like=1",
            "note": "toggle an image like. One like per visitor "
                    "(pseudonymous fingerprint from IP+UA, keyed by the "
                    "gallery's secret — the same visitor toggles off). "
                    "Hidden/expired images -> 404.",
            "response": {"id": "str", "likes": "int", "liked": "bool"},
        },
        "pics_likes_json": {
            "method": "GET",
            "url": store_base + "/pics/g/<gid>?likes=1",
            "note": "cheap counters for polling / live ranking: image likes, "
                    "comment likes, comment count — no image payloads.",
            "response": {"likes": {"<id>": "int"}, "cl": {"<cid>": "int"},
                         "comments": "int"},
        },
        "pics_comment": {
            "method": "POST",
            "url": store_base + "/pics/g/<gid>?comment=1",
            "body": "urlencoded form (or JSON): name (<=40 chars, optional — "
                    "defaults to Gast), text (<=500 chars)",
            "note": f"guestbook comment, no login. Cooldown "
                    f"{PICS_COMMENT_COOLDOWN}s per visitor, max "
                    f"{PICS_MAX_COMMENTS} per gallery (env-tunable). Browsers "
                    f"posting the form get redirected back to #comments; JSON "
                    f"clients get the created comment.",
            "response": {"id": "str", "name": "str", "text": "str",
                         "ts": "float", "likes": "int"},
        },
        "pics_comment_like": {
            "method": "POST",
            "url": store_base + "/pics/g/<gid>?clike=<cid>",
            "note": "toggle a comment like — same fingerprint model as "
                    "pics_like.",
            "response": {"id": "str", "likes": "int", "liked": "bool"},
        },
        "pics_admin": {
            "method": "GET/POST",
            "url": store_base + "/pics/g/<gid>/<secret>",
            "note": "admin of ONE gallery: the per-gallery token from creation "
                    "(path segment, never a query param) or the server-wide "
                    "superadmin token (env THROWAWAY_PICS_ADMIN_TOKEN). GET: "
                    "admin page (/json for listing incl. hidden). POST form "
                    "(id, action): hide | unhide | delete | up | down | "
                    "cdel (delete a comment). Wrong "
                    "secret -> 404.",
        },
    }


HELP_TOPICS = {
    "pics": {
        "title": "Pics — event galleries",
        "summary": "Per-user image galleries: create, free upload, per-gallery admin token",
        "body": """WHAT IT IS
Image galleries inside throway, under {PUBLIC_BASE}/pics, for event
photos: anyone can create a gallery and becomes its admin via a
per-gallery secret token. Visitors upload freely, everyone views.

CREATE (like dirs: create-or-get for dir-style names)
   POST {PUBLIC_BASE}/pics?create=1&name=hochzeit-2026
   -> JSON: id, url (public), admin_url, token (shown exactly once!)
   A name like "hochzeit-2026" ([a-z0-9-], 5-32 chars) becomes the
   gallery's key: {PUBLIC_BASE}/pics/g/hochzeit-2026 (create-or-get,
   idempotent). Re-creating an existing named gallery returns it WITHOUT
   the token. Any other name is a display name on a fresh hex id.

The gallery admin link is {PUBLIC_BASE}/pics/g/<gid>/<token>. With it
you can hide, unhide, delete and reorder images. Galleries are unlisted
by default; &listed=1 puts them in the public index at GET /pics.

UPLOAD (public, no auth)
   POST {PUBLIC_BASE}/pics/g/<gid>?name=photo.jpg    (raw bytes)
   POST {PUBLIC_BASE}/pics/g/<gid>                   (multipart, batch)
   POST {PUBLIC_BASE}/pics/g/<gid>?url=<image-url>   (server-side import —
        drag a picture from another site onto the dropzone, e.g. straight
        from a Google Photos tab; public hosts, images only, max 30 MB)

Images at or under {PICS_EDGE}px are stored byte-identical (JPEG
metadata stripped losslessly, pixels untouched). Only larger images
are downscaled to {PICS_EDGE}px WebP q{PICS_QUALITY}, stepped down
only while the result exceeds ~1 MB (alpha preserved; GIFs pass
through). Fixed lifetime: {PICS_DAYS}
days per image — an upload also slides the gallery's lifetime. Own
pool: {PICS_GB} GB shared across galleries. Full pool REJECTS uploads
(507) — existing images are never evicted.

VIEW
   GET {PUBLIC_BASE}/pics              index (listed galleries)
   GET {PUBLIC_BASE}/pics/g/<gid>      one gallery
   GET {PUBLIC_BASE}/pics/g/<gid>?embed=1   minimal view for <iframe> embedding
   GET {PUBLIC_BASE}/pics/i/<id>       one image (?thumb=1 for preview)

LIKES & COMMENTS (guestbook, no login; since 1.38.0)
   POST {PUBLIC_BASE}/pics/i/<pid>?like=1        toggle image like
        -> {{id, likes, liked}}; one like per visitor (pseudonymous
        fingerprint from IP+UA, keyed by the gallery's secret token —
        same visitor, same button: toggles off)
   POST {PUBLIC_BASE}/pics/g/<gid>?comment=1     add a comment
        body: urlencoded form or JSON {{name, text}} — name <= 40 chars
        (optional, defaults to "Gast"), text <= 500 chars;
        20 s cooldown per visitor, max 500 per gallery
   POST {PUBLIC_BASE}/pics/g/<gid>?clike=<cid>   toggle comment like
   GET  {PUBLIC_BASE}/pics/g/<gid>?likes=1       cheap counters for
        polling: {{likes: {{pid: n}}, cl: {{cid: n}}, comments: m}}
   GET  {PUBLIC_BASE}/pics/g/<gid>?sort=likes    images ranked by likes
        (ties keep the curated order; ?embed=1 keeps it). In browsers
        liked images FLIP-climb live and a 30 s poll keeps counts fresh.
Comments render newest first; the gallery admin (or the superadmin
token) deletes them (admin action cdel). Nothing personal is persisted
with a comment — no IP, no fingerprint.

ADMIN (per-gallery token or the server superadmin token, as path segment)
   GET  {PUBLIC_BASE}/pics/g/<gid>/<secret>          admin page
   GET  {PUBLIC_BASE}/pics/g/<gid>/<secret>/json     listing incl. hidden
   POST {PUBLIC_BASE}/pics/g/<gid>/<secret>          form: id, action=hide|unhide|delete|up|down

Hidden images: 404 for everyone but the admin. Deleted images: gone
for good (bytes + meta).""",
    },
}
