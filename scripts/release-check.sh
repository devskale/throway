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
rm -rf "$ROOT"

[ "$FAIL" = 0 ] && echo "release-check: OK (version=$V_STORE, docs aktuell)" || exit 1
