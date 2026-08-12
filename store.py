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
PUBLIC_BASE = "https://lubu.skale.dev/throway"
PREFIX = "/throway"

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
        self.wfile.write(body)

    def _rate(self):
        if not allowed(self.client_address[0]):
            self._send(429, "rate limit exceeded\n"); return False
        return True

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
            while c := f.read(65536):
                self.wfile.write(c)

    def _serve_bundle_zip(self, dirpath, fid):
        """Stream the whole bundle as a zip (for agents / ?download=1)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(dirpath)):
                if f.endswith(".meta"):
                    continue
                z.write(os.path.join(dirpath, f), arcname=f)
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{fid}.zip"')
        self.end_headers()
        self.wfile.write(data)

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
            f'<li><a href="{_html_escape(f)}">{_html_escape(f)}</a> ({s} B)</li>'
            for f, s in rows)
        h = ("<!doctype html><html><head><meta charset=utf-8>"
             f"<title>throway bundle {fid}</title>"
             "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#222}"
             "a{color:#2563eb;text-decoration:none}</style></head><body>"
             f"<h1>Bundle {fid}</h1><ul>{lis}</ul>"
             f"<p><a href='?download=1'>download as zip</a></p></body></html>")
        self._send(200, h, "text/html")

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
        parts = path.lstrip("/").split("/")
        fid = parts[0]
        if not fid or fid.endswith(".meta"):
            return self._send(404, "not found\n")
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        force_dl = "download=1" in query
        now = time.time()

        # --- bundle directory ---
        dirpath = os.path.join(ROOT, os.path.basename(fid))
        if os.path.isdir(dirpath):
            m = _bundle_meta(dirpath, fid)
            expires = (m or {}).get("expires")
            if expires is None:
                expires = os.path.getmtime(dirpath) + TTL_HOURS * 3600
            if expires < now:
                shutil.rmtree(dirpath, ignore_errors=True)
                return self._send(404, "expired\n")
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
            # bundle root
            if force_dl or self._is_agent():
                return self._serve_bundle_zip(dirpath, fid)
            index = os.path.join(dirpath, "index.html")
            if os.path.isfile(index):
                return self._serve_file(index, "text/html", "index.html", False, "index.html")
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
        for kv in query.split("&"):
            if kv.startswith("name="):
                name_hint = _safe_name(kv[5:])[:128]

        ctype = self.headers.get("Content-Type", "application/octet-stream")
        # multipart/form-data upload (browser-friendly / -F)
        if ctype.startswith("multipart/form-data"):
            payload = self._read_body()
            if payload is None:
                return
            files = _parse_multipart(payload, ctype)
            named = [(n, d, c) for (n, d, c) in files if n]
            if not named:
                return self._send(400, json.dumps({"error": "no file part in multipart body"}), "application/json")
            # multiple files -> bundle
            if len(named) > 1:
                return self._store_bundle(named)
            n, d, c = named[0]
            return self._store(d, _safe_name(n)[:128] or None, c)

        # raw-body upload: body is the file content
        length = self.headers.get("Content-Length")
        if length is None:
            return self._send(411, json.dumps({"error": "length required"}), "application/json")
        length = int(length)
        if length > MAX_FILE:
            return self._send(413, json.dumps({"error": "too large (max 5MB)"}), "application/json")
        data = self.rfile.read(length)
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
        for n, d, c in files:
            safe = _safe_name(n)
            if not safe:
                continue
            if len(d) > MAX_FILE:
                return self._send(413, json.dumps({"error": f"too large (max 5MB): {safe}"}), "application/json")
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
            files_map[name] = c or "application/octet-stream"
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

    def do_DELETE(self):
        if not self._rate(): return
        fid = self.path.lstrip("/").split("/")[0]
        dirpath = os.path.join(ROOT, os.path.basename(fid))
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
   -> Returns JSON: id, url, bundle:true, files:[{name,url,size,content_type}…].
   The bundle URL serves index.html inline (or a zip for agents).
   Each file is reachable at {PUBLIC_BASE}/<id>/<filename>.

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
            "version": 1,
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
                "delete": {"method": "DELETE", "url": PUBLIC_BASE + "/<id>"},
                "edit_text": {"method": "PUT", "url": PUBLIC_BASE + "/<id>", "body": "new text content (text files only)", "note": "replaces the whole text content"},
                "append_text": {"method": "PATCH", "url": PUBLIC_BASE + "/<id>", "body": "text to append (text files only)"},
                "contract": {"method": "GET", "url": PUBLIC_BASE + "/api"},
                "write_for_agents": {"method": "GET", "url": PUBLIC_BASE + "/write_for_agents", "note": "human-readable description of this service for agents"},
                "copy_for_agents": {"method": "GET", "url": PUBLIC_BASE + "/copy_for_agents", "note": "HTML page with a copy-pasteable agent description"},
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
    h = ("<!doctype html><html><head><meta charset=utf-8><title>throway</title>"
         "<style>"
         "body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.5}"
         "h1{font-size:1.6rem;margin-bottom:.2rem}"
         ".stats{background:#f4f4f5;border:1px solid #e4e4e7;border-radius:8px;padding:.8rem 1rem;margin:.5rem 0 1rem}"
         ".stats div{padding:.15rem 0}"
         ".stats b{color:#111}"
         ".stats .pct{color:#2563eb;font-weight:600}"
         "form{display:flex;gap:.5rem;align-items:center;margin:1rem 0;padding:1rem;background:#fafafa;border:1px solid #e4e4e7;border-radius:8px}"
         "input[type=file]{font-size:.9rem}"
         "button,input[type=submit]{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:.45rem .9rem;font-size:.9rem;cursor:pointer}"
         "button:hover,input[type=submit]:hover{background:#1d4ed8}"
         "a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}"
         ".hint{color:#666;font-size:.9rem;display:flex;align-items:center;gap:.4rem}"
         ".cp{display:inline-flex;align-items:center;gap:.3rem;cursor:pointer;color:#2563eb;background:none;border:0;font-size:.9rem;padding:.2rem .4rem;border-radius:6px}"
         ".cp:hover{background:#eff6ff}"
         ".cp svg{width:16px;height:16px}"
         "details.agents{margin:1rem 0;background:#fafafa;border:1px solid #e4e4e7;border-radius:8px;padding:.5rem .9rem}"
         "details.agents summary{cursor:pointer;font-weight:600;color:#333}"
         "details.agents pre{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px;line-height:1.5;color:#444;margin:.5rem 0 0;padding-top:.5rem;border-top:1px solid #eee}"
         "#result{margin:1rem 0;padding:1rem;border-radius:8px;background:#eff6ff;border:1px solid #bfdbfe;display:none}"
         "#result a{word-break:break-all}"
         "#result .row{padding:.15rem 0}"
         "#result .lbl{color:#555;font-size:.75rem;text-transform:uppercase;letter-spacing:.02em}"
         "#status{color:#666;font-size:.9rem}"
         "</style></head><body>"
         f"<h1>throway</h1>"
         f"<div class='stats'>"
         f"<div><b>actual:</b> {len(rows)} files, {_fmt_size(actual)} · <span class='pct'>{pct:.0f}%</span> of max</div>"
         f"<div><b>total:</b> {tot_files} files, {_fmt_size(tot_bytes)} ever</div>"
         f"</div>"
         f"<form id='upload' method='post' action='{PREFIX}/' enctype='multipart/form-data'>"
         "<input type='file' name='f' multiple required><input type='submit' value='Upload'></form>"
         "<div id='status'></div>"
         "<div id='result'></div>"
         f"<p class='hint'>Files live ~{TTL_HOURS}h. "
         f"<a href='{PREFIX}/api'>API</a> · <a href='{PREFIX}/write_for_agents'>description</a> · "
         "<button class='cp' id='cpBtn' title='Copy agent description'>"
         "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect width='14' height='14' x='8' y='8' rx='2' ry='2'/><path d='M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2'/></svg>"
         "copy for agents</button></p>"
         "<details class='agents'><summary>Agent info</summary><pre>" + _html_escape(self._agent_description()) + "</pre></details>"
         "<script>"
         "var form=document.getElementById('upload');"
         "form.addEventListener('submit',function(e){"
         "e.preventDefault();"
         "var fd=new FormData(form);"
         "var st=document.getElementById('status');var r=document.getElementById('result');"
         "st.textContent='Uploading…';r.style.display='none';"
         "fetch(form.action,{method:'POST',body:fd})"
         ".then(function(res){return res.json().then(function(d){return {ok:res.ok,data:d};});})"
         ".then(function(o){"
         "if(!o.ok){st.textContent='Error: '+(o.data.error||'upload failed');return;}"
         "var d=o.data;"
         "r.style.display='block';"
         "r.innerHTML='<div class=\"row\"><span class=\"lbl\">URL</span><br><a href=\"'+d.url+'\" target=\"_blank\">'+d.url+'</a></div>'"
         "+'<div class=\"row\"><span class=\"lbl\">Name</span> '+d.name+'</div>'"
         "+'<div class=\"row\"><span class=\"lbl\">Size</span> '+d.size+' B</div>'"
         "+'<div class=\"row\"><span class=\"lbl\">Type</span> '+d.content_type+'</div>'"
         "+'<div class=\"row\"><span class=\"lbl\">Expires</span> '+d.expires_at+'</div>'"
         "+'<div class=\"row\"><span class=\"lbl\">Download</span> <a href=\"'+d.url+'?download=1\">force download</a></div>';"
         "st.textContent='';"
         "form.reset();"
         "})"
         ".catch(function(err){st.textContent='Error: '+err;});"
         "});"
         "document.getElementById('cpBtn').addEventListener('click',function(){"
         "fetch('" + PREFIX + "/write_for_agents').then(function(res){return res.text();}).then(function(t){"
         "navigator.clipboard.writeText(t).then(function(){"
         "var b=document.getElementById('cpBtn');var old=b.innerHTML;"
         "b.innerHTML='<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"24\" height=\"24\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M20 6 9 17l-5-5\"/></svg> copied';"
         "setTimeout(function(){b.innerHTML=old;},1500);"
         "});"
         "});"
         "});"
         "</script>"
         "</body></html>")
    self._send(200, h, "text/html")


# patch _index into Handler
Handler._index = _index

if __name__ == "__main__":
    sweep()
    print(f"store on :{PORT} root={ROOT} ttl={TTL_HOURS}h")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
