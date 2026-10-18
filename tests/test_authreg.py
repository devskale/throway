"""1.56.0 — Email-Registrierung (Blueprint-Port, LOGIN-FLOW.md).

Zwei Schichten:
  - in-process (authreg direkt): Token-/Reset-/Day-Cap-Logik, die über
    HTTP nicht sichtbar ist (Tokens stehen nur gehasht im Store)
  - subprocess (Server): Endpunkt-Vertrag (uniform, Cookies, TTL-Stufen)

SMTP bleibt unkonfiguriert → mailer dry-run (nie Netzwerk in Tests).
"""
import json
import os
import time

import pytest

from conftest import Server

AGENT = {"User-Agent": "curl/8.0"}

AUTH_ENV = {
    "REGISTRATION_MODE": "open",
    "REGISTER_DAILY_MAX": "20",
    "MAIL_DAILY_MAX": "1000",          # Tests wollen nie an die Wand
}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path / "main",
               env_extra={**AUTH_ENV,
                          "THROWAWAY_USERS_FILE": str(tmp_path / "main" / "users.json")})
    s.users_file = str(tmp_path / "main" / "users.json")
    yield s
    s.stop()


# ── in-process (authreg-Kern) ─────────────────────────────────────────
@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setenv("THROWAWAY_USERS_FILE", str(tmp_path / "u.json"))
    monkeypatch.setenv("REGISTRATION_MODE", "open")
    monkeypatch.setenv("MAIL_DAILY_MAX", "1000")
    monkeypatch.setenv("MAIL_PER_MINUTE", "1000")   # Tests wollen nie an die Wand
    import throway.authreg as a
    from throway import mailer
    mailer._reset_for_test()
    a.set_users_file_for_test(str(tmp_path / "u.json"))
    return a


def test_canonicalization(reg):
    n = reg.normalize_email("  Foo.Bar@Example.COM ")
    assert n["original"] == "Foo.Bar@Example.COM"
    assert n["norm"] == "foo.bar@example.com"     # whole-address lower, KEIN dot-folding
    assert n["ok"]
    assert reg.normalize_email("junk") is None
    assert reg.normalize_email("a@b") is None or not reg.normalize_email("a@b")["ok"]
    assert reg.normalize_email("a b@x.io")["ok"] is False


def test_register_verify_flow_and_single_use(reg):
    r = reg.register_user("Foo@Example.COM", "passphrase123", ip="1.1.1.1")
    assert r["status"] == "created"
    u = reg.registered_user("foo@example.com")
    assert u["status"] == "pending" and u["email"] == "Foo@Example.COM"
    token = reg._mint_token("verify", "foo@example.com")
    v = reg.verify_registration(token)
    assert v and v["was_pending"] and v["user"] == "foo@example.com"
    assert reg.verify_registration(token) is None          # single-use
    assert reg.registered_user("foo@example.com")["status"] == "active"


def test_register_guards(reg, monkeypatch):
    assert reg.register_user("bad", "passphrase123")["status"] == "invalid-email"
    assert reg.register_user("a@b.io", "short")["status"] == "weak-passphrase"
    monkeypatch.setenv("REGISTRATION_MODE", "closed")
    assert reg.register_user("a@b.io", "passphrase123")["status"] == "closed"
    monkeypatch.setenv("REGISTRATION_MODE", "invite")
    assert reg.register_user("a@b.io", "passphrase123")["status"] == "bad-invite"
    monkeypatch.setenv("THROWAWAY_INVITE_CODES", "beta1, beta2")
    assert reg.register_user("a@b.io", "passphrase123", invite_code=" beta2 ")["status"] == "created"


def test_ip_rate_wall(reg):
    for i in range(10):
        assert reg.register_user(f"u{i}@x.io", "passphrase123", ip="2.2.2.2")["status"] == "created"
    assert reg.register_user("u10@x.io", "passphrase123", ip="2.2.2.2")["status"] == "rate-ip"
    assert reg.register_user("other@x.io", "passphrase123", ip="3.3.3.3")["status"] == "created"


def test_day_cap_counts_new_only_and_kills(reg, monkeypatch):
    monkeypatch.setenv("REGISTER_DAILY_MAX", "1")
    assert reg.register_user("first@x.io", "passphrase123", ip="9.9.9.9")["status"] == "created"
    assert reg.register_user("second@x.io", "passphrase123", ip="9.9.9.8")["status"] == "day-cap"
    # pending re-register desselben accounts created nichts → kein day-cap
    reg.register_user("first@x.io", "passphrase1234", ip="9.9.9.7")
    # kill-switch
    monkeypatch.setenv("REGISTER_DAILY_MAX", "0")
    assert reg.register_user("third@x.io", "passphrase123", ip="9.9.9.6")["status"] == "day-cap"


def test_reset_kills_sessions(reg):
    reg.register_user("r@x.io", "passphrase123", ip="4.4.4.4")
    reg.verify_registration(reg._mint_token("verify", "r@x.io"))
    s1 = reg.issue_session("r@x.io")
    tok = reg._mint_token("reset", "r@x.io")
    assert reg.consume_reset(tok, "short")["status"] == "weak-passphrase"   # early, token unberührt
    assert reg.consume_reset(tok, "new-passphrase-1")["status"] == "ok"
    assert reg.session_norm(s1) is None                                     # OWASP: alle Sessions tot
    assert reg.login("r@x.io", "new-passphrase-1") == "r@x.io"


def test_login_unknown_burns_dummy(reg):
    reg.register_user("k@x.io", "passphrase123", ip="5.5.5.5")
    reg.verify_registration(reg._mint_token("verify", "k@x.io"))
    assert reg.login("k@x.io", "wrong-passphrase") is None
    assert reg.login("unknown@x.io", "whatever-pass") is None               # dummy-Pfad, kein Crash
    assert reg.login("k@x.io", "passphrase123") == "k@x.io"
    # pending accounts können sich nicht einloggen
    reg.register_user("p@x.io", "passphrase123", ip="5.5.5.4")
    assert reg.login("p@x.io", "passphrase123") is None


def test_store_perms_and_shape(reg):
    reg.register_user("s@x.io", "passphrase123", ip="6.6.6.6")
    p = reg._users_file()
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    d = json.load(open(p))
    assert set(d.keys()) == {"users", "tokens", "sessions"}
    assert "passphrase" not in json.dumps(d)          # niemals klartext


def test_mailer_budget_walls(monkeypatch):
    monkeypatch.setenv("MAIL_DAILY_MAX", "2")
    monkeypatch.setenv("MAIL_PER_MINUTE", "1")
    from throway import mailer
    mailer._reset_for_test()
    mailer.send_mail("a@x.io", "t1", "x")             # dry-run (unconfigured)
    try:
        mailer.send_mail("a@x.io", "t2", "x")
        assert False, "minute wall"
    except mailer.MailBudgetError as e:
        assert e.code == "EMAIL_BUDGET_MINUTE"
    mailer._minute_stamps.clear()
    mailer.send_mail("a@x.io", "t3", "x")
    try:
        mailer.send_mail("a@x.io", "t4", "x")
        assert False, "daily wall"
    except mailer.MailBudgetError as e:
        assert e.code == "EMAIL_BUDGET_DAILY"


# ── subprocess (Endpunkt-Vertrag) ─────────────────────────────────────
def _auth_srv_user(srv, tmp_path):
    """Aktiven User direkt in den (geteilten) Store schreiben — in-process,
    gleiche Datei wie der Server (mtime-cache lädt nach)."""
    import throway.authreg as a
    a.set_users_file_for_test(srv.users_file)
    a.register_user("e2e@x.io", "passphrase123", ip="7.7.7.7")
    a.verify_registration(a._mint_token("verify", "e2e@x.io"))
    return a


def test_config_and_whoami(srv):
    st, _, b = srv.get("/api/auth/config")
    d = json.loads(b)
    assert st == 200 and d["registration"] == "open"
    assert d["mail_configured"] is False          # dry-run sichtbar, nie still
    st, _, b = srv.get("/api/auth/whoami")
    assert st == 200 and json.loads(b)["user"] is None


def test_register_uniform_and_guards(srv):
    st, _, b = srv.post("/api/auth/register",
                        json.dumps({"email": "anon@x.io", "passphrase": "passphrase123"}).encode(),
                        {**AGENT, "Content-Type": "application/json"})
    assert st == 200
    d = json.loads(b)
    assert d.get("ok") and "eligible" in d.get("message", "")
    # invalid email / weak passphrase sind bewusst NICHT uniform (400)
    st, _, _ = srv.post("/api/auth/register", json.dumps({"email": "junk", "passphrase": "passphrase123"}).encode(),
                        {**AGENT, "Content-Type": "application/json"})
    assert st == 400
    st, _, _ = srv.post("/api/auth/register", json.dumps({"email": "j2@x.io", "passphrase": "short"}).encode(),
                        {**AGENT, "Content-Type": "application/json"})
    assert st == 400


def test_login_session_logout(srv, tmp_path):
    a = _auth_srv_user(srv, tmp_path)
    st, h, b = srv.post("/api/auth/login",
                        json.dumps({"email": "E2E@x.io", "passphrase": "passphrase123"}).encode(),
                        {**AGENT, "Content-Type": "application/json"})
    assert st == 200 and json.loads(b)["user"] == "e2e@x.io"
    cookie = h.get("Set-Cookie", "")
    assert cookie.startswith("ta_session=") and "HttpOnly" in cookie
    jar = {"Cookie": cookie.split(";")[0]}
    st, _, b = srv.get("/api/auth/whoami", jar)
    assert json.loads(b)["user"] == "e2e@x.io"
    st, _, b = srv.post("/api/auth/logout", b"", {**AGENT, **jar,
                        "Content-Type": "application/json"})
    assert st == 200
    st, _, b = srv.get("/api/auth/whoami", jar)
    assert json.loads(b)["user"] is None
    # falsche credentials
    st, _, _ = srv.post("/api/auth/login",
                        json.dumps({"email": "e2e@x.io", "passphrase": "nope-nope-nope"}).encode(),
                        {**AGENT, "Content-Type": "application/json"})
    assert st == 401


def test_anonymous_ttl_cap_and_authed_14d(srv, tmp_path):
    st, _, b = srv.post("/?x=1&ttl=7d", b"hello", {**AGENT, "Content-Type": "text/plain"})
    assert st == 403 and "register" in json.loads(b)["error"].lower()
    st, _, b = srv.post("/?x=1&ttl=2h", b"hello", {**AGENT, "Content-Type": "text/plain"})
    assert st == 200, b                                        # ≤4h bleibt frei
    fid = json.loads(b)["id"]
    # PUT-extend anonym über cap → 403
    st, _, _ = srv.put(f"/{fid}?ttl=14d", b"", AGENT)
    assert st == 403
    # authed: 14d upload + PUT-extend
    a = _auth_srv_user(srv, tmp_path)
    sess = a.issue_session("e2e@x.io")
    jar = {"Cookie": f"ta_session={sess}"}
    st, _, b = srv.post("/?x=1&ttl=14d", b"long lived", {**AGENT, **jar, "Content-Type": "text/plain"})
    assert st == 200, b
    st, _, b = srv.put(f"/{fid}?ttl=14d", b"", {**AGENT, **jar})
    assert st == 200 and json.loads(b)["expires_in"] == 14 * 24 * 3600
    # 20d wird still auf 14d geklemmt (altes Verhalten für authed)
    st, _, b = srv.post("/?x=1&ttl=20d", b"x", {**AGENT, **jar, "Content-Type": "text/plain"})
    assert st == 200 and json.loads(b)["expires_in"] == 14 * 24 * 3600


def test_dir_ttl_gate(srv):
    st, _, b = srv.post("/?dir=1&ttl=14d", b"", AGENT)
    assert st == 403 and "register" in json.loads(b)["error"].lower()
    st, _, b = srv.post("/?dir=1&ttl=7d", b"", AGENT)          # dir-default bleibt anonym frei
    assert st == 200, b
