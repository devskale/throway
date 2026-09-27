"""Test harness: spawn the real store.py as a subprocess against a tmp root.

Behavior-level tests — they survive any internal refactor (RFQ TR-7 /
modularization step 1). The server is a *copy* in a tmpdir so stats.json
and RELEASES.md side effects never touch the repo.
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects in tests — we assert on the raw status."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_OPENER = urllib.request.build_opener(_NoRedirect)

import sys as _sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in _sys.path:
    _sys.path.insert(0, REPO)   # makes `throway` (mdrender) importable in tests
VENV_PY = os.path.join(REPO, ".venv", "bin", "python")
SRC_PY = os.path.join(REPO, "store.py")
SRC_PKG = os.path.join(REPO, "throway")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def multipart(files, field="f"):
    """Build a multipart/form-data body. files = [(filename, bytes, ctype)]."""
    b = "----pytestboundary42"
    out = []
    for name, data, ctype in files:
        out.append(
            f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
            + data
            + b"\r\n"
        )
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def urlencode(form):
    """application/x-www-form-urlencoded body from a dict."""
    from urllib.parse import quote
    return "&".join(f"{quote(str(k), safe='')}={quote(str(v), safe='')}"
                    for k, v in form.items()).encode()


class Server:
    def __init__(self, tmpdir, env_extra=None, stdout_path=None):
        app = os.path.join(tmpdir, "app")
        os.makedirs(app, exist_ok=True)
        shutil.copy(SRC_PY, app)
        if os.path.isdir(SRC_PKG):
            shutil.copytree(SRC_PKG, os.path.join(app, "throway"))
        self.root = os.path.join(tmpdir, "root")
        os.makedirs(self.root, exist_ok=True)
        self.port = _free_port()
        env = dict(os.environ)
        env.update({
            "THROWAWAY_ROOT": self.root,
            "STORE_PORT": str(self.port),
            "THROWAWAY_PICS_ADMIN_TOKEN": "testtoken123",
        })
        env.update(env_extra or {})
        self.proc = subprocess.Popen(
            [VENV_PY, os.path.join(app, "store.py")],
            env=env, cwd=app,
            stdout=open(stdout_path or os.path.join(tmpdir, "server.log"), "wb"),
            stderr=subprocess.STDOUT,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                st, _, _ = self.get("/api")
                if st == 200:
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"server died at startup — see {tmpdir}/server.log")
            time.sleep(0.1)
        raise RuntimeError("server did not come up in 20s")

    def request(self, method, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with _OPENER.open(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, data=None, headers=None):
        return self.request("POST", path, data=data, headers=headers)

    def put(self, path, data=None, headers=None):
        return self.request("PUT", path, data=data, headers=headers)

    def patch(self, path, data=None, headers=None):
        return self.request("PATCH", path, data=data, headers=headers)

    def delete(self, path, headers=None):
        return self.request("DELETE", path, headers=headers)

    def jpost(self, path, data=None, headers=None):
        st, hd, body = self.post(path, data=data, headers=headers)
        return st, json.loads(body) if body else {}

    def upload_raw(self, data, name=None, qs=""):
        path = "/"
        if name or qs:
            path = "/?" + "&".join(
                ([f"name={name}"] if name else []) + ([qs] if qs else []))
        return self.jpost(path, data=data)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
