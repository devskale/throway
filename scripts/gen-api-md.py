#!/usr/bin/env python3
"""API.md-Gen-Regionen aus throway/contract.py fuettern (1.53.0).

Marker-Ansatz (Q3b): Prosa bleibt handgeschrieben; nur die Region zwischen
<!-- gen:limits --> ... <!-- /gen:limits --> wird aus contract.limits()
generiert. `--check` beendet mit 1, wenn die Datei nicht frisch ist
(release-check-Guard + test_contract).

Hinweis: setzt THROWAWAY_ROOT auf ein Tempdir, falls ungesetzt — die
Limits lesen nur Env-Konfig, nie das Daten-Root."""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("THROWAWAY_ROOT", tempfile.mkdtemp(prefix="gen-api-md-"))
sys.path.insert(0, _HERE)

import store                                    # noqa: E402
from throway import contract                    # noqa: E402

API_MD = os.path.join(_HERE, "API.md")
OPEN = "<!-- gen:limits -->"
CLOSE = "<!-- /gen:limits -->"


def limits_table(lim):
    t, mx, dd = lim["TTL_HOURS"], lim["TTL_MAX_D"], lim["DIR_DEFAULT_D"]
    return "\n".join([
        "| Limit | Value |",
        "|---|---|",
        f"| URL lifetime | {t} hours ({t * 3600}s) default — single files & bundles |",
        f"| Single-file lifetime | default {t}h; `ttl=` override clamped to [4h, {mx}d] (max {mx} days) |",
        f"| Dir lifetime | fixed, default {dd} days (**max {mx} days**: `ttl=` clamp [4h, {mx}d]) |",
        f"| Dir history | last {lim['HISTORY_LIMIT']} edits per dir |",
        f"| Max file size | {lim['MAX_FILE_MB']} MB |",
        f"| Pool size | {lim['POOL_MB']} MB (oldest files evicted first) |",
        f"| Rate limit | {lim['RATE_LIMIT']} req/min per IP |",
        "| Retention | token-gated (server-side `THROWAWAY_RETAIN_TOKEN`): token uploads **never expire**, exempt from pool eviction; public read, token-gated write (see [Retention](#retention-indefinite-objects-token-gated-since-1440)) |",
        f"| pics gallery | own {lim['PICS_GB']} GB pool (full → 507 reject, never evicts), fixed {lim['PICS_DAYS']}-day lifetime, {lim['PICS_MAX_FILE_MB']} MB max/image; ≤ {lim['PICS_EDGE']} px stored byte-identical (JPEG metadata stripped losslessly), larger downscaled to {lim['PICS_EDGE']} px WebP q{lim['PICS_QUALITY']} (≤ 1 MB) |",
    ])


def regenerate(text):
    i = text.find(OPEN)
    j = text.find(CLOSE)
    if i == -1 or j == -1 or j < i:
        raise SystemExit("API.md: gen-Marker fehlen/verdreht — <!-- gen:limits --> einbauen")
    return text[:i + len(OPEN)] + "\n" + limits_table(contract.limits()) + "\n" + text[j:]


def main():
    text = open(API_MD, encoding="utf-8").read()
    fresh = regenerate(text)
    if "--check" in sys.argv:
        if fresh != text:
            raise SystemExit("API.md veraltet: scripts/gen-api-md.py laufen lassen und committen")
        print("api-md: OK (frisch)")
        return
    if fresh != text:
        tmp = API_MD + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(fresh)
        os.replace(tmp, API_MD)
        print("API.md aktualisiert (gen:limits)")


if __name__ == "__main__":
    main()
