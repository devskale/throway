#!/usr/bin/env python3
"""1.53.4 Rest-Fixes: strikte ttl-Validierung + pics-404s als JSON."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch import apply

def _iap(path, edits):
    cur = open(path, encoding="utf-8").read()
    todo = [(o, n, l) for (o, n, l) in edits if n not in cur]
    if todo:
        apply(path, todo)
    else:
        print(f"{path}: nichts zu tun (bereits gepatcht)")

# --- ttl= unparsebar -> 400 bad_request ------------------------------------
_iap("store.py", [
    # Helper vor _parse_tags
    (
        "def _parse_tags(query_tags):",
        'def _ttl_or_400(handler, qp):\n'
        '    """&ttl= strikt (Retro 1.53.4): unparsebare Werte sind 400\n'
        '    bad_request statt stiller Default. None = Parameter nicht\n'
        '    gegeben (Default gilt). False = 400 bereits gesendet."""\n'
        '    raw = (qp.get("ttl") or [""])[0]\n'
        '    if not raw:\n'
        '        return None\n'
        '    secs = dirs.parse_ttl(raw)\n'
        '    if secs is None:\n'
        '        handler._err(400, "invalid ttl: " + repr(raw)\n'
        '                    + " — use hours (12, 12h) or days (7d)")\n'
        '        return False\n'
        '    return secs\n'
        '\n'
        'def _parse_tags(query_tags):',
        "store ttl helper",
    ),
    # multipart single-file upload
    (
        '            ttl = dirs.parse_ttl((qp.get("ttl") or [""])[0])\n'
        '            if share:\n'
        '                return dirs.share_store(self._kit(), d, _safe_name(n)[:128] or None, c, share, ttl,',
        '            ttl = _ttl_or_400(self, qp)\n'
        '            if ttl is False:\n'
        '                return\n'
        '            if share:\n'
        '                return dirs.share_store(self._kit(), d, _safe_name(n)[:128] or None, c, share, ttl,',
        "store ttl strict multipart",
    ),
    # raw-body upload
    (
        '        ttl = dirs.parse_ttl((qp.get("ttl") or [""])[0])\n'
        '        if share:\n'
        '            return dirs.share_store(self._kit(), data, name_hint or None, ctype, share, ttl,',
        '        ttl = _ttl_or_400(self, qp)\n'
        '        if ttl is False:\n'
        '            return\n'
        '        if share:\n'
        '            return dirs.share_store(self._kit(), data, name_hint or None, ctype, share, ttl,',
        "store ttl strict raw",
    ),
    # dir create
    (
        '            else:\n'
        '                key = secrets.token_hex(8)\n',
        '            else:\n'
        '                key = secrets.token_hex(8)\n'
        '            ttl = _ttl_or_400(self, qp)\n'
        '            if ttl is False:\n'
        '                return\n',
        "store ttl strict dir",
    ),
])

# --- pics-404s als JSON mit code --------------------------------------------
_iap("throway/pics.py", [
    (
        '    return kit.send(404, "not found\\n")\n\n\ndef post(kit, rest, qp):',
        '    return kit.err(404, "not found")\n\ndef post(kit, rest, qp):',
        "pics get fallback 404",
    ),
    (
        '        return kit.send(404, "not found\\n")\n    if "likes=1" in query:',
        '        return kit.err(404, "gallery not found")\n    if "likes=1" in query:',
        "pics gallery 404",
    ),
])

print("REST PATCHES APPLIED")
