"""mailer — outbound transactional mail (email-registration blueprint,
Python port of aiui server/mailer.js; LOGIN-FLOW.md 2026-10-18).

Stdlib-only (throway is zero-dep): smtplib + email.message. Env shape is
the fleet contract:

    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / MAIL_FROM
    MAIL_DAILY_MAX (default 100) / MAIL_PER_MINUTE (default 10)

Budget walls live HERE, not at the provider (later OCI Email Delivery:
3k/month, 100/day, 10/min hard) — a runaway resend loop hits an
in-process wall. In-memory, UTC-day reset (protects the channel, not the
accounting). Unconfigured → dry-run: renders the mail, returns
{"dry_run": True, ...} instead of sending. Tests never touch the network.

Session-restart resets counters — acceptable per blueprint.
"""
from __future__ import annotations

import os
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate

_SERVICE = os.environ.get("THROWAWAY_MAIL_SERVICE", "throway")


def _daily_max() -> int:
    return int(os.environ.get("MAIL_DAILY_MAX", "100"))


def _per_minute_max() -> int:
    return int(os.environ.get("MAIL_PER_MINUTE", "10"))


_sent_today = 0
_day_key = ""
_minute_stamps: list[float] = []


class MailBudgetError(RuntimeError):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


def _budget_state(now: float | None = None) -> dict:
    now = time_now() if now is None else now
    today = datetime.fromtimestamp(now / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    global _sent_today, _day_key
    if today != _day_key:
        _day_key = today
        _sent_today = 0
    while _minute_stamps and now - _minute_stamps[0] >= 60_000:
        _minute_stamps.pop(0)
    return {"sent_today": _sent_today, "minute_count": len(_minute_stamps)}


def time_now() -> float:
    return time.time() * 1000


def assert_mail_budget(now: float | None = None) -> None:
    s = _budget_state(now)
    if s["sent_today"] >= _daily_max():
        raise MailBudgetError("EMAIL_BUDGET_DAILY",
                              f"mail budget: daily cap reached ({_daily_max()})")
    if s["minute_count"] >= _per_minute_max():
        raise MailBudgetError("EMAIL_BUDGET_MINUTE",
                              f"mail budget: per-minute cap reached ({_per_minute_max()})")


def peek_mail_budget() -> dict:
    s = _budget_state()
    return {**s, "daily_max": _daily_max(), "per_minute_max": _per_minute_max()}


def mail_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASS") and os.environ.get("MAIL_FROM"))


# ── core send ─────────────────────────────────────────────────────────
_transport = None
_transport_error: Exception | None = None


def _get_transport() -> smtplib.SMTP:
    global _transport, _transport_error
    if _transport_error is not None:
        raise _transport_error
    if _transport is not None:
        return _transport
    if not mail_configured():
        _transport_error = RuntimeError(
            "mailer: SMTP_HOST/SMTP_USER/SMTP_PASS/MAIL_FROM not set")
        raise _transport_error
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    t = smtplib.SMTP(host, port, timeout=20)
    t.starttls()                       # 587 + STARTTLS (kasserver contract)
    t.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    _transport = t
    return _transport


def _reset_for_test() -> None:
    global _transport, _transport_error, _sent_today, _day_key
    _sent_today = 0
    _day_key = ""
    _minute_stamps.clear()
    _transport = None
    _transport_error = None


def send_mail(to: str, subject: str, text: str, html: str | None = None) -> dict:
    """Send one transactional mail (or dry-run). Raises MailBudgetError
    when a cap would break; SMTP failures raise smtplib exceptions so
    callers can map them to mail-failed (slot freed — consume only on
    successful submission, like the JS contract)."""
    assert_mail_budget()
    frm = os.environ.get("MAIL_FROM", "")
    consume = _consume
    if not mail_configured():
        consume()  # dry-run consumes budget too — the wall behaves identically
        return {"dry_run": True, "from": frm, "to": to, "subject": subject,
                "text": text, "html": html}
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    _get_transport().send_message(msg)
    consume()
    return {"dry_run": False, "to": to}


def _consume() -> None:
    now = time_now()
    _budget_state(now)
    global _sent_today
    _sent_today += 1
    _minute_stamps.append(now)


# ── templates (one purpose, one CTA; TTL stated; text is the truth) ───
def _footer() -> str:
    base = os.environ.get("THROWAWAY_PUBLIC_BASE", "https://skale.dev/throway")
    return (f"You received this because of an action on {_SERVICE} — the "
            f"disposable file store at {base}.")


def _base_html(title: str, body: str, cta_label: str, url: str, footer: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#fafaf9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#1a1a1a;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:2rem 1rem;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:34rem;background:#ffffff;border:1px solid #e5e5e5;border-radius:.5rem;">
<tr><td style="padding:1.5rem 1.75rem;">
<h2 style="margin:0 0 .75rem;font-size:1.15rem;">{title}</h2>
<p style="margin:0 0 1rem;line-height:1.55;color:#374151;">{body}</p>
<p style="margin:1.25rem 0 .5rem;"><a href="{url}" style="display:inline-block;background:#1a1a1a;color:#ffffff;text-decoration:none;padding:.65rem 1.1rem;border-radius:.35rem;">{cta_label}</a></p>
<p style="margin:0 0 .25rem;font-size:.85rem;color:#6b7280;">Or copy this link:<br><a href="{url}" style="color:#1a1a1a;word-break:break-all;">{url}</a></p>
<p style="margin:1.5rem 0 0;padding-top:1rem;border-top:1px solid #e5e5e5;font-size:.78rem;color:#9ca3af;line-height:1.5;">{footer}</p>
</td></tr></table></td></tr></table></body></html>"""


def send_verify_email(to: str, url: str, ttl_minutes: int = 30) -> dict:
    subject = f"{_SERVICE}: verify your email"
    text = (f"Verify your email for {_SERVICE}.\n\n"
            f"Open this link to activate your account (valid for {ttl_minutes} minutes):\n"
            f"{url}\n\nIf you did not request this, ignore this mail.\n\n{_footer()}")
    html = _base_html("Verify your email",
                      f"Open the link below to activate your {_SERVICE} account.",
                      "Verify email", url,
                      f"Link valid for <strong>{ttl_minutes} minutes</strong>. "
                      f"Didn&rsquo;t request this? Ignore this mail.<br>{_footer()}")
    return send_mail(to, subject, text, html)


def send_reset_email(to: str, url: str, ttl_minutes: int = 30) -> dict:
    subject = f"{_SERVICE}: reset your passphrase"
    text = (f"A passphrase reset was requested for {_SERVICE}.\n\n"
            f"Open this link to set a new passphrase (valid for {ttl_minutes} minutes):\n"
            f"{url}\n\nIf you did not request this, ignore this mail — your "
            f"passphrase stays unchanged.\n\n{_footer()}")
    html = _base_html("Reset your passphrase",
                      f"A reset was requested for your {_SERVICE} account.",
                      "Reset passphrase", url,
                      f"Link valid for <strong>{ttl_minutes} minutes</strong>. "
                      f"Didn&rsquo;t request this? Ignore this mail — your "
                      f"passphrase stays unchanged.<br>{_footer()}")
    return send_mail(to, subject, text, html)


def send_welcome_email(to: str, login_url: str) -> dict:
    subject = f"Welcome to {_SERVICE}"
    text = (f"Your {_SERVICE} account is active.\n\nSign in:\n{login_url}\n\n"
            f"What you get:\n- lifetimes up to 14 days on uploads (anonymous: 4 h)\n"
            f"- extend any of your objects while they live\n\n{_footer()}")
    html = _base_html(f"Welcome to {_SERVICE}",
                      "Your account is active. Longer lifetimes unlocked.",
                      f"Open {_SERVICE}", login_url,
                      f"Lifetimes up to 14 days; extend while it lives.<br>{_footer()}")
    return send_mail(to, subject, text, html)


def send_test_mail(to: str) -> dict:
    subject = f"{_SERVICE} mailer test"
    text = (f"Mailer transport test from {_SERVICE}.\n\nIf you read this, "
            f"SMTP submission works end to end.\n\n{_footer()}")
    html = _base_html("Mailer test", "SMTP submission works end to end.",
                      f"Open {_SERVICE}",
                      os.environ.get("THROWAWAY_PUBLIC_BASE",
                                     "https://skale.dev/throway"), _footer())
    return send_mail(to, subject, text, html)
