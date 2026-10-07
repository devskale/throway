#!/usr/bin/env python3
"""Disposable throwaway store — upload, get a 4-hour URL back.

- Upload a thing -> stored under a random ID -> returns a URL
- Upload 2+ files (multipart) -> a BUNDLE under one URL, files served
  at /<id>/<filename>; index.html renders inline for browsers (a mini
  throwaway website), whole bundle is a zip for agents
- URL valid for TTL_HOURS (default 4) — files expire & auto-delete
- Images + text-like types render inline in browser; others download
  ?download=1 forces a download for any file
- Rolling THROW_POOL_SIZE pool (oldest evicted first)
- Max file MAX_FILE, no auth, RATE_LIMIT req/min per IP
"""
import os
import re
import json
import time
import shutil
import secrets
import socket
import threading
import zipfile
import ipaddress
import mimetypes
import urllib.error
import urllib.request
import hmac
import hashlib
import html as _html
from urllib.parse import unquote, quote, urlparse, urljoin
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from throway import contract, dirs, index, og, pics, retain, storage as _storage


def _html_escape(s):
    return _html.escape(s)

# ---------------------------------------------------------------------------
# Build config — every important operational parameter lives here and can be
# overridden via THROWAWAY_* env vars (e.g. in the systemd unit). Defaults
# below are the shipped configuration.
# ---------------------------------------------------------------------------
def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default

ROOT = os.environ.get("THROWAWAY_ROOT", "/srv/storage2/throway")
THROW_POOL_SIZE = _env_int("THROWAWAY_POOL_BYTES", 100 * 1024 * 1024)   # 100MB rolling pool
MAX_FILE = _env_int("THROWAWAY_MAX_FILE_BYTES", 5 * 1024 * 1024)        # 5MB
RATE_LIMIT = _env_int("THROWAWAY_RATE_LIMIT", 100)                      # req/min per IP
TTL_HOURS = _env_int("THROWAWAY_TTL_HOURS", 4)                          # default URL lifetime

# Dirs — one unified concept under /d/<key>. A dir is addressable by an
# opaque hex id (unnamed) or a memorable name (named). Sliding lifetime,
# optional tags/listed, and a lightweight edit history.
DIR_NS = dirs.NS                       # namespace prefix for all dirs (owned by throway.dirs, 1.49.0)
# dir lifetimes + history limit are owned by throway/dirs.py (env-tuned
# there); aliased here for /api and the HELP rendering:
DIR_MIN_AGE = dirs.MIN_AGE
DIR_MAX_AGE = dirs.MAX_AGE
DIR_DEFAULT_AGE = dirs.DEFAULT_AGE
DIR_ABS_MAX = dirs.ABS_MAX
HISTORY_LIMIT = dirs.HISTORY_LIMIT
MAX_TAGS = _env_int("THROWAWAY_MAX_TAGS", 5)
MAX_TAG_LEN = 24

PUBLIC_BASE = os.environ.get("THROWAWAY_PUBLIC_BASE", "https://skale.dev/throway")
# URL-Prefix der Ausgabe-Links. Retro 2026-10-01 (P6): im Prod steht nginx
# davor, lokal (store.py direkt gestartet) serviert der Server an "/" — mit
# THROWAWAY_PREFIX="" zeigen alle generierten Links dann auf denselben Origin
# (kein Reverse-Proxy noetig, um Browser-Tests lokal zu fahren).
PREFIX = os.environ.get("THROWAWAY_PREFIX", "/throway").rstrip("/")

# 1.41.2 (P1 des SOTA-Reviews): stabile maschinenlesbare Codes fuer jede
# Fehlerantwort. Ein Agent verzweigt auf `code`, nicht auf Prosa; der
# Text bleibt fuer Menschen. Retry-After nur, wo Warten der richtige Zug
# ist (429/507).
_ERR_CODES = {400: "bad_request", 401: "write_denied", 403: "forbidden",
              404: "not_found", 405: "method_not_allowed",
              411: "length_required", 413: "too_large",
              429: "rate_limited", 500: "server_error",
              507: "pool_full"}

# semantic version + single source of truth for release notes
VERSION = "1.54.1"
RELEASES_FILE = os.path.join(os.path.dirname(__file__), "RELEASES.md")

# content types browsers render inline (not download)
INLINE_TYPES = (
    "image/",
    "text/",
    "application/pdf",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/svg+xml",
)
PORT = int(os.environ.get("STORE_PORT", "8111"))

# image thumbnails for the HTML listings (smartphone UX): generated lazily
# on the first ?thumb=1 request, cached on disk next to the original, served
# as a tiny WebP so phones fetch KBs instead of MBs when scrolling a dir.
THUMB_PX = _env_int("THROWAWAY_THUMB_PX", 96)            # longest edge in px
THUMB_QUALITY = _env_int("THROWAWAY_THUMB_QUALITY", 70)  # WebP quality

os.makedirs(ROOT, exist_ok=True)
_hits = {}
STATS_FILE = os.path.join(os.path.dirname(__file__), "stats.json")

# Since-start counters (RAM-only): reset to zero on every process start.
# Unlike stats.json (all-time, persisted across restarts), these track only
# activity since this server instance came up.
_since_start = {"files": 0, "bytes": 0}


def _bump_since_start(files, bytes_):
    """Increment the since-start counters (files count, bytes)."""
    _since_start["files"] += files
    _since_start["bytes"] += bytes_


@_storage.locked("files")
def _bump_stats(files, bytes_):
    """All-time totals (stats.json) + since-start, serialized: parallel
    uploads used to race the load->increment->save chain and lose counts."""
    s = _load_stats()
    s["files"] += files
    s["bytes"] += bytes_
    _save_stats(s)
    _bump_since_start(files, bytes_)


def _load_stats():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"files": 0, "bytes": 0}


def _save_stats(s):
    try:
        _storage.atomic_json(STATS_FILE, s)
    except Exception:
        pass


def _cumulative():
    """All-time totals (files ever uploaded, bytes ever uploaded)."""
    return _load_stats()

def _atomic_json(path, obj):
    """Delegate to the shared mechanics module (1.52.0, candidate 4) —
    same tmp + os.replace contract, one home instead of three."""
    return _storage.atomic_json(path, obj)


def _idem_map_path():
    return os.path.join(ROOT, ".idem.json")


def _idem_load():
    try:
        with open(_idem_map_path(), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _idem_get(key):
    """key -> gespeicherte Upload-Antwort (falls Ziel noch lebt)."""
    if not key:
        return None
    try:
        return _idem_load().get(hashlib.sha256(key.encode()).hexdigest())
    except Exception:
        return None


def _idem_put(key, resp):
    if not key:
        return
    try:
        m = _idem_load()
        h = hashlib.sha256(key.encode()).hexdigest()
        # aufraeumen: Eintraege, deren Ziel nicht mehr existiert
        for k, v in list(m.items()):
            fp = _id_path(v.get("id", ""))
            mp = _meta_path(v.get("id", ""))
            if not (os.path.isfile(fp) and os.path.isfile(mp)):
                del m[k]
        m[h] = resp
        _atomic_json(_idem_map_path(), m)
    except Exception:
        pass


def _id_path(fid):
    # ids are secrets.token_hex, safe; guard anyway
    return os.path.join(ROOT, os.path.basename(fid))

def _meta_path(fid):
    return os.path.join(ROOT, os.path.basename(fid) + ".meta")

def allowed(ip, count=True):
    now = time.time()
    t = _hits.setdefault(ip, [])
    t[:] = [x for x in t if x > now - 60]
    if len(t) >= RATE_LIMIT:
        return False
    if count:
        t.append(now)
    return True

def _dir_size(p):
    """Total bytes of all files inside a bundle directory (excl. .meta)."""
    total = 0
    try:
        for f in os.listdir(p):
            fp = os.path.join(p, f)
            if os.path.isfile(fp) and not f.endswith(".meta"):
                total += os.path.getsize(fp)
    except OSError:
        pass
    return total

def total_size():
    """Total bytes of all stored data (single files + bundle contents)."""
    total = 0
    for f in os.listdir(ROOT):
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            if not f.endswith(".meta"):
                total += os.path.getsize(p)
        elif os.path.isdir(p):
            if f == pics.NS:
                continue    # pics has its own pool (RFQ TR-1), never counted here
            if f == DIR_NS:
                total += dirs.ns_total()
            else:
                total += _dir_size(p)
    return total


def _units():
    """Yield (path, is_dir, mtime) for each top-level storage unit.
    Units are single files OR whole bundle directories — eviction/expiry
    treats each as one atomic thing. Dirs (ROOT/d/<key>) are yielded
    individually (one unit per dir), so eviction can target them
    independently."""
    for f in os.listdir(ROOT):
        if f.endswith(".meta"):
            continue
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            if _meta_retained(p + ".meta"):
                continue    # retained units are never eviction candidates
            yield p, False, os.path.getmtime(p)
        elif os.path.isdir(p):
            if f == pics.NS:
                continue    # pics is never an eviction unit (own pool, RFQ TR-1)
            if f == DIR_NS:
                yield from dirs.evict_units()
            else:
                if (_bundle_meta(p, f) or {}).get("retain"):
                    continue
                yield p, True, os.path.getmtime(p)

def _meta_retained(mp):
    """True when the meta file at mp marks its unit as retained."""
    if os.path.isfile(mp):
        try:
            with open(mp, "r", encoding="utf-8") as _f:
                return bool(json.load(_f).get("retain"))
        except Exception:
            pass
    return False


def _meta_expired(m, now):
    """True when a manifest is past its lifetime. Retained units
    (m['retain']) never expire; a missing manifest counts as expired."""
    if m is None:
        return True
    if m.get("retain"):
        return False
    return m.get("expires", 0) < now


def _fmt_exp(expires):
    """ISO expiry timestamp, or None for retained (indefinite) objects."""
    if expires is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))


def _retain_meta_dict(m):
    """Flip a LOADED manifest to indefinite retention in place (Retro
    review 1.45.4: dieser Block stand 5x). Singles/Bundles haben kein
    max_age — pop ist dort harmlos."""
    m["retain"] = True
    m.pop("expires", None)
    m.pop("max_age", None)
    return m


def evict(target):
    """Delete oldest units (by mtime) until total data size <= target."""
    while total_size() > target:
        units = list(_units())
        if not units:
            return
        oldest = min(units, key=lambda u: u[2])
        _remove_unit(oldest[0], oldest[1])

def _remove(fp):
    try:
        os.remove(fp)
    except OSError:
        pass
    for suffix in (".meta", ".thumb"):
        xp = fp + suffix
        if os.path.isfile(xp):
            try:
                os.remove(xp)
            except OSError:
                pass

def _remove_unit(path, is_dir):
    if is_dir:
        shutil.rmtree(path, ignore_errors=True)
    else:
        _remove(path)

def _bundle_meta(dirpath, fid):
    """Read a bundle's manifest; None if missing/unreadable."""
    mp = os.path.join(dirpath, fid + ".meta")
    if os.path.isfile(mp):
        try:
            with open(mp, "r", encoding="utf-8") as _f:
                return json.load(_f)
        except Exception:
            pass
    return None

def sweep():
    """Delete expired files and bundles."""
    now = time.time()
    for f in os.listdir(ROOT):
        if f.endswith(".meta"):
            continue
        if f.endswith(".thumb"):
            continue  # dies together with its original via _remove
        if f.endswith(".thumbtmp"):
            # orphaned temp file from a crashed thumbnail generation
            p = os.path.join(ROOT, f)
            try:
                if os.path.getmtime(p) < now - 3600:
                    os.remove(p)
            except OSError:
                pass
            continue
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            mp = p + ".meta"
            if _meta_retained(mp):
                continue    # retained files never expire
            expires = None
            if os.path.isfile(mp):
                try:
                    with open(mp, "r", encoding="utf-8") as _f:
                        expires = json.load(_f).get("expires")
                except Exception:
                    pass
            if expires is None:
                expires = os.path.getmtime(p) + TTL_HOURS * 3600
            if expires < now:
                _remove(p)
        elif os.path.isdir(p):
            if f == DIR_NS:
                dirs.sweep(now)
                continue
            if f == pics.NS:
                pics.sweep(ROOT, now)
                continue
            m = _bundle_meta(p, f)
            if m and m.get("retain"):
                continue    # retained bundles never expire
            expires = (m or {}).get("expires")
            if expires is None:
                expires = os.path.getmtime(p) + TTL_HOURS * 3600
            if expires < now:
                shutil.rmtree(p, ignore_errors=True)


def _safe_name(name):
    """Reduce a user filename to a safe basename for Content-Disposition.
    Also neutralizes reserved suffixes (.meta/.history/.thumb/.thumbtmp) so an
    upload can never masquerade as server bookkeeping — such names get a
    trailing underscore."""
    if not name:
        return None
    name = os.path.basename(name.replace("\\", "/"))
    # strip control chars and quotes that could break the header
    name = re.sub(r'[\r\n\"\x00-\x1f]', "", name).strip()
    lower = name.lower()
    if any(lower.endswith(s) for s in (".meta", ".history", ".thumb", ".thumbtmp")):
        name += "_"
    return name or None


# --- URL import & link docs -------------------------------------------------
# POST /?url=<u>            -> fetch the remote document server-side, store it
# POST /?url=<u>&link=1     -> store the URL itself as a tiny redirect HTML doc

class _FetchError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # we follow redirects manually to re-check SSRF per hop


ALLOW_PRIVATE_FETCH = os.environ.get("THROWAWAY_ALLOW_PRIVATE_FETCH", "") == "1"


def _assert_public_host(host):
    """Reject hosts that resolve to private/loopback/reserved IPs (SSRF guard).
    THROWAWAY_ALLOW_PRIVATE_FETCH=1 disables the guard (tests/dev only —
    never set this in production)."""
    if ALLOW_PRIVATE_FETCH:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise _FetchError(400, "cannot resolve host")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local or
                ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise _FetchError(400, "blocked host (private/reserved IP)")


def _fetch_remote(raw_url, max_bytes=None):
    """Fetch an http(s) URL server-side. Returns (data, name, ctype).
    Raises _FetchError on any problem. Size-capped (default MAX_FILE;
    pics passes its own 30 MB budget)."""
    u = urlparse(raw_url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise _FetchError(400, "url must be http(s) and absolute")
    name = os.path.basename(unquote(u.path)) or None
    ctype = None
    url = raw_url
    opener = urllib.request.build_opener(_NoRedirect())
    resp = None
    for _hop in range(4):
        hop = urlparse(url)
        if hop.scheme not in ("http", "https") or not hop.hostname:
            raise _FetchError(400, "redirect target must be http(s)")
        _assert_public_host(hop.hostname)
        req = urllib.request.Request(url, headers={"User-Agent": "throway-import/1.12"})
        try:
            resp = opener.open(req, timeout=10)
            break
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if 300 <= e.code < 400 else None
            if not loc:
                raise _FetchError(502, f"upstream HTTP {e.code}")
            url = urljoin(url, loc)
        except (urllib.error.URLError, OSError):
            raise _FetchError(502, "upstream unreachable")
    else:
        raise _FetchError(502, "too many redirects")
    disp = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', disp)
    if m:
        name = unquote(m.group(1))
    ct_hdr = resp.headers.get("Content-Type", "")
    ctype = ct_hdr.split(";")[0].strip() or None
    cap = max_bytes or MAX_FILE
    data = resp.read(cap + 1)
    if len(data) > cap:
        raise _FetchError(413, f"too large (max {cap // (1024 * 1024)}MB)")
    fname = _safe_name(name) if name else None
    if not ctype or ctype == "application/octet-stream":
        if fname:
            ctype = mimetypes.guess_type(fname)[0] or ctype
    return data, fname, ctype or "application/octet-stream"


def _link_doc(target):
    """Tiny redirect HTML document for a stored URL (link=1 uploads)."""
    esc = _html.escape(target, quote=True)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"refresh\" content=\"0; url={esc}\">"
        f"<title>{esc}</title></head>"
        "<body style=\"font-family:sans-serif;padding:2em\">"
        f"<p>Redirecting to <a href=\"{esc}\">{esc}</a>&hellip;</p>"
        "</body></html>"
    ).encode()

def _parse_multipart(payload, content_type):
    """Extract a list of (filename, data, ctype) from multipart/form-data."""
    import email
    import email.parser
    try:
        msg = email.parser.BytesParser().parsebytes(payload)
    except Exception:
        return []
    if not msg.is_multipart():
        # fallback: manual boundary split
        m = re.search(r'boundary="?([^";]+)"?', content_type)
        if not m:
            return []
        boundary = m.group(1).encode()
        parts = payload.split(b"--" + boundary)
        out = []
        for part in parts:
            if b"filename=" in part[:200]:
                header, _, body = part.partition(b"\r\n\r\n")
                hm = re.search(r'filename="([^"]*)"', header.decode("latin1"))
                name = hm.group(1) if hm else None
                ctype = re.search(r'Content-Type:\s*(\S+)', header.decode("latin1"), re.I)
                out.append((name, body.rstrip(b"\r\n--"),
                            ctype.group(1) if ctype else "application/octet-stream"))
        return out
    out = []
    for part in msg.get_payload():
        fn = part.get_filename()
        if fn:
            data = part.get_payload(decode=True) or b""
            out.append((fn, data, part.get_content_type() or "application/octet-stream"))
    return out

def _dedupe_names(names):
    """Rename collisions within a bundle: a.txt, a-1.txt, a-2.txt …"""
    seen = {}
    out = []
    for n in names:
        base = n
        if base in seen:
            stem, ext = os.path.splitext(base)
            i = 1
            while f"{stem}-{i}{ext}" in seen:
                i += 1
            base = f"{stem}-{i}{ext}"
        seen[base] = True
        out.append(base)
    return out


def _valid_tag(t):
    return bool(t) and len(t) <= MAX_TAG_LEN and re.fullmatch(r"[a-z0-9-]+", t)


def _ttl_or_400(handler, qp):
    """&ttl= strikt (Retro 1.53.4): unparsebare Werte sind 400
    bad_request statt stiller Default. None = Parameter nicht
    gegeben (Default gilt). False = 400 bereits gesendet."""
    raw = (qp.get("ttl") or [""])[0]
    if not raw:
        return None
    secs = dirs.parse_ttl(raw)
    if secs is None:
        handler._err(400, "invalid ttl: " + repr(raw)
                    + " — use hours (12, 12h) or days (7d)")
        return False
    return secs

def _parse_tags(query_tags):
    """Normalize + dedupe a list of raw tag values; cap at MAX_TAGS."""
    out = []
    for t in query_tags:
        t = (t or "").strip().lower()
        if _valid_tag(t) and t not in out:
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


# ---------------------------------------------------------------------------
# Modular help — served individually at /help/<topic> so agents can gather
# only the pieces they need, and assembled into the full plain-text agent
# description. Single source of truth: this dict.
# Bodies use .format() placeholders ({PUBLIC_BASE}, {TTL_HOURS}, …) resolved
# at serve time against the live values.
# ---------------------------------------------------------------------------
HELP_ORDER = ["overview", "files", "bundles", "dirs", "markdown", "view", "edit", "delete", "limits", "contract"]

HELP = {
    "overview": {
        "title": "Overview",
        "summary": "What throway is and isn't",
        "body": """WHAT IT IS FOR
- Sharing a file (image, text, binary) by giving someone a URL.
- Sharing a BUNDLE of files (e.g. an html/css/js website) under one URL.
- Sharing a DIR: a long-lived, nameable collection under /d/<key> that an
  agent can keep adding to / editing over days, with a lightweight edit
  history. Addressable by an opaque id or a memorable name.
- A scratchpad for text: create a note, append to it, rewrite it.
- Passing data between agents / machines without setting up accounts.
- Publishing markdown that renders for humans: any .md file renders as a
  self-contained HTML page in a browser; agents get the raw markdown
  (see /help/markdown).

WHAT IT IS NOT
- Not permanent storage. Files are automatically deleted after their TTL.
- Not private. Anyone who has a URL can read, edit, or delete that file.
- Not a database. It is a flat, throwaway store.
- Not an app platform. State = files and dirs, nothing else: a "board" or
  "dashboard" URL you find on throway is simply an uploaded HTML file.

Base URL: {PUBLIC_BASE}""",
    },
    "files": {
        "title": "Upload a file",
        "summary": "POST a file, get a URL back",
        "body": """UPLOAD a file (raw body or multipart):
   POST {PUBLIC_BASE}/?name=filename.ext
   with the file bytes as the body.
   -> Returns JSON: id, url, size, name, content_type, expires_in, expires_at.

LIFETIME (optional):
   POST {PUBLIC_BASE}/?name=x.txt&ttl=24h
   -> default is {TTL_HOURS}h; &ttl=<h|d> extends a single file, clamped to
      [4h, 14d] (MAX 14 days).

SHARE NAME (optional):
   POST {PUBLIC_BASE}/?share=my-note
   -> store the upload under a chosen, memorable name (create-or-get, like a
      named dir) at /d/my-note, instead of a random hex id. Rules: 5-32 chars
      [a-z0-9-], >=1 letter, not a reserved word. Sliding lifetime (default
      7d, &ttl= clamped [4h,14d]). For markdown docs (rendering, edits,
      history) see /help/markdown.

DOWNLOAD ONCE (optional, single files only):
   POST {PUBLIC_BASE}/?once=1
   -> burn-after-reading: the file auto-deletes after the first download.
      A second GET returns 404. Not combinable with &share= (dirs).

TAGS on uploads/imports (filter+sort later):
   POST {PUBLIC_BASE}/?name=x.pdf&tag=papers&tag=2026
   -> up to 5 tags per file ([a-z0-9-], 1-24 chars); returned in the JSON.
   Update later:  POST {PUBLIC_BASE}/<id>?tag=a&untag=b
   Browse/filter: GET  {PUBLIC_BASE}/browse?tag=papers&q=&sort=created&order=desc
   (sort: created | name | size | expires)

IMPORT FROM A URL (server-side fetch):
   POST {PUBLIC_BASE}/?url=<encoded-url>[&name=<filename>]
   -> server downloads the document and stores it like a normal upload.
   Max 5MB; private/loopback hosts are blocked.

STORE A URL AS A DOCUMENT (link doc):
   POST {PUBLIC_BASE}/?url=<encoded-url>&link=1[&name=<name>]
   -> stores a tiny editable HTML redirect page for that URL.""",
    },
    "bundles": {
        "title": "Upload a bundle",
        "summary": "Multiple files under one URL (a mini website)",
        "body": """UPLOAD a BUNDLE (multiple files, e.g. a website):
   POST {PUBLIC_BASE}/   with multipart/form-data containing 2+ file parts.
   -> Returns JSON: id, url, bundle:true, files:[{{name,url,size,content_type}}...].
   The bundle URL serves index.html inline (or a zip for agents).
   Each file is reachable at {PUBLIC_BASE}/<id>/<filename>.""",
    },
    "markdown": {
        "title": "Markdown (.md)",
        "summary": ".md renders as an HTML page for browsers; raw for agents; living-doc recipe",
        "body": """MARKDOWN (.md / .markdown):
   Any .md upload renders as a self-contained HTML page in a browser.
   Agents (curl UA) and ?raw=1 always get the raw text/markdown — same URL.
   Rendered pages embed social-card meta (og:title = first heading or
   filename, og:description = first paragraph) so links shared on
   X/Slack/Discord show a preview card.

   ONE-OFF DOC:
   POST {PUBLIC_BASE}/?name=notes.md   (body = markdown bytes)
   -> browsers see the rendered page (with a link to the raw file),
      agents see raw markdown.

   LIVING DOC (editable, with history) -> use a NAMED DIR:
   POST {PUBLIC_BASE}/?dir=1&name=my-docs          # create-or-get, idempotent
   curl -F "f=@notes.md;type=text/markdown" {PUBLIC_BASE}/d/my-docs
   PUT   {PUBLIC_BASE}/d/my-docs/notes.md   # replace content
   PATCH {PUBLIC_BASE}/d/my-docs/notes.md   # append content
   -> the dir URL is the stable link; each edit slides the lifetime
      forward; {PUBLIC_BASE}/d/my-docs/history shows what changed.
   .md files INSIDE a dir render exactly like standalone ones.

   RECIPE — publish an issue / spec / report an agent keeps updating:
   named dir + .md file + PUT edits. Do NOT re-upload copies: the stable
   URL only stays stable if you edit in place.

   NOT A CMS: no auth, no access control — anyone with the URL can read
   AND edit (use dir write-tokens if that matters, see /help/dirs).
   Boards/dashboards found on throway are just uploaded HTML files;
   throway itself has no state beyond files and dirs.""",
    },
    "view": {
        "title": "Download / view",
        "summary": "Inline vs download; bundle/dir behavior",
        "body": """DOWNLOAD / VIEW a file:
   GET {PUBLIC_BASE}/<id>
   Images and text-like types (text, html, json, pdf, svg) render inline
   in a browser; other files download. .md / .markdown files render as a
   self-contained HTML page for browsers; agents and ?raw=1 get the raw
   markdown (see /help/markdown).
   For a bundle, GET {PUBLIC_BASE}/<id> serves index.html inline (browser)
   or the whole bundle as a zip (agents). GET {PUBLIC_BASE}/<id>/<file>
   serves one file.
   Append ?download=1 to force a download of any file or the bundle zip.

   SOCIAL PREVIEW CARDS (1.54.0): shared links carry og:/twitter: meta.
   Rendered .md pages, dir pages and bundle index pages embed it for
   every browser; raw .html files get it injected only for card crawlers
   (Twitterbot, facebookexternalhit, Slackbot, Discordbot, ...) — a page
   with its own og: tags keeps them. Browsers/agents: unchanged bytes.""",
    },
    "edit": {
        "title": "Edit / append text",
        "summary": "PUT replaces, PATCH appends (text files only)",
        "body": """EDIT TEXT (text files only; images are immutable):
   PUT   {PUBLIC_BASE}/<id>   with new text body  -> replace whole content
   PATCH {PUBLIC_BASE}/<id>   with text body      -> append to content

Every upload/listing response includes an \"editable\" boolean per file, so an
agent can tell at a glance whether PUT/PATCH will work: true for text/* and
application/json, false for images and other binaries. A bundle or dir object
itself is editable:false; only its text/* or application/json files are.""",
    },
    "delete": {
        "title": "Delete",
        "summary": "Remove a file, bundle, or dir",
        "body": """DELETE a file:
   DELETE {PUBLIC_BASE}/<id>
   DELETE {PUBLIC_BASE}/d/<key>/<file>  -> remove one file from a dir
   DELETE {PUBLIC_BASE}/d/<key>         -> delete a whole dir""",
    },
    "limits": {
        "title": "Limits",
        "summary": "Lifetimes, sizes, pool, rate limit",
        "body": """LIMITS
- URL lifetime:  {TTL_HOURS} hours by default; single files can be extended
  via &ttl=<h|d> when uploading, clamped to [4h, 14d] (MAX 14 days)
- Dir lifetime: sliding, default 7 days, MAX 14 days via ttl= (clamped
  [4h, 14d]);
  each add/edit/delete slides expires_at forward, capped at 30 days total
- Max file size: {MAX_FILE_MB} MB
- Pool size:     {POOL_MB} MB (oldest files evicted first)
- Rate limit:    {RATE_LIMIT} requests/min per IP
- Dir history:   last {HISTORY_LIMIT} entries kept per dir

PERSISTENCE — how long something lives, per type (also in each response's
\"persistence\" block):
- single file:  {TTL_HOURS}h by default (extendable_by:none; &ttl= up to 14d
                set at upload time; &share= stores it under a chosen name
                with a sliding 7d default lifetime instead; &once=1 = burn-
                after-reading, auto-deletes after the first download)
- dir:          sliding lifetime (default 7d), extendable by activity
                (extendable_by:activity), capped at 30 days total
- bundle:       fixed {TTL_HOURS}h snapshot, not extendable (extendable_by:none)""",
    },
    "contract": {
        "title": "Machine-readable contract",
        "summary": "Read /api for current limits + endpoints as JSON",
        "body": """MACHINE-READABLE CONTRACT
GET {PUBLIC_BASE}/api  -> returns the same limits + endpoints as JSON.
An agent should read /api to discover current limits before acting.""",
    },
}


HELP.update({
    "errors": {
        "title": "Error codes + retry strategy",
        "summary": "Every error JSON carries `code`; what to do on 429/507",
        "body": contract.render_errors_help(contract.limits()),
    },
})
HELP_ORDER.append("errors")

HELP.update(pics.HELP_TOPICS)
HELP_ORDER.append("pics")
HELP.update(retain.HELP_TOPICS)
HELP_ORDER.append("retention")
HELP.update(dirs.HELP_TOPICS)


def _render_help_body(key):
    """Return a help topic's body with live values substituted."""
    t = HELP.get(key)
    if not t:
        return None
    vals = contract.limits()
    return t["body"].format(**vals)


def _is_editable(ctype):
    """Whether a stored file can be edited via PUT/PATCH.
    Text and JSON are editable (JSON also renders inline, matching the
    named-dir path). Everything else (images, binaries) is immutable."""
    if not ctype:
        return False
    return ctype.startswith("text/") or ctype == "application/json"


def _persistence_block(ptype, expires, max_age=None, extendable_by="none"):
    """A small, machine-readable block describing how long a resource lives
    and how an agent can keep it alive. Kept additive so old agents that only
    read id/url/expires_at are unaffected."""
    if expires is None:        # retained (indefinite, token-gated)
        return {
            "type": ptype,
            "expires_at": None,
            "extendable_by": "none",
            "max_age": None,
            "retention": "indefinite",
        }
    return {
        "type": ptype,          # "single" | "dir" | "bundle"
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)),
        "extendable_by": extendable_by,
        "max_age": max_age,     # seconds, or None for single (fixed 4h)
    }


def _is_thumbable(ctype):
    """True for raster image types we make thumbnails for. SVG is excluded:
    it is already tiny and scales losslessly in the browser."""
    return bool(ctype) and ctype.startswith("image/") and ctype != "image/svg+xml"


def _make_thumb(fpath, tpath, px=None):
    """Create a small WebP thumbnail for the image at fpath (Pillow).
    px = longest edge (defaults to THUMB_PX; srcset candidates pass their
    own width -> one cached file per width next to the original).
    Respects EXIF rotation (phone photos), flattens transparency onto white,
    and writes to a temp file + atomic replace so a concurrent request never
    sees a half-written thumb. Raises on failure — callers fall back to
    serving the original bytes."""
    import tempfile
    from PIL import Image, ImageOps
    fd, tmp = tempfile.mkstemp(suffix=".thumbtmp", dir=os.path.dirname(tpath))
    os.close(fd)
    try:
        with Image.open(fpath) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((px or THUMB_PX, px or THUMB_PX))
            im.save(tmp, "WEBP", quality=THUMB_QUALITY, method=4)
        os.replace(tmp, tpath)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# shared head metas for every browser page: mobile viewport + UI tint.
# Several pages used to ship without a viewport tag and were unreadable
# on smartphones (zoomed-out desktop layout).
_META_MOBILE = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<meta name=theme-color content='#2563eb'>")

# shared stylesheet for the secondary pages (listings, help, history, …).
# Mobile-first rules live here once: touch-friendly rows (44px tap targets),
# truncating filenames, thumb boxes, responsive padding.
_BASE_CSS = (
    ":root{--bg:#fff;--card:#fafafa;--card2:#f4f4f5;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}"
    "*{box-sizing:border-box}"
    "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.55;min-height:100vh;-webkit-text-size-adjust:100%}"
    "main{max-width:720px;margin:0 auto;padding:3rem 1.5rem}"
    "h1{font-size:1.4rem;letter-spacing:-.01em}"
    "ul{list-style:none;padding:0;margin:0}"
    "li{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.5rem .8rem;margin:.4rem 0;display:flex;justify-content:space-between;align-items:center;gap:.6rem}"
    "li a{color:var(--ink);text-decoration:none;font-weight:500;min-height:44px;display:flex;align-items:center;flex:1;overflow:hidden;overflow-wrap:anywhere}"
    "li a:hover{color:var(--accent)}"
    "li .sz,li .meta{color:var(--muted);font-size:.8rem;white-space:nowrap}"
    "li .tags{color:var(--accent);font-size:.75rem;width:100%}"
    "img.thumb{width:44px;height:44px;object-fit:cover;border-radius:6px;flex:none;background:var(--card2)}"
    "a.btn{display:inline-block;min-height:44px;background:var(--accent);color:#fff;text-decoration:none;font-size:.95rem;font-weight:600;padding:.6rem 1.2rem;border-radius:8px}"
    "a.btn:hover{background:#1d4ed8}"
    "a.back{display:inline-flex;align-items:center;min-height:44px;margin-top:1rem;color:var(--muted);text-decoration:none;font-size:.9rem}"
    "a.back:hover{color:var(--accent)}"
    ".agenthint{margin-top:1.2rem}"
    ".agenthint summary{cursor:pointer;color:var(--muted);font-size:.8rem;user-select:none}"
    ".agenthint pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.7rem .9rem;font:75%/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-x:auto;color:var(--ink)}"
    "@media(max-width:560px){main{padding:1.5rem 1rem 3rem}h1{font-size:1.25rem}}"
)


def _agent_hint(*lines):
    """Collapsed 'agent hint' block for browser HTML pages: copy-pasteable
    curl lines with absolute URLs. Collapsed for humans, fully present in
    source/a11y tree for agents that land on the page with a browser UA.
    Lines are HTML-escaped here — pass them raw (&, <, > are fine)."""
    if not lines:
        return ""
    body = "\n".join(_html_escape(l) for l in lines)
    return ("<details class=agenthint>"
            "<summary>agent hint — this page is machine-readable</summary>"
            f"<pre>{body}</pre></details>")


# ---------------------------------------------------------------------------
# The request kit — the explicit seam between the Handler and the throway/
# modules (born with dirs 1.49.0; pics followed 1.50.0). A module receives
# exactly this object, never the Handler itself: sending, request context,
# shared helpers and config live here, and nothing else crosses the seam.
# ---------------------------------------------------------------------------

class _RequestKit:
    def __init__(self, h):
        self._h = h

    # --- request context ---
    @property
    def query(self):
        """Raw query string of the request ('' when none)."""
        return self._h.path.split("?", 1)[1] if "?" in self._h.path else ""

    def header(self, name, default=None):
        """A request header value (e.g. Accept), `default` when absent."""
        return self._h.headers.get(name, default)

    @property
    def content_type(self):
        return self._h.headers.get("Content-Type", "application/octet-stream")

    def write_token_given(self):
        """The dir write token this request carries (X-Throway-Write header
        or ?write=<token>), '' when none."""
        given = (self._h.headers.get("X-Throway-Write") or "").strip()
        if not given:
            q = self.query
            for kv in q.split("&"):
                k, _, v = kv.partition("=")
                if k == "write" and v:
                    return unquote(v).strip()
            return ""
        return given

    def retain_token(self):
        """The retain token this request carries ('' when none)."""
        return retain.token_from(self._h)

    def retain_valid(self):
        return retain.valid(self.retain_token())

    def retain_write_denied(self, meta):
        """None when the write may proceed, else (code, error-dict)."""
        return retain.write_denied(self._h, meta)

    def client_ip(self):
        return self._h._client_ip()

    def is_agent(self):
        return self._h._is_agent()

    def wants_agent_repr(self):
        return self._h._wants_agent_repr()

    def read_body(self):
        return self._h._read_body()

    def read_raw(self, n):
        """Read up to n bytes straight from the request body socket —
        streaming reads and over-size drains (keep-alive policy stays
        with the caller). Prefer read_body() for the simple
        Content-Length case."""
        return self._h.rfile.read(n)

    # --- response plumbing ---
    def send(self, code, body=b"", ctype="text/plain", extra=None):
        return self._h._send(code, body, ctype, extra)

    def err(self, code, msg, retry_after=None):
        return self._h._err(code, msg, retry_after=retry_after)

    def serve_file(self, fp, ctype, orig, force_dl, fid, cache=None):
        return self._h._serve_file(fp, ctype, orig, force_dl, fid, cache=cache)

    def serve_thumb(self, fpath, ctype, px=None):
        return self._h._serve_thumb(fpath, ctype, px=px)

    def serve_zip(self, dirpath, fid):
        return self._h._serve_bundle_zip(dirpath, fid)

    def serve_markdown(self, fp, ctype, orig, fid):
        """True when the request was handled (markdown rendered)."""
        return self._h._maybe_serve_markdown(fp, ctype, orig, fid)

    # --- shared helpers + config (cross-cutting, owned by store.py) ---
    def atomic_json(self, path, obj):
        return _atomic_json(path, obj)

    def mark_retained(self, m):
        """Flip a loaded manifest to indefinite retention, in place."""
        return _retain_meta_dict(m)

    def evict_pool(self):
        """Evict oldest units until the throwaway pool is within budget."""
        return evict(THROW_POOL_SIZE)

    def bump_stats(self, files, bytes_):
        return _bump_stats(files, bytes_)

    def safe_name(self, name):
        return _safe_name(name)

    def dedupe_names(self, names):
        return _dedupe_names(names)

    def parse_tags(self, tags):
        return _parse_tags(tags)

    def parse_multipart(self, payload, ctype):
        return _parse_multipart(payload, ctype)

    def meta_expired(self, m, now):
        return _meta_expired(m, now)

    def fmt_exp(self, expires):
        return _fmt_exp(expires)

    def fmt_size(self, n):
        return _fmt_size(n)

    def persistence_block(self, *a, **kw):
        return _persistence_block(*a, **kw)

    def is_editable(self, ctype):
        return _is_editable(ctype)

    def agent_hint(self, *lines):
        return _agent_hint(*lines)

    def html_escape(self, s):
        return _html_escape(s)

    @property
    def root(self):
        """The storage root (THROWAWAY_ROOT) — same process as store.py."""
        return ROOT

    def fetch_remote(self, raw_url, max_bytes=None):
        """Server-side URL fetch with the SSRF guard (redirects re-checked
        per hop). Raises kit.FetchError(code, msg) on any problem."""
        return _fetch_remote(raw_url, max_bytes=max_bytes)

    FetchError = _FetchError    # exception class, for `except kit.FetchError`

    @property
    def base_css(self):
        return _BASE_CSS

    @property
    def meta_mobile(self):
        return _META_MOBILE

    @property
    def public_base(self):
        return PUBLIC_BASE

    @property
    def prefix(self):
        return PREFIX

    @property
    def max_file(self):
        return MAX_FILE

    @property
    def pool_size(self):
        return THROW_POOL_SIZE


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _kit(self):
        """The request kit for throway/ modules (dirs) — the one seam
        they get; see _RequestKit. Reads live handler state, so caching
        per handler is safe across keep-alive requests."""
        k = getattr(self, "_kit_obj", None)
        if k is None:
            k = self._kit_obj = _RequestKit(self)
        return k

    def _err(self, code, msg, retry_after=None):
        """Structured error (1.41.2 / P1): code + message, plus a
        Retry-After header/body field where waiting is the right move."""
        payload = {"error": msg, "code": _ERR_CODES.get(code, "error")}
        extra = None
        if retry_after:
            payload["retry_after"] = retry_after
            extra = {"Retry-After": str(retry_after)}
        return self._send(code, json.dumps(payload), "application/json", extra)

    def _send(self, code, body=b"", ctype="text/plain", extra=None):
        # 1.44.0: Idempotenz-Key resp. persistieren (1.42.x speicherte den
        # Key, rief _idem_put aber nie — Replay war still tot, dazu fraen
        # NameErrors die Loader). Erste 200-JSON-Antwort unter dem Key merken.
        pend = getattr(self, "_idem_pending", None)
        if pend and code == 200 and ctype == "application/json":
            try:
                _idem_put(pend, json.loads(body))
            except Exception:
                pass
            self._idem_pending = None
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if isinstance(body, str): body = body.encode()
        self.send_header("Content-Length", str(len(body)))
        headers = dict(extra or {})
        # agent hint (RFC 8288): every JSON response points at the
        # machine-readable contract, so any client that received JSON —
        # listing, upload result, even an error — learns where /api lives
        if ctype == "application/json" and "Link" not in headers:
            headers["Link"] = f'<{PUBLIC_BASE}/api>; rel="help"'
        # Fehler-Response auf eine Anfrage MIT Body: Connection schliessen.
        # Der ungelesene Rest-Body wird sonst nach der Antwort als
        # "naechste Anfrage" geparsed (phantom-4xx/5xx auf der Folgerequest,
        # Raw-Socket bewiesen — Retro 1.45.5). Keep-Alive bleibt fuer
        # fehlerfreie und koerperlose Anfragen erhalten.
        if code >= 400:
            try:
                cl = int((self.headers or {}).get("Content-Length") or 0)
            except (TypeError, ValueError):
                cl = 0
            te = (self.headers or {}).get("Transfer-Encoding") or ""
            # chunked-Body hat keinen Content-Length, wird nie gelesen
            # (411) und vergiftet sonst equally den Socket — 1.45.6
            if cl > 0 or "chunked" in te.lower():
                self.close_connection = True
                headers.setdefault("Connection", "close")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        # HEAD: send headers + Content-Length but no body
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # client hung up mid-response — nothing to do
                return False
        return True

    def _rate(self, count=True):
        if not allowed(self._client_ip(), count=count):
            # 1.41.2/P1: strukturierter Code + Retry-After (Rate-Fenster)
            self._err(429, "rate limit exceeded", retry_after=60)
            return False
        return True

    def _client_ip(self):
        """Real client IP. Behind nginx the socket peer is 127.0.0.1, so use
        the forwarded headers nginx sets (X-Real-IP / X-Forwarded-For)."""
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            ip = xff.split(",")[0].strip()
            if ip:
                return ip
        xri = self.headers.get("X-Real-IP")
        if xri:
            return xri.strip()
        return self.client_address[0]

    def _is_agent(self):
        """True if the requester looks like a non-browser client (curl/wget/python/agent)."""
        ua = self.headers.get("User-Agent", "").lower()
        if not ua:
            return True
        # browsers -> False (show HTML); everything else -> True (show agent info)
        browsers = ("mozilla", "chrome", "safari", "firefox", "edge", "opera")
        return not any(b in ua for b in browsers)


    def _wants_agent_repr(self):
        """P6 (sota): ?json=1 erzwingt die Agent-/JSON-Ansicht, ?html=1 die
        Browser-/HTML-Ansicht — auch gegen die UA-Heuristik (Custom-UAs und
        Parser bekommen sonst die falsche Representation). Default: UA."""
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        # exakte Params — Substring ("json=1" in q) matchte auch ?notjson=1
        params = set(kv for kv in q.split("&") if "=" in kv)
        if "json=1" in params:
            return True
        if "html=1" in params:
            return False
        return self._is_agent()

    def _wants_md_render(self):
        """Browser (non-agent UA, Accept: text/html) and no raw escape?
        ?raw=1 / ?download=1 always opt out. Agents always get raw."""
        if self._is_agent():
            return False
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        if "raw=1" in query or "download=1" in query:
            return False
        return "text/html" in (self.headers.get("Accept") or "")

    def _maybe_serve_markdown(self, fp, ctype, orig, fid):
        """Issue throway-md-render-browser: .md files render as self-contained
        HTML for browsers; agents and ?raw=1 keep getting the raw bytes.
        Returns True when the request was handled here."""
        if not self._wants_md_render():
            return False
        name = (orig or fid or "").lower()
        if not (ctype == "text/markdown" or name.endswith((".md", ".markdown"))):
            return False
        try:
            text = open(fp, "rb").read().decode("utf-8", "replace")
        except OSError:
            return False
        from throway import mdrender
        fname = (orig or fid or "markdown").rsplit("/", 1)[-1]
        raw_url = self.path.split("?", 1)[0] + "?raw=1"
        og_ctx = {"url": og.canonical(PUBLIC_BASE, PREFIX,
                                      self.path.split("?", 1)[0])}
        self._send(200, mdrender.render(text, title=fname, raw_url=raw_url,
                                        og=og_ctx),
                   "text/html; charset=utf-8")
        return True

    def _serve_file(self, fp, ctype, orig, force_dl, fid, cache=None):
        """Serve a single stored file (inline or attachment). Agents also get
        a Link hint pointing at the machine-readable contract."""
        size = os.path.getsize(fp)
        is_inline = any(ctype.startswith(p) for p in INLINE_TYPES)
        hint = {'Link': f'<{PUBLIC_BASE}/api>; rel="help"'} if self._is_agent() else None
        # Social-card crawlers (Twitterbot & co.) get the page bytes PLUS
        # og:/twitter: meta injected — real browsers/agents stay untouched.
        # ?raw=1 is the byte-exact escape: no injection either.
        _q = self.path.split("?", 1)[1] if "?" in self.path else ""
        if (not force_dl and is_inline and ctype == "text/html"
                and "raw=1" not in _q
                and og.is_crawler(self.headers.get("User-Agent", ""))):
            return self._serve_html_card(fp, orig, fid)
        if force_dl or not is_inline:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            fname = _safe_name(orig) or fid
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            for k, v in (hint or {}).items():
                self.send_header(k, v)
            if cache:
                self.send_header("Cache-Control", cache)
            self.end_headers()
        else:
            ct = ctype
            if ctype.startswith("text/") and "charset" not in ctype:
                ct = ctype + "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "inline")
            for k, v in (hint or {}).items():
                self.send_header(k, v)
            if cache:
                self.send_header("Cache-Control", cache)
            self.end_headers()
        with open(fp, "rb") as f:
            if self.command != "HEAD":
                while c := f.read(65536):
                    self.wfile.write(c)

    def _serve_html_card(self, fp, orig, fid):
        """Crawler variant of an inline text/html serve (1.54.0): the file's
        own bytes with og:/twitter: meta injected into <head> — unless the
        page carries its own card tags (the uploader's og: wins). Title and
        description come from the page's own <title>/<meta description>
        when present. Browsers and agents never take this path."""
        with open(fp, "rb") as f:
            html = f.read()
        if not og.has_card(html):
            fname = _safe_name(orig) or fid
            title = og.page_title(html, fallback=fname)
            desc = og.page_description(html) or (
                "HTML page hosted on throway — opens live in the browser.")
            url = og.canonical(PUBLIC_BASE, PREFIX, self.path.split("?", 1)[0])
            html = og.inject(html, og.meta(title=title, description=desc,
                                           url=url))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Content-Disposition", "inline")
        self.send_header("Vary", "User-Agent")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(html)

    def _serve_thumb(self, fpath, ctype, px=None):
        """Serve ?thumb[=N]: a small cached WebP preview for images.
        Generated lazily on first request, cached on disk as <file>.thumb
        (or <file>.thumb<N> for a srcset candidate width) so the CPU cost
        is paid once per file+width, not per view. Falls back to the
        original bytes if the type isn't thumbable (SVG) or generation
        fails, so <img src='…?thumb=1'> always shows something."""
        tp = fpath + (".thumb" if not px else f".thumb{int(px)}")
        usable = _is_thumbable(ctype) and os.path.isfile(tp)
        if _is_thumbable(ctype) and not usable:
            try:
                _make_thumb(fpath, tp, px)
                usable = True
            except Exception:
                usable = False
                try:
                    os.remove(tp)
                except OSError:
                    pass
        path, ct = (tp, "image/webp") if usable else (fpath, ctype or "application/octet-stream")
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._err(404, "not found")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            while c := f.read(65536):
                self.wfile.write(c)

    def _serve_bundle_index(self, index_path, dirpath, fid):
        """Serve a bundle's index.html to a browser, injecting a <base> tag
        so relative sub-resource URLs resolve against /<fid>/ instead of the
        parent path (fixes 404s for style.css/app.js/img in multi-file
        bundles viewed at /<fid> with no trailing slash)."""
        with open(index_path, "rb") as f:
            html = f.read()
        base = f'<base href="{PREFIX}/{fid}/">'
        # inject right after <head> (case-insensitive) or before <html>/start
        head = re.search(rb"<head[^>]*>", html, re.I)
        if head:
            html = html[:head.end()] + base.encode() + html[head.end():]
        else:
            # no <head>: prepend a minimal one with the base tag
            html = b"<head>" + base.encode() + b"</head>" + html
        crawler = og.is_crawler(self.headers.get("User-Agent", ""))
        if crawler and not og.has_card(html):
            url = og.canonical(PUBLIC_BASE, PREFIX, self.path.split("?", 1)[0])
            html = og.inject(html, og.meta(
                title=og.page_title(html, fallback="bundle " + fid),
                description=("HTML bundle hosted on throway — "
                             "opens live in the browser."),
                url=url))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Content-Disposition", "inline")
        # agents get the zip, browsers the page, crawlers injected meta
        self.send_header("Vary", "User-Agent")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(html)

    def _serve_bundle_zip(self, dirpath, fid):
        """Stream the whole bundle as a zip (for agents / ?download=1).
        Writes to a temp file so we don't hold the whole zip in RAM, then
        streams it out in chunks."""
        import tempfile
        tmp = tempfile.NamedTemporaryFile(prefix="throwayzip_", suffix=".zip", delete=True)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(dirpath)):
                if f.endswith((".meta", ".history", ".thumb", ".thumbtmp")):
                    continue
                z.write(os.path.join(dirpath, f), arcname=f)
        size = tmp.tell()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{fid}.zip"')
        self.end_headers()
        if self.command == "HEAD":
            tmp.close()
            return
        tmp.seek(0)
        try:
            while True:
                chunk = tmp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            tmp.close()

    def _bundle_listing(self, dirpath, fid):
        """Simple HTML file listing for a bundle with no index.html
        (mobile-friendly, with lazy image thumbnails)."""
        m = _bundle_meta(dirpath, fid) or {}
        rows = []
        for f in sorted(os.listdir(dirpath)):
            if f.endswith(".meta") or f.endswith(".thumb") or f.endswith(".thumbtmp"):
                continue
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                ct = m.get("files", {}).get(f) or mimetypes.guess_type(f)[0] or "application/octet-stream"
                rows.append((f, os.path.getsize(fp), ct))
        lis = "\n".join(
            "<li>"
            + (f'<img class=thumb src="{_html_escape(quote(f))}?thumb=1" alt="" loading=lazy decoding=async width=44 height=44>'
               if ct.startswith("image/") else "")
            + f'<a href="{_html_escape(quote(f))}">{_html_escape(f)}</a>'
            + f'<span class=sz>{_fmt_size(s)}</span></li>'
            for f, s, ct in rows)
        hint = _agent_hint(
            f"curl -A curl {PUBLIC_BASE}/{fid}                   # bundle root: whole bundle as zip for agents",
            f"curl {PUBLIC_BASE}/{fid}/<file>                # fetch one file",
            f"curl -OJ '{PUBLIC_BASE}/{fid}?download=1'      # force the zip download",
            f"curl {PUBLIC_BASE}/api                         # full machine-readable API",
        )
        h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"{_META_MOBILE}"
             f"<base href='{PREFIX}/{fid}/'>"
             f"<title>throway bundle {fid}</title>"
             f"<style>{_BASE_CSS}"
             ".btnrow{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1rem}"
             "@media(max-width:560px){.btnrow{flex-direction:column}.btnrow a.btn{text-align:center}}"
             "</style></head><body><main>"
             f"<h1>Bundle {fid}</h1><ul>{lis}</ul>"
             "<div class=btnrow>"
             f"<a class=btn href='?download=1'>download as zip</a>"
             "</div>"
             f"{hint}"
             f"<a class=back href='{PREFIX}/'>← throway</a>"
             "</main></body></html>")
        self._send(200, h, "text/html")

    def do_HEAD(self):
        """HEAD = GET headers without the body. Route through do_GET."""
        self.do_GET()

    def do_GET(self):
        # ?thumb=N requests (N ∈ 1|160|320|640) are cached micro-WebPs; a
        # gallery page fires 60+ of them (srcset wählt pro Feld EINE Breite),
        # so they stay OUTSIDE the rate counter (full images, uploads and
        # API calls still count). Two full gallery pages per minute would
        # otherwise 429 from request #101.
        # 1.39.3-Fix: der Test prüfte literal "thumb=1" — die seit 1.39.2
        # ausgelieferten srcset-Breiten thumb=320/thumb=640 fielen dadurch
        # UNTER den Zähler und 429ten jede Galerie ab Request #101
        # (Sichtbar als ~30 s Leerlauf beim Galerie-Laden).
        _q = self.path.split("?", 1)[1] if "?" in self.path else ""
        if not self._rate(count="thumb=" not in _q): return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", ""):
            if self._is_agent():
                return self._home_help()
            return self._index()
        if path == "/api":
            return self._api()
        if path == "/write_for_agents":
            return self._write_for_agents()
        if path == "/copy_for_agents":
            return self._copy_for_agents()
        if path == "/releases":
            return self._releases()
        if path == "/help":
            return self._help()
        if path.startswith("/help/"):
            key = path[len("/help/"):]
            if key:
                return self._help_topic(key)
            return self._help()
        # --- dirs: /d (listing) and /d/<key>[/<file>|/history] ---
        if path == "/" + DIR_NS:
            return dirs.listing_browse(self._kit())
        # --- tagged file browser: /browse?tag=<t>&q=&sort=&order= ---
        if path == "/browse":
            return self._browse(self.path.split("?", 1)[1] if "?" in self.path else "")
        # --- pics: event gallery — /pics, /pics/i/<id>, /pics/<secret>/… ---
        pics_parts = path.lstrip("/").split("/")
        if pics_parts and pics_parts[0] == pics.NS:
            return pics.get(self._kit(), pics_parts[1:],
                            self.path.split("?", 1)[1] if "?" in self.path else "")
        parts = path.lstrip("/").split("/")
        if parts and parts[0] == DIR_NS and len(parts) >= 2 and parts[1]:
            return dirs.get(self._kit(), parts[1], parts[1:],
                            self.path.split("?", 1)[1] if "?" in self.path else "")
        parts = path.lstrip("/").split("/")
        fid = parts[0]
        if not fid or fid.endswith(".meta") or fid.endswith(".thumb") or fid.endswith(".thumbtmp"):
            return self._err(404, "not found")
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        force_dl = "download=1" in query
        now = time.time()

        # --- bundle / dir directory ---
        dirpath = os.path.join(ROOT, os.path.basename(fid))
        if os.path.isdir(dirpath):
            m = _bundle_meta(dirpath, fid)
            if not (m or {}).get("retain"):
                expires = (m or {}).get("expires")
                if expires is None:
                    expires = os.path.getmtime(dirpath) + TTL_HOURS * 3600
                if expires < now:
                    shutil.rmtree(dirpath, ignore_errors=True)
                    return self._send(404, "expired\n")
            is_dir = (m or {}).get("type") == "dir"
            # /<fid>/<file>
            if len(parts) >= 2 and parts[1]:
                fname = os.path.basename(unquote(parts[1]))
                if not fname or fname.endswith(".meta") or fname.endswith(".thumb") or fname.endswith(".thumbtmp"):
                    return self._err(404, "not found")
                fpath = os.path.join(dirpath, fname)
                if not os.path.isfile(fpath):
                    return self._err(404, "not found")
                ctype = (m or {}).get("files", {}).get(fname)
                if not ctype:
                    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                if "thumb=1" in query:
                    return self._serve_thumb(fpath, ctype)
                if self._maybe_serve_markdown(fpath, ctype, fname, fname):
                    return
                return self._serve_file(fpath, ctype, fname, force_dl, fname)
            # dir root: JSON listing for agents, HTML for browsers, zip on ?zip=1
            if is_dir:
                if force_dl or "zip=1" in query or self._wants_agent_repr():
                    # agents get JSON listing; ?zip=1 / ?download=1 get zip
                    if "zip=1" in query or force_dl:
                        return self._serve_bundle_zip(dirpath, fid)
                    return dirs.response(self._kit(), fid, dirpath, m)
                # BUGFIX: was _dir_listing(dirpath, fid) — wrong arity, crashed
                # for browsers with a 500 on the legacy /<dir-id> path
                return dirs.listing(self._kit(), fid, dirpath, m)
            # bundle root
            if force_dl or self._is_agent():
                return self._serve_bundle_zip(dirpath, fid)
            index = os.path.join(dirpath, "index.html")
            if os.path.isfile(index):
                return self._serve_bundle_index(index, dirpath, fid)
            return self._bundle_listing(dirpath, fid)

        # --- single file ---
        fp = _id_path(fid)
        if not os.path.isfile(fp):
            return self._err(404, "not found")
        mp = fp + ".meta"
        if not _meta_retained(mp):
            expires = None
            if os.path.isfile(mp):
                try:
                    with open(mp, "r", encoding="utf-8") as _f:
                        expires = json.load(_f).get("expires")
                except Exception:
                    pass
            if expires is None:
                expires = os.path.getmtime(fp) + TTL_HOURS * 3600
            if expires < now:
                _remove(fp)
                return self._send(404, "expired\n")
        ctype = "application/octet-stream"
        orig = None
        if os.path.isfile(mp):
            try:
                with open(mp, "r", encoding="utf-8") as _f:
                    m = json.load(_f)
                ctype = m.get("ctype") or ctype
                orig = m.get("name")
            except Exception:
                pass
        # once=1 files: auto-delete after the first successful read, so the
        # link works exactly once (burn-after-reading). The file is removed
        # before the body is written; a concurrent second GET gets 404.
        once = False
        if os.path.isfile(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    once = bool(json.load(f).get("once"))
            except Exception:
                once = False
        if once:
            if "thumb=1" in query:
                return self._serve_thumb(fp, ctype)
            try:
                with open(fp, "rb") as f:
                    data = f.read()
            except OSError:
                return self._err(404, "not found")
            _remove(fp)
            self._send(200, data, ctype, {"X-Once": "1", "X-Expires": "0"})
            return
        if "thumb=1" in query:
            return self._serve_thumb(fp, ctype)
        if self._maybe_serve_markdown(fp, ctype, orig, fid):
            return
        self._serve_file(fp, ctype, orig, force_dl, fid)

    def do_POST(self):
        if not self._rate(): return
        # 1.42.x P2: Idempotenz-Key. Ein Agent, der nach Timeout wiederholt,
        # darf keine zweite Datei erzeugen. Header oder ?idem=; der Key wird
        # nur gehasht gespeichert. Treffer -> dieselbe Antwort wie beim
        # ersten Mal (sofern das Ziel noch lebt).
        idem = (self.headers.get("Idempotency-Key")
                or (unquote((self.path.split("?", 1)[1].partition("idem=")[2]
                             if "idem=" in self.path else "")).split("&")[0]))
        idem = idem.strip()[:128] if idem else None
        if idem:
            prev = _idem_get(idem)
            if prev and os.path.isfile(_id_path(prev.get("id", ""))):
                prev = dict(prev)
                prev["idempotent_replay"] = True
                return self._send(200, json.dumps(prev, indent=2),
                                  "application/json")
        self._idem_pending = idem
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        qp = {}
        for kv in query.split("&"):
            if not kv:
                continue
            k, _, v = kv.partition("=")
            qp.setdefault(k, []).append(v)
        name_hint = None
        want_dir = ("dir=1" in query)
        share = (qp.get("share") or [""])[0].strip()
        once = ("once=1" in query)
        tags = _parse_tags(qp.get("tag", []))
        if "name" in qp:
            name_hint = _safe_name(unquote(qp["name"][0]))[:128]

        # token-gated indefinite retention (throway.retain): a request
        # carrying a token either becomes retained or fails 401 — never
        # silently disposable. ?retain=1 without a token is a 401 too.
        retained, denied = retain.request_retention(self)
        if denied:
            return self._send(denied[0], json.dumps(denied[1]), "application/json")
        if retained and once:
            return self._err(400, "once=1 (burn-after-reading) and retention are mutually exclusive")
        # once=1 gilt nur fuer Einzeldateien (Doku: "not with &share=") —
        # vorher wurde share= still angenommen und once ignoriert (Luecken-
        # Batterie 2026-10-03, Vertragsbruch /api vs Code)
        if once and share:
            return self._err(400, "once=1 (burn-after-reading) and share= are mutually exclusive")

        path = self.path.split("?", 1)[0].rstrip("/")
        parts = path.lstrip("/").split("/")

        # --- pics: gallery upload (/pics) + admin actions (/pics/<secret>) ---
        if parts and parts[0] == pics.NS:
            return pics.post(self._kit(), parts[1:], qp)

        # POST /d/<key>?show=1 (token) -> flip a dir to a SHOW-DIR:
        # indefinite AND publicly writable. show=1 on non-dirs is a 400.
        if retained and "show=1" in query and not want_dir:
            if parts and parts[0] == DIR_NS and len(parts) == 2 and parts[1]:
                return dirs.show_flip(self._kit(), parts[1])
            return self._err(400, "show=1 applies to dirs only: POST /d/<key>?show=1")

        # POST /<id>?retain=1 (token) -> flip an existing file/bundle to
        # indefinite retention; POST /d/<key>?retain=1 flips a whole dir.
        if retained and "retain=1" in query:
            if parts and parts[0] == DIR_NS and len(parts) == 2 and parts[1]:
                return dirs.retain_flip(self._kit(), parts[1])
            if len(parts) == 1 and parts[0]:
                if parts[0] in (DIR_NS, pics.NS):
                    # reservierte Namespaces: kein Flip — und kein
                    # Fall-through in den Upload-Pfad (Retro hard-validate:
                    # POST /d?retain=1 erzeugte still retained Muell-Files)
                    return self._err(404, "not found")
                return self._retain_flip(parts[0])

        # POST /<id>?tag=a&tag=b&untag=c -> update tags on an existing file
        # (single-file ids only; dirs have their own tag handling at create)
        if len(parts) == 1 and parts[0] and parts[0] != DIR_NS \
                and not want_dir and "url" not in qp and not share \
                and ("tag" in qp or "untag" in qp):
            return self._file_tags(parts[0], qp)

        # POST /d/<key> -> add files to an existing dir (multipart)
        if parts and parts[0] == DIR_NS and len(parts) == 2 and parts[1]:
            r = dirs.post_add(self._kit(), parts[1])
            if r is not None:
                return r
            # Retro hard-validate: None (Dir fehlt/invalid) darf NICHT in
            # den Raw-Upload-Pfad fallen — aus einem Dir-Add wurde sonst
            # still ein Einzel-Upload mit 200.
            return self._err(404, "dir not found")

        # POST /?url=<url>[&name=<name>][&link=1] -> server-side import / link doc
        if "url" in qp:
            return self._url_import(qp, tags, retained=retained)

        # POST /?dir=1[&name=<name>][&listed=1][&tag=..][&ttl=..] -> create a dir
        if want_dir:
            if name_hint:
                ok, reason = dirs.valid_name(name_hint)
                if not ok:
                    return self._err(400, f"invalid dir name: {reason}")
                key = name_hint
            else:
                key = secrets.token_hex(8)
            ttl = _ttl_or_400(self, qp)
            if ttl is False:
                return
            ctype = self.headers.get("Content-Type", "application/octet-stream")
            initial = None
            if ctype.startswith("multipart/form-data"):
                payload = self._read_body()
                if payload is None:
                    return
                files = _parse_multipart(payload, ctype)
                initial = [(n, d, c) for (n, d, c) in files if n]
            else:
                length = self.headers.get("Content-Length")
                if length not in (None, "0"):
                    length = int(length)
                    if length > MAX_FILE:
                        return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")
                    data = self.rfile.read(length)
                    initial = [(name_hint or "file", data, "application/octet-stream")]
            return dirs.create(self._kit(), key, qp, initial, retained=retained)

        ctype = self.headers.get("Content-Type", "application/octet-stream")
        # multipart/form-data upload (browser-friendly / -F)
        if ctype.startswith("multipart/form-data"):
            payload = self._read_body()
            if payload is None:
                return
            files = _parse_multipart(payload, ctype)
            named = [(n, d, c) for (n, d, c) in files if n]
            if not named:
                return self._err(400, "no file part in multipart body")
            # multiple files -> bundle
            if len(named) > 1:
                return self._store_bundle(named, retained=retained)
            n, d, c = named[0]
            ttl = _ttl_or_400(self, qp)
            if ttl is False:
                return
            if share:
                return dirs.share_store(self._kit(), d, _safe_name(n)[:128] or None, c, share, ttl,
                                         retained=retained)
            return self._store(d, _safe_name(n)[:128] or None, c, tags, ttl_seconds=ttl, once=once,
                               retained=retained)

        # raw-body upload: body is the file content
        length = self.headers.get("Content-Length")
        if length is None:
            return self._err(411, "length required")
        length = int(length)
        if length > MAX_FILE:
            return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")
        data = self.rfile.read(length)
        if name_hint:
            ctype = mimetypes.guess_type(name_hint)[0] or "application/octet-stream"
        else:
            if ctype.startswith("multipart") or "boundary" in ctype:
                ctype = "application/octet-stream"
        ttl = _ttl_or_400(self, qp)
        if ttl is False:
            return
        if share:
            return dirs.share_store(self._kit(), data, name_hint or None, ctype, share, ttl,
                                      retained=retained)
        return self._store(data, name_hint or None, ctype, tags, ttl_seconds=ttl, once=once,
                           retained=retained)

    @_storage.locked("files")
    def _retain_flip(self, fid):
        """POST /<id>?retain=1 (token) — flip an existing single file or
        bundle to indefinite retention. Idempotent."""
        fp = _id_path(fid)
        bdir = os.path.join(ROOT, fid)
        if os.path.isdir(bdir) and fid != DIR_NS and fid != pics.NS:
            m = _retain_meta_dict(_bundle_meta(bdir, fid) or {})
            _atomic_json(os.path.join(bdir, fid + ".meta"), m)
            return self._send(200, json.dumps({
                "id": fid, "url": f"{PUBLIC_BASE}/{fid}", "bundle": True,
                "retention": "indefinite", "expires_at": None,
                "persistence": _persistence_block("bundle", None)}), "application/json")
        if not os.path.isfile(fp):
            return self._err(404, "not found")
        mp = fp + ".meta"
        if not os.path.isfile(mp):
            return self._err(404, "not found")
        try:
            with open(mp, "r", encoding="utf-8") as _f:
                m = json.load(_f)
        except Exception:
            return self._err(404, "not found")
        _retain_meta_dict(m)
        _atomic_json(mp, m)
        return self._send(200, json.dumps({
            "id": fid, "url": f"{PUBLIC_BASE}/{fid}",
            "retention": "indefinite", "expires_at": None,
            "persistence": _persistence_block("single", None)}), "application/json")

    @_storage.locked("files")
    def _retain_meta(self, fid):
        """Flip a single file's meta to indefinite retention (token write)."""
        mp = _id_path(fid) + ".meta"
        if not os.path.isfile(mp):
            return
        try:
            with open(mp, "r", encoding="utf-8") as _f:
                m = json.load(_f)
        except Exception:
            return
        _retain_meta_dict(m)
        _atomic_json(mp, m)

    @_storage.locked("files")
    def _file_tags(self, fid, qp):
        """POST /<id>?tag=<t>&untag=<t> — update meta tags on a stored file."""
        fp = _id_path(fid)
        mp = fp + ".meta"
        if not os.path.isfile(fp) or not os.path.isfile(mp):
            return self._err(404, "not found")
        try:
            with open(mp, "r", encoding="utf-8") as _f:
                meta = json.load(_f)
        except Exception:
            return self._err(500, "meta unreadable")
        denied = retain.write_denied(self, meta)
        if denied:
            return self._err(denied[0], denied[1]["error"])
        add = _parse_tags(qp.get("tag", []))
        remove = _parse_tags(qp.get("untag", []))
        cur = list(meta.get("tags", []))
        cur = [t for t in cur if t not in remove]
        for t in add:
            if t not in cur:
                cur.append(t)
            if len(cur) >= MAX_TAGS:
                break
        meta["tags"] = cur
        _atomic_json(mp, meta)
        body = json.dumps({
            "id": fid,
            "url": f"{PUBLIC_BASE}/{fid}",
            "name": meta.get("name", fid),
            "tags": cur,
        })
        return self._send(200, body, "application/json")

    def _url_import(self, qp, tags=None, retained=False):
        """POST /?url=<u>[&name=<name>][&link=1]
        Default: fetch the remote document server-side and store it.
        link=1:  store the URL itself as a tiny redirect HTML document."""
        raw = unquote(qp["url"][0]).strip()
        override = qp.get("name", [None])[0]
        if override:
            override = _safe_name(unquote(override))[:128] or None
        if not raw.startswith(("http://", "https://")):
            return self._err(400, "url must start with http:// or https://")
        if "link" in qp:
            base = (override or
                    _safe_name(os.path.basename(unquote(urlparse(raw).path))) or
                    urlparse(raw).hostname or "link")
            if not base.lower().endswith((".html", ".htm")):
                base += ".html"
            return self._store(_link_doc(raw), base, "text/html", tags, retained=retained)
        try:
            data, fname, ctype = _fetch_remote(raw)
        except _FetchError as e:
            return self._err(e.code, e.msg)
        return self._store(data, override or fname, ctype, tags, retained=retained)

    def _browse(self, query):
        """GET /browse?tag=<t>[&tag=<t2>][&q=<substr>][&sort=created|name|size|expires][&order=asc|desc]
        JSON listing of live single files, filterable by tags (AND) and name
        substring. JSON for agents, simple HTML for browsers."""
        qp = {}
        for kv in query.split("&"):
            if not kv:
                continue
            k, _, v = kv.partition("=")
            qp.setdefault(k, []).append(v)
        want_tags = _parse_tags(qp.get("tag", []))
        qtext = (unquote((qp.get("q") or [""])[0]) or "").strip().lower()
        sort = (unquote((qp.get("sort") or [""])[0]) or "created").strip().lower()
        order = (unquote((qp.get("order") or [""])[0]) or "desc").strip().lower()
        if sort not in ("created", "name", "size", "expires"):
            sort = "created"
        if order not in ("asc", "desc"):
            order = "desc"
        now = time.time()
        entries = []
        for f in os.listdir(ROOT):
            if f.endswith(".meta"):
                continue
            p = os.path.join(ROOT, f)
            if not os.path.isfile(p):
                continue  # bundles/dirs are listed elsewhere
            mp = p + ".meta"
            try:
                with open(mp, "r", encoding="utf-8") as _f:
                    meta = json.load(_f)
            except Exception:
                continue
            if meta.get("retain"):
                expires = None      # retained: never expires
            else:
                expires = meta.get("expires", os.path.getmtime(p) + TTL_HOURS * 3600)
                if expires < now:
                    continue
            ftags = meta.get("tags", [])
            if want_tags and not all(t in ftags for t in want_tags):
                continue
            name = meta.get("name", f)
            if qtext and qtext not in name.lower() and not any(qtext in t for t in ftags):
                continue
            entries.append({
                "id": f,
                "url": f"{PUBLIC_BASE}/{f}",
                "name": name,
                "content_type": meta.get("ctype", "application/octet-stream"),
                "size": os.path.getsize(p),
                "tags": ftags,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.get("created", now))),
                "expires_at": _fmt_exp(expires),
                "_k": {"created": meta.get("created", 0), "name": name.lower(),
                        "size": os.path.getsize(p),
                        "expires": expires if expires is not None else float("inf")}[sort],
            })
        entries.sort(key=lambda e: e["_k"], reverse=(order != "asc"))
        total = len(entries)
        for e in entries:
            e.pop("_k", None)
        if self._wants_agent_repr():
            return self._send(200, json.dumps({"files": entries, "total": total}), "application/json")
        rows = "".join(
            '<li>'
            + (f'<img class=thumb src="{_html_escape(e["url"])}?thumb=1" alt="" loading=lazy decoding=async width=44 height=44>'
               if e["content_type"].startswith("image/") else "")
            + f'<a href="{_html_escape(e["url"])}">{_html_escape(e["name"])}</a> '
            + f'<span class=meta>{_fmt_size(e["size"])} · {e["content_type"]} · exp {e["expires_at"] or "∞ retained"}</span>'
            + (f'<span class=tags>{" ".join("#" + _html_escape(t) for t in e["tags"])}</span>' if e["tags"] else "")
            + '</li>'
            for e in entries)
        ahint = _agent_hint(
            f"curl -A curl '{PUBLIC_BASE}/browse?tag=<t>&q=<substr>'  # same list as JSON (filter+sort)",
            f"curl -X POST --data-binary @local '{PUBLIC_BASE}/?name=<file>'  # upload a file",
            f"curl {PUBLIC_BASE}/api                        # full machine-readable API",
        )
        h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"{_META_MOBILE}"
             f"<title>throway — files</title><style>{_BASE_CSS}"
             "li{flex-wrap:wrap}"
             "li .meta{display:block;width:100%;font-size:.75rem}"
             "li .tags{display:block;font-size:.75rem}"
             "h1 small{color:var(--muted);font-weight:normal}"
             "</style></head><body><main>"
             f"<h1>throway files <small>{total}</small></h1><ul>{rows}</ul>"
             f"{ahint}"
             f"<a class=back href='{PREFIX}/'>← throway</a></main></body></html>")
        return self._send(200, h, "text/html")

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return None
        return self.rfile.read(int(length))

    def _store(self, data, name_hint, ctype, tags=None, ttl_seconds=None, once=False,
               retained=False):
        if len(data) > MAX_FILE:
            return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")
        fid = secrets.token_hex(8)
        fp = _id_path(fid)
        with open(fp, "wb") as f:
            f.write(data)
        lifetime = ttl_seconds or TTL_HOURS * 3600  # default 4h; ttl= override (clamped 4h..14d)
        meta = {
            "ctype": ctype or "application/octet-stream",
            "name": name_hint or fid,
            "created": time.time(),
        }
        if retained:
            meta["retain"] = True          # token-gated: never expires
        else:
            meta["expires"] = time.time() + lifetime
        if once:
            meta["once"] = True
        if tags:
            meta["tags"] = tags
        _atomic_json(fp + ".meta", meta)
        evict(THROW_POOL_SIZE)
        _bump_stats(1, len(data))
        url = f"{PUBLIC_BASE}/{fid}"
        body = json.dumps({
            "id": fid,
            "url": url,
            "size": len(data),
            "name": meta["name"],
            "content_type": meta["ctype"],
            "editable": _is_editable(meta["ctype"]),
            **({"tags": meta["tags"]} if meta.get("tags") else {}),
            "persistence": _persistence_block("single", meta.get("expires")),
            "expires_in": None if retained else lifetime,
            "expires_at": _fmt_exp(meta.get("expires")),
        })
        if retained:
            self._send(200, body, "application/json")
        else:
            self._send(200, body, "application/json", {"X-Expires": str(lifetime)})

    def _store_bundle(self, files, retained=False):
        """Store multiple files as a bundle directory; return JSON response."""
        clean = []
        total = 0
        for n, d, c in files:
            safe = _safe_name(n)
            if not safe:
                continue
            if len(d) > MAX_FILE:
                return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB): {safe}")
            total += len(d)
            if total > THROW_POOL_SIZE:
                return self._err(413, f"bundle too large (pool max {THROW_POOL_SIZE // (1024 * 1024)}MB)")
            clean.append((safe, d, c))
        if not clean:
            return self._err(400, "no valid file parts")
        names = _dedupe_names([n for n, _, _ in clean])
        fid = secrets.token_hex(8)
        dirpath = os.path.join(ROOT, fid)
        os.makedirs(dirpath, exist_ok=True)
        files_map = {}
        for (_, d, c), name in zip(clean, names):
            with open(os.path.join(dirpath, name), "wb") as f:
                f.write(d)
            # sniff from extension first (like the single-file path), then
            # fall back to the multipart-provided type, then octet-stream
            files_map[name] = mimetypes.guess_type(name)[0] or c or "application/octet-stream"
        meta = {
            "bundle": True,
            "created": time.time(),
            "files": files_map,
        }
        if retained:
            meta["retain"] = True          # token-gated: never expires
        else:
            meta["expires"] = time.time() + TTL_HOURS * 3600
        _atomic_json(os.path.join(dirpath, fid + ".meta"), meta)
        evict(THROW_POOL_SIZE)
        _bump_stats(len(clean), sum(len(d) for _, d, _ in clean))
        expires_at = _fmt_exp(meta.get("expires"))
        body = json.dumps({
            "id": fid,
            "url": f"{PUBLIC_BASE}/{fid}",
            "bundle": True,
            "editable": False,
            "persistence": _persistence_block("bundle", meta.get("expires")),
            "files": [
                {"name": n,
                 "url": f"{PUBLIC_BASE}/{fid}/{quote(n)}",
                 "size": os.path.getsize(os.path.join(dirpath, n)),
                 "content_type": files_map[n]}
                for n in names
            ],
            "size": sum(os.path.getsize(os.path.join(dirpath, n)) for n in names),
            "expires_in": None if retained else TTL_HOURS * 3600,
            "expires_at": expires_at,
        })
        if retained:
            self._send(200, body, "application/json")
        else:
            self._send(200, body, "application/json", {"X-Expires": str(TTL_HOURS * 3600)})

    # ------------------------------------------------------------------
    def do_DELETE(self):
        if not self._rate(): return
        path = self.path.split("?", 1)[0].lstrip("/").rstrip("/")
        parts = path.split("/")
        # DELETE /d/<key>[/<file>]
        if parts and parts[0] == DIR_NS and len(parts) >= 2 and parts[1]:
            return dirs.delete(self._kit(), parts[1], parts[1:])
        fid = parts[0]
        fp = _id_path(fid)
        if os.path.isfile(fp):
            denied = retain.write_denied(self, self._meta_of(fid))
            if denied:
                return self._err(denied[0], denied[1]["error"])
            _remove(fp); self._send(200, "deleted\n")
        elif os.path.isdir(fp) and fid not in (DIR_NS, pics.NS):
            # bundle dir: DELETE /<id> removes the whole bundle (documented
            # since 1.0; retained bundles need the retain token — Retro
            # 2026-10-02 hard-validate: kein Create ohne Delete-Pfad)
            denied = retain.write_denied(self, _bundle_meta(fp, fid))
            if denied:
                return self._err(denied[0], denied[1]["error"])
            shutil.rmtree(fp, ignore_errors=True)
            self._send(200, "deleted\n")
        else:
            self._err(404, "not found")

    def _meta_of(self, fid):
        mp = _id_path(fid) + ".meta"
        if os.path.isfile(mp):
            try:
                with open(mp, "r", encoding="utf-8") as _f:
                    return json.load(_f)
            except Exception:
                pass
        return None

    def _is_text(self, fid):
        m = self._meta_of(fid)
        return _is_editable((m or {}).get("ctype", ""))

    def _text_result(self, fid):
        fp = _id_path(fid)
        meta = self._meta_of(fid) or {}
        size = os.path.getsize(fp)
        return json.dumps({
            "id": fid,
            "url": f"{PUBLIC_BASE}/{fid}",
            "size": size,
            "name": meta.get("name", fid),
            "content_type": meta.get("ctype", "text/plain"),
            "editable": _is_editable(meta.get("ctype", "text/plain")),
            "persistence": _persistence_block("single", meta.get("expires")),
            "expires_at": _fmt_exp(meta.get("expires")),
        })

    def do_PUT(self):
        """Replace text content (edit)."""
        if not self._rate(): return
        p = self.path.split("?", 1)[0].lstrip("/")
        parts = p.split("/")
        # PUT /d/<key>/<file> -> edit a file inside a dir
        if parts and parts[0] == DIR_NS and len(parts) >= 3:
            return dirs.edit(self._kit(), parts[1], parts[1:], append=False)
        fid = parts[0]
        if not fid or fid.endswith(".meta"):
            return self._err(404, "not found")
        fp = _id_path(fid)
        if os.path.isdir(fp) and fid not in (DIR_NS, pics.NS):
            # Bundle: existiert, ist aber nicht editierbar — 403, nicht
            # 404 (Retro 1.53.4: ein Agent darf nicht auf eine falsche
            # ID schließen, wenn der Edit gemeint war)
            return self._err(403, "bundles are immutable snapshots (editable: false)")
        if not os.path.isfile(fp):
            return self._err(404, "not found")
        if not self._is_text(fid):
            return self._err(400, "only text files can be edited")
        denied = retain.write_denied(self, self._meta_of(fid))
        if denied:
            return self._err(denied[0], denied[1]["error"])
        data = self._read_body()
        if data is None:
            return self._err(411, "length required")
        if len(data) > MAX_FILE:
            return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")
        with open(fp, "wb") as f:
            f.write(data)
        if retain.valid(retain.token_from(self)):
            self._retain_meta(fid)     # token write implies retention
        evict(THROW_POOL_SIZE)
        self._send(200, self._text_result(fid), "application/json")

    def do_PATCH(self):
        """Append text to existing content."""
        if not self._rate(): return
        p = self.path.split("?", 1)[0].lstrip("/")
        parts = p.split("/")
        # PATCH /d/<key>/<file> -> append to a file inside a dir
        if parts and parts[0] == DIR_NS and len(parts) >= 3:
            return dirs.edit(self._kit(), parts[1], parts[1:], append=True)
        fid = parts[0]
        if not fid or fid.endswith(".meta"):
            return self._err(404, "not found")
        fp = _id_path(fid)
        if os.path.isdir(fp) and fid not in (DIR_NS, pics.NS):
            return self._err(403, "bundles are immutable snapshots (editable: false)")
        if not os.path.isfile(fp):
            return self._err(404, "not found")
        if not self._is_text(fid):
            return self._err(400, "only text files can be appended to")
        denied = retain.write_denied(self, self._meta_of(fid))
        if denied:
            return self._err(denied[0], denied[1]["error"])
        data = self._read_body()
        if data is None:
            return self._err(411, "length required")
        cur = os.path.getsize(fp)
        if cur + len(data) > MAX_FILE:
            return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")
        with open(fp, "ab") as f:
            f.write(data)
        if retain.valid(retain.token_from(self)):
            self._retain_meta(fid)     # token write implies retention
        evict(THROW_POOL_SIZE)
        self._send(200, self._text_result(fid), "application/json")

    def _home_help(self):
        """GET / (agent curl). A structured --help style summary: a compact
        usage overview plus pointers telling the agent where to get the full
        help and the machine-readable API index. Plain text, no markdown."""
        topics = ", ".join(HELP_ORDER)
        body = f"""throway — disposable file store

A no-auth, ephemeral file store for agents and programs. Upload a file, a
bundle of files (a mini website), or a dir; get a short-lived
URL. Everything auto-expires after {TTL_HOURS} hours (unless uploaded
with a retain token — /help/retention).

USAGE
  POST {PUBLIC_BASE}/?name=file.txt   upload a file (body = file bytes)
  POST {PUBLIC_BASE}/?name=x&ttl=24h  upload with longer lifetime (max 14d)
  POST {PUBLIC_BASE}/?share=my-note   upload under a chosen name -> /d/my-note
  POST {PUBLIC_BASE}/?once=1          burn-after-reading (auto-delete after 1 download)
  POST {PUBLIC_BASE}/?name=doc.md     upload markdown -> renders as an HTML
                                      page for browsers (guide: /help/markdown)
  POST {PUBLIC_BASE}/                 upload a bundle (multipart, 2+ files)
  POST {PUBLIC_BASE}/?dir=1           create a dir (under /d/<key>)
  GET  {PUBLIC_BASE}/d/<key>          view a dir (listing / files / zip)
  GET  {PUBLIC_BASE}/d/<key>/history  edit history of a dir
  GET  {PUBLIC_BASE}/<id>             download / view
  PUT/PATCH {PUBLIC_BASE}/<id>        edit / append text
  DELETE {PUBLIC_BASE}/<id>           delete
  POST {PUBLIC_BASE}/<id>?retain=1    flip to indefinite (retain token)

WHERE TO GET MORE
  Full usage guide : GET {PUBLIC_BASE}/write_for_agents
  API index (JSON) : GET {PUBLIC_BASE}/api
  Help by topic    : GET {PUBLIC_BASE}/help  (then /help/<topic>)
  Topics available : {topics}
  Release notes    : GET {PUBLIC_BASE}/releases
"""
        self._send(200, body, "text/plain; charset=utf-8")

    def _agent_description(self):
        """Full plain-text description, assembled from the same topics served
        individually at /help/<topic>. Single source of truth: the HELP dict."""
        parts = [
            "THROWAWAY STORE — FOR AGENTS\n",
            "You are talking to a disposable file store. It lets you upload a\n"
            "file and share a short-lived URL. Everything is open (no auth) and\n"
            f"everything expires after {TTL_HOURS} hours — unless uploaded\n"
            "with a retain token (see the retention topic below).\n",
        ]
        for key in HELP_ORDER:
            parts.append(_render_help_body(key))
        return "\n".join(parts)

    def _help(self):
        """GET /help — modular, API-gatherable help. Agents get a JSON index of
        topics; browsers get an HTML list. Each topic is fetched separately at
        /help/<topic>, so an agent pulls only the pieces it needs instead of
        one giant copy-paste blob."""
        if self._is_agent():
            topics = [{"id": k, "title": HELP[k]["title"], "summary": HELP[k]["summary"]}
                      for k in HELP_ORDER]
            return self._send(200, json.dumps({
                "service": "throwaway-store",
                "version": VERSION,
                "help": topics,
                "fetch": PUBLIC_BASE + "/help/<topic>",
            }, indent=2), "application/json")
        rows = "".join(
            f'<li><a href="{PREFIX}/help/{_html_escape(k)}">{_html_escape(HELP[k]["title"])}</a>'
            f'<span class=m>{_html_escape(HELP[k]["summary"])}</span></li>'
            for k in HELP_ORDER)
        ahint = _agent_hint(
            f"curl -A curl {PUBLIC_BASE}/help                   # this index as JSON",
            f"curl -A curl {PUBLIC_BASE}/help/<topic>           # one topic as plain text",
        )
        h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"{_META_MOBILE}"
             f"<title>throway — help</title>"
             f"<style>{_BASE_CSS}"
             "li{flex-wrap:wrap}"
             "li .m{color:var(--muted);font-size:.8rem;width:100%}"
             "</style></head><body><main>"
             f"<h1>throway help</h1><ul>{rows}</ul>"
             f"{ahint}"
             f"<a class=back href='{PREFIX}/'>← throway</a>"
             "</main></body></html>")
        self._send(200, h, "text/html")

    def _help_topic(self, key):
        """GET /help/<topic> — one help topic. Agents get plain text; browsers
        get a simple HTML page. 404 for unknown topics."""
        t = HELP.get(key)
        if not t:
            return self._err(404, "unknown help topic")
        body = _render_help_body(key)
        if self._is_agent():
            return self._send(200, body, "text/plain; charset=utf-8")
        esc = _html_escape(body)
        ahint = _agent_hint(
            f"curl -A curl {PUBLIC_BASE}/help/{key}             # this topic as plain text",
        )
        h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"{_META_MOBILE}"
             f"<title>throway help — {_html_escape(t['title'])}</title>"
             f"<style>{_BASE_CSS}"
             "body{font-family:ui-monospace,monospace;line-height:1.6}"
             "main{max-width:820px}"
             "pre{white-space:pre-wrap;overflow-x:auto;font-family:ui-monospace,monospace;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}"
             "a.back{margin-top:0;margin-bottom:1rem}"
             ".agenthint{margin-top:.8rem}"
             "</style></head><body><main>"
             f"<a class=back href='{PREFIX}/help'>← all help</a>"
             f"<pre>{esc}</pre>"
             f"{ahint}"
             "</main></body></html>")
        self._send(200, h, "text/html; charset=utf-8")

    def _write_for_agents(self):
        """A description of this service written for agents."""
        self._send(200, self._agent_description(), "text/plain")

    def _releases(self):
        """Serve the release notes. Single source: RELEASES.md.
        Agents get the raw markdown; browsers get a rendered HTML page.
        Both come from the same file — nothing duplicated."""
        try:
            with open(RELEASES_FILE) as f:
                md = f.read()
        except OSError:
            return self._send(404, "release notes unavailable\n")
        # Single source of truth for the version: VERSION. Rewrite the
        # "Current version" line in RELEASES.md so it can never drift.
        md = re.sub(r'(?m)^\*\*Current version:\*\*.*$', f'**Current version:** `{VERSION}`', md, count=1)
        if self._is_agent():
            return self._send(200, md, "text/markdown; charset=utf-8")
        # browsers: render as an HTML page (escape + minimal md-ish styling)
        esc = _html_escape(md)
        h = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
{_META_MOBILE}
<title>throway — releases v{VERSION}</title>
<style>
  :root{{--bg:#fff;--card:#fafafa;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.6;min-height:100vh}}
  main{{max-width:820px;margin:0 auto;padding:3rem 1.5rem 5rem}}
  h1{{font-size:1.6rem;border-bottom:2px solid var(--accent);padding-bottom:.3rem}}
  h2{{font-size:1.2rem;margin-top:1.8rem;color:var(--accent)}}
  pre{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem;overflow-x:auto;font-family:ui-monospace,monospace;font-size:13px}}
  code{{background:var(--card);padding:.1rem .3rem;border-radius:4px;font-size:.9em;color:var(--accent)}}
  a{{color:var(--accent)}}
  .back{{display:inline-block;margin-bottom:1rem;color:var(--muted);text-decoration:none;font-size:.85rem}}
  .back:hover{{color:var(--accent)}}
  .raw{{color:var(--muted);font-size:.85rem}}
</style></head><body><main>
<a class=back href="{PREFIX}/">← throway</a>
<pre>{esc}</pre>
<p class=raw>raw: <a href="{PREFIX}/releases?raw=1">markdown</a></p>
</main></body></html>"""
        self._send(200, h, "text/html; charset=utf-8")

    def _copy_for_agents(self):
        """HTML page with a copy-pasteable agent description."""
        desc = self._agent_description()
        import html as _html
        esc = _html.escape(desc)
        h = f"""<!doctype html><html><head><meta charset=utf-8>
{_META_MOBILE}
<title>Agent description — copy me</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ font-size: 1.3rem; }}
  textarea {{ width: 100%; height: 60vh; font-family: ui-monospace, monospace; font-size: 13px; padding: 12px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 6px; }}
  button {{ margin-top: .6rem; padding: .5rem 1rem; font-size: 14px; border: 0; border-radius: 6px; background: #2563eb; color: #fff; cursor: pointer; }}
  button:active {{ opacity: .8; }}
</style></head>
<body>
<h1>Agent description — copy this</h1>
<p>Select all or click copy, then paste it into your agent's context.</p>
<textarea id="desc" readonly>{esc}</textarea>
<br><button onclick="copyDesc()">Copy</button> <span id="done"></span>
<script>
function copyDesc() {{
  var ta = document.getElementById('desc');
  ta.select(); ta.setSelectionRange(0, 999999);
  navigator.clipboard.writeText(ta.value).then(function(){{
    document.getElementById('done').textContent = '✓ copied';
  }});
}}
</script>
</body></html>"""
        self._send(200, h, "text/html")

    def _api(self):
        """Machine-readable contract for agents."""
        lim = contract.limits()
        spec = {
            "service": "throwaway-store",
            "version": VERSION,
            "base_url": PUBLIC_BASE,
            "ttl_seconds": TTL_HOURS * 3600,
            "dir_ttl_seconds": {"min": DIR_MIN_AGE, "default": DIR_DEFAULT_AGE, "max": DIR_MAX_AGE},
            "max_file_bytes": MAX_FILE,
            "pool_bytes": THROW_POOL_SIZE,
            "rate_limit_per_min": RATE_LIMIT,
            "retention_token": retain.ENABLED,
            "errors": contract.errors_payload(lim),
            "endpoints": {
                "upload": {
                    "method": "POST",
                    "url": PUBLIC_BASE + "/?name=<filename>[&ttl=<h|d>][&share=<name>][&once=1]",
                    "body": "raw file bytes (or multipart/form-data with a file part)",
                    "note": "default lifetime is 4h; optional &ttl=<h|d> extends a single file, clamped to [4h, {TTL_MAX_D}d] (max {TTL_MAX_D} days); optional &share=<name> stores it under a chosen memorable name (create-or-get, 5-32 chars [a-z0-9-], >=1 letter, not reserved) at /d/<name> with sliding lifetime (default 7d, ttl= clamped [4h,14d]); optional &once=1 = burn-after-reading (single files only, not with &share=): the file auto-deletes after the first download; with a retain token (Authorization: Bearer <token> or ?token=, see /help/retention) the upload NEVER expires and is exempt from pool eviction", 
                    "response": {"id": "str", "url": "str", "size": "int", "name": "str", "content_type": "str", "editable": "bool", "tags?": ["str"], "persistence": {"type": "single|dir|bundle", "expires_at": "str|null", "extendable_by": "none|activity", "max_age": "int|null", "retention?": "indefinite"}, "expires_in": "int|null", "expires_at": "str|null"},
                },
                "upload_bundle": {
                    "method": "POST",
                    "url": PUBLIC_BASE + "/",
                    "body": "multipart/form-data with 2+ file parts",
                    "note": "creates a bundle: one URL, files served at /<id>/<filename>, index.html inline for browsers; bundles are immutable snapshots (editable:false)",
                    "response": {"id": "str", "url": "str", "bundle": True, "editable": False, "persistence": {"type": "bundle", "expires_at": "str", "extendable_by": "none", "max_age": None}, "files": [{"name": "str", "url": "str", "size": "int", "content_type": "str"}], "expires_at": "str"},
                },
                "download": {"method": "GET", "url": PUBLIC_BASE + "/<id>", "note": "images and text-like types render inline; bundle root serves index.html inline (browser) or zip (agent); append ?download=1 to force download; append ?thumb=1 for a small cached WebP preview (raster images only)"},
                "import_url": {"method": "POST", "url": PUBLIC_BASE + "/?url=<url>[&name=<name>][&link=1][&tag=<t>]", "note": "MAX {MAX_FILE_MB}MB. Two modes: (1) default — fetch the remote http(s) document SERVER-SIDE and store it as a normal file (name from Content-Disposition/URL path, overridable via &name=); (2) &link=1 — store the URL itself as a tiny redirect HTML document (browsers get redirected, agents can PUT/PATCH it). Private/loopback hosts are blocked. Optional &tag=<t> (repeatable, up to 5) attaches tags.", "response": "same JSON as upload"},
                "browse_files": {"method": "GET", "url": PUBLIC_BASE + "/browse?tag=<t>[&q=<substr>][&sort=created|name|size|expires][&order=asc|desc]", "note": "JSON listing of live single files for agents (HTML page for browsers). Filter by one or more &tag= values (AND), by name/tag substring &q=; sort with &sort= (default created) and &order= (default desc). Each entry: id, url, name, content_type, size, tags, created_at, expires_at."},
                "tag_file": {"method": "POST", "url": PUBLIC_BASE + "/<id>?tag=<t>[&tag=<t2>][&untag=<t3>]", "note": "update tags on an existing single file without touching its content or expiry. Tags: lowercase [a-z0-9-], 1-24 chars, max 5 per file."},
                "download_bundle_file": {"method": "GET", "url": PUBLIC_BASE + "/<id>/<filename>", "note": "serve a single file from a bundle; append &thumb=1 for a small cached WebP preview (raster images only; falls back to the original bytes)"},
                "create_dir": {"method": "POST", "url": PUBLIC_BASE + "/?dir=1[&name=<name>][&listed=1][&tag=<tag>][&ttl=<h|d>][&write=1|<token>]", "note": "create a dir: unnamed (opaque hex id) or named (create-or-get, 5-32 chars [a-z0-9-], >=1 letter, not reserved); listed=1 to appear in GET /d; tags up to 5; ttl = sliding lifetime, MAX {TTL_MAX_D} days (clamped [4h,{TTL_MAX_D}d]), default {DIR_DEFAULT_D}d; each add/edit/delete slides expires_at forward (capped 30d). Flags honored only on first creation. Optional &write=1 protects writes: the response contains write_token (shown once); writes then need it as the X-Throway-Write header or ?write=<token> (401 otherwise); reads/history/zip stay open; &show=1 (retain token) creates a SHOW-DIR: indefinite AND publicly writable — everyone with the URL may add/edit/delete files, whole-dir delete stays token-gated; flags (incl. show=1) honored only on first creation — flip an existing dir via POST /d/<key>?show=1. Multipart file parts on the create call become the dir's initial files; POSTing parts to an EXISTING named dir ADDS them like POST /d/<key> (retry-safe, write gates apply) — P5 bridge. ?json=1 / ?html=1 force the listing representation (P6)", "response": {"id": "str", "url": "str", "dir": True, "editable": False, "persistence": {"type": "dir", "expires_at": "str", "extendable_by": "activity", "max_age": "int"}, "files": [{"name": "str", "url": "str", "size": "int", "content_type": "str", "editable": "bool"}], "expires_at": "str", "max_age": "int", "name": "str?", "listed": "bool?", "open?": "bool (show-dir)", "tags": ["str"]}},
                "add_to_dir": {"method": "POST", "url": PUBLIC_BASE + "/d/<key>", "body": "multipart/form-data file parts", "note": "add files to a dir; slides expires_at forward by ttl; 401 without the X-Throway-Write token when the dir is write-protected"},
                "get_dir": {"method": "GET", "url": PUBLIC_BASE + "/d/<key>", "note": "JSON listing for agents, HTML page for browsers"},
                "get_dir_file": {"method": "GET", "url": PUBLIC_BASE + "/d/<key>/<file>", "note": "fetch one file from a dir; append &thumb=1 for a small cached WebP preview (raster images only; falls back to the original bytes)"},
                "dir_zip": {"method": "GET", "url": PUBLIC_BASE + "/d/<key>?zip=1", "note": "download the whole dir as a zip"},
                "get_dir_history": {"method": "GET", "url": PUBLIC_BASE + "/d/<key>/history", "note": "edit history: list of {ts,file,action,bytes} entries, newest first, capped at HISTORY_LIMIT"},
                "edit_dir_file": {"method": "PUT", "url": PUBLIC_BASE + "/d/<key>/<file>", "body": "new text (text files only)", "note": "replace a file in a dir, bumps updated_at"},
                "append_dir_file": {"method": "PATCH", "url": PUBLIC_BASE + "/d/<key>/<file>", "body": "text to append (text files only)", "note": "append to a file in a dir, bumps updated_at"},
                "delete_dir_file": {"method": "DELETE", "url": PUBLIC_BASE + "/d/<key>/<file>", "note": "remove one file from a dir, bumps updated_at"},
                "delete_dir": {"method": "DELETE", "url": PUBLIC_BASE + "/d/<key>", "note": "delete a whole dir"},
                "list_dirs": {"method": "GET", "url": PUBLIC_BASE + "/d", "note": "list dirs created with listed=1; filters ?q=<sub> (name or tag), ?created_after/before=<ts>, ?updated_after/before=<ts>; sort ?sort=created|updated|name&order=asc|desc (default created desc)"},
                "delete": {"method": "DELETE", "url": PUBLIC_BASE + "/<id>"},
                "show_dir": {"method": "POST", "url": PUBLIC_BASE + "/?dir=1&show=1[&name=<slug>]", "note": "create a SHOW-DIR (retain token): indefinite lifetime AND public write — everyone with the URL can add/edit/delete files (no token), whole-dir delete needs the token, history records everything. Flip an existing dir with POST /d/<key>?show=1 (token, idempotent). See /help/retention.", "response": {"id": "str", "url": "str", "dir": True, "open": True, "persistence": {"type": "dir", "expires_at": None, "extendable_by": "none", "max_age": None, "retention": "indefinite"}}},
                "retain": {"method": "POST", "url": PUBLIC_BASE + "/<id>?retain=1", "note": "flip an EXISTING file, bundle (/<id>) or dir (/d/<key>?retain=1) to indefinite retention: never expires, exempt from pool eviction, public read, but writes/deletes need the retain token. Requires the token (Authorization: Bearer <token> or ?token=). Idempotent. Token-authenticated uploads and PUT/PATCH writes retain implicitly. See /help/retention.", "response": {"id": "str", "url": "str", "retention": "indefinite", "expires_at": None, "persistence": {"type": "single|dir|bundle", "expires_at": None, "extendable_by": "none", "max_age": None, "retention": "indefinite"}}},
                "edit_text": {"method": "PUT", "url": PUBLIC_BASE + "/<id>", "body": "new text content (text files only)", "note": "replaces the whole text content; bundles are immutable (403 forbidden)"},
                "append_text": {"method": "PATCH", "url": PUBLIC_BASE + "/<id>", "body": "text to append (text files only)", "note": "bundles are immutable (403 forbidden)"},
                "contract": {"method": "GET", "url": PUBLIC_BASE + "/api"},
                "write_for_agents": {"method": "GET", "url": PUBLIC_BASE + "/write_for_agents", "note": "human-readable description of this service for agents"},
                "copy_for_agents": {"method": "GET", "url": PUBLIC_BASE + "/copy_for_agents", "note": "HTML page with a copy-pasteable agent description"},
                "help": {"method": "GET", "url": PUBLIC_BASE + "/help", "note": "modular help index (JSON for agents, HTML for browsers); each topic fetched separately at /help/<topic> so agents gather only what they need"},
                "releases": {"method": "GET", "url": PUBLIC_BASE + "/releases", "note": "release notes; raw markdown for agents, rendered HTML for browsers"},
            },
        }
        spec["endpoints"].update(pics.api_endpoints(PUBLIC_BASE))
        for _ep in spec["endpoints"].values():
            if isinstance(_ep.get("note"), str):
                _ep["note"] = contract.substitute(_ep["note"], lim)
        self._send(200, json.dumps(spec, indent=2), "application/json")

    def _index(self):
        """GET / (browser) — homepage: gather engine state, render via the
        pure page module throway.index (1.51.0), send. Agents got the
        plain-text help instead (see do_GET UA split)."""
        sweep()
        files_now = 0
        bytes_now = 0
        for f in os.listdir(ROOT):
            if f.endswith(".meta") or not os.path.isfile(os.path.join(ROOT, f)):
                continue
            files_now += 1
            bytes_now += os.path.getsize(os.path.join(ROOT, f))
        stats = {
            "files_now": files_now,
            "bytes_now": bytes_now,
            "pool_size": THROW_POOL_SIZE,
            "since_files": _since_start["files"],
            "since_bytes": _since_start["bytes"],
            "version": VERSION,
            "ttl_hours": TTL_HOURS,
            "prefix": PREFIX,
            "max_mb": MAX_FILE // (1024 * 1024),
            "agent_description": self._agent_description(),
        }
        self._send(200, index.page(stats), "text/html")

def _fmt_size(n):
    """Format bytes with a sensible unit: B, kB, MB, GB."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} kB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"

class StoreServer(ThreadingHTTPServer):
    # 1.40.2: Default-Backlog ist 5 — H2-Bursts des Browsers öffnen über
    # nginx Dutzende gleichzeitige Upstream-Verbindungen; Überläufe kosteten
    # ~1 s SYN-Retransmit pro betroffenem Request (sichtbar als ~1,2-s-Stalls
    # im Galerie-Burst). 128 schluckt Bursts, ohne den Accept-Loop zu ändern.
    request_queue_size = 128


if __name__ == "__main__":
    sweep()
    try:                                        # 1.40.0: fehlende Thumbs nachreichen
        pics.set_thumb_maker(_make_thumb)
        pics.warm_existing(ROOT)
    except Exception:
        pass
    print(f"store on :{PORT} root={ROOT} ttl={TTL_HOURS}h")
    StoreServer(("0.0.0.0", PORT), Handler).serve_forever()
