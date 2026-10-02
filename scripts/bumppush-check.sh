#!/usr/bin/env bash
# bumppush-check.sh — Koordinations-Guardrail für parallele Agent-Sessions.
#
# Retro 2026-10-01: zwei Sessions arbeiteten im selben Working-Tree. Die
# eine hat 1.39.3/1.40.0–1.40.2 gelandet, waehrend die andere ihr Release
# vorbereitete — das Ergebnis war ein versionenloser Commit (CI rot) und
# eine nicht nachvollziehbare Versionskette.
#
# Regel (kurz): Wer an diesem Repo arbeitet, hat einen Eintrag in
# <handoff>/issues/active/. Ein Bumppush ist nur erlaubt, wenn active/ leer
# ist — bzw. der eigene Claim-Slug im Commit-Betreff steht.
#
# Usage:  bash scripts/bumppush-check.sh [slug]
#         slug optional = der Issue-Slug, den diese Session bearbeitet
set -uo pipefail
cd "$(dirname "$0")/.."

HANDOFF=".handoff/issues"
[ -d "$HANDOFF" ] || HANDOFF="$HOME/code/handoffs/throway/issues"

fail=0

if [ ! -d "$HANDOFF/active" ]; then
  echo "FAIL: keine issues-Struktur unter $HANDOFF"
  exit 1
fi

ACTIVE=$(ls -1 "$HANDOFF/active"/*.md 2>/dev/null | sed 's#.*/##; s#\.md$##')
COUNT=$(printf "%s\n" "$ACTIVE" | grep -c . || true)

if [ "$COUNT" -eq 0 ]; then
  echo "bumppush-check: OK (active/ leer — kein fremder Claim)"
  exit 0
fi

SLUG="${1:-}"
SUBJ=$(git log -1 --pretty=%s 2>/dev/null || echo "")

echo "Aktive Claims in $HANDOFF/active:"
printf "  - %s\n" $ACTIVE

OWNED=0
for a in $ACTIVE; do
  # eigener Claim: slug im aktuellen Branch- oder HEAD-Betreff genannt?
  if [ -n "$SLUG" ] && [ "$SLUG" = "$a" ]; then OWNED=1; fi
  case "$SUBJ" in *"$a"*) OWNED=1 ;; esac
done

if [ "$OWNED" = "1" ]; then
  echo "bumppush-check: OK (eigener Claim im Betreff: '$SLUG')"
  exit 0
fi

echo "FAIL: $COUNT aktive(r) Claim(s), aber keiner gehört zu dieser Session."
echo "      Vor dem Bumppush klären: Arbeitet die andere Session noch,"
echo "      oder ist deren Claim stale (Issue auf DONE/archive)?"
echo "      Mit eigenem Claim:  bash scripts/bumppush-check.sh <slug>"
exit 1