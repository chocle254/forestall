"""Buyer emails over SMTP: shipping confirmation and the seller's approved message.

Email is best effort: a failure is written to the order's timeline and never breaks
the step that triggered it. In the sandbox, set EMAIL_REDIRECT_TO so every buyer
email goes to your own inbox (sandbox buyer addresses don't receive mail).
"""

import asyncio
import contextvars
import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import db
from .config import settings

# Public origin of the current request (set by middleware in main.py), used for links in emails.
ORIGIN: contextvars.ContextVar[str] = contextvars.ContextVar("origin", default="")


def configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def status() -> str:
    return "ok" if configured() else "not_configured"


def base_url() -> str:
    return (settings.public_url or ORIGIN.get() or "").rstrip("/")


def track_url(order) -> str:
    return f"{base_url()}/track/{order['track_token']}"


def _send_sync(msg: EmailMessage) -> None:
    port = settings.smtp_port
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, port, context=ctx, timeout=20) as s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return
    with smtplib.SMTP(settings.smtp_host, port, timeout=20) as s:
        if settings.smtp_starttls:
            s.starttls(context=ctx)
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def build(to: str, subject: str, text: str, html_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.store_name, settings.smtp_from))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = settings.smtp_reply_to or settings.smtp_from
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.split("@")[-1] or None)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _recipient(order) -> tuple[str | None, str]:
    """(address, note). In the sandbox every email goes to EMAIL_REDIRECT_TO."""
    if settings.email_redirect_to:
        return settings.email_redirect_to, f"sandbox: redirected from {order['buyer_email'] or 'buyer'}"
    return order["buyer_email"], ""


def _page(heading: str, paragraphs: list[str], button: tuple[str, str] | None, quote: str | None = None) -> str:
    """Small, email-client-safe HTML (inline styles, one column)."""
    esc = html.escape
    body = "".join(f'<p style="margin:0 0 14px">{esc(p)}</p>' for p in paragraphs)
    if quote:
        body += ('<div style="margin:0 0 18px;padding:12px 14px;border-left:3px solid #116149;background:#f3f7f4;'
                 f'white-space:pre-wrap">{esc(quote)}</div>')
    if button:
        body += (f'<p style="margin:22px 0"><a href="{esc(button[1])}" style="background:#116149;color:#fff;'
                 'padding:11px 18px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">'
                 f'{esc(button[0])}</a></p>')
    return (
        '<div style="background:#f5f6f4;padding:24px 12px;font-family:Inter,Segoe UI,Arial,sans-serif;color:#121a16">'
        '<div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e3e7e3;border-radius:12px;padding:24px">'
        f'<div style="font-weight:700;color:#116149;margin-bottom:16px">{esc(settings.store_name)}</div>'
        f'<h1 style="font-size:20px;margin:0 0 14px">{esc(heading)}</h1>{body}'
        '</div><p style="text-align:center;color:#8a938e;font-size:12px;margin-top:14px">'
        'Order updates powered by Forestall</p></div>'
    )


async def _deliver(order, kind: str, subject: str, text: str, html_body: str) -> dict:
    """Send and record it on the timeline. Never raises."""
    from . import watcher  # local import: watcher imports this module

    pool = await db.get_pool()
    assert pool
    to, note = _recipient(order)
    if not configured():
        return {"sent": False, "reason": "email not configured"}
    if not to:
        async with pool.acquire() as conn:
            await watcher.log(conn, order["id"], "email", "Email not sent: no buyer email address",
                              {"kind": kind}, watcher._now(order))
        return {"sent": False, "reason": "no buyer email"}
    if settings.email_redirect_to:
        subject = f"[Sandbox · to {order['buyer_name'] or 'buyer'}] {subject}"
    try:
        await asyncio.to_thread(_send_sync, build(to, subject, text, html_body))
    except Exception as e:  # SMTP is best effort
        async with pool.acquire() as conn:
            await watcher.log(conn, order["id"], "email", "Email to the buyer failed",
                              {"kind": kind, "error": f"{type(e).__name__}: {str(e)[:160]}"}, watcher._now(order))
        return {"sent": False, "reason": f"{type(e).__name__}"}
    async with pool.acquire() as conn:
        await watcher.log(conn, order["id"], "email", f"Emailed the buyer: {subject}",
                          {"kind": kind, "to": to, "note": note or None}, watcher._now(order))
    return {"sent": True, "to": to}


async def shipping_confirmation(order_id) -> dict:
    """Automatic "your order is on its way" with the tracking page link."""
    if not configured() or not settings.auto_shipping_email:
        return {"sent": False, "reason": "off"}
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id=$1", order_id)
    if not o:
        return {"sent": False, "reason": "order not found"}
    first = (o["buyer_name"] or "there").split(" ")[0]
    url = track_url(o)
    carrier = f"{o['carrier'] or 'USPS'} {o['tracking_number']}"
    text = (f"Hi {first},\n\nGood news: your order is on its way.\nTracking: {carrier}\n\n"
            f"Follow it here, and message us from the same page if anything looks wrong:\n{url}\n\n"
            f"Thanks,\n{settings.store_name}")
    page = _page("Your order is on its way", [
        f"Hi {first},", "Good news: your order has shipped.", f"Tracking: {carrier}",
        "You can follow every scan on your tracking page, and message us from there if anything looks wrong.",
    ], ("Track your order", url))
    return await _deliver(o, "shipping_confirmation", "Your order is on its way", text, page)


async def seller_message(order, message: str) -> dict:
    """The seller approved the AI's message: email it, with the tracking page for replies."""
    url = track_url(order)
    text = (f"{message}\n\nTrack your order and reply here (fastest):\n{url}\n\n{settings.store_name}")
    page = _page("An update on your order", [], ("View your order and reply", url), quote=message)
    return await _deliver(order, "seller_message", "An update on your order", text, page)


async def send_test() -> dict:
    """Prove the SMTP settings work. Only ever sends to the seller's own address."""
    if not configured():
        raise ValueError("SMTP is not configured (set SMTP_HOST and SMTP_FROM)")
    address = settings.email_redirect_to or settings.smtp_reply_to or settings.smtp_from
    msg = build(address, "Forestall test email",
                "SMTP works. Forestall can now email your buyers.",
                _page("SMTP works", ["Forestall can now email your buyers shipping updates and your approved messages."], None))
    await asyncio.to_thread(_send_sync, msg)
    return {"sent": True, "to": address}
