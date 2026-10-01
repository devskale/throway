#!/usr/bin/env python3
"""browser-probe.py — ein Helfer statt fünf Wegwerf-CDP-Scripts.

Retro 2026-10-01 (P2): jede Browser-Verifikation baute State-File-Parsing,
Frame-Tree-Walk, Isolated-World-Attach und Device-Metrics von Hand neu
(cdp.py, vp.py, livecheck.py, diag24.py, browser_thumb.py + Proxies).
Dieses Script bündelt den wiederkehrenden Flow gegen den rodney-Chrome.

Voraussetzung: `rodney start` (+ optional `rodney open <url>`), State in
~/.rodney/state.json. Webs Paket `websockets` (python3 -m pip --user).

Subcommands:
  emulate W H [DPR] [--mobile]     Device-Metrics auf dem aktiven Tab setzen
  open URL [--emulate W H [DPR]] [--mobile]
                                   navigieren (mit optionaler Emulation)
  eval JS [--frame URLSUBSTR]      JS auswerten; --frame: im iframe, dessen
                                   URL URLSUBSTR enthaelt (Isolated World,
                                   sieht das DOM, nicht Seiten-Globals).
                                   JSON-Ergebnis wird pretty-geprintet.
  marker URLSUBSTR 'AUSDRUCK'      eval-Shortcut: Wahrheitstest im Frame —
                                   exit 0 wenn truthy. Fuer den CODING_RULES-
                                   Grundsatz "Marker im geladenen DOM
                                   verifizieren, nicht am HTML-Quelltext".
  net URL [--emulate W H [DPR]] [--match SUBSTR] [--wait S]
                                   navigieren und alle Request-URLs, die
                                   SUBSTR enthalten, mit Haeufigkeit
                                   ausgeben (CDP Network, bis --wait still).

Beispiele:
  browser-probe.py emulate 390 844 3 --mobile
  browser-probe.py net http://127.0.0.1:8731/pics/g/x?embed=1 --match thumb=
  browser-probe.py eval "document.querySelectorAll('.grid a').length" --frame pics/g/
  browser-probe.py marker pics/g/ "document.getElementById('lb') !== null"
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter

STATE = os.path.expanduser("~/.rodney/state.json")


def _page_target():
    dbg = json.load(open(STATE))["debug_url"]
    netloc = urllib.parse.urlparse(dbg).netloc
    targets = json.load(urllib.request.urlopen(f"http://{netloc}/json/list"))
    page = next((t for t in targets if t["type"] == "page"), None)
    if page is None:
        # seitenloser Chrome (rodney start ohne open): Target anlegen —
        # genau die Falle, die sonst jede Session aufs Neue kostet.
        # /json/new verlangt PUT (Chrome >= 111) und http:// (nicht ws://).
        req = urllib.request.Request(
            f"http://{netloc}/json/new?about:blank", method="PUT")
        page = json.load(urllib.request.urlopen(req, timeout=10))
    return page, dbg


async def _session(ws_url):
    import websockets
    ws = await websockets.connect(ws_url, max_size=64 * 1024 * 1024)
    n = [0]

    async def cmd(method, params=None, sid=None):
        n[0] += 1
        msg = {"id": n[0], "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        await ws.send(json.dumps(msg))
        while True:
            raw = json.loads(await ws.recv())
            if raw.get("id") == n[0]:
                if "error" in raw:
                    raise RuntimeError(f"{method}: {raw['error']}")
                return raw.get("result", {})

    return ws, cmd


def _metrics(args):
    if not args.emulate:
        return None
    w, h, *rest = args.emulate
    dpr = float(rest[0]) if rest else 1.0
    return {"width": int(w), "height": int(h), "deviceScaleFactor": dpr,
            "mobile": bool(args.mobile), "screenWidth": int(w),
            "screenHeight": int(h)}


async def _find_frame(cmd, sid, substr):
    tree = await cmd("Page.getFrameTree", sid=sid)
    found = {}

    def walk(node):
        if substr in node["frame"].get("url", ""):
            found["f"] = node["frame"]
        for c in node.get("childFrames", []):
            walk(c)
    walk(tree["frameTree"])
    if "f" not in found:
        raise SystemExit(f"browser-probe: kein Frame mit '{substr}'")
    return found["f"]


async def run(args):
    page, _ = _page_target()
    ws, cmd = await _session(page["webSocketDebuggerUrl"])
    try:
        st = await cmd("Target.attachToTarget",
                       {"targetId": page["id"], "flatten": True})
        sid = st["sessionId"]
        metrics = _metrics(args) if hasattr(args, "emulate") else None
        if args.cmd in ("open", "net"):
            await cmd("Page.enable", sid=sid)
            await cmd("Runtime.enable", sid=sid)
            if metrics:
                await cmd("Emulation.setDeviceMetricsOverride",
                          metrics, sid=sid)
            url = args.url
            if args.cmd == "open":
                await cmd("Page.navigate", {"url": url}, sid=sid)
                await asyncio.sleep(float(args.wait))
                print("offen:", url)
                return
            # net: navigieren + Requests mitlesen
            await cmd("Network.enable", sid=sid)
            await cmd("Page.navigate", {"url": url}, sid=sid)
            urls = []
            loop = asyncio.get_event_loop()
            end = loop.time() + float(args.wait)
            while loop.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                ev = json.loads(raw)
                if ev.get("method") == "Network.requestWillBeSent":
                    u = ev["params"]["request"]["url"]
                    if not args.match or args.match in u:
                        urls.append(u)
            for u, c in Counter(urls).most_common():
                print(f"{c:4d}  {u}")
            return
        if args.cmd == "emulate":
            await cmd("Page.enable", sid=sid)
            await cmd("Emulation.setDeviceMetricsOverride", metrics, sid=sid)
            print(f"emuliert: {args.width}x{args.height} @{args.dpr}")
            return
        if args.cmd in ("eval", "marker"):
            await cmd("Page.enable", sid=sid)
            await cmd("Runtime.enable", sid=sid)
            if args.frame:
                f = await _find_frame(cmd, sid, args.frame)
                world = await cmd("Page.createIsolatedWorld", {
                    "frameId": f["id"], "worldName": "probe",
                    "grantUniveralAccess": True}, sid=sid)
                ctx = world["executionContextId"]
            else:
                ctx = None
            r = await cmd("Runtime.evaluate", {
                "expression": args.js if args.cmd == "eval" else
                f"!!({args.js})",
                **({"contextId": ctx} if ctx else {}),
                "returnByValue": True, "awaitPromise": True,
                "userGesture": True}, sid=sid)
            val = r.get("result", {}).get("value")
            if args.cmd == "marker":
                print(json.dumps(val))
                sys.exit(0 if val else 1)
            print(json.dumps(val, indent=1, ensure_ascii=False)
                  if isinstance(val, (dict, list)) else val)
            return
        raise SystemExit(f"unbekanntes Subcommand: {args.cmd}")
    finally:
        await ws.close()


def main():
    ap = argparse.ArgumentParser(prog="browser-probe",
                                 description=__doc__.split("Subcommands:")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("emulate")
    p.add_argument("width", type=int)
    p.add_argument("height", type=int)
    p.add_argument("dpr", nargs="?", type=float, default=1.0)
    p.add_argument("--mobile", action="store_true")

    def add_nav(p):
        p.add_argument("url")
        p.add_argument("--emulate", nargs="+", metavar=("W", "H [DPR]"))
        p.add_argument("--mobile", action="store_true")
        p.add_argument("--wait", type=float, default=5.0,
                       help="Sekunden nach Navigation (default 5)")

    p = sub.add_parser("open")
    add_nav(p)
    p = sub.add_parser("net")
    add_nav(p)
    p.add_argument("--match", default=None, help="nur URLs mit SUBSTR")

    p = sub.add_parser("eval")
    p.add_argument("js")
    p.add_argument("--frame", default=None,
                   help="im Frame, dessen URL SUBSTR enthaelt")

    p = sub.add_parser("marker")
    p.add_argument("frame")
    p.add_argument("js")
    p.add_argument("--wait", type=float, default=3.0)

    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()