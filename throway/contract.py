"""Contract als Daten — eine Quelle für Limits und Error-Codes (1.53.0).

Review candidate 5, pragmatic slice (Q1b): the FULL vision (one structure
rendering HELP prose AND /api AND API.md) is a wrong seam — HELP is a
tutorial register, /api a machine register, ~80% different content. What
IS one truth, today split across hand-kept surfaces, is thinner:

- the LIVE VALUES (4h, 14d, 5 MB, pools, rate limits...) — already
  substituted into HELP bodies and live in /api metadata, but HARDCODED
  in /api note strings and in API.md (which no validator reads)
- the ERROR TABLE — ASCII copy in HELP, absent from /api

This module is that thin truth as data, with one renderer per surface.
Agent COPY stays in HELP (recorded rule: HELP is the single source for
agent copy) — this extends, not contradicts: the numbers have one home.

Interface:
    limits()                every live value (store/pics config stays the
                            source of truth; this is the one mapping)
    errors()                the stable error codes as data (copies)
    errors_payload(lim)     /api block: {code: {http, meaning, retry}}
    render_errors_help(lim) the HELP "errors" topic body (byte-stable)
    substitute(text, lim)   placeholder substitution that leaves unknown
                            placeholders (and literal braces) untouched —
                            safe to run over any note string
"""
import re


def limits():
    """Alle Live-Werte als Substitutionsmap. Late-import: store.py holds
    the config this reads (and imports this module first) — calling this
    at store-import time works because config precedes HELP there."""
    import store
    from throway import dirs, pics
    return {
        "PUBLIC_BASE": store.PUBLIC_BASE,
        "TTL_HOURS": store.TTL_HOURS,
        "TTL_MAX_D": dirs.MAX_AGE // 86400,
        "DIR_DEFAULT_D": dirs.DEFAULT_AGE // 86400,
        "MAX_FILE_MB": store.MAX_FILE // (1024 * 1024),
        "POOL_MB": store.THROW_POOL_SIZE // (1024 * 1024),
        "RATE_LIMIT": store.RATE_LIMIT,
        "HISTORY_LIMIT": store.HISTORY_LIMIT,
        "PICS_DAYS": pics.PICS_TTL // 86400,
        "PICS_GB": pics.PICS_POOL // 1024**3,
        "PICS_MAX_FILE_MB": pics.PICS_MAX_FILE // 1024**2,
        "PICS_RETRY_AFTER": pics.PICS_RETRY_AFTER,
        "PICS_EDGE": pics.PICS_EDGE,
        "PICS_QUALITY": pics.PICS_QUALITY,
    }


# Die stabilen Error-Codes als Daten (Bedeutung + Retry-Strategie sind
# Contract, kein Prosa-Register). Placeholder in `meaning` werden von den
# Renderern mit limits() substituiert.
_ERRORS = (
    {"code": "bad_request", "http": 400, "meaning": "invalid input (name, flags, combinations)", "retry": "fix the request, do not retry"},
    {"code": "write_denied", "http": 401, "meaning": "write/retain token missing or wrong", "retry": "provide the token (Authorization: Bearer or ?token=), then retry"},
    {"code": "forbidden", "http": 403, "meaning": "action not allowed on this object", "retry": "do not retry"},
    {"code": "not_found", "http": 404, "meaning": "id/name unknown, expired or deleted", "retry": "do not retry with the same id"},
    {"code": "missing_length", "http": 411, "meaning": "no Content-Length on a bodied request", "retry": "send Content-Length, then retry"},
    {"code": "too_large", "http": 413, "meaning": "file > {MAX_FILE_MB}MB (pics: {PICS_MAX_FILE_MB}MB)", "retry": "do not retry unchanged"},
    {"code": "rate_limited", "http": 429, "meaning": "too many requests ({RATE_LIMIT}/min/IP)", "retry": "WAIT: `Retry-After` header (seconds) + `retry_after` field; then retry unchanged"},
    {"code": "pool_full", "http": 507, "meaning": "pics pool exhausted", "retry": "WAIT: `Retry-After` ({PICS_RETRY_AFTER}s); admin must delete or wait for expiry"},
    {"code": "unsupported", "http": 501, "meaning": "method/encoding not supported", "retry": "do not retry"},
)


def errors():
    """The stable error codes as data (list of copies, in table order)."""
    return [dict(e) for e in _ERRORS]


def errors_payload(lim):
    """/api-Block (additiv): {code: {http, meaning, retry}} — meanings
    und retry-Texte live-substituiert. Old agents reading only what they
    know stay unaffected."""
    return {e["code"]: {"http": e["http"],
                        "meaning": substitute(e["meaning"], lim),
                        "retry": substitute(e["retry"], lim)}
            for e in _ERRORS}


def render_errors_help(lim):
    """HELP-'errors'-Topic-Body aus den Daten. Spalten jetzt einheitlich
    generiert — die Handfassung hatte zwei schiefe Zeilen (413/429),
    Inhalt und Worte sind unveraendert."""
    lines = [
        "ERRORS + RETRY STRATEGY",
        "Every error response is JSON with an `error` message and a stable `code`:",
        "",
    ]
    for e in _ERRORS:
        lines.append("  {http} {code:<19}{meaning:<42} -> {retry}".format(
            http=e["http"], code=e["code"],
            meaning=substitute(e["meaning"], lim),
            retry=substitute(e["retry"], lim)))
    lines += [
        "",
        'Retry rule of thumb: only 429/507 are "later again" — every other code',
        "means the request itself is wrong; a retry loop must not resend it.",
    ]
    return "\n".join(lines)


class _Keep(dict):
    """format_map-Mapping: bekannte Platzhalter einsetzen, unbekannte
    (und damit auch Literal-Braces wie {ts,file,action}) unverändert
    lassen — substituieren ohne zu zerstören."""
    def __missing__(self, key):
        return "{" + key + "}"


def substitute(text, lim):
    """Placeholders ersetzen, alles Unbekannte bleibt stehen."""
    return text.format_map(_Keep(lim))
