#!/usr/bin/env python3
"""Luecken-Batterie: alles, was /api verspricht, aber keine Suite, kein
validate-live und keine Matrix abdeckt — entstanden beim Test-Inventur-
Diff (Runde 5, 1.45.8: once=1&share= war ein stiller Vertragsbruch).

Deckt: ttl=-Clamps (Upload min/max, Dir-TTL), once+share-400,
download=1-Disposition, Zip-INHALTE (Dir-Zip + Bundle-zu-Agent erstmals
entpackt, nicht nur Status geprueft), /d-Filter+sort, Leer-Upload,
PATCH-404, Idempotenz via ?idem=.

Selbststaendig: spawnt eigenen store.py (Port 8152, hohes Rate-Limit),
raeumt via atexit auf. Exit 0 = alles PASS.

    python scripts/validate-gaps.py [--port N]
"""
import atexit
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8152
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])
BASE = f"http://127.0.0.1:{PORT}"
UA = {"User-Agent": "curl/8.0"}
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name, "" if cond else detail)


def call(path, data=None, headers=None, method=None, raw=False):
    r = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in {**UA, **(headers or {})}.items():
        r.add_header(k, v)
    resp = urllib.request.urlopen(r)
    body = resp.read()
    return (resp, body) if raw else json.loads(body)


def dir_expires_in(j):
    from datetime import datetime
    dt = datetime.strptime(j["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    ct = datetime.strptime(j["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    return int((dt - ct).total_seconds())


def main():
    root = tempfile.mkdtemp(prefix="tx-gaps-")
    env = dict(os.environ, THROWAWAY_ROOT=root, STORE_PORT=str(PORT),
               THROWAWAY_RATE_LIMIT="500")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "store.py")],
                            env=env, stdout=open(root + "/log", "wb"),
                            stderr=subprocess.STDOUT)
    atexit.register(lambda: (proc.terminate(), shutil.rmtree(root, ignore_errors=True)))
    time.sleep(1.2)

    # 1) ttl= clamps (Upload + Dir)
    j = call("/?name=t.txt&ttl=24h", b"x")
    check("ttl=24h -> ~86400s", abs(j["expires_in"] - 86400) < 5, j["expires_in"])
    j = call("/?name=t.txt&ttl=30d", b"x")
    check("ttl=30d -> clamp 14d", j["expires_in"] == 1209600, j["expires_in"])
    j = call("/?name=t.txt&ttl=1h", b"x")
    check("ttl=1h -> clamp min 4h", j["expires_in"] == 14400, j["expires_in"])
    j = call("/?dir=1&name=dt&ttl=2d", b"")
    e = dir_expires_in(j)
    check("Dir ttl=2d -> 172800", e == 172800, (e, sorted(j)))
    j = call("/?dir=1&name=dt2&ttl=100d", b"")
    e = dir_expires_in(j)
    check("Dir ttl=100d -> clamp max", e == 1209600, (e, sorted(j)))

    # 2) once=1 + share -> 400 (1.45.8: war still akzeptiert, once ignoriert)
    try:
        call("/?once=1&share=oncekey", b"x")
        check("once+share abgelehnt", False, "ging durch")
    except urllib.error.HTTPError as e:
        check("once+share abgelehnt", e.code in (400, 404), e.code)

    # 3) ?download=1 -> Attachment-Disposition
    _, body = call("/?name=d.bin", b"\x00\x01", raw=True)
    fid = json.loads(body)["id"]
    resp, _ = call(f"/{fid}?download=1", raw=True)
    cd = resp.headers.get("Content-Disposition", "")
    check("download=1 -> attachment", "attachment" in cd, cd)

    # 4) Zip-Inhalte wirklich entpackbar UND korrekt
    call("/?dir=1&name=zp", b"")
    MP = (b"--z\r\nContent-Disposition: form-data; name=\"f\"; filename=\"a.txt\"\r\n"
          b"Content-Type: text/plain\r\n\r\nINHALT-A\r\n--z\r\n"
          b"Content-Disposition: form-data; name=\"f\"; filename=\"b.txt\"\r\n"
          b"Content-Type: text/plain\r\n\r\nINHALT-B\r\n--z--\r\n")
    r = urllib.request.Request(BASE + "/d/zp", data=MP, method="POST")
    r.add_header("User-Agent", "curl/8.0")
    r.add_header("Content-Type", "multipart/form-data; boundary=z")
    urllib.request.urlopen(r).read()
    r = urllib.request.Request(BASE + "/d/zp?zip=1")
    r.add_header("User-Agent", "curl/8.0")
    zf = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(r).read()))
    names = sorted(zf.namelist())
    check("Dir-Zip enthaelt a+b", names == ["a.txt", "b.txt"], names)
    check("Dir-Zip Inhalt korrekt",
          zf.read("a.txt") == b"INHALT-A" and zf.read("b.txt") == b"INHALT-B")

    r = urllib.request.Request(BASE + "/", data=MP, method="POST")
    r.add_header("User-Agent", "curl/8.0")
    r.add_header("Content-Type", "multipart/form-data; boundary=z")
    bid = json.loads(urllib.request.urlopen(r).read())["id"]
    r = urllib.request.Request(BASE + f"/{bid}")
    r.add_header("User-Agent", "curl/8.0")
    zf2 = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(r).read()))
    check("Bundle-Antwort ist valides Zip mit 2 Files",
          sorted(zf2.namelist()) == ["a.txt", "b.txt"], zf2.namelist())

    # 5) GET /d Filter + sort
    call("/?dir=1&name=fa", b"")
    call("/?dir=1&name=fb&listed=1&tag=q1", b"")
    j = call("/d?q=fb")
    check("/d?q= filtert", j["total"] == 1 and j["dirs"][0]["name"] == "fb", j.get("total"))
    j = call("/d?sort=name&order=asc")
    names = [d["name"] for d in j["dirs"]]
    check("/d sort=name asc", names == sorted(names), names)

    # 6) Leer-Upload (0 Bytes)
    j = call("/?name=leer.txt", b"")
    check("Leerdatei 200, size 0", j["size"] == 0, j)
    _, body = call(f"/{j['id']}", raw=True)
    check("Leerdatei lesbar, 0 bytes", body == b"")

    # 7) PATCH auf fehlender Datei -> 404
    try:
        call("/deadbeef0000000f", b"x", method="PATCH")
        check("PATCH fehlt -> 404", False, "ging")
    except urllib.error.HTTPError as e:
        check("PATCH fehlt -> 404", e.code == 404, e.code)

    # 8) Idempotenz via ?idem= Query
    a = call("/?name=i1.txt&idem=kw1", b"data1")
    b2 = call("/?name=i1.txt&idem=kw1", b"data1")
    check("idem= Query: gleiche id",
          a["id"] == b2["id"] and b2.get("idempotent_replay") is True,
          (a["id"], b2["id"]))

    proc.terminate()
    fails = [x for x in results if not x]
    print(f"\n{len(results) - len(fails)}/{len(results)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
