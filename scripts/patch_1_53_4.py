#!/usr/bin/env python3
"""1.53.4 — Error-Code-Disziplin.

Alle rohen Fehler-Sends (kit.send + json.dumps({"error": ...})) auf
kit.err() umstellen; retain.py-Dicts um code ergänzen; store.py:
Dir-Namen validieren, unparseable ttl= → 400, Bundle-PUT/PATCH → 403.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patch import apply

def _iap(path, edits):
    """Idempotenter apply: bereits angewandte Edits (new schon im Source)
    werden uebersprungen, damit ein abgebrochener Lauf fortsetzbar ist."""
    cur = open(path, encoding="utf-8").read()
    todo = [(o, n, l) for (o, n, l) in edits if n not in cur]
    if todo:
        apply(path, todo)
    else:
        print(f"{path}: nichts zu tun (bereits gepatcht)")

# --- throway/dirs.py -------------------------------------------------------
_iap("throway/dirs.py", [
    # 401 write-gate Tupel: code ergänzen (Schnittstelle bleibt (code, dict))
    ('return (401, {"error": "write token required: send the X-Throway-Write "',
     'return (401, {"code": "write_denied", "error": "write token required: send the X-Throway-Write "', 'dirs write-token-tupel'),
    ('return (401, {"error": "invalid write token"})',
     'return (401, {"code": "write_denied", "error": "invalid write token"})', 'dirs invalid-write-token-tupel'),
    # rohe Sends → kit.err
    ('return kit.send(400, json.dumps(\n                {"error": "invalid write token: use write=1 (server generates) "\n                          "or 8-64 chars [A-Za-z0-9._-]"}), "application/json")',
     'return kit.err(400, "invalid write token: use write=1 (server generates) "\n                          "or 8-64 chars [A-Za-z0-9._-]")', 'dirs invalid write token flag'),
    ('return kit.send(400, json.dumps({"error": "dir add requires multipart"}), "application/json")',
     'return kit.err(400, "dir add requires multipart")', 'dirs multipart'),
    ('return kit.send(404, json.dumps({"error": "expired"}), "application/json")',
     'return kit.err(404, "expired")', 'dirs expired'),
    ('return kit.send(400, json.dumps({"error": "no file parts"}), "application/json")',
     'return kit.err(400, "no file parts")', 'dirs no parts'),
    ('return kit.send(413, json.dumps({"error": f"too large (max 5MB): {safe}"}), "application/json")',
     'return kit.err(413, f"too large (max 5MB): {safe}")', 'dirs too large file'),
    ('        cur += len(d)\n        if cur > kit.pool_size:\n            return kit.send(413, json.dumps({"error": "dir too large (pool max 100MB)"}), "application/json")\n    files_map, _, _ = _write_files(kit, key, dirpath, m, named)',
     '        cur += len(d)\n        if cur > kit.pool_size:\n            return kit.err(413, "dir too large (pool max 100MB)")\n    files_map, _, _ = _write_files(kit, key, dirpath, m, named)', 'dirs too large pool a'),
    ('    if files_map is None:\n        return kit.send(413, json.dumps({"error": "dir too large (pool max 100MB)"}), "application/json")\n    kit.evict_pool()',
     '    if files_map is None:\n        return kit.err(413, "dir too large (pool max 100MB)")\n    kit.evict_pool()', 'dirs too large pool b'),
    ('return kit.send(400, json.dumps({"error": f"invalid share name: {reason}"}), "application/json")',
     'return kit.err(400, f"invalid share name: {reason}")', 'dirs share name'),
    ('return kit.send(413, json.dumps({"error": "store failed (too large?)"}), "application/json")',
     'return kit.err(413, "store failed (too large?)")', 'dirs store failed'),
    ('return kit.send(400, json.dumps({"error": "file required"}), "application/json")',
     'return kit.err(400, "file required")', 'dirs file required'),
    ('return kit.send(400, json.dumps({"error": "only text files can be edited"}), "application/json")',
     'return kit.err(400, "only text files can be edited")', 'dirs text edit'),
    ('{"error": "whole-dir delete on a retained dir needs the retain "',
     '{"code": "write_denied", "error": "whole-dir delete on a retained dir needs the retain "', 'dirs retained delete'),
])

# --- throway/pics.py -------------------------------------------------------
_iap("throway/pics.py", [
    ('return kit.send(400, json.dumps({"error": "url required"}), "application/json")',
     'return kit.err(400, "url required")', 'pics url required'),
    ('return kit.send(404, json.dumps({"error": "gallery not found"}), "application/json")',
     'return kit.err(404, "gallery not found")', 'pics gallery not found a'),
    ('return kit.send(404, json.dumps({"error": "gallery not found"}),\n                       "application/json")',
     'return kit.err(404, "gallery not found")', 'pics gallery not found b'),
    ('        length = kit.header("Content-Length")\n        if length is None:\n            return kit.send(411, json.dumps({"error": "length required"}), "application/json")\n        if int(length) > 200 * 1024 * 1024:',
     '        length = kit.header("Content-Length")\n        if length is None:\n            return kit.err(411, "length required")\n        if int(length) > 200 * 1024 * 1024:', 'pics length required a'),
    ('return kit.send(413, json.dumps({"error": "batch too large"}), "application/json")',
     'return kit.err(413, "batch too large")', 'pics batch too large'),
    ('return kit.send(400, json.dumps({"error": "bad body"}), "application/json")',
     'return kit.err(400, "bad body")', 'pics bad body'),
    ('return kit.send(400, json.dumps({"error": "no file part"}), "application/json")',
     'return kit.err(400, "no file part")', 'pics no file part'),
    ('    # raw body: one image per request (JS queue + agents)\n    length = kit.header("Content-Length")\n    if length is None:\n        return kit.send(411, json.dumps({"error": "length required"}), "application/json")',
     '    # raw body: one image per request (JS queue + agents)\n    length = kit.header("Content-Length")\n    if length is None:\n        return kit.err(411, "length required")', 'pics length required b'),
    ('return kit.send(404, json.dumps({"error": "image not found"}),\n                       "application/json")',
     'return kit.err(404, "image not found")', 'pics image not found'),
    ('return kit.send(404, json.dumps({"error": "comment not found"}),\n                       "application/json")',
     'return kit.err(404, "comment not found")', 'pics comment not found'),
    ('return kit.send(code if isinstance(code, int) else 502,\n                       json.dumps({"error": f"fetch failed: {msg}"}),\n                       "application/json")',
     'return kit.err(code if isinstance(code, int) else 502, f"fetch failed: {msg}")', 'pics fetch failed'),
    ('return kit.send(400, json.dumps(\n                    {"error": "page contains no importable images (only direct "\n                              "image URLs or Google Photos share links work)"}),\n                    "application/json")',
     'return kit.err(400, "page contains no importable images (only direct "\n                              "image URLs or Google Photos share links work)")', 'pics no importable'),
    ('return kit.send(400, json.dumps(\n            {"error": f"not an image (content-type {ctype or \'unknown\'})"}), "application/json")',
     'return kit.err(400, f"not an image (content-type {ctype or \'unknown\'})")', 'pics not an image'),
    ('return kit.send(413, json.dumps(\n            {"error": f"too large (max {_fmt(PICS_MAX_FILE)})"}), "application/json")',
     'return kit.err(413, f"too large (max {_fmt(PICS_MAX_FILE)})")', 'pics too large'),
    ('{"error": "nothing to do — use ?create=1 to create a gallery, "',
     '{"code": "bad_request", "error": "nothing to do — use ?create=1 to create a gallery, "', 'pics nothing to do'),
])

# --- throway/retain.py -----------------------------------------------------
_iap("throway/retain.py", [
    ('return False, (401, {"error": "invalid retain token"})',
     'return False, (401, {"code": "write_denied", "error": "invalid retain token"})', 'retain invalid token'),
    ('return False, (401, {"error": "retention is not enabled on this server"})',
     'return False, (401, {"code": "write_denied", "error": "retention is not enabled on this server"})', 'retain disabled'),
    ('return False, (401, {"error": TOKEN_HINT})',
     'return False, (401, {"code": "write_denied", "error": TOKEN_HINT})', 'retain hint'),
    ('return (401, {"error": "object is retained (indefinite); writes and "',
     'return (401, {"code": "write_denied", "error": "object is retained (indefinite); writes and "', 'retain write denied'),
])

# --- store.py --------------------------------------------------------------
_iap("store.py", [
    # PUT/PATCH "only text files" → _err
    ('return self._send(400, json.dumps({"error": "only text files can be edited"}), "application/json")',
     'return self._err(400, "only text files can be edited")', 'store put text'),
    ('return self._send(400, json.dumps({"error": "only text files can be appended to"}), "application/json")',
     'return self._err(400, "only text files can be appended to")', 'store patch text'),
    # 500 meta unreadable → _err
    ('return self._send(500, json.dumps({"error": "meta unreadable"}), "application/json")',
     'return self._err(500, "meta unreadable")', 'store meta unreadable'),
    # denied-Tupel: code steckt jetzt im dict — _err nutzen
    ('denied = retain.write_denied(self, meta)\n        if denied:\n            return self._send(denied[0], json.dumps(denied[1]), "application/json")',
     'denied = retain.write_denied(self, meta)\n        if denied:\n            return self._err(denied[0], denied[1]["error"])', 'store denied tags'),
    ('denied = retain.write_denied(self, self._meta_of(fid))\n            if denied:\n                return self._send(denied[0], json.dumps(denied[1]), "application/json")\n            _remove(fp); self._send(200, "deleted\\n")',
     'denied = retain.write_denied(self, self._meta_of(fid))\n            if denied:\n                return self._err(denied[0], denied[1]["error"])\n            _remove(fp); self._send(200, "deleted\\n")', 'store denied delete file'),
    ('denied = retain.write_denied(self, _bundle_meta(fp, fid))\n            if denied:\n                return self._send(denied[0], json.dumps(denied[1]), "application/json")\n            shutil.rmtree(fp, ignore_errors=True)',
     'denied = retain.write_denied(self, _bundle_meta(fp, fid))\n            if denied:\n                return self._err(denied[0], denied[1]["error"])\n            shutil.rmtree(fp, ignore_errors=True)', 'store denied delete bundle'),
    ('denied = retain.write_denied(self, self._meta_of(fid))\n        if denied:\n            return self._send(denied[0], json.dumps(denied[1]), "application/json")\n        data = self._read_body()\n        if data is None:\n            return self._err(411, "length required")\n        if len(data) > MAX_FILE:\n            return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")\n        with open(fp, "wb") as f:',
     'denied = retain.write_denied(self, self._meta_of(fid))\n        if denied:\n            return self._err(denied[0], denied[1]["error"])\n        data = self._read_body()\n        if data is None:\n            return self._err(411, "length required")\n        if len(data) > MAX_FILE:\n            return self._err(413, f"too large (max {MAX_FILE // (1024 * 1024)}MB)")\n        with open(fp, "wb") as f:', 'store denied put'),
    ('denied = retain.write_denied(self, self._meta_of(fid))\n        if denied:\n            return self._send(denied[0], json.dumps(denied[1]), "application/json")\n        data = self._read_body()\n        if data is None:\n            return self._err(411, "length required")\n        cur = os.path.getsize(fp)',
     'denied = retain.write_denied(self, self._meta_of(fid))\n        if denied:\n            return self._err(denied[0], denied[1]["error"])\n        data = self._read_body()\n        if data is None:\n            return self._err(411, "length required")\n        cur = os.path.getsize(fp)', 'store denied patch'),
    # PUT/PATCH auf Bundle-Dir → 403 forbidden statt 404 not_found
    ('        fid = parts[0]\n        if not fid or fid.endswith(".meta"):\n            return self._err(404, "not found")\n        fp = _id_path(fid)\n        if not os.path.isfile(fp):\n            return self._err(404, "not found")\n        if not self._is_text(fid):\n            return self._err(400, "only text files can be edited")',
     '        fid = parts[0]\n        if not fid or fid.endswith(".meta"):\n            return self._err(404, "not found")\n        fp = _id_path(fid)\n        if os.path.isdir(fp) and fid not in (DIR_NS, pics.NS):\n            # Bundle: existiert, ist aber nicht editierbar — 403, nicht\n            # 404 (Retro 1.53.4: ein Agent darf nicht auf eine falsche\n            # ID schließen, wenn der Edit gemeint war)\n            return self._err(403, "bundles are immutable snapshots (editable: false)")\n        if not os.path.isfile(fp):\n            return self._err(404, "not found")\n        if not self._is_text(fid):\n            return self._err(400, "only text files can be edited")', 'store put bundle 403'),
    ('        fid = parts[0]\n        if not fid or fid.endswith(".meta"):\n            return self._err(404, "not found")\n        fp = _id_path(fid)\n        if not os.path.isfile(fp):\n            return self._err(404, "not found")\n        if not self._is_text(fid):\n            return self._err(400, "only text files can be appended to")',
     '        fid = parts[0]\n        if not fid or fid.endswith(".meta"):\n            return self._err(404, "not found")\n        fp = _id_path(fid)\n        if os.path.isdir(fp) and fid not in (DIR_NS, pics.NS):\n            return self._err(403, "bundles are immutable snapshots (editable: false)")\n        if not os.path.isfile(fp):\n            return self._err(404, "not found")\n        if not self._is_text(fid):\n            return self._err(400, "only text files can be appended to")', 'store patch bundle 403'),
    # Dir-Namen validieren + unparseable ttl → 400
    ('        if want_dir:\n            if name_hint:\n                key = name_hint\n            else:',
     '        if want_dir:\n            if name_hint:\n                ok, reason = dirs.valid_name(name_hint)\n                if not ok:\n                    return self._err(400, f"invalid dir name: {reason}")\n                key = name_hint\n            else:', 'store dir name validate'),
])

print("ALL PATCHES APPLIED")
