"""Token-gated indefinite retention for throway (1.44.0).

throway is disposable by design — but some artifacts (skill installs,
stable share URLs) need a URL that never dies. A retain token splits
that off cleanly, configured via THROWAWAY_RETAIN_TOKEN (one token or
a comma-separated list; unset/empty = feature off, the default):

- token-authenticated uploads (Authorization: Bearer <t>, documented
  ?token=<t>) create objects with NO expiry: sweep() and evict() skip
  them ("retain": true in the manifest),
- ?retain=1 with the token flips an EXISTING object to indefinite,
- token-authenticated writes (PUT/PATCH/dir writes) imply retention,
- retained objects keep public READS; every mutating op needs the
  token (401 otherwise) — a permanent URL must not be defaceable.

Comparisons are constant-time (hmac.compare_digest). A wrong token is
a plain 401; the object URLs themselves stay public, so there is
nothing to enumerate. Assume query strings reach server logs — agents
should prefer the header.
"""
import hmac
import os
from urllib.parse import unquote

RETAIN_TOKENS = tuple(t.strip() for t in
                      os.environ.get("THROWAWAY_RETAIN_TOKEN", "").split(",")
                      if t.strip())
ENABLED = bool(RETAIN_TOKENS)

TOKEN_HINT = ("retain token required: send Authorization: Bearer <token> "
              "or ?token=<token> (query strings can reach server logs)")


def query_string(handler):
    """The raw query string of a request ('' when none). Retro review
    1.45.4: das Split-Muster stand 4x ueber Modulgrenzen verstreut."""
    return handler.path.split("?", 1)[1] if "?" in handler.path else ""


def token_from(handler):
    """The retain token this request carries, '' when none.
    Bearer header first, then the documented ?token= query param."""
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = query_string(handler)
    for kv in q.split("&"):
        k, _, v = kv.partition("=")
        if k == "token" and v:
            return unquote(v).strip()
    return ""


def valid(token):
    """Constant-time check against every configured token."""
    if not token or not RETAIN_TOKENS:
        return False
    given = token.encode()
    return any(hmac.compare_digest(given, cfg.encode()) for cfg in RETAIN_TOKENS)


def request_retention(handler):
    """(retained, denied) for an incoming request.

    (True, None)   — token-authenticated: objects this request creates
                     or writes become indefinite.
    (False, tuple) — retention was requested but is not authorized;
                     the caller must send (code, {"error": ...}) and abort.
    (False, None)  — ordinary disposable request, proceed as ever.
    """
    token = token_from(handler)
    if token:
        if valid(token):
            return True, None
        return False, (401, {"error": "invalid retain token"})
    qs = query_string(handler)
    if "retain=1" in qs or "show=1" in qs:
        if not ENABLED:
            return False, (401, {"error": "retention is not enabled on this server"})
        return False, (401, {"error": TOKEN_HINT})
    return False, None


def write_denied(handler, meta):
    """Retained objects are public-read, token-gated-write. None when the
    write may proceed; else an (http_code, error_dict) the caller sends."""
    if not meta or not meta.get("retain"):
        return None
    if valid(token_from(handler)):
        return None
    return (401, {"error": "object is retained (indefinite); writes and "
                           "deletes need the retain token (Authorization: "
                           "Bearer or ?token=)"})


HELP_TOPICS = {
    "retention": {
        "title": "Indefinite retention (token-gated)",
        "summary": "uploads with a retain token never expire; retained objects stay public-read but need the token for writes",
        "body": """RETENTION — INDEFINITE OBJECTS, TOKEN-GATED

throway is disposable by default. When the server has a retain token
configured (see /api limits: "retention_token"), requests that
authenticate with it create objects that NEVER expire and are exempt
from pool eviction:

  curl -X POST --data-binary @skill.tar.gz \\
       -H "Authorization: Bearer <token>" \\
       "{PUBLIC_BASE}/?name=skill.tar.gz"

Rules:
- Token: "Authorization: Bearer <token>" header or "?token=<token>"
  query param. Query strings can reach server logs — prefer the header.
- Works for single files, bundles, dirs, ?share= names and ?url=
  server-side imports alike.
- Retained objects answer "expires_at": null and carry
  persistence.retention = "indefinite".
- Reads stay PUBLIC (anyone with the URL). Writes and deletes on
  retained objects REQUIRE the token (401 otherwise).
- Token-authenticated PUT/PATCH on an existing object makes it
  indefinite — write implies retention.
- POST {PUBLIC_BASE}/<id>?retain=1 (with token) flips an EXISTING file or
  bundle to indefinite; POST {PUBLIC_BASE}/d/<key>?retain=1 flips a dir.
  Idempotent.
- "&once=1" (burn-after-reading) cannot be combined with retention (400).
- Retained units are never swept and never pool-evicted; when the pool
  runs tight, only disposable units are evicted.

SHOW-DIRS — permanent, publicly writable dirs

  curl -X POST -H "Authorization: Bearer <token>" \\
       "{PUBLIC_BASE}/?dir=1&show=1&name=team-board"

- A show-dir is retained (never expires, never evicted) AND open:
  EVERYONE with the URL can add, edit and delete files — no token.
- Creating or flipping needs the token. Deleting the WHOLE dir needs
  the token too (protects the retention promise); file-level deletes
  stay open.
- Flip an existing dir: POST {PUBLIC_BASE}/d/<key>?show=1 (token).
- Dir responses carry "open": true; the edit history records every
  change, so abuse is traceable even without auth.

Errors: 401 invalid retain token / token required / retention not
enabled; 400 once=1 combined with retention.""",
    },
}
