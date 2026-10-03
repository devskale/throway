#!/usr/bin/env python3
"""Matrix-Batterie: die Ein-Response-Invariante ueber die ganze Routen-
Oberflaeche — jede Methode x Happy/Error-Pfad, roh am Socket:
exakt EINE Statuszeile pro Anfrage + Socket danach gesund (bzw. gezielt
 geschlossen, dann folgefaehig ueber frische Connection).

Entstanden in der Fix-Validierung zu 1.45.5 (Wurzel: _send gab nie
True/False zurueck — jede Dir-Add-Antwort bekam ein 404 nachgeschoben)
und gehaertet in Runde 4 (1.45.6: Transfer-Encoding: chunked).

Selbststaendig: spawnt eigenen store.py (Port 8150, 429-Subserver 8151,
hohes Rate-Limit), raeumt via atexit auf. Exit 0 = alles PASS.

    python scripts/validate-matrix.py [--port N]
"""
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8150
PORT2 = 8151
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])
    PORT2 = PORT + 1
BASE = f"http://127.0.0.1:{PORT}"
AUTH = {"Authorization": "Bearer vt"}
UA = {"User-Agent": "curl/8.0"}
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name, "" if cond else detail)


def start_server(port, root, rate_limit="500"):
    env = dict(os.environ, THROWAWAY_ROOT=root, STORE_PORT=str(port),
               THROWAWAY_RETAIN_TOKEN="vt", THROWAWAY_RATE_LIMIT=rate_limit)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "store.py")],
                            env=env, stdout=open(root + "/log", "wb"),
                            stderr=subprocess.STDOUT)
    time.sleep(1.2)
    return proc


def up(path, data=b"", headers=None, method="POST"):
    r = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in {**UA, **(headers or {})}.items():
        r.add_header(k, v)
    return json.loads(urllib.request.urlopen(r).read())


def raw(port, req_bytes, follow=True):
    """Eine Anfrage roh senden; antwort + Folgerequest-Gesundheit liefern."""
    s = socket.create_connection(("127.0.0.1", port))
    s.send(req_bytes)
    time.sleep(0.3)
    s.settimeout(1.5)
    d = b""
    try:
        while True:
            ch = s.recv(8192)
            if not ch:
                break
            d += ch
    except socket.timeout:
        pass
    follow_bytes = b""
    if follow:
        try:
            s.send(b"GET /api HTTP/1.1\r\nHost: x\r\n\r\n")
            s.settimeout(1.5)
            follow_bytes = s.recv(65536)
        except Exception:
            pass
        if not follow_bytes:
            # close ist ok — aber eine frische Connection muss funktionieren
            try:
                s2 = socket.create_connection(("127.0.0.1", port))
                s2.send(b"GET /api HTTP/1.1\r\nHost: x\r\n\r\n")
                s2.settimeout(1.5)
                follow_bytes = s2.recv(65536)
                s2.close()
            except Exception:
                follow_bytes = b"<closed>"
    s.close()
    return d, follow_bytes


def req(method, path, body=None, ctype=None, expect_first=None):
    h = f"{method} {path} HTTP/1.1\r\nHost: x\r\n"
    if ctype:
        h += f"Content-Type: {ctype}\r\n"
    if body is not None:
        h += f"Content-Length: {len(body)}\r\n"
    r = (h + "\r\n").encode() + (body or b"")
    d, follow = raw(PORT, r)
    lines = d.count(b"HTTP/1.1")
    first = d.split(b"\r\n", 1)[0].decode(errors="replace")
    ok_resp = lines == 1 and (expect_first is None or first.startswith(expect_first))
    ok_follow = follow.startswith(b"HTTP/1.1 200") if follow else True
    return ok_resp and ok_follow, f"lines={lines} first={first!r} follow={follow[:30]!r}"


def main():
    root = tempfile.mkdtemp(prefix="tx-matrix-")
    proc = start_server(PORT, root)
    atexit.register(lambda: (proc.terminate(), shutil.rmtree(root, ignore_errors=True)))

    # Fixtures
    f_txt = up("/?name=m.txt", b"hello")
    f_ret = up("/?name=mr.txt", b"retained", AUTH)
    f_bin = up("/?name=m.bin", b"\x89PNG")
    up("/?dir=1&name=mn")
    up("/?dir=1&name=mr", headers=AUTH)
    f_md = up("/?name=d2.md", b"# x", AUTH)

    MP = (b"--x\r\nContent-Disposition: form-data; name=\"f\"; filename=\"n.txt\"\r\n"
          b"Content-Type: text/plain\r\n\r\nhi\r\n--x--\r\n")
    CT = "multipart/form-data; boundary=x"

    matrix = [
        ("GET /api",                 req("GET", "/api", expect_first="HTTP/1.1 200")),
        ("GET file",                 req("GET", f"/{f_txt['id']}", expect_first="HTTP/1.1 200")),
        ("GET fehlt (404)",          req("GET", "/deadbeef0000000f", expect_first="HTTP/1.1 404")),
        ("GET dir listing",          req("GET", "/d/mn", expect_first="HTTP/1.1 200")),
        ("GET dir zip",              req("GET", "/d/mn?zip=1", expect_first="HTTP/1.1 200")),
        ("GET dir history",          req("GET", "/d/mn/history", expect_first="HTTP/1.1 200")),
        ("GET retained dir zip",     req("GET", "/d/mr?zip=1", expect_first="HTTP/1.1 200")),
        ("GET markdown retained",    req("GET", f"/{f_md['id']}", expect_first="HTTP/1.1 200")),
        ("HEAD /api",                req("HEAD", "/api", expect_first="HTTP/1.1 200")),
        ("POST upload",              req("POST", "/?name=h.txt", b"x", "text/plain", "HTTP/1.1 200")),
        ("POST retain ohne Token",   req("POST", "/?retain=1", b"x", "text/plain", "HTTP/1.1 401")),
        ("POST once ohne Token (legal, 200)",
         req("POST", "/?once=1", b"x", "text/plain", "HTTP/1.1 200")),
        ("POST dir add",             req("POST", "/d/mn", MP, CT, "HTTP/1.1 200")),
        ("POST dir add fehlt",       req("POST", "/d/gibtsnicht", MP, CT, "HTTP/1.1 404")),
        ("POST dir non-multipart",   req("POST", "/d/mn", b"Y" * 30, "text/plain", "HTTP/1.1 400")),
        ("POST tags retained 401",   req("POST", f"/{f_ret['id']}?tag=x", b"Z" * 20,
                                         "text/plain", "HTTP/1.1 401")),
        ("POST flip ohne Token 401", req("POST", f"/{f_txt['id']}?retain=1", b"",
                                         "text/plain", "HTTP/1.1 401")),
        ("PUT text",                 req("PUT", f"/{f_txt['id']}", b"neu", "text/plain", "HTTP/1.1 200")),
        ("PUT binary 400",           req("PUT", f"/{f_bin['id']}", b"X" * 40, "text/plain", "HTTP/1.1 400")),
        ("PUT retained 401",         req("PUT", f"/{f_ret['id']}", b"h", "text/plain", "HTTP/1.1 401")),
        ("PUT fehlt 404",            req("PUT", "/deadbeef0000000f", b"x", "text/plain", "HTTP/1.1 404")),
        ("PATCH text",               req("PATCH", f"/{f_txt['id']}", b"+more", "text/plain", "HTTP/1.1 200")),
        ("PATCH binary 400",         req("PATCH", f"/{f_bin['id']}", b"Y" * 30, "text/plain", "HTTP/1.1 400")),
        ("DELETE normal",            req("DELETE", f"/{f_bin['id']}", expect_first="HTTP/1.1 200")),
        ("DELETE retained 401",      req("DELETE", f"/{f_ret['id']}", expect_first="HTTP/1.1 401")),
        ("chunked upload-Pfad 411",
         (lambda d, f: (d.count(b"HTTP/1.1") == 1
                        and d.startswith(b"HTTP/1.1 411")
                        and (f.startswith(b"HTTP/1.1 200") or f == b""),
          f"{d[:80]!r} {f[:40]!r}"))(
             *raw(PORT, b"POST /?name=c.txt HTTP/1.1\r\nHost: x\r\n"
                        b"Content-Type: text/plain\r\nTransfer-Encoding: chunked\r\n"
                        b"\r\n4\r\nWiki\r\n0\r\n\r\n"))),
    ]
    for name, outcome in matrix:
        ok, det = outcome
        check(name, ok, det)

    proc.terminate()

    # 429: eigener Mini-Limit-Server — auch der Rate-Limiter antwortet einfach
    root2 = tempfile.mkdtemp(prefix="tx-matrix429-")
    proc2 = start_server(PORT2, root2, rate_limit="8")
    atexit.register(lambda: (proc2.terminate(), shutil.rmtree(root2, ignore_errors=True)))
    for _ in range(12):
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"http://127.0.0.1:{PORT2}/api")).read()
        except urllib.error.HTTPError:
            pass
    d, _ = raw(PORT2, b"GET /api HTTP/1.1\r\nHost: x\r\n\r\n", follow=False)
    check("429: 1 Statuszeile", d.count(b"HTTP/1.1") == 1, d[:120])
    check("429: ist 429", d.startswith(b"HTTP/1.1 429"), d[:60])
    proc2.terminate()

    fails = [r for r in results if not r]
    print(f"\n{len(results) - len(fails)}/{len(results)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
