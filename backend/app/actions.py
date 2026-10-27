"""Slice 7: the seller approves, edits or dismisses a fix; Forestall executes it on PayPal.

Nothing here runs without a seller tap. A buyer's reply to an offer only *proposes*
the follow-up (refund or replacement); the seller approves that too.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db, easypost, emails, paypal, recommend, watcher

TITLES = {
    "attach_tracking": "attach tracking to PayPal",
    "message_buyer": "message to the buyer",
    "partial_refund": "goodwill partial refund",
    "full_refund": "full refund",
    "ship_replacement": "ship a replacement",
}


def _now(order) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=order["demo_shift_hours"])


async def _load(action_id: str):
    pool = await db.get_pool()
    assert pool
    a = await pool.fetchrow("select * from actions where id::text=$1", action_id)
    if not a:
        raise LookupError("action not found")
    o = await pool.fetchrow("select * from orders where id=$1", a["order_id"])
    params = json.loads(a["params"]) if isinstance(a["params"], str) else a["params"]
    return pool, a, o, params


# --- Edit / dismiss -----------------------------------------------------------

async def edit(action_id: str, message: str | None = None, note_to_payer: str | None = None,
               amount: float | None = None) -> dict:
    pool, a, o, p = await _load(action_id)
    if a["status"] != "proposed":
        raise ValueError(f"action is {a['status']}; only proposed fixes can be edited")
    if message is not None:
        if a["type"] != "message_buyer":
            raise ValueError("only a buyer message has message text")
        msg = message.strip()
        if not 20 <= len(msg) <= recommend.MESSAGE_LIMIT:
            raise ValueError(f"message must be 20-{recommend.MESSAGE_LIMIT} characters")
        p["message"] = msg
    if note_to_payer is not None:
        if a["type"] not in ("partial_refund", "full_refund"):
            raise ValueError("only refunds carry a note to the buyer")
        p["note_to_payer"] = note_to_payer.strip()[:recommend.PAYPAL_NOTE_LIMIT]
    if amount is not None:
        if a["type"] != "partial_refund":
            raise ValueError("only a partial refund amount can be edited")
        cap = round(float(o["amount"]) * recommend.MAX_REFUND_SHARE, 2)
        if not 0 < amount <= cap:
            raise ValueError(f"amount must be between 0 and {cap:.2f} ({int(recommend.MAX_REFUND_SHARE*100)}% cap)")
        p["amount"] = f"{amount:.2f}"
        p["share_of_order_pct"] = round(100 * amount / float(o["amount"]), 1)
    row = await pool.fetchrow("update actions set params=$2, edited=true, updated_at=now() where id=$1 returning *",
                              a["id"], json.dumps(p))
    async with pool.acquire() as conn:
        await watcher.log(conn, o["id"], "action", f"Seller edited the {TITLES[a['type']]}", {"action_id": str(a["id"])},
                          _now(o))
    return recommend._out(row)


async def dismiss(action_id: str) -> dict:
    pool, a, o, _ = await _load(action_id)
    if a["status"] != "proposed":
        raise ValueError(f"action is {a['status']}; only proposed fixes can be dismissed")
    row = await pool.fetchrow("update actions set status='dismissed', updated_at=now() where id=$1 returning *", a["id"])
    async with pool.acquire() as conn:
        await watcher.log(conn, o["id"], "action", f"Seller dismissed the {TITLES[a['type']]}",
                          {"action_id": str(a["id"])}, _now(o))
    return recommend._out(row)


# --- Approve + execute --------------------------------------------------------

async def approve(action_id: str) -> dict:
    """Seller approved: execute the fix. PayPal fixes are real sandbox API calls."""
    pool, a, o, p = await _load(action_id)
    if a["status"] != "proposed":
        raise ValueError(f"action is {a['status']}; only proposed fixes can be approved")
    await pool.execute("update actions set status='approved', updated_at=now() where id=$1", a["id"])
    async with pool.acquire() as conn:
        await watcher.log(conn, o["id"], "action", f"Seller approved: {TITLES[a['type']]}",
                          {"action_id": str(a["id"]), "edited": a["edited"]}, _now(o))

    try:
        result, resolution = await _execute(a, o, p)
    except paypal.PayPalError as e:
        await pool.execute("update actions set status='failed', paypal_response=$2, updated_at=now() where id=$1",
                           a["id"], json.dumps({"status": e.status, "error": e.body}, default=str))
        async with pool.acquire() as conn:
            await watcher.log(conn, o["id"], "action", f"PayPal rejected the {TITLES[a['type']]}",
                              {"paypal_status": e.status, "paypal_error": e.body}, _now(o))
        return {"action_id": str(a["id"]), "status": "failed", "paypal_status": e.status, "paypal_error": e.body}

    await pool.execute(
        "update actions set status='executed', executed_at=now(), paypal_response=$2, updated_at=now() where id=$1",
        a["id"], json.dumps(result, default=str))
    await pool.execute("update orders set resolution=$2 where id=$1", o["id"], resolution)

    # Let the watcher re-assess with the new facts (e.g. tracking now on PayPal clears the flag).
    status = await pool.fetchval("select watch_status from orders where id=$1", o["id"])
    after = await watcher.check(o["id"], trigger="fix executed") if status == "watching" else {"watch_status": status}
    return {"action_id": str(a["id"]), "status": "executed", "type": a["type"], "resolution": resolution,
            "result": result, "after": after}


async def _execute(a, o, p) -> tuple[dict, str]:
    pool = await db.get_pool()
    assert pool
    now = _now(o)
    kind = a["type"]

    if kind == "attach_tracking":
        res = await watcher.attach_tracking(o["id"], actor="Forestall, approved by seller", notify_payer=True)
        return {"paypal_order": res.get("id"), "paypal_status": res.get("status"),
                "trackers": paypal.trackers_on(res)}, "tracking_fixed"

    if kind in ("partial_refund", "full_refund"):
        amount = p.get("amount") if kind == "partial_refund" else None
        res = await paypal.refund_capture(o["capture_id"], amount, o["currency"], p.get("note_to_payer"),
                                          idempotency_key=f"refund-{a['id']}")
        refunded = float((res.get("amount") or {}).get("value") or amount or o["amount"])
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("update orders set refunded_amount = refunded_amount + $2 where id=$1", o["id"], refunded)
            await watcher.log(conn, o["id"], "paypal", f"PayPal refunded ${refunded:.2f} ({res.get('status')})",
                              {"refund_id": res.get("id"), "status": res.get("status"), "paypal_response": res}, now)
            if kind == "full_refund":
                await conn.execute("update flags set resolved=true, updated_at=now() where order_id=$1 and not resolved", o["id"])
                await conn.execute("update orders set watch_status='closed', next_check_at=null where id=$1", o["id"])
                await watcher.log(conn, o["id"], "protected", "Resolved: buyer refunded in full before any dispute",
                                  {"refund_id": res.get("id")}, now)
        return {"refund_id": res.get("id"), "status": res.get("status"), "amount": refunded}, (
            "refunded" if kind == "full_refund" else "goodwill_refund")

    if kind == "message_buyer":
        # Email it when SMTP is set up; otherwise the seller sends it and Forestall records it.
        mail = await emails.seller_message(o, str(p.get("message"))) if emails.configured() else {"sent": False}
        channel = "email" if mail.get("sent") else "seller"
        title = "Message emailed to the buyer" if channel == "email" else "Message sent to the buyer (by the seller)"
        async with pool.acquire() as conn:
            await watcher.log(conn, o["id"], "action", title,
                              {"message": p.get("message"), "offers": p.get("offers"), "awaiting_reply": True,
                               "channel": channel}, now)
        return {"sent": True, "channel": channel, "email": mail, "offers": p.get("offers")}, "awaiting_buyer_reply"

    if kind == "ship_replacement":
        res = await ship_replacement(o)
        return res, "replacement_shipped"

    raise ValueError(f"unknown action type {kind}")


async def _cancel_paypal_tracker(o, now) -> None:
    pool = await db.get_pool()
    assert pool
    tracker_id = f"{o['capture_id']}-{o['tracking_number']}" if o["capture_id"] else None
    if not tracker_id or not o["tracking_attached_at"]:
        return
    try:
        res = await paypal.cancel_tracker(o["paypal_order_id"], tracker_id)
        title, detail = "PayPal: first parcel's tracking marked CANCELLED", {"tracker_id": tracker_id, "paypal_response": res}
    except paypal.PayPalError as e:
        title, detail = "PayPal did not cancel the first parcel's tracking", {"tracker_id": tracker_id, "error": e.body}
    async with pool.acquire() as conn:
        await watcher.log(conn, o["id"], "paypal", title, detail, now)


async def ship_replacement(o) -> dict:
    """Send a replacement: new tracking on the PayPal transaction, then keep watching the new parcel."""
    pool = await db.get_pool()
    assert pool
    now = _now(o)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("update flags set resolved=true, updated_at=now() where order_id=$1 and not resolved", o["id"])
        await recommend.supersede_for_order(conn, o["id"])
        # The watch now follows the replacement only; the first parcel's scans stay on the timeline,
        # and the first parcel itself is still followed in the background.
        last = await conn.fetchval(
            "select status from shipment_events where order_id=$1 order by occurred_at desc limit 1", o["id"])
        await conn.execute("delete from shipment_events where order_id=$1", o["id"])
        await conn.execute(
            """update orders set replacement_shipped_at=$2, original_tracking_number=tracking_number,
               original_easypost_tracker_id=easypost_tracker_id, original_parcel_status=$3 where id=$1""",
            o["id"], now, last)
        await watcher.log(conn, o["id"], "action", "To do: file a USPS loss claim for the first parcel",
                          {"tracking_number": o["tracking_number"],
                           "how": "usps.com/help/claims.htm (or your carrier account). Forestall keeps following "
                                  "the first parcel and will tell you if it is returned or delivered late."}, now)
    # Cancel the failed parcel's tracking on PayPal so the record shows one cancelled, one live parcel.
    await _cancel_paypal_tracker(o, now)
    if easypost.configured():
        tracker = await easypost.create_tracker("EZ4000000004", o["carrier"] or "USPS")
        times = [datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00"))
                 for e in easypost.details_to_events(tracker) if e["occurred_at"]]
        offset_s, extra_h = 0, 0
        if easypost.test_mode() and times:
            offset_s = int(((now + timedelta(hours=1)) - min(times)).total_seconds())
            last = max(times) + timedelta(seconds=offset_s)
            extra_h = max(0, int(((last - now).total_seconds() + 3599) // 3600) + 1)
        await pool.execute(
            """update orders set tracking_number='EZ4000000004', carrier_source='easypost', easypost_tracker_id=$2,
               tracking_public_url=$3, carrier_time_offset_s=$4, demo_shift_hours=demo_shift_hours+$5,
               demo_scenario=null, tracking_attached_at=null, watch_status='watching', next_check_at=now() where id=$1""",
            o["id"], tracker["id"], tracker.get("public_url"), offset_s, extra_h)
        async with pool.acquire() as conn:
            await watcher.log(conn, o["id"], "shipped", "Replacement shipped · USPS EZ4000000004 (EasyPost)",
                              {"easypost_tracker": tracker["id"]}, now + timedelta(hours=1))
        await watcher.ingest_easypost(o["id"], tracker)
        number = "EZ4000000004"
    else:
        from .simulator import tracking_number
        number = tracking_number()
        await pool.execute(
            """update orders set tracking_number=$2, carrier_source='manual', demo_scenario=null,
               tracking_attached_at=null, watch_status='watching', next_check_at=now() where id=$1""", o["id"], number)
        async with pool.acquire() as conn:
            await watcher.log(conn, o["id"], "shipped", f"Replacement shipped · USPS {number}", {}, now)
    res = await watcher.attach_tracking(o["id"], actor="Forestall, replacement approved by seller", notify_payer=True)
    return {"tracking_number": number, "paypal_order": res.get("id"), "trackers": paypal.trackers_on(res)}


# --- The buyer replies to an offer ---------------------------------------------

class ReplyIntent(BaseModel):
    choice: Literal["replacement", "full_refund", "partial_refund", "accepts_wait", "unclear"]
    summary: str = Field(description="One short sentence: what the buyer wants.")


REPLY_SYSTEM = """You read a buyer's reply to a seller's message about a delayed or missing order. The seller's
message offered specific options (numbered in the order they appear in the message). Decide which option the
buyer chose. If the buyer is happy to wait, choose accepts_wait. If you can't tell, choose unclear."""


async def buyer_reply(order_id: str, text: str) -> dict:
    """Record the buyer's reply; if they picked an offered option, propose it for the seller to approve."""
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id::text=$1", order_id)
    if not o:
        raise LookupError("order not found")
    now = _now(o)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("insert into buyer_messages (order_id, body, received_at) values ($1,$2,$3)", o["id"], text, now)
        await watcher.log(conn, o["id"], "buyer_message", "Buyer replied", {"text": text}, now)

    sent = await pool.fetchrow(
        "select * from actions where order_id=$1 and type='message_buyer' and status='executed'"
        " order by executed_at desc limit 1", o["id"])
    if not sent:
        return {"recorded": True, "intent": None, "proposal": None}
    sp = json.loads(sent["params"]) if isinstance(sent["params"], str) else sent["params"]
    offers = set(sp.get("offers") or [])

    intent = await ai.chat_json(REPLY_SYSTEM, json.dumps(
        {"seller_message": sp.get("message"), "offers": sorted(offers), "buyer_reply": text}), ReplyIntent)

    if intent.choice == "accepts_wait":
        await pool.execute("update orders set resolution='buyer_reassured' where id=$1", o["id"])
        async with pool.acquire() as conn:
            await watcher.log(conn, o["id"], "action", "Buyer is happy to wait", {"summary": intent.summary}, now)
        return {"recorded": True, "intent": intent.model_dump(), "proposal": None}
    if intent.choice == "unclear" or intent.choice not in offers:
        async with pool.acquire() as conn:
            await watcher.log(conn, o["id"], "action", "Buyer reply needs the seller's attention",
                              {"summary": intent.summary, "choice": intent.choice}, now)
        return {"recorded": True, "intent": intent.model_dump(), "proposal": None}

    params: dict = {}
    if intent.choice == "full_refund":
        params = {"amount": f"{float(o['amount']) - float(o['refunded_amount']):.2f}", "currency": o["currency"],
                  "note_to_payer": "Refund as agreed. Sorry about the trouble with your order.",
                  "paypal_call": f"POST /v2/payments/captures/{o['capture_id']}/refund"}
        kind = "full_refund"
    elif intent.choice == "replacement":
        params = {"carrier": o["carrier"] or "USPS", "service": "Priority Mail",
                  "paypal_call": f"POST /v2/checkout/orders/{o['paypal_order_id']}/track (new tracking)"}
        kind = "ship_replacement"
    else:
        cap = round(float(o["amount"]) * recommend.MAX_REFUND_SHARE, 2)
        params = {"amount": f"{cap:.2f}", "currency": o["currency"], "note_to_payer": "A goodwill refund for the delay.",
                  "share_of_order_pct": round(100 * cap / float(o["amount"]), 1),
                  "paypal_call": f"POST /v2/payments/captures/{o['capture_id']}/refund"}
        kind = "partial_refund"

    async with pool.acquire() as conn, conn.transaction():
        await recommend.supersede_for_order(conn, o["id"])
        row = await conn.fetchrow(
            """insert into actions (flag_id, order_id, type, params, justification, status, model, parent_action_id)
               values ($1,$2,$3,$4,$5,'proposed',$6,$7) returning *""",
            sent["flag_id"], o["id"], kind, json.dumps(params), f"The buyer chose this: {intent.summary}",
            ai.last_model_used or ai.model_name(), sent["id"])
        await watcher.log(conn, o["id"], "action", f"Buyer chose: {TITLES[kind]} (awaiting your approval)",
                          {"summary": intent.summary, "action_id": str(row["id"])}, now)
    return {"recorded": True, "intent": intent.model_dump(), "proposal": recommend._out(row)}
