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
import io
import json
import time
import shutil
import secrets
import zipfile
import mimetypes
import html as _html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _html_escape(s):
    return _html.escape(s)

ROOT = "/srv/storage2/throway"
THROW_POOL_SIZE = 100 * 1024 * 1024   # 100MB rolling pool
MAX_FILE = 5 * 1024 * 1024            # 5MB
RATE_LIMIT = 100                      # req/min per IP
TTL_HOURS = 4                         # default URL lifetime
DIR_MAX_AGE = 24 * 3600               # hard ceiling for a dir's total lifetime
PUBLIC_BASE = "https://lubu.skale.dev/throway"
PREFIX = "/throway"

# semantic version + single source of truth for release notes
VERSION = "1.4.0"
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

os.makedirs(ROOT, exist_ok=True)
_hits = {}
STATS_FILE = os.path.join(os.path.dirname(__file__), "stats.json")


def _load_stats():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"files": 0, "bytes": 0}


def _save_stats(s):
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(s, f)
    except Exception:
        pass


def _cumulative():
    """All-time totals (files ever uploaded, bytes ever uploaded)."""
    return _load_stats()

def _id_path(fid):
    # ids are secrets.token_hex, safe; guard anyway
    return os.path.join(ROOT, os.path.basename(fid))

def _meta_path(fid):
    return os.path.join(ROOT, os.path.basename(fid) + ".meta")

def allowed(ip):
    now = time.time()
    t = _hits.setdefault(ip, [])
    t[:] = [x for x in t if x > now - 60]
    if len(t) >= RATE_LIMIT:
        return False
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
            total += _dir_size(p)
    return total

def _units():
    """Yield (path, is_dir, mtime) for each top-level storage unit.
    Units are single files OR whole bundle directories — eviction/expiry
    treats each as one atomic thing."""
    for f in os.listdir(ROOT):
        if f.endswith(".meta"):
            continue
        p = os.path.join(ROOT, f)
        if os.path.isfile(p) or os.path.isdir(p):
            yield p, os.path.isdir(p), os.path.getmtime(p)

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
    mp = fp + ".meta"
    if os.path.isfile(mp):
        try:
            os.remove(mp)
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
            return json.load(open(mp))
        except Exception:
            pass
    return None

def sweep():
    """Delete expired files and bundles."""
    now = time.time()
    for f in os.listdir(ROOT):
        if f.endswith(".meta"):
            continue
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            mp = p + ".meta"
            expires = None
            if os.path.isfile(mp):
                try:
                    expires = json.load(open(mp)).get("expires")
                except Exception:
                    pass
            if expires is None:
                expires = os.path.getmtime(p) + TTL_HOURS * 3600
            if expires < now:
                _remove(p)
        elif os.path.isdir(p):
            m = _bundle_meta(p, f)
            expires = (m or {}).get("expires")
            if expires is None:
                expires = os.path.getmtime(p) + TTL_HOURS * 3600
            if expires < now:
                shutil.rmtree(p, ignore_errors=True)

def _safe_name(name):
    """Reduce a user filename to a safe basename for Content-Disposition."""
    if not name:
        return None
    name = os.path.basename(name.replace("\\", "/"))
    # strip control chars and quotes that could break the header
    name = re.sub(r'[\r\n\"\x00-\x1f]', "", name).strip()
    return name or None

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

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _send(self, code, body=b"", ctype="text/plain", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if isinstance(body, str): body = body.encode()
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        # HEAD: send headers + Content-Length but no body
        if self.command != "HEAD":
            self.wfile.write(body)

    def _rate(self):
        if not allowed(self._client_ip()):
            self._send(429, "rate limit exceeded\n"); return False
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


    def _serve_file(self, fp, ctype, orig, force_dl, fid):
        """Serve a single stored file (inline or attachment)."""
        size = os.path.getsize(fp)
        is_inline = any(ctype.startswith(p) for p in INLINE_TYPES)
        if force_dl or not is_inline:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            fname = _safe_name(orig) or fid
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            self.end_headers()
        else:
            ct = ctype
            if ctype.startswith("text/") and "charset" not in ctype:
                ct = ctype + "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "inline")
            self.end_headers()
        with open(fp, "rb") as f:
            if self.command != "HEAD":
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
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Content-Disposition", "inline")
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
                if f.endswith(".meta"):
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
        """Simple HTML file listing for a bundle with no index.html."""
        rows = []
        for f in sorted(os.listdir(dirpath)):
            if f.endswith(".meta"):
                continue
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                rows.append((f, os.path.getsize(fp)))
        lis = "\n".join(
            f'<li><a href="{_html_escape(f)}">{_html_escape(f)}</a> <span>{s} B</span></li>'
            for f, s in rows)
        h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"<title>throway bundle {fid}</title>"
             "<style>"
             ":root{--bg:#fff;--card:#fafafa;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}"
             "*{box-sizing:border-box}"
             "body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--ink);min-height:100vh}"
             "main{max-width:720px;margin:0 auto;padding:3rem 1.5rem}"
             "h1{font-size:1.4rem;color:var(--ink)}"
             "ul{list-style:none;padding:0}"
             "li{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.6rem .9rem;margin:.4rem 0;display:flex;justify-content:space-between;align-items:center}"
             "li a{color:var(--ink);text-decoration:none}"
             "li a:hover{color:var(--accent)}"
             "li span{color:var(--muted);font-size:.85rem}"
             "a.btn{display:inline-block;margin-top:1rem;background:var(--accent);color:#fff;text-decoration:none;font-size:.9rem;padding:.5rem 1rem;border-radius:8px}"
             "a.btn:hover{background:#1d4ed8}"
             "a.back{display:inline-block;margin-top:1rem;margin-left:1rem;color:var(--muted);text-decoration:none;font-size:.9rem}"
             "a.back:hover{color:var(--accent)}"
             "</style></head><body><main>"
             f"<h1>Bundle {fid}</h1><ul>{lis}</ul>"
             f"<a class=btn href='?download=1'>download as zip</a>"
             f"<a class=back href='{PREFIX}/'>← throway</a>"
             "</main></body></html>")
        self._send(200, h, "text/html")

    def do_HEAD(self):
        """HEAD = GET headers without the body. Route through do_GET."""
        self.do_GET()

    def do_GET(self):
        if not self._rate(): return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", ""):
            if self._is_agent():
                return self._write_for_agents()
            return self._index()
        if path == "/api":
            return self._api()
        if path == "/write_for_agents":
            return self._write_for_agents()
        if path == "/copy_for_agents":
            return self._copy_for_agents()
        if path == "/releases":
            return self._releases()
        parts = path.lstrip("/").split("/")
        fid = parts[0]
        if not fid or fid.endswith(".meta"):
            return self._send(404, "not found\n")
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        force_dl = "download=1" in query
        now = time.time()

        # --- bundle / dir directory ---
        dirpath = os.path.join(ROOT, os.path.basename(fid))
        if os.path.isdir(dirpath):
            m = _bundle_meta(dirpath, fid)
            expires = (m or {}).get("expires")
            if expires is None:
                expires = os.path.getmtime(dirpath) + TTL_HOURS * 3600
            if expires < now:
                shutil.rmtree(dirpath, ignore_errors=True)
                return self._send(404, "expired\n")
            is_dir = (m or {}).get("type") == "dir"
            # /<fid>/<file>
            if len(parts) >= 2 and parts[1]:
                fname = os.path.basename(parts[1])
                if not fname or fname.endswith(".meta"):
                    return self._send(404, "not found\n")
                fpath = os.path.join(dirpath, fname)
                if not os.path.isfile(fpath):
                    return self._send(404, "not found\n")
                ctype = (m or {}).get("files", {}).get(fname)
                if not ctype:
                    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                return self._serve_file(fpath, ctype, fname, force_dl, fname)
            # dir root: JSON listing for agents, HTML for browsers, zip on ?zip=1
            if is_dir:
                if force_dl or "zip=1" in query or self._is_agent():
                    # agents get JSON listing; ?zip=1 / ?download=1 get zip
                    if "zip=1" in query or force_dl:
                        return self._serve_bundle_zip(dirpath, fid)
                    return self._dir_response(fid, dirpath, m)
                return self._dir_listing(dirpath, fid)
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
            return self._send(404, "not found\n")
        mp = fp + ".meta"
        expires = None
        if os.path.isfile(mp):
            try:
                expires = json.load(open(mp)).get("expires")
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
                m = json.load(open(mp))
                ctype = m.get("ctype") or ctype
                orig = m.get("name")
            except Exception:
                pass
        self._serve_file(fp, ctype, orig, force_dl, fid)

    def do_POST(self):
        if not self._rate(): return
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        name_hint = None
        want_dir = False
        for kv in query.split("&"):
            if kv.startswith("name="):
                name_hint = _safe_name(kv[5:])[:128]
            elif kv == "dir=1":
                want_dir = True

        # POST /<dirid> -> add files to an existing dir (multipart)
        path = self.path.split("?", 1)[0].rstrip("/")
        parts = path.lstrip("/").split("/")
        if len(parts) >= 1 and parts[0] and len(parts) == 1 and self.path.split("?", 1)[0] != "/":
            existing = self._add_to_dir(parts[0])
            if existing is not None:
                return existing

        ctype = self.headers.get("Content-Type", "application/octet-stream")
        # multipart/form-data upload (browser-friendly / -F)
        if ctype.startswith("multipart/form-data"):
            payload = self._read_body()
            if payload is None:
                return
            files = _parse_multipart(payload, ctype)
            named = [(n, d, c) for (n, d, c) in files if n]
            if want_dir:
                return self._store_dir(named)
            if not named:
                return self._send(400, json.dumps({"error": "no file part in multipart body"}), "application/json")
            # multiple files -> bundle
            if len(named) > 1:
                return self._store_bundle(named)
            n, d, c = named[0]
            return self._store(d, _safe_name(n)[:128] or None, c)

        # raw-body upload: body is the file content
        length = self.headers.get("Content-Length")
        # empty dir creation: POST /?dir=1 with no body
        if want_dir and length in (None, "0"):
            return self._store_dir([])
        if length is None:
            return self._send(411, json.dumps({"error": "length required"}), "application/json")
        length = int(length)
        if length > MAX_FILE:
            return self._send(413, json.dumps({"error": "too large (max 5MB)"}), "application/json")
        data = self.rfile.read(length)
        if want_dir:
            # raw-body dir creation: single file named by ?name=
            return self._store_dir([(name_hint or "file", data, "application/octet-stream")])
        if name_hint:
            ctype = mimetypes.guess_type(name_hint)[0] or "application/octet-stream"
        else:
            if ctype.startswith("multipart") or "boundary" in ctype:
                ctype = "application/octet-stream"
        return self._store(data, name_hint or None, ctype)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return None
        return self.rfile.read(int(length))

    def _store(self, data, name_hint, ctype):
        if len(data) > MAX_FILE:
            return self._send(413, json.dumps({"error": "too large (max 5MB)"}), "application/json")
        fid = secrets.token_hex(8)
        fp = _id_path(fid)
        with open(fp, "wb") as f:
            f.write(data)
        meta = {
            "expires": time.time() + TTL_HOURS * 3600,
            "ctype": ctype or "application/octet-stream",
            "name": name_hint or fid,
            "created": time.time(),
        }
        json.dump(meta, open(fp + ".meta", "w"))
        evict(THROW_POOL_SIZE)
        s = _load_stats()
        s["files"] += 1
        s["bytes"] += len(data)
        _save_stats(s)
        url = f"{PUBLIC_BASE}/{fid}"
        body = json.dumps({
            "id": fid,
            "url": url,
            "size": len(data),
            "name": meta["name"],
            "content_type": meta["ctype"],
            "expires_in": TTL_HOURS * 3600,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta["expires"])),
        })
        self._send(200, body, "application/json", {"X-Expires": str(TTL_HOURS * 3600)})

    def _store_bundle(self, files):
        """Store multiple files as a bundle directory; return JSON response."""
        clean = []
        total = 0
        for n, d, c in files:
            safe = _safe_name(n)
            if not safe:
                continue
            if len(d) > MAX_FILE:
                return self._send(413, json.dumps({"error": f"too large (max 5MB): {safe}"}), "application/json")
            total += len(d)
            if total > THROW_POOL_SIZE:
                return self._send(413, json.dumps({"error": "bundle too large (pool max 100MB)"}), "application/json")
            clean.append((safe, d, c))
        if not clean:
            return self._send(400, json.dumps({"error": "no valid file parts"}), "application/json")
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
            "expires": time.time() + TTL_HOURS * 3600,
            "created": time.time(),
            "files": files_map,
        }
        json.dump(meta, open(os.path.join(dirpath, fid + ".meta"), "w"))
        evict(THROW_POOL_SIZE)
        s = _load_stats()
        s["files"] += len(clean)
        s["bytes"] += sum(len(d) for _, d, _ in clean)
        _save_stats(s)
        expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta["expires"]))
        body = json.dumps({
            "id": fid,
            "url": f"{PUBLIC_BASE}/{fid}",
            "bundle": True,
            "files": [
                {"name": n,
                 "url": f"{PUBLIC_BASE}/{fid}/{n}",
                 "size": os.path.getsize(os.path.join(dirpath, n)),
                 "content_type": files_map[n]}
                for n in names
            ],
            "size": sum(os.path.getsize(os.path.join(dirpath, n)) for n in names),
            "expires_in": TTL_HOURS * 3600,
            "expires_at": expires_at,
        })
        self._send(200, body, "application/json", {"X-Expires": str(TTL_HOURS * 3600)})

    def _store_dir(self, files):
        """Create a mutable dir (type=dir) with optional initial files."""
        clean = []
        total = 0
        for n, d, c in files:
            safe = _safe_name(n)
            if not safe:
                continue
            if len(d) > MAX_FILE:
                return self._send(413, json.dumps({"error": f"too large (max 5MB): {safe}"}), "application/json")
            total += len(d)
            if total > THROW_POOL_SIZE:
                return self._send(413, json.dumps({"error": "dir too large (pool max 100MB)"}), "application/json")
            clean.append((safe, d, c))
        fid = secrets.token_hex(8)
        dirpath = os.path.join(ROOT, fid)
        os.makedirs(dirpath, exist_ok=True)
        now = time.time()
        files_map = {}
        for (_, d, c), name in zip(clean, _dedupe_names([n for n, _, _ in clean])):
            with open(os.path.join(dirpath, name), "wb") as f:
                f.write(d)
            files_map[name] = mimetypes.guess_type(name)[0] or c or "application/octet-stream"
        meta = {
            "type": "dir",
            "created": now,
            "expires": now + TTL_HOURS * 3600,
            "files": files_map,
        }
        json.dump(meta, open(os.path.join(dirpath, fid + ".meta"), "w"))
        evict(THROW_POOL_SIZE)
        s = _load_stats()
        s["files"] += len(clean)
        s["bytes"] += sum(len(d) for _, d, _ in clean)
        _save_stats(s)
        return self._dir_response(fid, dirpath, meta)

    def _add_to_dir(self, fid):
        """POST /<dirid>: add multipart files to an existing dir, reset TTL.
        Returns None if fid is not a dir (so do_POST falls through to normal
        upload handling)."""
        dirpath = os.path.join(ROOT, os.path.basename(fid))
        if not os.path.isdir(dirpath):
            return None
        m = _bundle_meta(dirpath, fid)
        if not m or m.get("type") != "dir":
            return None
        # expired?
        now = time.time()
        if m.get("expires", 0) < now:
            shutil.rmtree(dirpath, ignore_errors=True)
            return self._send(404, json.dumps({"error": "expired"}), "application/json")
        ctype = self.headers.get("Content-Type", "application/octet-stream")
        if not ctype.startswith("multipart/form-data"):
            return self._send(400, json.dumps({"error": "dir add requires multipart"}), "application/json")
        payload = self._read_body()
        if payload is None:
            return self._send(411, json.dumps({"error": "length required"}), "application/json")
        files = _parse_multipart(payload, ctype)
        named = [(n, d, c) for (n, d, c) in files if n]
        if not named:
            return self._send(400, json.dumps({"error": "no file parts"}), "application/json")
        # enforce per-file + total dir size
        cur = _dir_size(dirpath)
        for n, d, c in named:
            safe = _safe_name(n)
            if not safe:
                continue
            if len(d) > MAX_FILE:
                return self._send(413, json.dumps({"error": f"too large (max 5MB): {safe}"}), "application/json")
            cur += len(d)
            if cur > THROW_POOL_SIZE:
                return self._send(413, json.dumps({"error": "dir too large (pool max 100MB)"}), "application/json")
        files_map = m.get("files", {})
        existing = set(os.listdir(dirpath))
        existing.discard(fid + ".meta")
        for n, d, c in named:
            safe = _safe_name(n)
            if not safe:
                continue
            name = _dedupe_names([safe] + [x for x in existing if x != safe])[0]
            with open(os.path.join(dirpath, name), "wb") as f:
                f.write(d)
            files_map[name] = mimetypes.guess_type(name)[0] or c or "application/octet-stream"
            existing.add(name)
        # sliding TTL: +4h from now, capped at 24h total from creation
        m["expires"] = min(now + TTL_HOURS * 3600, m.get("created", now) + DIR_MAX_AGE)
        m["files"] = files_map
        json.dump(m, open(os.path.join(dirpath, fid + ".meta"), "w"))
        evict(THROW_POOL_SIZE)
        s = _load_stats()
        s["files"] += len(named)
        s["bytes"] += sum(len(d) for _, d, _ in named)
        _save_stats(s)
        return self._dir_response(fid, dirpath, m)

    def _dir_response(self, fid, dirpath, meta):
        """JSON response describing a dir."""
        files = []
        total = 0
        for f in sorted(os.listdir(dirpath)):
            if f.endswith(".meta"):
                continue
            fp = os.path.join(dirpath, f)
            if not os.path.isfile(fp):
                continue
            sz = os.path.getsize(fp)
            total += sz
            files.append({"name": f, "url": f"{PUBLIC_BASE}/{fid}/{f}", "size": sz,
                          "content_type": meta.get("files", {}).get(f, "application/octet-stream")})
        return self._send(200, json.dumps({
            "id": fid,
            "url": f"{PUBLIC_BASE}/{fid}",
            "dir": True,
            "files": files,
            "size": total,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.get("expires", 0))),
        }), "application/json", {"X-Expires": str(TTL_HOURS * 3600)})

    def _dir_listing(self, dirpath, fid):
        """HTML listing for a dir viewed in a browser."""
        rows = []
        for f in sorted(os.listdir(dirpath)):
            if f.endswith(".meta"):
                continue
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                rows.append((f, os.path.getsize(fp)))
        lis = "\n".join(
            f'<li><a href="{_html_escape(f)}">{_html_escape(f)}</a> ({s} B)</li>'
            for f, s in rows)
        h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"<title>throway dir {fid}</title>"
             "<style>"
             ":root{--bg:#fff;--card:#fafafa;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}"
             "*{box-sizing:border-box}"
             "body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--ink);min-height:100vh}"
             "main{max-width:720px;margin:0 auto;padding:3rem 1.5rem}"
             "h1{font-size:1.4rem}"
             "ul{list-style:none;padding:0}"
             "li{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.6rem .9rem;margin:.4rem 0;display:flex;justify-content:space-between;align-items:center}"
             "li a{color:var(--ink);text-decoration:none}"
             "li a:hover{color:var(--accent)}"
             "li span{color:var(--muted);font-size:.85rem}"
             "a.btn{display:inline-block;margin-top:1rem;background:var(--accent);color:#fff;text-decoration:none;font-size:.9rem;padding:.5rem 1rem;border-radius:8px}"
             "a.btn:hover{background:#1d4ed8}"
             "a.back{display:inline-block;margin-top:1rem;margin-left:1rem;color:var(--muted);text-decoration:none;font-size:.9rem}"
             "a.back:hover{color:var(--accent)}"
             "</style></head><body><main>"
             f"<h1>Dir {fid}</h1><ul>{lis}</ul>"
             f"<a class=btn href='?zip=1'>download as zip</a>"
             f"<a class=back href='{PREFIX}/'>← throway</a>"
             "</main></body></html>")
        self._send(200, h, "text/html")

    def do_DELETE(self):
        if not self._rate(): return
        path = self.path.lstrip("/").rstrip("/")
        parts = path.split("/")
        fid = parts[0]
        dirpath = os.path.join(ROOT, os.path.basename(fid))
        # DELETE /<dirid>/<file> -> remove one file from a dir
        if os.path.isdir(dirpath) and len(parts) >= 2 and parts[1]:
            m = _bundle_meta(dirpath, fid)
            fname = os.path.basename(parts[1])
            fpath = os.path.join(dirpath, fname)
            if fname.endswith(".meta") or not os.path.isfile(fpath):
                return self._send(404, "not found\n")
            os.remove(fpath)
            if m and "files" in m:
                m["files"].pop(fname, None)
                json.dump(m, open(os.path.join(dirpath, fid + ".meta"), "w"))
            return self._send(200, "deleted\n")
        if os.path.isdir(dirpath):
            shutil.rmtree(dirpath, ignore_errors=True)
            self._send(200, "deleted\n")
            return
        fp = _id_path(fid)
        if os.path.isfile(fp):
            _remove(fp); self._send(200, "deleted\n")
        else:
            self._send(404, "not found\n")

    def _meta_of(self, fid):
        mp = _id_path(fid) + ".meta"
        if os.path.isfile(mp):
            try:
                return json.load(open(mp))
            except Exception:
                pass
        return None

    def _is_text(self, fid):
        m = self._meta_of(fid)
        return bool(m and (m.get("ctype") or "").startswith("text/"))

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
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.get("expires", time.time()))),
        })

    def do_PUT(self):
        """Replace text content (edit)."""
        if not self._rate(): return
        fid = self.path.lstrip("/").split("/")[0]
        if not fid or fid.endswith(".meta"):
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        fp = _id_path(fid)
        if not os.path.isfile(fp):
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        if not self._is_text(fid):
            return self._send(400, json.dumps({"error": "only text files can be edited"}), "application/json")
        data = self._read_body()
        if data is None:
            return self._send(411, json.dumps({"error": "length required"}), "application/json")
        if len(data) > MAX_FILE:
            return self._send(413, json.dumps({"error": "too large (max 5MB)"}), "application/json")
        with open(fp, "wb") as f:
            f.write(data)
        evict(THROW_POOL_SIZE)
        self._send(200, self._text_result(fid), "application/json")

    def do_PATCH(self):
        """Append text to existing content."""
        if not self._rate(): return
        fid = self.path.lstrip("/").split("/")[0]
        if not fid or fid.endswith(".meta"):
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        fp = _id_path(fid)
        if not os.path.isfile(fp):
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        if not self._is_text(fid):
            return self._send(400, json.dumps({"error": "only text files can be appended to"}), "application/json")
        data = self._read_body()
        if data is None:
            return self._send(411, json.dumps({"error": "length required"}), "application/json")
        cur = os.path.getsize(fp)
        if cur + len(data) > MAX_FILE:
            return self._send(413, json.dumps({"error": "too large (max 5MB)"}), "application/json")
        with open(fp, "ab") as f:
            f.write(data)
        evict(THROW_POOL_SIZE)
        self._send(200, self._text_result(fid), "application/json")

    def _agent_description(self):
        return f"""THROWAWAY STORE — FOR AGENTS

You are talking to a disposable file store. It lets you upload a file and
share a short-lived URL. Everything is open (no auth) and everything
expires after {TTL_HOURS} hours.

WHAT IT IS FOR
- Sharing a file (image, text, binary) by giving someone a URL.
- Sharing a BUNDLE of files (e.g. an html/css/js website) under one URL.
- Sharing a mutable DIR: keep adding files to one URL; the dir is deleted
  4h after the latest upload (max 24h total).
- A scratchpad for text: create a note, append to it, rewrite it.
- Passing data between agents / machines without setting up accounts.

WHAT IT IS NOT
- Not permanent storage. Files are automatically deleted after {TTL_HOURS} hours.
- Not private. Anyone who has a URL can read, edit, or delete that file.
- Not a database. It is a flat, throwaway store.

HOW TO USE IT
Base URL: {PUBLIC_BASE}

1) UPLOAD a file (raw body or multipart):
   POST {PUBLIC_BASE}/?name=filename.ext
   with the file bytes as the body.
   -> Returns JSON: id, url, size, name, content_type, expires_in, expires_at.

   UPLOAD a BUNDLE (multiple files, e.g. a website):
   POST {PUBLIC_BASE}/   with multipart/form-data containing 2+ file parts.
   -> Returns JSON: id, url, bundle:true, files:[{{name,url,size,content_type}}…].
   The bundle URL serves index.html inline (or a zip for agents).
   Each file is reachable at {PUBLIC_BASE}/<id>/<filename>.

   CREATE a DIR (mutable, keep adding files):
   POST {PUBLIC_BASE}/?dir=1   -> create an empty dir: {{id, url, dir:true, files:[]}}
   POST {PUBLIC_BASE}/<dirid>  -> add files (multipart) to that dir, resets TTL
   GET  {PUBLIC_BASE}/<dirid>  -> JSON listing (agents) / HTML page (browsers)
   GET  {PUBLIC_BASE}/<dirid>/<file> -> fetch one file
   GET  {PUBLIC_BASE}/<dirid>?zip=1  -> download the whole dir as a zip
   DELETE {PUBLIC_BASE}/<dirid>/<file> -> remove one file
   DELETE {PUBLIC_BASE}/<dirid>       -> delete the whole dir
   A dir is deleted {TTL_HOURS}h after the latest upload (capped at 24h
   total). LIST it with GET to see current files + expiry.

2) DOWNLOAD / VIEW a file:
   GET {PUBLIC_BASE}/<id>
   Images and text-like types (text, html, json, pdf, svg) render inline
   in a browser; other files download.
   For a bundle, GET {PUBLIC_BASE}/<id> serves index.html inline (browser)
   or the whole bundle as a zip (agents). GET {PUBLIC_BASE}/<id>/<file>
   serves one file.
   Append ?download=1 to force a download of any file or the bundle zip.

3) EDIT TEXT (text files only; images are immutable):
   PUT   {PUBLIC_BASE}/<id>   with new text body  -> replace whole content
   PATCH {PUBLIC_BASE}/<id>   with text body      -> append to content

4) DELETE a file:
   DELETE {PUBLIC_BASE}/<id>

LIMITS
- URL lifetime:  {TTL_HOURS} hours
- Max file size: {MAX_FILE // (1024*1024)} MB
- Pool size:     {THROW_POOL_SIZE // (1024*1024)} MB (oldest files evicted first)
- Rate limit:    {RATE_LIMIT} requests/min per IP

MACHINE-READABLE CONTRACT
GET {PUBLIC_BASE}/api  -> returns the same limits + endpoints as JSON.
An agent should read /api to discover current limits before acting.
"""

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
        if self._is_agent():
            return self._send(200, md, "text/markdown; charset=utf-8")
        # browsers: render as an HTML page (escape + minimal md-ish styling)
        esc = _html_escape(md)
        h = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
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
        spec = {
            "service": "throwaway-store",
            "version": VERSION,
            "base_url": PUBLIC_BASE,
            "ttl_seconds": TTL_HOURS * 3600,
            "max_file_bytes": MAX_FILE,
            "pool_bytes": THROW_POOL_SIZE,
            "rate_limit_per_min": RATE_LIMIT,
            "endpoints": {
                "upload": {
                    "method": "POST",
                    "url": PUBLIC_BASE + "/?name=<filename>",
                    "body": "raw file bytes (or multipart/form-data with a file part)",
                    "response": {"id": "str", "url": "str", "size": "int", "name": "str", "content_type": "str", "expires_in": "int", "expires_at": "str"},
                },
                "upload_bundle": {
                    "method": "POST",
                    "url": PUBLIC_BASE + "/",
                    "body": "multipart/form-data with 2+ file parts",
                    "note": "creates a bundle: one URL, files served at /<id>/<filename>, index.html inline for browsers",
                    "response": {"id": "str", "url": "str", "bundle": True, "files": [{"name": "str", "url": "str", "size": "int", "content_type": "str"}], "expires_at": "str"},
                },
                "download": {"method": "GET", "url": PUBLIC_BASE + "/<id>", "note": "images and text-like types render inline; bundle root serves index.html inline (browser) or zip (agent); append ?download=1 to force download"},
                "download_bundle_file": {"method": "GET", "url": PUBLIC_BASE + "/<id>/<filename>", "note": "serve a single file from a bundle"},
                "create_dir": {"method": "POST", "url": PUBLIC_BASE + "/?dir=1", "note": "create a mutable dir; add files later; deleted 4h after latest upload (max 24h total)", "response": {"id": "str", "url": "str", "dir": True, "files": []}},
                "add_to_dir": {"method": "POST", "url": PUBLIC_BASE + "/<dirid>", "body": "multipart/form-data file parts", "note": "add files to a dir, resets its TTL"},
                "list_dir": {"method": "GET", "url": PUBLIC_BASE + "/<dirid>", "note": "JSON listing for agents, HTML page for browsers"},
                "dir_zip": {"method": "GET", "url": PUBLIC_BASE + "/<dirid>?zip=1", "note": "download the whole dir as a zip"},
                "delete_dir_file": {"method": "DELETE", "url": PUBLIC_BASE + "/<dirid>/<filename>", "note": "remove one file from a dir"},
                "delete": {"method": "DELETE", "url": PUBLIC_BASE + "/<id>"},
                "edit_text": {"method": "PUT", "url": PUBLIC_BASE + "/<id>", "body": "new text content (text files only)", "note": "replaces the whole text content"},
                "append_text": {"method": "PATCH", "url": PUBLIC_BASE + "/<id>", "body": "text to append (text files only)"},
                "contract": {"method": "GET", "url": PUBLIC_BASE + "/api"},
                "write_for_agents": {"method": "GET", "url": PUBLIC_BASE + "/write_for_agents", "note": "human-readable description of this service for agents"},
                "copy_for_agents": {"method": "GET", "url": PUBLIC_BASE + "/copy_for_agents", "note": "HTML page with a copy-pasteable agent description"},
                "releases": {"method": "GET", "url": PUBLIC_BASE + "/releases", "note": "release notes; raw markdown for agents, rendered HTML for browsers"},
            },
        }
        self._send(200, json.dumps(spec, indent=2), "application/json")

def _fmt_size(n):
    """Format bytes with a sensible unit: B, kB, MB, GB."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} kB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"

class _IndexMixin:
    pass

def _index(self):
    sweep()
    rows = []
    actual = 0
    for f in os.listdir(ROOT):
        if f.endswith(".meta") or not os.path.isfile(os.path.join(ROOT, f)):
            continue
        s = os.path.getsize(os.path.join(ROOT, f))
        rows.append((f, s))
        actual += s
    cum = _cumulative()
    tot_files = cum["files"]
    tot_bytes = cum["bytes"]
    pct = 100.0 * actual / THROW_POOL_SIZE
    h = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
         "<meta name=viewport content='width=device-width,initial-scale=1'>"
         "<title>throway — disposable file store</title>"
         "<style>"
         ":root{--bg:#ffffff;--card:#fafafa;--card2:#f4f4f5;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}"
         "*{box-sizing:border-box}"
         "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.55;min-height:100vh}"
         "main{max-width:840px;margin:0 auto;padding:3rem 1.5rem 5rem}"
         "header{display:flex;align-items:baseline;gap:.75rem;margin-bottom:.25rem}"
         "h1{font-size:2rem;margin:0;letter-spacing:-.02em}"
         "h1 .dot{color:var(--accent)}"
         ".version{font-size:.8rem;color:var(--muted);background:var(--card2);border:1px solid var(--line);padding:.15rem .5rem;border-radius:999px}"
         "p.lede{color:var(--muted);max-width:60ch;margin:.5rem 0 1.5rem}"
         "ul.feats{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem;list-style:none;padding:0;margin:0 0 1.5rem}"
         "ul.feats li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem .9rem;font-size:.9rem}"
         "ul.feats li b{color:var(--accent)}"
         "ul.feats li small{display:block;color:var(--muted);margin-top:.15rem}"
         "#drop{border:2px dashed #d1d5db;border-radius:14px;padding:2.2rem 1.5rem;text-align:center;cursor:pointer;transition:border-color .15s,background .15s;background:var(--card);margin-bottom:.8rem}"
         "#drop:hover,#drop.drag{border-color:var(--accent);background:#eff6ff}"
         "#drop .big{font-size:1.05rem;font-weight:600}"
         "#drop .sub{color:var(--muted);font-size:.85rem;margin-top:.2rem}"
         "#drop input{display:none}"
         "#fileList{margin:.4rem 0 .8rem;font-size:.85rem;color:var(--muted)}"
         "#fileList span{display:inline-block;background:var(--card2);border:1px solid var(--line);border-radius:6px;padding:.15rem .5rem;margin:.15rem;font-size:.8rem}"
         ".controls{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}"
         "label.mode{display:flex;align-items:center;gap:.4rem;font-size:.85rem;color:var(--muted);cursor:pointer}"
         "label.mode input{margin:0}"
         "button,.btn{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:.6rem 1.2rem;font-size:.95rem;font-weight:600;cursor:pointer;transition:background .15s}"
         "button:hover,.btn:hover{background:#1d4ed8}"
         "button:active{transform:translateY(1px)}"
         "button:disabled{opacity:.5;cursor:not-allowed}"
         "#status{margin:.8rem 0;font-size:.9rem}"
         "#status.err{color:#dc2626}"
         "#result{margin:1rem 0;padding:1rem 1.2rem;border-radius:12px;background:#eff6ff;border:1px solid #bfdbfe;display:none}"
         "#result h3{margin:.2rem 0 .6rem;font-size:1.05rem}"
         "#result .row{padding:.25rem 0;font-size:.9rem}"
         "#result .lbl{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;margin-right:.5rem}"
         "#result a{color:var(--accent);word-break:break-all}"
         "#result .urlbox{display:flex;gap:.4rem;align-items:center;background:#fff;border:1px solid var(--line);border-radius:8px;padding:.4rem .6rem;margin:.3rem 0}"
         "#result .urlbox input{flex:1;background:none;border:0;color:var(--ink);font-size:.85rem;font-family:ui-monospace,monospace;outline:none}"
         "#result .files{font-size:.85rem;color:var(--muted)}"
         "#result .files div{padding:.15rem 0}"
         "#result .files a{color:var(--ink)}"
         "#result .files a:hover{color:var(--accent)}"
         ".stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin:1.4rem 0}"
         ".stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem .9rem}"
         ".stat b{font-size:1.15rem;display:block}"
         ".stat span{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}"
         ".meter{height:6px;background:#e5e7eb;border-radius:999px;overflow:hidden;margin-top:.4rem}"
         ".meter i{display:block;height:100%;background:var(--accent);border-radius:999px}"
         "nav.links{display:flex;gap:1.2rem;margin-top:2rem;font-size:.88rem;flex-wrap:wrap}"
         "nav.links a{color:var(--muted);text-decoration:none}"
         "nav.links a:hover{color:var(--accent)}"
         "details.agents{margin-top:1.5rem;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem 1rem}"
         "details.agents summary{cursor:pointer;font-weight:600;color:var(--accent)}"
         "details.agents pre{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px;line-height:1.5;color:var(--muted);margin:.6rem 0 0;padding-top:.6rem;border-top:1px solid var(--line)}"
         "@media(max-width:560px){main{padding:2rem 1rem 4rem}}"
         "</style></head><body><main>"
         f"<header><h1>throway<span class='dot'>.</span></h1>"
         f"<span class='version'>v{VERSION}</span></header>"
         "<p class='lede'>A disposable file store for agents and humans. Upload a file, a"
         " bundle, or a mutable dir — share a short-lived URL. No accounts, no setup,"
         " nothing permanent.</p>"
         "<ul class='feats'>"
         "<li><b>Files</b> — one URL per upload<small>inline for images &amp; text, download otherwise</small></li>"
         "<li><b>Bundles</b> — a whole mini-website<small>index.html renders inline; zip for agents</small></li>"
         "<li><b>Dirs</b> — keep adding files<small>deleted 4h after last upload (max 24h)</small></li>"
         "</ul>"
         "<div id='drop'>"
         "<div class='big'>Drop files here, or click to choose</div>"
         "<div class='sub'>Select one or many files</div>"
         "<input type='file' id='file' name='f' multiple>"
         "</div>"
         "<div id='fileList'></div>"
         "<div class='controls'>"
         "<button id='up'>Upload</button>"
         "<label class='mode'><input type='checkbox' id='dirMode'>create a <b>dir</b> (mutable)</label>"
         "<span style='flex:1'></span>"
         "<span style='color:var(--muted);font-size:.8rem'>files live ~" + str(TTL_HOURS) + "h</span>"
         "</div>"
         "<div id='status'></div>"
         "<div id='result'></div>"
         "<div class='stats'>"
         f"<div class='stat'><b>{len(rows)}</b><span>files live now</span></div>"
         f"<div class='stat'><b>{_fmt_size(actual)}</b><span>stored</span>"
         f"<div class='meter'><i style='width:{min(100,pct):.0f}%'></i></div></div>"
         f"<div class='stat'><b>{tot_files}</b><span>files ever</span></div>"
         f"<div class='stat'><b>{_fmt_size(tot_bytes)}</b><span>uploaded ever</span></div>"
         "</div>"
         "<nav class='links'>"
         f"<a href='{PREFIX}/api'>API</a>"
         f"<a href='{PREFIX}/write_for_agents'>for agents</a>"
         f"<a href='{PREFIX}/releases'>releases</a>"
         "</nav>"
         "<details class='agents'><summary>Agent info</summary><pre>" + _html_escape(self._agent_description()) + "</pre></details>"
         "<script>"
         "var drop=document.getElementById('drop'),file=document.getElementById('file'),list=document.getElementById('fileList');"
         "var up=document.getElementById('up'),status=document.getElementById('status'),res=document.getElementById('result');"
         "var dirMode=document.getElementById('dirMode');"
         "function showFiles(){var n=file.files.length;if(!n){list.textContent='';return;}"
         "list.innerHTML='';for(var i=0;i<n;i++){var s=document.createElement('span');s.textContent=file.files[i].name;list.appendChild(s);}}"
         "file.addEventListener('change',showFiles);"
         "['dragover','dragenter'].forEach(function(e){drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.add('drag');});});"
         "drop.addEventListener('dragleave',function(){drop.classList.remove('drag');});"
         "drop.addEventListener('drop',function(ev){ev.preventDefault();drop.classList.remove('drag');file.files=ev.dataTransfer.files;showFiles();});"
         "drop.addEventListener('click',function(){file.click();});"
         "up.addEventListener('click',function(){if(!file.files.length){status.textContent='Choose at least one file';status.className='err';return;}"
         "var fd=new FormData();for(var i=0;i<file.files.length;i++)fd.append('f',file.files[i]);"
         "var url='" + PREFIX + "/'+(dirMode.checked?'?dir=1':'');"
         "status.textContent='Uploading…';status.className='';res.style.display='none';up.disabled=true;"
         "fetch(url,{method:'POST',body:fd}).then(function(r){return r.json().then(function(d){return {ok:r.ok,data:d};});})"
         ".then(function(o){up.disabled=false;if(!o.ok){status.textContent='Error: '+(o.data.error||'upload failed');status.className='err';return;}"
         "var d=o.data;status.textContent='';res.style.display='block';"
         "var html='<h3>Done ✓</h3>'"
         "+'<div class=row><span class=lbl>URL</span><div class=urlbox><input readonly value=\"'+d.url+'\"><button class=btn onclick=\"navigator.clipboard.writeText(this.previousElementSibling.value)\">copy</button></div></div>'"
         "+(d.dir?('<div class=row><span class=lbl>Dir</span> '+d.files.length+' files · expires '+d.expires_at+'</div>')"
         "+'<div class=files>'+d.files.map(function(f){return '<div>• <a href=\"'+f.url+'\" target=_blank>'+f.name+'</a> ('+f.size+' B)</div>';}).join('')+'</div>')"
         ":d.bundle?('<div class=row><span class=lbl>Bundle</span> '+d.files.length+' files · expires '+d.expires_at+'</div>'"
         "+'<div class=files>'+d.files.map(function(f){return '<div>• <a href=\"'+f.url+'\" target=_blank>'+f.name+'</a></div>';}).join('')+'</div>')"
         ":('<div class=row><span class=lbl>Name</span> '+d.name+'</div>'"
         "+'<div class=row><span class=lbl>Size</span> '+d.size+' B</div>'"
         "+'<div class=row><span class=lbl>Type</span> '+d.content_type+'</div>'"
         "+'<div class=row><span class=lbl>Expires</span> '+d.expires_at+'</div>');"
         "res.innerHTML=html;file.value='';showFiles();"
         "}).catch(function(err){up.disabled=false;status.textContent='Error: '+err;status.className='err';});"
         "});"
         "</script>"
         "</main></body></html>")
    self._send(200, h, "text/html")


# patch _index into Handler
Handler._index = _index

if __name__ == "__main__":
    sweep()
    print(f"store on :{PORT} root={ROOT} ttl={TTL_HOURS}h")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
