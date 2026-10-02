#!/usr/bin/env python3
"""patch.py — sicheres Editieren der Stringlastigen Module (store.py,
pics.py) per exaktem Anker, nach CODING_RULES „Edit-Disziplin".

Reihenfolge ist der Punkt (Retro 2026-10-01, P1): ANKER prüfen → ERSETZEN
→ **COMPILE IM SPEICHER** → erst dann atomisch schreiben. Ein Patch-Script,
das erst schreibt und dann kompiliert, hinterlässt bei einem Syntaxfehler
ein kaputtes Modul (passiert: 1.39.0 Grace-Period).

Usage (als Bibliothek importieren):

    from scripts.patch import apply
    apply("throway/pics.py", [
        ("alter text", "neuer text", "label"),   # count muss 1 sein
        ...
    ])

Oder als Einmal-Script: unten ARGV-Modus mit JSON-Datei.
"""
import json
import os
import sys
import tempfile


def _near_misses(src, old, limit=3):
    """Bei Anker-Fehlschlag: aehnlichste Zeilen mit Kontext melden.
    Retro 2026-10-02: ~6 Session-Round-Trips gingen in Anker-Debugging
    (trailing space, falsche Fehlerformate, Quote-Ebenen) — der Report
    nennt die Stelle, die der Anker-aehnelt, statt sie suchen zu lassen.
    Whitespace-normalisierter difflib-Vergleich der ersten Anker-Zeile."""
    import difflib
    import re
    first = re.sub(r"\s+", " ", old.split("\n", 1)[0]).strip()
    if len(first) < 8:
        return []
    lines = src.split("\n")
    scored = []
    for i, ln in enumerate(lines):
        cand = re.sub(r"\s+", " ", ln).strip()
        if len(cand) < 8:
            continue
        ratio = difflib.SequenceMatcher(None, first, cand).ratio()
        if ratio >= 0.72 or first[:40] in cand:
            scored.append((ratio, i, ln))
    scored.sort(key=lambda t: -t[0])
    out = []
    for ratio, i, ln in scored[:limit]:
        out.append(f"Zeile {i+1} (Aehnlichkeit {ratio:.2f}): {ln[:100]}")
        if len(lines) > i + 1:
            out.append(f"  danach: {lines[i+1][:100]}")
    return out


def apply(path, edits, quiet=False):
    """edits: Liste von (old, new, label). Jeder Anker muss exakt 1x
    vorkommen. Kompiliert das Ergebnis im SPEICHER, bevor geschrieben
    wird; bricht ab, bevor irgendetwas kaputtgeht."""
    src = open(path, encoding="utf-8").read()
    for old, new, label in edits:
        n = src.count(old)
        if n != 1:
            near = _near_misses(src, old)
            hint = ("\n  Near-Miss:\n" + "\n".join("    " + x for x in near)
                    if near else "")
            raise SystemExit(
                f"patch.py: Anker '{label}' kommt {n}x vor (erwartet 1) — "
                f"nichts geändert.{hint}")
        src = src.replace(old, new)
        if not quiet:
            print(f"ok: {label}")
    # Validierung im Speicher — NICHT auf die Platte schreiben, bevor das
    # Ergebnis parsebar ist. (Nur .py; Markdown/Shell entfaellt.)
    if path.endswith(".py"):
        compile(src, path, "exec")
    fd, tmp = tempfile.mkstemp(
        suffix=".patchtmp", dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, path)
    if not quiet:
        print(f"geschrieben: {path} ({len(edits)} Edits, kompiliert im Speicher)")


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    spec = json.load(open(argv[1], encoding="utf-8"))
    apply(spec["path"], [(e[0], e[1], e[2]) for e in spec["edits"]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
