#!/usr/bin/env bash
# Release-Konsistenz (Retro 2026-09-27, Befund 2):
#   1. VERSION in store.py == "Current version" in RELEASES.md
#   2. Trägt der letzte Commit-Betreff eine (x.y.z)-Version, muss sie matchen
#   3. Jeder Endpunkt-Name aus der /api-Spec taucht in API.md auf (Docs-Drift)
set -euo pipefail
cd "$(dirname "$0")/.."
FAIL=0

V_STORE=$(grep -m1 '^VERSION = ' store.py | sed 's/[^0-9.]//g')
V_REL=$(grep -m1 'Current version' RELEASES.md | sed 's/[^0-9.]//g')
[ "$V_STORE" = "$V_REL" ] || { echo "FAIL: store.py VERSION ($V_STORE) != RELEASES.md ($V_REL)"; FAIL=1; }

SUBJ=$(git log -1 --pretty=%s)
V_COMMIT=$(echo "$SUBJ" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ -n "$V_COMMIT" ] && [ "$V_COMMIT" != "$V_STORE" ]; then
  echo "FAIL: Commit-Betreff ($V_COMMIT) != VERSION ($V_STORE)"; FAIL=1
fi

# /api-Spec dynamisch ziehen (Server auf freiem Port, tmp-root)
# Vorab: Koordinations-Guardrail (parallele Sessions im selben Baum) —
# Retro 2026-10-01: zwei Sessions, ein Working-Tree, eine versionslose
# Commit-Kette. Lokal mit .handoff/ vorhanden: eigener Claim nötig.
# Ohne Handoff-Struktur (CI-Runner) wird der Check übersprungen.
if [ -d .handoff/issues/active ] || [ -d "$HOME/code/handoffs/throway/issues/active" ]; then
  # $CLAIM darf leer sein (bumppush-check.sh behandelt '' als kein eigener
  # Claim); unter set -u ist eine ungesetzte Variable aber ein Fehler.
  CLAIM="${CLAIM:-}"
  bash scripts/bumppush-check.sh "$CLAIM" || { echo "FAIL: bumppush-check"; FAIL=1; }
else
  echo "bumppush-check: uebersprungen (keine issues-Struktur — CI)"
fi

ROOT=$(mktemp -d); PORT=$(python3 - <<'PY'
import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()
PY
)
THROWAWAY_ROOT="$ROOT" STORE_PORT="$PORT" THROWAWAY_PICS_ADMIN_TOKEN=check \
  python3 store.py >/dev/null 2>&1 &
SRV=$!
for i in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api" && break; sleep 0.25; done
python3 - "$PORT" <<'PY' > "$ROOT/endpoints.txt"
import json, sys, urllib.request
spec = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api", timeout=10))
print("\n".join(spec["endpoints"].keys()))
PY
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null || true
while read -r ep; do
  grep -q "$ep" API.md || { echo "FAIL: Endpunkt '$ep' fehlt in API.md (Docs-Drift)"; FAIL=1; }
done < "$ROOT/endpoints.txt"

# API.md-Frische (1.53.0): die gen:limits-Region wird aus throway/contract.py
# generiert — Hand-Edits an den Zahlen driften wieder; der Guard macht die
# Datei zum Build-Artifact der Regionen (Prosa bleibt handgeschrieben).
python3 scripts/gen-api-md.py --check >/dev/null 2>&1 || { echo "FAIL: API.md veraltet (scripts/gen-api-md.py laufen lassen)"; FAIL=1; }

# Help-Topics-Drift (Retro 1.38.3): API.md nannte 'named_dirs', das nie
# existierte — jedes HELP-Order-Topic muss in API.md erwähnt sein.
TOPICS=$(python3 -c "import re;s=open('store.py').read();m=re.search(r'HELP_ORDER = \[([^]]+)\]',s);print(' '.join(re.findall(r'\"(\w+)\"',m.group(1))))")
for t in $TOPICS; do
  grep -q "\`$t\`" API.md || { echo "FAIL: Help-Topic '$t' fehlt in API.md (Docs-Drift)"; FAIL=1; }
done
rm -rf "$ROOT"

# Atomic-Write-Guard (Retro 2026-10-02, hard-validate; 1.53.1 verschärft):
# Persistenz-Schreiben nur via storage.atomic_json (tmp + os.replace) — die
# Data-Loss-Race von 1.45.2. Die alte Regex (json.dump(.. open(..)) sah die
# with-Block-Form NICHT — genau so blieben pics save_meta/save_gallery
# monatelang nicht-atomar unentdeckt (1.52.0-Fund). Seit 1.52.0 gehen ALLE
# JSON-Writes durch throway/storage.py → die enge Regel: json.dump (nicht
# json.dumps — das ist nur Serialisierung) existiert nur noch dort.
DUMPS=$(grep -nE 'json\.dump\(' store.py throway/*.py | grep -v 'throway/storage.py' || true)
[ -z "$DUMPS" ] || { echo "FAIL: json.dump ausserhalb von throway/storage.py (→ storage.atomic_json):"; echo "$DUMPS"; FAIL=1; }

# Prosa-Glitch-Guard (1.53.1): CJK/Hangul in deutscher Agent-Prosa ist die
# LLM-Glitch-Klasse (passiert: „读者“ statt „Leser“ in CONTEXT.md, 1.52.0 —
# vom Korrekturlesen gefangen, von keinem Check). Deterministisch fangbar.
# BSD-grep hat kein -P, deshalb Python.
python3 - <<'PY' || { echo "FAIL: CJK/Hangul in Agent-Prosa"; FAIL=1; }
import re
bad = []
for fn in ("CONTEXT.md", "RELEASES.md", "AGENTS.md", "API.md"):
    try:
        t = open(fn, encoding="utf-8").read()
    except OSError:
        continue
    for m in re.finditer(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t):
        bad.append(f"{fn}: {t.count(m.group(0))}x {m.group(0)!r}")
        break
if bad:
    raise SystemExit(" | ".join(bad))
PY

# CSS-Klammer-Balance (Retro 2026-10-02): Verwaiste/doppelte } legen die
# FOLGENDE Regel still weg (in throway 2× passiert). Die CSS-Strings liegen
# als Python-String-Literale in pics.py (_GALLERY_CSS, _EMBED_CSS, _LB_CSS,
# _SOCIAL_CSS, _ADMIN_CSS) und seit 1.51.0 auch in index.py (_INDEX_CSS —
# Homepage-CSS, größte CSS-Masse im Projekt). Jede Regel ist ein eigenes
# Literal mit { } im selben String — wir zählen { vs } über alle Literale,
# ignorieren aber Kommentare und Strings (sonst schlagen echte } in
# content-/url-Werten an).
python3 - <<'PY' || { echo "FAIL: CSS-Klammer-Balance"; FAIL=1; }
import re
src = open("throway/pics.py", encoding="utf-8").read()
# Alle *_CSS = ( ... ) Blöcke sammeln (konkatenierte String-Literale)
blocks = re.findall(r"_(?:GALLERY|EMBED|LB|SOCIAL|ADMIN)_CSS = \((.*?)\n\)", src, re.S)
if not blocks:
    raise SystemExit("keine CSS-Bloecke gefunden")
iflags = open("throway/index.py", encoding="utf-8").read()
iblocks = re.findall(r"_INDEX_CSS = \((.*?)\n\)", iflags, re.S)
if not iblocks:
    raise SystemExit("kein _INDEX_CSS-Block gefunden (1.51.0)")
def css_strings(block):
    # Die CSS-Regeln sind die "..."-String-Inhalte im Block. Wichtig: wir
    # extrahieren sie, statt die Strings zu ENTFERNEN — sonst löschen wir
    # die Regeln mitsamt ihren { } und die Balance ist immer 0 (nutzlos).
    return re.findall(r'"((?:\\.|[^"\\])*)"', block)

def balance(txt):
    depth = 0; mn = 0
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)   # CSS-Kommentare
    for ch in txt:
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth < mn: mn = depth
    return depth, mn

bad = []
for name, block in zip(("_GALLERY_CSS", "_EMBED_CSS", "_LB_CSS", "_SOCIAL_CSS", "_ADMIN_CSS"), blocks):
    d, mn = balance("".join(css_strings(block)))
    if d != 0 or mn < 0:
        bad.append(f"{name}: End-Tiefe {d}, min {mn}")
d, mn = balance("".join(css_strings(iblocks[0])))
if d != 0 or mn < 0:
    bad.append(f"_INDEX_CSS: End-Tiefe {d}, min {mn}")
if bad:
    raise SystemExit(" | ".join(bad))
print("css-balance: OK (6 Bloecke balanciert)")
PY

[ "$FAIL" = 0 ] && echo "release-check: OK (version=$V_STORE, docs aktuell)" || exit 1
