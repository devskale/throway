"""dirs — the unified dir storage for throway (see /help/dirs).

One concept under /d/<key>: a workspace directory with a <key>.meta
manifest + a <key>.history log, addressable by opaque hex id or by name
(create-or-get). Sliding lifetime, optional write token, edit history,
whole-dir zip. Extracted from store.py in 1.49.0; the dir domain lives
here, the HTTP routing stays in store.py.

ROOT is read from THROWAWAY_ROOT — the same env var store.py reads, in
the same process, so both always agree.

Interface (the whole surface store.py needs to know):

    get(kit, key, parts, query)     dispatch GET  /d/<key>[/<file>|/history]
    listing_browse(kit)             dispatch GET  /d (listed dirs only)
    create(kit, key, qp, initial_files, retained)
                                    POST /?dir=1 (create-or-get; multipart
                                    parts on an existing named dir ADD)
    post_add(kit, key)              POST /d/<key> — add multipart files.
                                    None = dir missing (dispatcher 404s);
                                    True = a guard already answered.
    share_store(kit, data, name_hint, ctype, share, ttl_seconds, retained)
                                    POST /?share=<name> — single file under
                                    a chosen name at /d/<name> (dir machinery)
    edit(kit, key, parts, append)   PUT/PATCH /d/<key>/<file>
    delete(kit, key, parts)         DELETE /d/<key>[/<file>]
    show_flip(kit, key)             POST /d/<key>?show=1 (token)
    retain_flip(kit, key)           POST /d/<key>?retain=1 (token)
    response(kit, key, dirpath, meta, write_token=None)
                                    JSON dir response (also the legacy
                                    /<dir-id> GET path in store.py)
    listing(kit, key, dirpath, meta)
                                    HTML dir listing (legacy path too)
    sweep(now)                      delete expired dirs (engine sweep hook)
    ns_total()                      bytes across all dirs (pool accounting)
    evict_units()                   yield (path, True, mtime) per
                                    non-retained dir (engine eviction hook)
    parse_ttl(s)                    &ttl= parsing, clamped [MIN_AGE, MAX_AGE]
    NS / MIN_AGE / MAX_AGE / DEFAULT_AGE / ABS_MAX / HISTORY_LIMIT
                                    config (env-tuned here; store.py aliases)
    HELP_TOPICS                     entries for /help

The kit is the seam (architecture review 2026-10-04): this module never
sees the Handler, only the request kit store.py builds per request.
Everything below the interface line is implementation; tests drive it
through HTTP.
"""
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil

from throway import storage as _storage
import time
from urllib.parse import quote, unquote

from throway import og, retain


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


NS = "d"
ROOT = os.environ.get("THROWAWAY_ROOT", "/srv/storage2/throway")

MIN_AGE = _env_int("THROWAWAY_DIR_MIN_AGE", 4 * 3600)               # min sliding lifetime (4h)
MAX_AGE = _env_int("THROWAWAY_DIR_MAX_AGE", 14 * 24 * 3600)         # max sliding lifetime (14 days)
DEFAULT_AGE = _env_int("THROWAWAY_DIR_DEFAULT_AGE", 7 * 24 * 3600)  # default when &ttl= not given
ABS_MAX = _env_int("THROWAWAY_DIR_ABS_MAX", 30 * 24 * 3600)         # absolute lifetime ceiling (30d)
HISTORY_LIMIT = _env_int("THROWAWAY_HISTORY_LIMIT", 50)             # history entries kept per dir

RESERVED_NAMES = {
    "api", "index", "d", "releases", "llms", "llms-full", "llms_full",
    "write_for_agents", "copy_for_agents", "store", "static", "favicon",
    "robots", "sitemap", "assets", "health", "browse", "list", "pics",
}


# --- mutation lock -----------------------------------------------------------

_LOCK = _storage.lock_for("dirs")   # 1.52.0: Mechanik im storage-Modul


def _locked(fn):
    """Serialize dir mutations (RLock, reentrant). Retro 2026-10-02
    hard-validate: read-modify-write races on meta/history lost files
    under parallel writes (show-dirs are parallel writers by design)."""
    def wrapper(*a, **kw):
        with _LOCK:
            return fn(*a, **kw)
    return wrapper


# --- storage helpers ---------------------------------------------------------

def _path(key):
    """On-disk path for a dir, keyed by id or name (always ROOT/d/<key>;
    ids are hex, names are [a-z0-9-] — both share the /d namespace, so
    they never collide with file/bundle ids at ROOT/<id>)."""
    return os.path.join(ROOT, NS, os.path.basename(key))


def _meta_path(key):
    return os.path.join(_path(key), key + ".meta")


def _meta(key):
    mp = _meta_path(key)
    if os.path.isfile(mp):
        try:
            with open(mp, "r", encoding="utf-8") as _f:
                return json.load(_f)
        except Exception:
            pass
    return None


def _history_path(key):
    return os.path.join(_path(key), key + ".history")


def _history(key):
    """Read a dir's history log (list of entries, newest last)."""
    hp = _history_path(key)
    if os.path.isfile(hp):
        try:
            with open(hp, "r", encoding="utf-8") as _f:
                return json.load(_f)
        except Exception:
            pass
    return []


@_locked
def _append_history(kit, key, entry):
    """Append a history entry, trimmed to HISTORY_LIMIT newest."""
    h = _history(key)
    h.append(entry)
    if len(h) > HISTORY_LIMIT:
        h = h[-HISTORY_LIMIT:]
    kit.atomic_json(_history_path(key), h)


def _touch(meta, now):
    """Slide a dir's expiry forward by its TTL on activity, capped at an
    absolute ceiling from creation. Returns the new expires timestamp
    (None for retained dirs — they never expire)."""
    if meta.get("retain"):
        meta["updated"] = now
        return None
    ttl = meta.get("max_age", DEFAULT_AGE)
    created = meta.get("created", now)
    # sliding: now + ttl, but never beyond created + ABS_MAX
    expires = min(now + ttl, created + ABS_MAX)
    meta["expires"] = expires
    meta["updated"] = now
    return expires


def _size(p):
    """Total bytes of all files inside a dir (excl. .meta/.history)."""
    total = 0
    try:
        for f in os.listdir(p):
            fp = os.path.join(p, f)
            if os.path.isfile(fp) and not f.endswith(".meta"):
                total += os.path.getsize(fp)
    except OSError:
        pass
    return total


def _retain(kit, key):
    """Flip a dir's manifest to indefinite retention. True on success."""
    m = _meta(key)
    if not m:
        return False
    if not m.get("retain"):
        kit.mark_retained(m)
        kit.atomic_json(_meta_path(key), m)
    return True


# --- name / ttl rules --------------------------------------------------------

def valid_name(name):
    """Validate a named-dir name per the ruling. Returns (ok, reason).
    Rules: len >4 and <=32; charset [a-z0-9-]; >=1 letter; not reserved."""
    if not name:
        return False, "name required"
    if not (5 <= len(name) <= 32):
        return False, "name must be 5-32 chars"
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return False, "name must be lowercase letters, digits, hyphens"
    if not re.search(r"[a-z]", name):
        return False, "name must contain a letter"
    if name in RESERVED_NAMES:
        return False, "reserved name"
    return True, None


def is_hex_id(s):
    """True if the key looks like an opaque hex id (16 lowercase hex chars).
    A dir key is either a hex id (unnamed) or a valid name."""
    return bool(re.fullmatch(r"[0-9a-f]{16}", s))


def parse_ttl(s):
    """Parse a &ttl= value into seconds, clamped to [MIN_AGE, MAX_AGE].
    Accepts plain hours (number), 'h' suffix, or 'd' suffix. Returns None if unparseable."""
    if not s:
        return None
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+)\s*(h|d)?", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        secs = n * 24 * 3600
    else:
        secs = n * 3600  # bare number or 'h' = hours
    if secs <= 0:
        return None
    return max(MIN_AGE, min(secs, MAX_AGE))


# --- write gates -------------------------------------------------------------

def _write_denied(kit, key):
    """Issue throway-dir-write-token: None when writing is allowed, else
    an (http_code, json_error) tuple. Dirs without a write_token stay
    open as ever (backward compatible). Protected dirs require the
    token via the X-Throway-Write header or ?write=<token>, compared
    constant-time."""
    m = _meta(key)
    if not m:
        return None
    if m.get("write_token"):
        given = kit.write_token_given()
        if not given:
            return (401, {"code": "write_denied", "error": "write token required: send the X-Throway-Write "
                                   "header or ?write=<token>"})
        if not hmac.compare_digest(given.encode(), m["write_token"].encode()):
            return (401, {"code": "write_denied", "error": "invalid write token"})
    if m.get("open"):
        return None      # show-dir: everyone with the URL may write
    return kit.retain_write_denied(m)


def _write_guard(kit, key):
    """Send the 401 when _write_denied fires; True = request handled.
    A token-authenticated write also retains the dir (write implies
    retention)."""
    denied = _write_denied(kit, key)
    if denied:
        kit.send(denied[0], json.dumps(denied[1]), "application/json")
        return True
    if retain.valid(kit.retain_token()):
        _retain(kit, key)
    return False


# --- write path --------------------------------------------------------------

@_locked
def _write_files(kit, key, dirpath, meta, files, create=False):
    """Write new files into a dir, update meta + stats + history.
    Returns (files_map, added_bytes, added_count)."""
    clean = []
    total = 0
    for n, d, c in files:
        safe = kit.safe_name(n)
        if not safe:
            continue
        if len(d) > kit.max_file:
            return None
        total += len(d)
        if total > kit.pool_size:
            return None
        clean.append((safe, d, c))
    if not clean:
        return None
    files_map = meta.get("files", {})
    existing = set(os.listdir(dirpath))
    meta_name = key + ".meta"
    existing.discard(meta_name)
    existing.discard(key + ".history")
    added = 0
    added_bytes = 0
    written = []
    for n, d, c in clean:
        safe = kit.safe_name(n)
        if not safe:
            continue
        name = kit.dedupe_names([safe] + [x for x in existing if x != safe])[0]
        with open(os.path.join(dirpath, name), "wb") as f:
            f.write(d)
        files_map[name] = mimetypes.guess_type(name)[0] or c or "application/octet-stream"
        existing.add(name)
        written.append(name)
        added += 1
        added_bytes += len(d)
    meta["files"] = files_map
    _touch(meta, time.time())
    kit.atomic_json(_meta_path(key), meta)
    # History am ERFOLGsort (CR 1.48.2): NUR die neu geschriebenen
    # Namen (deduped) — files_map enthaelt auch die alten Dateien
    for name in written:
        _append_history(kit, key, {"ts": time.time(), "action": "add", "file": name})
    kit.bump_stats(added, added_bytes)
    return (files_map, added_bytes, added)


# --- dispatch: create / add / share ------------------------------------------

@_locked
def create(kit, key, qp, initial_files=None, retained=False):
    """Create (or get, if named & exists) a dir. key is a hex id (unnamed)
    or a name. initial_files is a list of (name, data, ctype) or None."""
    now = time.time()
    dirpath = _path(key)
    # named create-or-get
    if not is_hex_id(key):
        existing = _meta(key)
        if existing is not None:
            if kit.meta_expired(existing, now):
                shutil.rmtree(dirpath, ignore_errors=True)
                existing = None
            else:
                if initial_files:
                    # P5 (sota): create-or-get mit Multipart-Parts fuegt
                    # die Parts hinzu (gleiches Verhalten wie POST
                    # /d/<key>) statt sie still zu verwerfen — ein
                    # Agent-Retry-Loop konvergiert so ohne Datenverlust.
                    if _write_guard(kit, key):
                        return True   # 401 bereits gesendet
                    meta = _meta(key) or existing
                    dp = _path(key)
                    # pre-check: einzelne Datei zu gross -> praezise 413
                    # (CR 1.48.2: _write_files-None vermengte das
                    # mit "pool full" — post_add unterscheidet korrekt)
                    for n, d, _c in initial_files:
                        if len(d) > kit.max_file:
                            return kit.err(413, f"too large (max 5MB): {kit.safe_name(n)}")
                    files_map, _, _ = _write_files(kit, key, dp, meta, initial_files)
                    if files_map is None:
                        return kit.err(413, "dir too large (pool max 100MB)")
                    kit.evict_pool()
                    return response(kit, key, dp, meta)
                return response(kit, key, dirpath, existing)
    os.makedirs(dirpath, exist_ok=True)
    ttl = parse_ttl((qp.get("ttl") or [""])[0]) or DEFAULT_AGE
    qs = kit.query
    listed = "listed=1" in qs
    show = "show=1" in qs
    tags = kit.parse_tags(qp.get("tag", []))
    write_flag = (qp.get("write") or [""])[0].strip()
    write_token = None
    if write_flag:
        if write_flag == "1":
            write_token = secrets.token_hex(24)
        elif re.fullmatch(r"[A-Za-z0-9._-]{8,64}", write_flag):
            write_token = write_flag
        else:
            return kit.err(400, "invalid write token: use write=1 (server generates) "
                          "or 8-64 chars [A-Za-z0-9._-]")
    meta = {
        "type": "dir",
        "created": now,
        "updated": now,
        "listed": listed,
        "tags": tags,
        "files": {},
    }
    if retained and show:
        meta["retain"] = True          # show-dir: indefinite ...
        meta["open"] = True            # ... and publicly writable
    elif retained:
        meta["retain"] = True          # token-gated: never expires
    else:
        meta["expires"] = now + ttl
        meta["max_age"] = ttl
    if is_hex_id(key):
        meta["id"] = key
    else:
        meta["name"] = key
    if write_token:
        meta["write_token"] = write_token
    kit.atomic_json(_meta_path(key), meta)
    # write initial files (+ history: P5 verlangt 3 adds bei 3 Parts)
    if initial_files:
        _write_files(kit, key, dirpath, meta, initial_files, create=True)
    kit.evict_pool()
    return response(kit, key, dirpath, meta, write_token=write_token)


def post_add(kit, key):
    """POST /d/<key> — add multipart files to an existing dir.
    Returns None when the dir is missing/invalid (the dispatcher answers
    404), True when a guard already answered — Retro hard-validate:
    None must NOT fall through into the raw-upload path."""
    dirpath = _path(key)
    if not os.path.isdir(dirpath):
        return None
    # Body VOR dem Write-Guard lesen + Guard-Return True (nicht None):
    # sonst (a) sendet der Dispatcher nach dem Guard-401 noch ein 404
    # hinterher (Retro review 1.45.4: verifizierte Doppel-Response)
    # und (b) vergiftet der ungelesene Body den Keep-Alive-Socket —
    # die Bytes gelten als naechste Anfrage (phantom-400).
    ctype = kit.content_type
    if not ctype.startswith("multipart/form-data"):
        return kit.err(400, "dir add requires multipart")
    payload = kit.read_body()
    if payload is None:
        return kit.err(411, "length required")
    if _write_guard(kit, key):
        return True      # 401 bereits gesendet — NICHT None zurueckgeben
    m = _meta(key)
    if not m or m.get("type") != "dir":
        return None
    now = time.time()
    if kit.meta_expired(m, now):
        shutil.rmtree(dirpath, ignore_errors=True)
        return kit.err(404, "expired")
    files = kit.parse_multipart(payload, ctype)
    named = [(n, d, c) for (n, d, c) in files if n]
    if not named:
        return kit.err(400, "no file parts")
    # size checks
    cur = _size(dirpath)
    for n, d, c in named:
        safe = kit.safe_name(n)
        if not safe:
            continue
        if len(d) > kit.max_file:
            return kit.err(413, f"too large (max 5MB): {safe}")
        cur += len(d)
        if cur > kit.pool_size:
            return kit.err(413, "dir too large (pool max 100MB)")
    files_map, _, _ = _write_files(kit, key, dirpath, m, named)
    if files_map is None:
        return kit.err(413, "dir too large (pool max 100MB)")
    kit.evict_pool()
    return response(kit, key, dirpath, m)


@_locked
def share_store(kit, data, name_hint, ctype, share, ttl_seconds=None,
                retained=False):
    """POST /?share=<name> — store a single file under a chosen, memorable
    name (create-or-get, like a named dir) at /d/<name>. Reuses the dir
    machinery: sliding lifetime (default 7d, ttl= clamped [4h,14d])."""
    ok, reason = valid_name(share)
    if not ok:
        return kit.err(400, f"invalid share name: {reason}")
    if len(data) > kit.max_file:
        return kit.err(413, "too large (max 5MB)")
    key = share
    dirpath = _path(key)
    now = time.time()
    meta = _meta(key)
    if kit.meta_expired(meta, now):
        shutil.rmtree(dirpath, ignore_errors=True)
        meta = None
    if meta is not None:
        if _write_guard(kit, key):
            return
        # der Guard kann die Dir soeben retained gemacht haben
        # (write implies retention) — Meta neu laden, sonst schreibt
        # _write_files das stale Meta zurueck (Retro hard-validate)
        meta = _meta(key)
    if meta is None:
        os.makedirs(dirpath, exist_ok=True)
        ttl = ttl_seconds or DEFAULT_AGE
        meta = {
            "type": "dir",
            "created": now,
            "updated": now,
            "listed": False,
            "tags": [],
            "files": {},
            "name": key,
        }
        if retained:
            meta["retain"] = True          # token-gated: never expires
        else:
            meta["expires"] = now + ttl
            meta["max_age"] = ttl
        kit.atomic_json(_meta_path(key), meta)
    fname = name_hint or "file"
    r = _write_files(kit, key, dirpath, meta, [(fname, data, ctype)], create=True)
    if r is None:
        return kit.err(413, "store failed (too large?)")
    kit.evict_pool()
    return response(kit, key, dirpath, meta)


# --- dispatch: read paths ----------------------------------------------------

def get(kit, key, parts, query):
    """GET /d/<key>[/<file>] — listing, a file, zip, or history."""
    dirpath = _path(key)
    m = _meta(key)
    now = time.time()
    if not m or m.get("type") != "dir":
        return kit.err(404, "not found")
    if kit.meta_expired(m, now):
        shutil.rmtree(dirpath, ignore_errors=True)
        return kit.send(404, "expired\n")
    force_dl = "download=1" in query
    # /d/<key>/history
    if len(parts) >= 2 and parts[1] == "history" and len(parts) == 2:
        return _history_view(kit, key, dirpath, m)
    # /d/<key>/<file>
    if len(parts) >= 2 and parts[1]:
        fname = os.path.basename(unquote(parts[1]))
        if not fname or fname.endswith(".meta") or fname.endswith(".history") or fname.endswith(".thumb") or fname.endswith(".thumbtmp"):
            return kit.err(404, "not found")
        fpath = os.path.join(dirpath, fname)
        if not os.path.isfile(fpath):
            return kit.err(404, "not found")
        ctype = m.get("files", {}).get(fname) or mimetypes.guess_type(fname)[0] or "application/octet-stream"
        if "thumb=1" in query:
            return kit.serve_thumb(fpath, ctype)
        if kit.serve_markdown(fpath, ctype, fname, fname):
            return
        return kit.serve_file(fpath, ctype, fname, force_dl, f"{NS}/{key}/{fname}")
    # root: zip on ?zip=1 / ?download=1
    if "zip=1" in query or force_dl:
        return kit.serve_zip(dirpath, key)
    # JSON for agents, HTML for browsers
    if kit.wants_agent_repr():
        return response(kit, key, dirpath, m)
    # Issue throway-dir-index-landing: browsers get index.html inline
    # when present (parity with bundles) — ?listing=1 forces the listing.
    index_f = os.path.join(dirpath, "index.html")
    if os.path.isfile(index_f) and "listing=1" not in query:
        return _serve_index(kit, index_f, dirpath, key)
    return listing(kit, key, dirpath, m)


def _serve_index(kit, index_path, dirpath, key):
    """Serve a dir's index.html to a browser like a bundle root: inject a
    <base> tag so relative links resolve against /d/<key>/, plus a small
    footer link back to the file listing."""
    with open(index_path, "rb") as f:
        html = f.read()
    base = f'<base href="{kit.prefix}/{NS}/{key}/">'
    head = re.search(rb"<head[^>]*>", html, re.I)
    if head:
        html = html[:head.end()] + base.encode() + html[head.end():]
    else:
        html = b"<head>" + base.encode() + b"</head>" + html
    footer = ('<div style="margin:2rem 0 0;padding:.6rem .9rem;border-top:1px solid #e5e7eb;'
              'font:.8rem system-ui,sans-serif;color:#6b7280">'
              '<a href="?listing=1" style="color:#2563eb">files &amp; history</a>'
              ' · throway</div>')
    if b"</body>" in html:
        html = html.replace(b"</body>", footer.encode() + b"</body>", 1)
    else:
        html += footer.encode()
    kit.send(200, html, "text/html; charset=utf-8", {"Content-Disposition": "inline"})


def response(kit, key, dirpath, meta, write_token=None):
    """JSON response for a dir (agents). write_token appears only in
    the creation response — never re-revealed on create-or-get."""
    files = []
    total = 0
    for f in sorted(os.listdir(dirpath)):
        if f.endswith(".meta") or f.endswith(".history") or f.endswith(".thumb") or f.endswith(".thumbtmp"):
            continue
        fp = os.path.join(dirpath, f)
        if not os.path.isfile(fp):
            continue
        sz = os.path.getsize(fp)
        total += sz
        ctype = meta.get("files", {}).get(f, "application/octet-stream")
        files.append({"name": f, "url": f"{kit.public_base}/{NS}/{key}/{quote(f)}", "size": sz,
                      "content_type": ctype, "editable": kit.is_editable(ctype)})
    expires = meta.get("expires")    # None for retained dirs
    resp = {
        "id": key,
        "url": f"{kit.public_base}/{NS}/{key}",
        "dir": True,
        "editable": False,
        "persistence": kit.persistence_block("dir", expires,
                                             max_age=meta.get("max_age"),
                                             extendable_by="activity"),
        "files": files,
        "size": total,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.get("created", 0))),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.get("updated", meta.get("created", 0)))),
        "expires_at": kit.fmt_exp(expires),
        "max_age": meta.get("max_age"),
    }
    if meta.get("name"):
        resp["name"] = meta["name"]
    if meta.get("listed"):
        resp["listed"] = True
    if meta.get("open"):
        resp["open"] = True
    if meta.get("write_token"):
        resp["write_protected"] = True
    if write_token:
        resp["write_token"] = write_token
        resp["write_note"] = ("store this token now — writes need it as the "
                              "X-Throway-Write header or ?write=")
    if meta.get("tags"):
        resp["tags"] = meta["tags"]
    hdrs = {"X-Expires": str(expires)} if expires is not None else None
    return kit.send(200, json.dumps(resp), "application/json", hdrs)


def listing(kit, key, dirpath, meta):
    """HTML page for a dir viewed in a browser — mobile-friendly, with
    lazy image thumbnails (tiny WebP via ?thumb=1, loaded on scroll)."""
    files = meta.get("files", {})
    rows = []
    for f in sorted(os.listdir(dirpath)):
        if (f.endswith(".meta") or f.endswith(".history")
                or f.endswith(".thumb") or f.endswith(".thumbtmp")):
            continue
        fp = os.path.join(dirpath, f)
        if os.path.isfile(fp):
            ct = files.get(f) or mimetypes.guess_type(f)[0] or "application/octet-stream"
            rows.append((f, os.path.getsize(fp), ct))
    lis = "\n".join(
        "<li>"
        + (f'<img class=thumb src="{kit.html_escape(quote(f))}?thumb=1" alt="" loading=lazy decoding=async width=44 height=44>'
           if ct.startswith("image/") else "")
        + f'<a href="{kit.html_escape(quote(f))}">{kit.html_escape(f)}</a>'
        + f'<span class=sz>{kit.fmt_size(s)}</span></li>'
        for f, s, ct in rows)
    tags = "".join(f'<span class=tag>{kit.html_escape(t)}</span>' for t in meta.get("tags", []))
    title = meta.get("name") or key
    # agent hint: collapsed for humans, fully in source/a11y-tree for agents
    # that land on the HTML page with a browser UA. Absolute URLs so every
    # line is copy-paste runnable from anywhere.
    durl = f"{kit.public_base}/{NS}/{key}"
    hint = kit.agent_hint(
        f"curl -A curl {durl}                          # JSON listing: files[] with url, size, editable",
        f"curl {durl}/<file>                       # fetch a single file",
        f"curl -OJ '{durl}?zip=1'                      # whole dir as one zip",
        f"curl -X PUT --data-binary @local {durl}/<file>  # replace a text file (PATCH appends)",
        f"curl -A curl {durl}/history                  # edit history (JSON)",
    )
    h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
         f"{kit.meta_mobile}"
         + og.meta(title="Dir " + title,
                   description=(f"{len(rows)} file{'s' if len(rows) != 1 else ''} "
                                "in a throway dir — add, edit and delete over time."),
                   url=durl)
         + f"<base href='{kit.prefix}/{NS}/{key}/'>"
         f"<title>throway dir {title}</title>"
         f"<style>{kit.base_css}"
         ".tag{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:.1rem .6rem;font-size:.75rem;color:var(--muted);margin-right:.3rem}"
         ".btnrow{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1rem}"
         "@media(max-width:560px){.btnrow{flex-direction:column}.btnrow a.btn{text-align:center}}"
         "</style></head><body><main>"
         f"<h1>Dir {title}</h1><div>{tags}</div><ul>{lis}</ul>"
         f"{hint}"
         "<div class=btnrow>"
         f"<a class=btn href='?zip=1'>download as zip</a>"
         f"<a class=btn href='history'>history</a>"
         "</div>"
         f"<a class=back href='{kit.prefix}/'>← throway</a>"
         "</main></body></html>")
    kit.send(200, h, "text/html")


# --- dispatch: edit / delete -------------------------------------------------

@_locked
def edit(kit, key, parts, append):
    if _write_guard(kit, key):
        return
    """PUT/PATCH /d/<key>/<file> — replace or append text in a dir."""
    if len(parts) < 2 or not parts[1]:
        return kit.err(400, "file required")
    fname = os.path.basename(unquote(parts[1]))
    dirpath = _path(key)
    m = _meta(key)
    now = time.time()
    if not m or m.get("type") != "dir":
        return kit.err(404, "not found")
    if kit.meta_expired(m, now):
        shutil.rmtree(dirpath, ignore_errors=True)
        return kit.send(404, "expired\n")
    fpath = os.path.join(dirpath, fname)
    if fname.endswith(".meta") or fname.endswith(".history") or fname.endswith(".thumb") or fname.endswith(".thumbtmp") or not os.path.isfile(fpath):
        return kit.err(404, "not found")
    ctype = m.get("files", {}).get(fname) or ""
    if not kit.is_editable(ctype):
        return kit.err(400, "only text files can be edited")
    data = kit.read_body()
    if data is None:
        return kit.err(411, "length required")
    old_size = os.path.getsize(fpath)
    if append:
        if old_size + len(data) > kit.max_file:
            return kit.err(413, "too large (max 5MB)")
        with open(fpath, "ab") as f:
            f.write(data)
    else:
        if len(data) > kit.max_file:
            return kit.err(413, "too large (max 5MB)")
        with open(fpath, "wb") as f:
            f.write(data)
    _touch(m, now)
    kit.atomic_json(_meta_path(key), m)
    # history entry: action, file, delta
    entry = {"ts": now, "file": fname, "action": "append" if append else "put"}
    if append:
        entry["added_bytes"] = len(data)
    else:
        entry["old_bytes"] = old_size
        entry["new_bytes"] = len(data)
    _append_history(kit, key, entry)
    kit.evict_pool()
    return response(kit, key, dirpath, m)


@_locked
def delete(kit, key, parts):
    if _write_guard(kit, key):
        return
    """DELETE /d/<key> or /d/<key>/<file>. Whole-dir delete on a
    retained dir (incl. show-dirs) needs the retain token — file
    deletes stay open."""
    dm = _meta(key)
    if dm and dm.get("retain") and not retain.valid(kit.retain_token()) \
            and not (len(parts) >= 2 and parts[1]):
        return kit.send(401, json.dumps(
            {"code": "write_denied", "error": "whole-dir delete on a retained dir needs the retain "
                      "token (file-level deletes stay open)"}), "application/json")
    dirpath = _path(key)
    m = _meta(key)
    if not m or m.get("type") != "dir":
        return kit.err(404, "not found")
    now = time.time()
    if len(parts) >= 2 and parts[1]:
        fname = os.path.basename(unquote(parts[1]))
        fpath = os.path.join(dirpath, fname)
        if fname.endswith(".meta") or fname.endswith(".history") or fname.endswith(".thumb") or fname.endswith(".thumbtmp") or not os.path.isfile(fpath):
            return kit.err(404, "not found")
        os.remove(fpath)
        m["files"].pop(fname, None)
        _touch(m, now)
        kit.atomic_json(_meta_path(key), m)
        _append_history(kit, key, {"ts": now, "action": "delete", "file": fname})
        return kit.send(200, "deleted\n")
    shutil.rmtree(dirpath, ignore_errors=True)
    return kit.send(200, "deleted\n")


# --- flip routes (dispatched from store.py) ----------------------------------

@_locked
def show_flip(kit, key):
    """POST /d/<key>?show=1 (token) — flip a dir to a show-dir:
    retained (indefinite) AND publicly writable. Idempotent.
    (1.49.0: now under the mutation lock — the 1.45.2 race fix,
    nachgeruestet for this route.)"""
    m = _meta(key)
    if not m:
        return kit.err(404, "not found")
    if not (m.get("retain") and m.get("open")):
        kit.mark_retained(m)
        m["open"] = True
        kit.atomic_json(_meta_path(key), m)
    return response(kit, key, _path(key), _meta(key))


@_locked
def retain_flip(kit, key):
    """POST /d/<key>?retain=1 (token) — flip a dir to indefinite."""
    if not _retain(kit, key):
        return kit.err(404, "not found")
    return kit.send(200, json.dumps({
        "id": key, "url": f"{kit.public_base}/{NS}/{key}", "dir": True,
        "retention": "indefinite", "expires_at": None,
        "persistence": kit.persistence_block("dir", None)}), "application/json")


# --- history -----------------------------------------------------------------

def _history_view(kit, key, dirpath, meta):
    """GET /d/<key>/history — JSON for agents, HTML for browsers."""
    h = _history(key)
    # newest first
    h = list(reversed(h))
    if kit.wants_agent_repr():
        return kit.send(200, json.dumps({"dir": key, "history": h, "total": len(h)}), "application/json")
    rows = "".join(
        f'<li><span class=ts>{time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e.get("ts", 0)))}</span> '
        f'<span class=act>{kit.html_escape(e.get("action", ""))}</span> '
        f'<span class=file>{kit.html_escape(e.get("file", ""))}</span>'
        + _history_detail_html(e)
        + '</li>'
        for e in h)
    title = meta.get("name") or key
    hurl = f"{kit.public_base}/{NS}/{key}/history"
    ahint = kit.agent_hint(
        f"curl -A curl {hurl}        # this history as JSON",
    )
    htm = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"{kit.meta_mobile}"
           f"<title>throway dir history — {title}</title>"
           f"<style>{kit.base_css}"
           "li{flex-wrap:wrap}"
           "li .ts{color:var(--muted);font-size:.8rem;margin-right:.6rem}"
           "li .act{font-weight:600;color:var(--accent);margin-right:.6rem}"
           "li .file{font-family:ui-monospace,monospace;overflow-wrap:anywhere}"
           "li .det{color:var(--muted);font-size:.8rem;width:100%}"
           "</style></head><body><main>"
           f"<h1>History — {title}</h1>"
           f"{'<p style=color:var(--muted);font-size:.85rem>No edits yet.</p>' if not h else ''}"
           f"<ul>{rows}</ul>"
           f"{ahint}"
           f"<a class=back href='{kit.prefix}/{NS}/{key}'>← dir</a>"
           "</main></body></html>")
    kit.send(200, htm, "text/html")


def _history_detail_html(e):
    """Small detail fragment for a history entry in HTML."""
    a = e.get("action")
    if a in ("add",):
        return f'<div class=det>added {e.get("added_bytes", "?")} bytes</div>'
    if a == "append":
        return f'<div class=det>appended {e.get("added_bytes", "?")} bytes</div>'
    if a == "put":
        return f'<div class=det>{e.get("old_bytes", "?")} → {e.get("new_bytes", "?")} bytes</div>'
    if a == "delete":
        return '<div class=det>file removed</div>'
    return ""


# --- the /d listing (listed dirs only) ----------------------------------------

def listing_browse(kit):
    """GET /d — list dirs created with &listed=1 (agents JSON, browsers HTML)."""
    q = kit.query
    qp = {}
    for kv in q.split("&"):
        if not kv:
            continue
        k, _, v = kv.partition("=")
        qp.setdefault(k, []).append(v)
    def _ts(k):
        try:
            return float((qp.get(k) or [""])[0])
        except Exception:
            return None
    created_after = _ts("created_after"); created_before = _ts("created_before")
    updated_after = _ts("updated_after"); updated_before = _ts("updated_before")
    qtext = (qp.get("q") or [""])[0].strip().lower()
    sort = (qp.get("sort") or ["created"])[0]
    order = (qp.get("order") or ["desc"])[0]
    nd = os.path.join(ROOT, NS)
    now = time.time()
    entries = []
    if os.path.isdir(nd):
        for key in os.listdir(nd):
            p = os.path.join(nd, key)
            if not os.path.isdir(p):
                continue
            m = _meta(key)
            if not m or not m.get("listed"):
                continue
            if kit.meta_expired(m, now):
                continue
            created = m.get("created", 0); updated = m.get("updated", created)
            tags = m.get("tags", [])
            name = m.get("name") or key
            if created_after is not None and created <= created_after:
                continue
            if created_before is not None and created >= created_before:
                continue
            if updated_after is not None and updated <= updated_after:
                continue
            if updated_before is not None and updated >= updated_before:
                continue
            if qtext and qtext not in name and not any(qtext in t for t in tags):
                continue
            files = [f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))
                     and not f.endswith(".meta") and not f.endswith(".history")
                     and not f.endswith(".thumb") and not f.endswith(".thumbtmp")]
            size = _size(p)
            entries.append({
                "name": name,
                "url": f"{kit.public_base}/{NS}/{key}",
                "tags": tags,
                "files": len(files),
                "size": size,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created)),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(updated)),
                "expires_at": (None if m.get("retain")
                               else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m.get("expires", 0)))),
                "max_age": m.get("max_age"),
                "_c": created, "_u": updated,
            })
    def _key(e):
        if sort == "name":
            return e["name"]
        if sort == "updated":
            return e["_u"]
        return e["_c"]
    entries.sort(key=_key, reverse=(order != "asc"))
    for e in entries:
        e.pop("_c", None); e.pop("_u", None)
    if kit.wants_agent_repr():
        return kit.send(200, json.dumps({"dirs": entries, "total": len(entries)}), "application/json")
    cards = "".join(
        f'<li><a href="{kit.html_escape(e["url"])}">{kit.html_escape(e["name"])}</a> '
        f'<span class=meta>{e["files"]} files · {kit.fmt_size(e["size"])} · updated {e["updated_at"]}</span>'
        + (f'<span class=tags>{" ".join("#" + kit.html_escape(t) for t in e["tags"])}</span>' if e["tags"] else "")
        + '</li>'
        for e in entries)
    ahint = kit.agent_hint(
        f"curl -A curl {kit.public_base}/d                         # JSON: all listed dirs (?q= filter, ?sort=created|updated|name)",
        f"curl -A curl {kit.public_base}/d/<key>                # one dir as JSON listing",
        f"curl -X POST '{kit.public_base}/?dir=1&name=<name>&listed=1'  # create a dir",
        f"curl {kit.public_base}/api                            # full machine-readable API",
    )
    h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
         f"{kit.meta_mobile}"
         f"<title>throway — dirs</title>"
         f"<style>{kit.base_css}"
         "li{flex-wrap:wrap}"
         "</style></head><body><main>"
         f"<h1>Dirs</h1>{'<p style=color:var(--muted);font-size:.85rem>No listed dirs yet.</p>' if not entries else ''}"
         f"<ul>{cards}</ul>"
         f"{ahint}"
         f"<a class=back href='{kit.prefix}/'>← throway</a>"
         "</main></body></html>")
    kit.send(200, h, "text/html")


# --- engine hooks (pool accounting, sweep) ------------------------------------

def sweep(now):
    """Delete expired dirs (sliding lifetime from manifest, absolute cap)."""
    nd = os.path.join(ROOT, NS)
    if not os.path.isdir(nd):
        return
    for key in os.listdir(nd):
        p = os.path.join(nd, key)
        if not os.path.isdir(p):
            continue
        m = _meta(key)
        if m and m.get("retain"):
            continue    # retained dirs never expire
        expires = (m or {}).get("expires")
        if expires is None:
            expires = os.path.getmtime(p) + DEFAULT_AGE
        if expires < now:
            shutil.rmtree(p, ignore_errors=True)


def ns_total():
    """Total bytes across all dirs (ROOT/d/<key>) — pool accounting."""
    total = 0
    nd = os.path.join(ROOT, NS)
    if not os.path.isdir(nd):
        return 0
    for key in os.listdir(nd):
        p = os.path.join(nd, key)
        if os.path.isdir(p):
            total += _size(p)
    return total


def evict_units():
    """Yield (path, True, mtime) per non-retained dir — the engine's
    _units() delegates here so eviction can target dirs individually
    (retained dirs are never eviction candidates)."""
    nd = os.path.join(ROOT, NS)
    if not os.path.isdir(nd):
        return
    for key in os.listdir(nd):
        p = os.path.join(nd, key)
        if os.path.isdir(p):
            if (_meta(key) or {}).get("retain"):
                continue
            yield p, True, os.path.getmtime(p)


HELP_TOPICS = {
    "dirs": {
        "title": "Dirs",
        "summary": "One dir concept under /d/<key>: id or name, sliding lifetime, history",
        "body": """CREATE a DIR (one unified concept, addressable by id or name):
   POST {PUBLIC_BASE}/?dir=1            -> unnamed dir, opaque hex id
   POST {PUBLIC_BASE}/?dir=1&name=<name>[&listed=1][&tag=<tag>][&ttl=<h|d>]
        -> named dir (create-or-get); flags apply only on first creation
   Naming: 5-32 chars, [a-z0-9-], must contain a letter, not a reserved word.
   - &listed=1 -> appears in the public listing GET {PUBLIC_BASE}/d
   - &tag=<t>  -> up to 5 discoverability tags (lowercase [a-z0-9-])
   - &ttl=<h|d> -> SLIDING lifetime, clamped to [4h, 14d] (MAX 14 days);
     default 7 days.
WRITE PROTECTION (optional): create with &write=1 — the response
contains write_token exactly once. Afterwards writes (POST /d/<key>,
PUT/PATCH/DELETE on its files, DELETE the dir, POST /?share=<name>)
require the token via the X-Throway-Write header or ?write=<token>
(401 without it); reads, listing, history and zip stay open.

     Each add/edit/append/delete slides expires_at forward by ttl (capped at
     30 days total from creation). An active dir keeps living; an idle one
     dies ttl after its last activity.
   Reach a dir at {PUBLIC_BASE}/d/<key> (key = id or name):
   POST {PUBLIC_BASE}/d/<key>          -> add files (multipart)
   GET  {PUBLIC_BASE}/d/<key>          -> JSON (agents) / HTML (browsers)
   GET  {PUBLIC_BASE}/d/<key>/<file>   -> fetch one file (.md renders as
                                          HTML for browsers, raw for agents)
   GET  {PUBLIC_BASE}/d/<key>?zip=1    -> whole dir as zip
   PUT  {PUBLIC_BASE}/d/<key>/<file>   -> replace text (bumps updated)
   PATCH {PUBLIC_BASE}/d/<key>/<file>  -> append text (bumps updated)
   DELETE {PUBLIC_BASE}/d/<key>/<file> -> remove one file
   DELETE {PUBLIC_BASE}/d/<key>        -> delete the whole dir
        (open dirs: anyone with the URL can do this — protect with
         &write=1; retained dirs need the retain token)
   GET  {PUBLIC_BASE}/d/<key>/history  -> edit history (JSON for agents,
        HTML for browsers): last {HISTORY_LIMIT} entries, newest first, with
        date, file, action (add|put|append|delete) and byte deltas.
   updated_at = last add/edit/delete (slides expires_at forward).
   LIST dirs: GET {PUBLIC_BASE}/d  -> only dirs created with listed=1.
   Filters: ?q=<substring over name or tag>, ?created_after/before=<ts>,
   ?updated_after/before=<ts>. Sort: ?sort=created|updated|name&order=asc|desc.""",
    },
}
