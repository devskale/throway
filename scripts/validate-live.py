#!/usr/bin/env python3
"""validate-live.py — Live-Contract-Suite (hard-validate, Retro 2026-10-02).

Fährt einen adversarialen Verhaltens-Check gegen einen laufenden throway:
alles, was die Unit-Suite nicht sieht (HTTP-Ecken, Auth-Grenzen, Write-
Gates an ALLEN Routen, Parallel-Hammer, Create/Delete-Symmetrie). Diese
Suite fand 5 echte Bugs, die 130 grüne Unit-Tests nicht sahen.

Modi:
  python scripts/validate-live.py                # spawnt eigenen Server
                                                 # (tmp root, Test-Tokens)
  python scripts/validate-live.py --base URL     # gegen laufende Instanz
                                                 # (Tokens via THROWAWAY_RETAIN_TOKENS,
                                                 #  Komma-Liste; Default: 1 Test-Token
                                                 #  nur im Spawner-Modus)

Exit 0 = alles grün. Cleanup läuft via atexit (auch bei Crash); Objekt-
namen sind pro Run eindeutig (create-or-get revealt write_token nie wieder).
"""
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- Setup -----------------------------------------------------------------

RESULTS = []
CLEANUP_FILES = []
CLEANUP_DIRS = []
WT = None          # write_token des Lock-Tests (Cleanup braucht es)


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  [{detail}]"))


def parse_args():
    base, tokens = None, os.environ.get("THROWAWAY_RETAIN_TOKENS", "")
    argv = sys.argv[1:]
    if "--base" in argv:
        base = argv[argv.index("--base") + 1].rstrip("/")
    return base, [t.strip() for t in tokens.split(",") if t.strip()]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def spawn_server(tokens):
    """store.py als Subprozess in einem tmpdir (Copy wie conftest —
    stats.json/RELEASES-Nebeneffekte verlassen nie das Repo)."""
    tmp = tempfile.mkdtemp(prefix="validate-live-")
    app = os.path.join(tmp, "app")
    os.makedirs(app)
    shutil.copy(os.path.join(REPO, "store.py"), app)
    shutil.copytree(os.path.join(REPO, "throway"), os.path.join(app, "throway"))
    env = dict(os.environ)
    env.update({
        "THROWAWAY_ROOT": os.path.join(tmp, "root"),
        "STORE_PORT": str(free_port()),
        "THROWAWAY_RETAIN_TOKEN": ",".join(tokens),
    })
    log = open(os.path.join(tmp, "server.log"), "wb")
    proc = subprocess.Popen([sys.executable, os.path.join(app, "store.py")],
                            env=env, cwd=app, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{env['STORE_PORT']}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api", timeout=2) as r:
                if r.status == 200:
                    return base, proc, log
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"server died — see {tmp}/server.log")
        time.sleep(0.1)
    raise RuntimeError("server did not come up in 20s")


# --- HTTP helper ------------------------------------------------------------

BASE = None
TOK = None
AUTH = None
UA = {"User-Agent": "curl/8.0"}


def req(method, path, data=None, headers=None):
    r = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in {**UA, **(headers or {})}.items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except (urllib.error.URLError, BrokenPipeError, ConnectionResetError):
        # Server lehnt frueh ab (z.B. 413), OHNE den Body zu lesen —
        # der Client bricht dann beim Senden ab. Als "kein Status"
        # durchreichen; Aufrufer pruefen dann auf Ablehnung + Abwesenheit.
        return None, {}, b""


def jreq(method, path, data=None, headers=None):
    st, hd, raw = req(method, path, data, headers)
    try:
        return st, hd, json.loads(raw)
    except Exception:
        return st, hd, {}


def multipart(files):
    b = "----vl42"
    out = []
    for n, d, c in files:
        out.append((f"--{b}\r\nContent-Disposition: form-data; name=\"f\"; "
                    f"filename=\"{n}\"\r\nContent-Type: {c}\r\n\r\n").encode()
                   + d + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


SFX = str(time.time())[-6:]


# --- Check-Gruppen -----------------------------------------------------------

def check_surface(expect_version):
    st, _, spec = jreq("GET", "/api")
    check("live Version == Repo-VERSION", spec.get("version") == expect_version,
          spec.get("version"))
    check("api retention_token=true", spec.get("retention_token") is True)
    eps = spec.get("endpoints", {})
    check("api Endpunkte retain+show_dir", "retain" in eps and "show_dir" in eps)
    st, _, raw = req("GET", "/help/retention")
    check("/help/retention: SHOW-DIRS Sektion", b"SHOW-DIRS" in raw)
    st, _, raw = req("GET", "/write_for_agents")
    check("Agent-Guide erwähnt retention", b"retention" in raw.lower())


def check_retention_uploads():
    st, hd, d = jreq("POST", f"/?name=v{SFX}-keep.txt", b"retained", AUTH)
    CLEANUP_FILES.append(d.get("id"))
    check("Token-Upload: expires_at null", st == 200 and d.get("expires_at") is None)
    check("Token-Upload: persistence.retention=indefinite",
          d.get("persistence", {}).get("retention") == "indefinite")
    check("Token-Upload: kein x-expires", "x-expires" not in hd)
    st, _, _ = req("GET", f"/{d.get('id')}")
    check("Retained public GET 200", st == 200)

    st, _, d = jreq("POST", f"/?name=v{SFX}-q.txt&token={TOK}", b"x")
    CLEANUP_FILES.append(d.get("id"))
    check("Query-Token Upload retained", st == 200 and d.get("expires_at") is None)

    st, hd, d = jreq("POST", f"/?name=v{SFX}-n.txt", b"x")
    CLEANUP_FILES.append(d.get("id"))
    check("Ohne Token: expires_at gesetzt", st == 200 and d.get("expires_at") is not None)
    check("Ohne Token: x-expires=14400", hd.get("x-expires") == "14400")

    st, _, _ = req("POST", "/", b"x", {"Authorization": "Bearer falsch"})
    check("Falscher Token: 401", st == 401)
    st, _, _ = req("POST", "/?retain=1", b"x")
    check("retain=1 ohne Token: 401", st == 401)
    st, _, _ = req("POST", f"/?name=v{SFX}-o.txt&once=1", b"x", AUTH)
    check("once=1 + Token: 400", st == 400)
    st, _, d = jreq("POST", f"/?name=v{SFX}-lb.txt", b"x",
                    {"Authorization": f"bearer {TOK}"})
    CLEANUP_FILES.append(d.get("id"))
    check("kleingeschriebenes bearer ok", st == 200 and d.get("expires_at") is None)
    st, _, d = jreq("POST", f"/?name=v{SFX}-ba.txt", b"x",
                    {"Authorization": "Basic dXNlcjpwYXNz"})
    CLEANUP_FILES.append(d.get("id"))
    check("Basic-auth = kein Retain (Wegwerf)",
          st == 200 and d.get("expires_at") is not None)
    st, _, _ = req("POST", "/?name=big.bin", b"x" * (5 * 1024 * 1024 + 1), AUTH)
    _, _, brow = req("GET", "/browse")
    check("uebergross mit Token: abgewiesen (413/Verbindungsabbruch)",
          st in (413, None) and b"big.bin" not in brow, st)


def check_retention_writes():
    _, _, d = jreq("POST", f"/?name=v{SFX}-f.txt", b"flip")
    fid = d.get("id")
    CLEANUP_FILES.append(fid)
    st, _, d = jreq("POST", f"/{fid}?retain=1", b"", AUTH)
    check("Flip Bestandsfile: indefinite",
          st == 200 and d.get("retention") == "indefinite")
    for m in ("PUT", "PATCH"):
        st, _, _ = req(m, f"/{fid}", b"hack")
        check(f"{m} auf retained ohne Token: 401", st == 401)
    st, _, _ = req("POST", f"/{fid}?tag=x", b"")
    check("Tags auf retained ohne Token: 401", st == 401)
    st, _, _ = req("DELETE", f"/{fid}")
    check("DELETE retained ohne Token: 401", st == 401)
    for reserved in ("d", "pics"):
        st, _, _ = req("POST", f"/{reserved}?retain=1", b"", AUTH)
        check(f"Flip auf /{reserved} abgewiesen (404/400)", st in (400, 404), st)


def check_bundles():
    body, ct = multipart([("a.txt", b"aaa", "text/plain"), ("b.css", b"b{}", "text/css")])
    st, _, d = jreq("POST", "/", body, {**AUTH, "Content-Type": ct})
    bid = d.get("id")
    check("Bundle mit Token retained", st == 200 and d.get("expires_at") is None)
    st, _, _ = req("DELETE", f"/{bid}")
    check("DELETE retained Bundle ohne Token: 401", st == 401)
    st, _, _ = req("DELETE", f"/{bid}", headers=AUTH)
    check("DELETE retained Bundle mit Token: 200", st == 200)
    body, ct = multipart([("a.txt", b"aaa", "text/plain"), ("b.css", b"b{}", "text/css")])
    st, _, d = jreq("POST", "/", body, {"Content-Type": ct})
    bid = d.get("id")
    st, _, _ = req("DELETE", f"/{bid}")
    check("DELETE normales Bundle: 200 (Create/Delete-Symmetrie)", st == 200)


def check_share_implies_retention():
    req("POST", f"/?share=v{SFX}shr&name=one.txt", b"1")
    CLEANUP_DIRS.append(f"v{SFX}shr")
    st, _, d = jreq("POST", f"/?share=v{SFX}shr&name=two.txt", b"2", AUTH)
    check("Share + Token-Add impliziert Retention",
          st == 200 and d.get("expires_at") is None)
    st, _, _ = req("POST", f"/?share=v{SFX}shr&name=three.txt", b"3")
    check("Share danach write-gated (401)", st == 401)


def check_show_dirs():
    st, _, d = jreq("POST", f"/?dir=1&show=1&name=v{SFX}show", b"", AUTH)
    CLEANUP_DIRS.append(f"v{SFX}show")
    check("Show-Dir Create: open+indefinite",
          st == 200 and d.get("open") is True and d.get("expires_at") is None)
    body, ct = multipart([("pub.txt", b"public", "text/plain")])
    st, _, _ = req("POST", f"/d/v{SFX}show", body, {"Content-Type": ct})
    check("Show: oeffentlicher Add 200", st == 200)
    st, _, _ = req("PUT", f"/d/v{SFX}show/pub.txt", b"stranger")
    check("Show: oeffentlicher Edit 200", st == 200)
    st, _, _ = req("DELETE", f"/d/v{SFX}show/pub.txt")
    check("Show: File-Delete offen 200", st == 200)
    body, ct = multipart([("img.png", b"\x89PNG", "image/png")])
    req("POST", f"/d/v{SFX}show", body, {"Content-Type": ct})
    st, _, _ = req("PUT", f"/d/v{SFX}show/img.png", b"fake")
    check("PUT auf Binary im Dir: 400", st == 400)
    st, _, _ = req("DELETE", f"/d/v{SFX}show")
    check("Show: Whole-Delete ohne Token 401", st == 401)

    # write_token schlägt open
    st, _, d = jreq("POST", f"/?dir=1&show=1&write=1&name=v{SFX}lock", b"", AUTH)
    CLEANUP_DIRS.append(f"v{SFX}lock")
    global WT
    WT = d.get("write_token")
    body, ct = multipart([("x.txt", b"x", "text/plain")])
    st, _, _ = req("POST", f"/d/v{SFX}lock", body, {"Content-Type": ct})
    check("write_token schlägt open (401)", st == 401 and WT)
    st, _, _ = req("POST", f"/d/v{SFX}lock", body,
                   {"Content-Type": ct, "X-Throway-Write": WT})
    check("write_token + open: mit Token 200", st == 200)

    # Flip Bestandsdir + show vor retain
    req("POST", f"/?dir=1&name=v{SFX}norm", b"")
    CLEANUP_DIRS.append(f"v{SFX}norm")
    st, _, d = jreq("POST", f"/d/v{SFX}norm?show=1&retain=1", b"", AUTH)
    check("Flip Bestandsdir zu Show (show>retain)",
          st == 200 and d.get("open") is True and d.get("expires_at") is None)


def check_parallel_hammer():
    req("POST", f"/?dir=1&show=1&name=v{SFX}hammer", b"", AUTH)
    CLEANUP_DIRS.append(f"v{SFX}hammer")
    codes, dirflags = [], []

    def add(i):
        body, ct = multipart([(f"f{i}.txt", f"c{i}".encode(), "text/plain")])
        st, _, d = req("POST", f"/d/v{SFX}hammer", body, {"Content-Type": ct})
        codes.append(st)
        dirflags.append(d.get("dir"))

    ts = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("8 parallele Adds: alle 200 UND dir-Antwort",
          codes == [200] * 8 and all(dirflags), str(codes))
    st, _, d = jreq("GET", f"/d/v{SFX}hammer")
    check("8/8 Files nach Hammer (kein Data-Loss)", len(d.get("files", [])) == 8)


def check_squat_protection():
    """Angreifer belegt share-Namen zuerst; Owner-Token-Write flippt+gated."""
    req("POST", f"/?share=v{SFX}squat&name=attacker.txt", b"attacker")
    CLEANUP_DIRS.append(f"v{SFX}squat")
    st, _, d = jreq("POST", f"/?share=v{SFX}squat&name=owner.txt", b"owner", AUTH)
    check("Squat: Owner-Token-Write retaint das Dir",
          st == 200 and d.get("expires_at") is None)
    st, _, _ = req("POST", f"/?share=v{SFX}squat&name=attacker2.txt", b"a2")
    check("Squat: Angreifer danach 401", st == 401)


def check_names_escaping_idem():
    st, _, d = jreq("POST", "/?name=../../etc/passwd.txt", b"x", AUTH)
    CLEANUP_FILES.append(d.get("id"))
    check("traversal-name neutralisiert",
          st == 200 and "/" not in d.get("name", "x/y"), d.get("name"))
    st, _, d = jreq("POST", "/?name=evil.meta", b"x", AUTH)
    CLEANUP_FILES.append(d.get("id"))
    check(".meta-name neutralisiert", st == 200 and d.get("name") != "evil.meta")
    st, _, d = jreq("POST", f"/?name=v{SFX}<b>x</b>.txt", b"evil", AUTH)
    CLEANUP_FILES.append(d.get("id"))
    st, _, raw = req("GET", "/browse")
    check("HTML im Dateinamen escaped",
          not (b"<b>x</b>.txt" in raw and b"&lt;b&gt;x" not in raw))
    h = {**AUTH, "Idempotency-Key": f"vl-{SFX}"}
    s1, _, d1 = jreq("POST", f"/?name=v{SFX}-idem.txt", b"z", h)
    s2, _, d2 = jreq("POST", f"/?name=v{SFX}-idem.txt", b"z", h)
    CLEANUP_FILES.append(d1.get("id"))
    check("Idempotenz-Replay",
          s1 == 200 and s2 == 200 and d1.get("id") == d2.get("id")
          and d2.get("idempotent_replay") is True)


# --- Cleanup -----------------------------------------------------------------

def cleanup():
    for i in CLEANUP_FILES:
        if i:
            try:
                req("DELETE", f"/{i}", headers=AUTH)
            except Exception:
                pass
    for k in CLEANUP_DIRS:
        try:
            hdrs = dict(AUTH)
            if k.endswith("lock") and WT:
                hdrs["X-Throway-Write"] = WT
            req("DELETE", f"/d/{k}", headers=hdrs)
        except Exception:
            pass


def main():
    global BASE, TOK, AUTH
    base, tokens = parse_args()
    with open(os.path.join(REPO, "store.py")) as f:
        expect_version = [l for l in f if l.startswith("VERSION")][0].split('"')[1]
    proc = log = None
    if base:
        BASE = base
        if not tokens:
            print("FAIL  --base ohne THROWAWAY_RETAIN_TOKENS (Komma-Liste) gesetzt")
            sys.exit(2)
    else:
        tokens = tokens or ["validate-live-tok1", "validate-live-tok2"]
        BASE, proc, log = spawn_server(tokens)
    TOK = tokens[0]
    AUTH = {"Authorization": f"Bearer {TOK}"}
    atexit.register(cleanup)

    check_surface(expect_version)
    check_retention_uploads()
    check_retention_writes()
    check_bundles()
    check_share_implies_retention()
    check_show_dirs()
    check_parallel_hammer()
    check_squat_protection()
    check_names_escaping_idem()

    # Multi-Token (nur Spawner-Modus checkbar — Liste liegt uns vor)
    if len(tokens) > 1:
        st, _, d = jreq("POST", f"/?name=v{SFX}-mt.txt", b"x",
                        {"Authorization": f"Bearer {tokens[1]}"})
        CLEANUP_FILES.append(d.get("id"))
        check("Multi-Token: zweiter Token gilt", st == 200 and d.get("expires_at") is None)
        st, _, _ = jreq("POST", f"/?name=v{SFX}-mt.txt", b"x",
                        {"Authorization": f"Bearer {tokens[1]}x"})
        check("Multi-Token: Prefix abgelehnt (401)", st == 401)

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} PASS"
          + (f" — {len(fails)} FAIL!" if fails else ""))
    if proc:
        proc.terminate()
        log.close()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
