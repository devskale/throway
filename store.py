#!/usr/bin/env python3
"""Disposable throwaway store — upload, get a 4-hour URL back.

- Upload a thing -> stored under a random ID -> returns a URL
- URL valid for TTL_HOURS (default 4) — files expire & auto-delete
- Images render inline in browser (viewer); non-images download
  ?download=1 forces a download for any file
- Rolling THROW_POOL_SIZE pool (oldest evicted first)
- Max file MAX_FILE, no auth, RATE_LIMIT req/min per IP
"""
import os
import re
import json
import time
import secrets
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
PUBLIC_BASE = "https://lubu.skale.dev:8001/throway"
PREFIX = "/throway"
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

def total_size():
    return sum(os.path.getsize(os.path.join(ROOT, f))
               for f in os.listdir(ROOT)
               if os.path.isfile(os.path.join(ROOT, f))
               and not f.endswith(".meta"))

def evict(target):
    """Delete oldest files (by mtime) until total data size <= target."""
    while total_size() > target:
        files = [os.path.join(ROOT, f) for f in os.listdir(ROOT)
                 if os.path.isfile(os.path.join(ROOT, f))
                 and not f.endswith(".meta")]
        if not files:
            return
        oldest = min(files, key=os.path.getmtime)
        _remove(oldest)

def _remove(fp):
    try:
        os.remove(fp)
    except OSError:
        pass
    # remove matching .meta
    mp = fp + ".meta"
    if os.path.isfile(mp):
        try:
            os.remove(mp)
        except OSError:
            pass

def sweep():
    """Delete expired files."""
    now = time.time()
    for f in os.listdir(ROOT):
        if f.endswith(".meta"):
            continue
        fp = os.path.join(ROOT, f)
        if not os.path.isfile(fp):
            continue
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

def _safe_name(name):
    """Reduce a user filename to a safe basename for Content-Disposition."""
    if not name:
        return None
    name = os.path.basename(name.replace("\\", "/"))
    # strip control chars and quotes that could break the header
    name = re.sub(r'[\r\n\"\x00-\x1f]', "", name).strip()
    return name or None

def _parse_multipart(payload, content_type):
    """Extract (filename, data, ctype) from a multipart/form-data body."""
    import email
    import email.parser
    try:
        msg = email.parser.BytesParser().parsebytes(payload)
    except Exception:
        return None, None, None
    if not msg.is_multipart():
        # fallback: manual boundary split
        m = re.search(r'boundary="?([^";]+)"?', content_type)
        if not m:
            return None, None, None
        boundary = m.group(1).encode()
        parts = payload.split(b"--" + boundary)
        for part in parts:
            if b"filename=" in part[:200]:
                header, _, body = part.partition(b"\r\n\r\n")
                hm = re.search(r'filename="([^"]*)"', header.decode("latin1"))
                name = hm.group(1) if hm else None
                ctype = re.search(r'Content-Type:\s*(\S+)', header.decode("latin1"), re.I)
                return name, body.rstrip(b"\r\n--"), (ctype.group(1) if ctype else "application/octet-stream")
        return None, None, None
    for part in msg.get_payload():
        fn = part.get_filename()
        if fn:
            data = part.get_payload(decode=True) or b""
            return fn, data, part.get_content_type() or "application/octet-stream"
    return None, None, None

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
        fid = path.lstrip("/").split("/")[0]
        if not fid or fid.endswith(".meta"):
            return self._send(404, "not found\n")
        fp = _id_path(fid)
        if not os.path.isfile(fp):
            return self._send(404, "not found\n")
        # expiry check
        mp = fp + ".meta"
        expires = None
        if os.path.isfile(mp):
            try:
                expires = json.load(open(mp)).get("expires")
            except Exception:
                pass
        if expires is None:
            expires = os.path.getmtime(fp) + TTL_HOURS * 3600
        if expires < time.time():
            _remove(fp)
            return self._send(404, "expired\n")

        # meta content-type / original name
        ctype = "application/octet-stream"
        orig = None
        if os.path.isfile(mp):
            try:
                m = json.load(open(mp))
                ctype = m.get("ctype") or ctype
                orig = m.get("name")
            except Exception:
                pass
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        force_dl = "download=1" in query
        is_image = ctype.startswith("image/")
        size = os.path.getsize(fp)
        if force_dl or not is_image:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            fname = _safe_name(orig) or fid
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            self.end_headers()
        else:
            # viewer: inline image
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "inline")
            self.end_headers()
        with open(fp, "rb") as f:
            while c := f.read(65536):
                self.wfile.write(c)

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
            name, data, ctype = _parse_multipart(payload, ctype)
            if data is None:
                return self._send(400, json.dumps({"error": "no file part in multipart body"}), "application/json")
            if name:
                name_hint = _safe_name(name)[:128]
            return self._store(data, name_hint or None, ctype)

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

    def do_DELETE(self):
        if not self._rate(): return
        fid = self.path.lstrip("/").split("/")[0]
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

2) DOWNLOAD / VIEW a file:
   GET {PUBLIC_BASE}/<id>
   Images render inline in a browser; other files download.
   Append ?download=1 to force a download of any file.

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
                "download": {"method": "GET", "url": PUBLIC_BASE + "/<id>", "note": "images render inline; append ?download=1 to force download"},
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
         "<input type='file' name='f' required><input type='submit' value='Upload'></form>"
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
