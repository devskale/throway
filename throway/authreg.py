"""authreg — self-service email registration (Python port of the aiui
blueprint, server/email-auth.js; LOGIN-FLOW.md 2026-10-18).

Review-Tracker 2026-10-18 (aiui-Invarianten, alle gehalten):
  1 canonicalization  — whole-address lowercase key, original kept,
                        no provider munging (no Gmail dot-folding)
  2 tokens            — 32B random, stored sha256-only, single-use,
                        TTL 30 min, purpose-separated (verify|reset)
  3 enumeration-safe  — rich results here; the ROUTE layer maps every
                        outcome to the same HTTP response
  4 rate walls        — 10/h per IP register window; 60 s mail cooldown
                        per norm shared across register/resend/reset-request;
                        day-cap on NEW accounts (restart-safe via `created`)
  5 mail budget       — in mailer.py (100/day, 10/min), 0 = kill-switch
  6 passphrase        — scrypt salt:hash (16B salt, 32B key), same contract
  7 reset kills all sessions of the user
  8 dummy-scrypt      — login with unknown address burns the same scrypt
  9 (email-change)    — not built, blueprint P1

Port-fixes over the JS original (review findings 1+2):
  - ip/cooldown maps get a TTL sweep (public surface, no unbounded growth)
  - a failed store save raises into the caller (never silently memory-only)

Store: JSON file, atomic tmp+rename, 0600, mtime-cached — shape:
  { "users":    [{"email","norm","hash","status","created","invite"}],
    "tokens":   [{"hash","purpose","norm","expires_ms"}],
    "sessions": [{"hash","norm","expires_ms"}] }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from typing import Optional

from . import mailer

TOKEN_TTL_MS = 30 * 60 * 1000
SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000

_USERS_FILE = None            # set via _users_file() (env at first touch)
_store: Optional[dict] = None
_store_mtime = -1.0
_lock = threading.Lock()

# ── rate walls (in-memory; restart resets — acceptable, blueprint) ────
_ip_windows: dict[str, list[float]] = {}
_mail_cooldown: dict[str, float] = {}
_WALL_TTL_MS = 6 * 60 * 60 * 1000   # sweep horizon: older entries die


def _users_file() -> str:
    global _USERS_FILE
    if _USERS_FILE is None:
        _USERS_FILE = os.environ.get(
            "THROWAWAY_USERS_FILE",
            os.path.join(os.environ.get("THROWAWAY_ROOT", "/srv/storage2/throway"),
                         ".users-auth.json"))
    return _USERS_FILE


def set_users_file_for_test(path: str) -> None:
    """Fresh store per case (test seam — call before first store touch)."""
    global _USERS_FILE, _store, _store_mtime
    _USERS_FILE = path
    _store = None
    _store_mtime = -1.0
    _ip_windows.clear()
    _mail_cooldown.clear()


# ── store ─────────────────────────────────────────────────────────────
def _load_store() -> dict:
    global _store, _store_mtime
    with _lock:
        try:
            st = os.stat(_users_file())
            if st.st_mtime != _store_mtime:
                with open(_users_file(), "r", encoding="utf-8") as f:
                    raw = json.load(f)
                _store = {"users": raw.get("users") or [],
                          "tokens": raw.get("tokens") or [],
                          "sessions": raw.get("sessions") or []}
                _store_mtime = st.st_mtime
        except FileNotFoundError:
            if _store is None:          # ENOENT must not clobber memory
                _store = {"users": [], "tokens": [], "sessions": []}
            _store_mtime = -1.0
        except (OSError, ValueError):
            if _store is None:          # unreadable/invalid file: same guard
                _store = {"users": [], "tokens": [], "sessions": []}
            _store_mtime = -1.0
        return _store


def _save_store() -> None:
    """Atomic tmp+rename, 0600. Raises on failure (port-fix: never silent)."""
    now = time.time() * 1000
    s = _load_store()
    s["tokens"] = [t for t in s["tokens"] if t["expires_ms"] > now]
    s["sessions"] = [x for x in s["sessions"] if x["expires_ms"] > now]
    payload = json.dumps(s, indent=1)
    d = os.path.dirname(os.path.abspath(_users_file())) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".users-auth-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, _users_file())
        global _store_mtime
        _store_mtime = os.stat(_users_file()).st_mtime
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── canonicalization + hashing ────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$")


def normalize_email(email) -> Optional[dict]:
    raw = str(email or "").strip()
    at = raw.rfind("@")
    if at < 1 or at == len(raw) - 1:
        return None
    norm = raw.lower()
    ok = len(norm) <= 254 and bool(_EMAIL_RE.fullmatch(norm))
    return {"original": raw, "norm": norm, "ok": ok}


def hash_passphrase(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return (salt.hex() + ":" +
            hashlib.scrypt(pw.encode(), salt=salt, n=16384, r=8, p=1, dklen=32).hex())


def _verify_hash(pw: str, entry: str) -> bool:
    try:
        salt_hex, hash_hex = str(entry or "").split(":", 1)
        if not salt_hex or not hash_hex:
            return False
        computed = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex),
                                  n=16384, r=8, p=1, dklen=len(hash_hex) // 2)
        return hmac.compare_digest(computed, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False


# ── rate walls ────────────────────────────────────────────────────────
def _sweep_walls(now: float) -> None:
    if _ip_windows:
        for ip in [ip for ip, w in _ip_windows.items() if not w or now - w[0] > _WALL_TTL_MS]:
            _ip_windows.pop(ip, None)
    if _mail_cooldown:
        for n in [n for n, ts in _mail_cooldown.items() if now - ts > _WALL_TTL_MS]:
            _mail_cooldown.pop(n, None)


def note_register_attempt(ip: str) -> bool:
    now = time.time() * 1000
    _sweep_walls(now)
    w = _ip_windows.setdefault(ip or "?", [])
    w[:] = [x for x in w if x > now - 3_600_000]
    w.append(now)
    return len(w) <= 10


def _mail_cooldown_ok(norm: str) -> bool:
    return time.time() * 1000 - _mail_cooldown.get(norm, 0) >= 60_000


def _note_mail_sent(norm: str) -> None:
    _mail_cooldown[norm] = time.time() * 1000


# ── registration mode + day-cap ───────────────────────────────────────
def registration_mode() -> str:
    m = (os.environ.get("REGISTRATION_MODE", "open")).lower()
    return m if m in ("invite", "open", "closed") else "open"


def peek_registration_budget() -> dict:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    n = sum(1 for u in _load_store()["users"]
            if str(u.get("created", "")).startswith(today))
    return {"created_today": n, "daily_max": _daily_new_users_max()}


def _daily_new_users_max() -> int:
    return int(os.environ.get("REGISTER_DAILY_MAX", "20"))


def _created_today_count() -> int:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return sum(1 for u in _load_store()["users"]
               if str(u.get("created", "")).startswith(today))


def _invite_codes() -> list[str]:
    env = [c.strip() for c in
           (os.environ.get("THROWAWAY_INVITE_CODES") or "").split(",") if c.strip()]
    if env:
        return env
    try:
        with open(os.environ.get("THROWAWAY_AUTH_FILE",
                                 "/srv/storage2/throway/.auth.json"),
                  "r", encoding="utf-8") as f:
            c = json.load(f).get("inviteCodes")
            return list(c) if isinstance(c, list) else []
    except (OSError, ValueError):
        return []


# ── tokens + sessions ─────────────────────────────────────────────────
def _mint_token(purpose: str, norm: str) -> str:
    token = secrets.token_urlsafe(32)
    _load_store()["tokens"].append({
        "hash": hashlib.sha256(token.encode()).hexdigest(),
        "purpose": purpose, "norm": norm,
        "expires_ms": time.time() * 1000 + TOKEN_TTL_MS})
    _save_store()
    return token


def _consume_token(token: str, purpose: str) -> Optional[dict]:
    h = hashlib.sha256(str(token or "").encode()).hexdigest()
    s = _load_store()
    for i, t in enumerate(s["tokens"]):
        if t["hash"] == h and t["purpose"] == purpose:
            s["tokens"].pop(i)
            _save_store()
            if t["expires_ms"] < time.time() * 1000:
                return None
            return t
    return None


def issue_session(norm: str) -> str:
    token = secrets.token_urlsafe(32)
    _load_store()["sessions"].append({
        "hash": hashlib.sha256(token.encode()).hexdigest(),
        "norm": norm,
        "expires_ms": time.time() * 1000 + SESSION_TTL_MS})
    _save_store()
    return token


def session_norm(token: str) -> Optional[str]:
    if not token:
        return None
    h = hashlib.sha256(token.encode()).hexdigest()
    now = time.time() * 1000
    for x in _load_store()["sessions"]:
        if x["hash"] == h:
            if x["expires_ms"] > now:
                return x["norm"]
            return None
    return None


def drop_session(token: str) -> None:
    h = hashlib.sha256(token.encode()).hexdigest()
    s = _load_store()
    s["sessions"] = [x for x in s["sessions"] if x["hash"] != h]
    _save_store()


def revoke_sessions(norm: str) -> None:
    s = _load_store()
    s["sessions"] = [x for x in s["sessions"] if x["norm"] != norm]
    _save_store()


def registered_user(norm: str) -> Optional[dict]:
    for u in _load_store()["users"]:
        if u["norm"] == norm:
            return u
    return None


# ── flows (rich results; the route maps to uniform responses) ─────────
def _public_base() -> str:
    return os.environ.get("THROWAWAY_PUBLIC_BASE",
                          "https://skale.dev/throway").rstrip("/")


def register_user(email, passphrase, invite_code=None, ip=None) -> dict:
    if registration_mode() == "closed":
        return {"status": "closed"}
    if not note_register_attempt(ip):
        return {"status": "rate-ip"}
    n = normalize_email(email)
    if not n or not n["ok"]:
        return {"status": "invalid-email"}
    if not isinstance(passphrase, str) or len(passphrase) < 8:
        return {"status": "weak-passphrase"}
    if registration_mode() == "invite":
        codes = _invite_codes()
        if not invite_code or str(invite_code).strip() not in codes:
            return {"status": "bad-invite"}
    existing = registered_user(n["norm"])
    # day-cap counts NEW accounts only (pending re-register creates nothing)
    if existing is None and _created_today_count() >= _daily_new_users_max():
        return {"status": "day-cap"}
    if existing is not None and existing["status"] == "active":
        if not _mail_cooldown_ok(n["norm"]):
            return {"status": "cooldown"}
        try:
            # informational; enumeration-safe only because the route is uniform
            mailer.send_verify_email(n["original"],
                                     f"{_public_base()}/api/auth/verify?token=already-have-an-account")
            _note_mail_sent(n["norm"])
        except Exception:
            pass
        return {"status": "exists"}
    if not _mail_cooldown_ok(n["norm"]):
        return {"status": "cooldown"}
    s = _load_store()
    if existing is None:
        s["users"].append({"email": n["original"], "norm": n["norm"],
                           "hash": hash_passphrase(passphrase),
                           "status": "pending",
                           "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "invite": (invite_code or None)})
    else:
        existing["hash"] = hash_passphrase(passphrase)   # pending re-register
    token = _mint_token("verify", n["norm"])
    try:
        mailer.send_verify_email(n["original"],
                                 f"{_public_base()}/api/auth/verify?token={token}")
        _note_mail_sent(n["norm"])
    except mailer.MailBudgetError:
        _save_store()
        return {"status": "mail-failed"}
    except Exception:
        _save_store()   # user+token survive — resend can recover
        return {"status": "mail-failed"}
    _save_store()
    return {"status": "pending-resent" if existing else "created"}


def verify_registration(token: str) -> Optional[dict]:
    t = _consume_token(token, "verify")
    if not t:
        return None
    u = registered_user(t["norm"])
    if not u:
        return None
    was_pending = u["status"] == "pending"
    u["status"] = "active"
    _save_store()
    return {"user": u["norm"], "email": u["email"], "was_pending": was_pending}


def resend_verification(email, ip=None) -> dict:
    if not note_register_attempt(ip):
        return {"status": "rate-ip"}
    n = normalize_email(email)
    if not n:
        return {"status": "sent"}          # uniform — never confirm existence
    u = registered_user(n["norm"])
    if not u or u["status"] != "pending":
        return {"status": "sent"}
    if not _mail_cooldown_ok(n["norm"]):
        return {"status": "cooldown"}
    token = _mint_token("verify", n["norm"])
    try:
        mailer.send_verify_email(u["email"],
                                 f"{_public_base()}/api/auth/verify?token={token}")
        _note_mail_sent(n["norm"])
    except Exception:
        return {"status": "mail-failed"}
    return {"status": "sent"}


def request_reset(email, ip=None) -> dict:
    if not note_register_attempt(ip):
        return {"status": "rate-ip"}
    n = normalize_email(email)
    if not n:
        return {"status": "sent"}
    u = registered_user(n["norm"])
    if not u:
        return {"status": "sent"}          # uniform
    if not _mail_cooldown_ok(n["norm"]):
        return {"status": "cooldown"}
    token = _mint_token("reset", n["norm"])
    try:
        mailer.send_reset_email(u["email"],
                                f"{_public_base()}/api/auth/reset?token={token}")
        _note_mail_sent(n["norm"])
    except Exception:
        return {"status": "mail-failed"}
    return {"status": "sent"}


def consume_reset(token: str, new_passphrase) -> dict:
    if not isinstance(new_passphrase, str) or len(new_passphrase) < 8:
        return {"status": "weak-passphrase"}
    t = _consume_token(token, "reset")
    if not t:
        return {"status": "invalid-token"}
    u = registered_user(t["norm"])
    if not u:
        return {"status": "invalid-token"}
    u["hash"] = hash_passphrase(new_passphrase)
    u["status"] = "active"
    _save_store()
    revoke_sessions(t["norm"])          # OWASP: identity change kills sessions
    return {"status": "ok", "user": u["norm"]}


# ── login (dummy-scrypt for unknown addresses — timing parity) ────────
_DUMMY_ENTRY = "a3f1c2e4b9d84760513a2b4c5d6e7f80:" + "0" * 64


def login(email, passphrase) -> Optional[str]:
    """Returns the norm on success, else None. Unknown addresses burn a
    dummy scrypt so response time does not leak existence."""
    n = normalize_email(email)
    u = registered_user(n["norm"]) if n else None
    if u is not None and u["status"] != "active":
        u = None
    entry = u["hash"] if u else _DUMMY_ENTRY
    if _verify_hash(passphrase or "", entry) and u:
        return u["norm"]
    return None


def maybe_send_welcome(email: str, was_pending: bool) -> None:
    if not was_pending:
        return
    try:
        mailer.send_welcome_email(email, _public_base())
    except Exception:
        pass                                # fire-and-forget, budget-guarded
