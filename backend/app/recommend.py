"""AI job 2 of 3: recommend ONE fix for a flagged order.

The model picks the action and writes the words; the backend fills in every
PayPal parameter from real order data and enforces limits, so the model can't
invent a tracking number or refund more than allowed.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db, risk

MAX_REFUND_SHARE = 0.25     # never propose refunding more than 25% of the order
PAYPAL_NOTE_LIMIT = 255     # PayPal's note_to_payer limit
MESSAGE_LIMIT = 600


class Recommendation(BaseModel):
    type: Literal["attach_tracking", "message_buyer", "partial_refund"]
    justification: str = Field(description="One sentence for the seller: why this fix, for this order, now.")
    buyer_message: str | None = Field(
        default=None,
        description="message_buyer: the full message to send. partial_refund: a short note PayPal shows the buyer "
                    "(max 255 chars). attach_tracking: leave null.")
    refund_amount: float | None = Field(default=None, description="partial_refund only: amount in the order currency.")


SYSTEM = """You are Forestall's resolution agent. A small online seller's PayPal order has been flagged as at risk
of an "item not received" (INR) dispute. Recommend exactly ONE fix the seller can approve with one tap.

Available fixes:
- attach_tracking: put the seller's existing tracking number on the PayPal transaction (PayPal also emails the
  buyer the tracking). Only possible if the seller has a tracking number that is not yet on PayPal. This is the
  strongest fix whenever it is possible: it gives the seller proof for any dispute.
- message_buyer: a short, warm, specific message from the seller to the buyer (what is happening, what the
  seller is doing, when to expect an update). Best when tracking is already on PayPal and the buyer is anxious,
  the parcel is stalled, or delivery is disputed.
- partial_refund: a small goodwill refund (at most {max_share}% of the order) with a short note. Only when the
  buyer has been clearly inconvenienced (for example a long stall plus a worried buyer with a deadline).
  Never as the first step for a simple missing-tracking problem.

Write as the seller, in plain friendly English, no jargon, no promises you can't keep (do not promise a delivery
date the facts don't support). Never invent tracking numbers, carriers, or facts."""


def _paypal_call(rtype: str, order) -> str:
    if rtype == "attach_tracking":
        return f"POST /v2/checkout/orders/{order['paypal_order_id']}/track"
    if rtype == "partial_refund":
        return f"POST /v2/payments/captures/{order['capture_id']}/refund"
    return "none (PayPal has no pre-dispute messaging API; the seller sends it, Forestall records it)"


def _build_params(rec: Recommendation, order) -> dict:
    """Turn the model's choice into exact, validated parameters. Raises ValueError if impossible."""
    if rec.type == "attach_tracking":
        if not order["tracking_number"]:
            raise ValueError("attach_tracking needs a tracking number, and this order has none")
        if order["tracking_attached_at"]:
            raise ValueError("tracking is already on the PayPal transaction")
        return {"tracking_number": order["tracking_number"], "carrier": order["carrier"] or "USPS",
                "notify_payer": True, "capture_id": order["capture_id"]}
    if rec.type == "message_buyer":
        msg = (rec.buyer_message or "").strip()
        if len(msg) < 20:
            raise ValueError("message_buyer needs the full message text")
        return {"message": msg[:MESSAGE_LIMIT], "to": order["buyer_email"] or order["buyer_name"]}
    # partial_refund
    total = float(order["amount"])
    cap = round(total * MAX_REFUND_SHARE, 2)
    amount = round(float(rec.refund_amount or 0), 2)
    if amount <= 0:
        raise ValueError("partial_refund needs a positive refund_amount")
    if amount > cap:
        raise ValueError(f"refund_amount {amount} is above the {int(MAX_REFUND_SHARE*100)}% cap ({cap})")
    note = (rec.buyer_message or "").strip()[:PAYPAL_NOTE_LIMIT]
    return {"amount": f"{amount:.2f}", "currency": order["currency"], "note_to_payer": note,
            "capture_id": order["capture_id"], "share_of_order_pct": round(100 * amount / total, 1)}


async def recommend(order_id: str, force: bool = False) -> dict | None:
    """Propose a fix for the order's open flag. Returns the proposal, or None if there is no flag."""
    pool = await db.get_pool()
    assert pool
    flag = await pool.fetchrow("select * from flags where order_id=$1 and not resolved", order_id)
    if not flag:
        return None
    existing = await pool.fetchrow(
        "select * from actions where flag_id=$1 and status='proposed' order by created_at desc limit 1", flag["id"])
    # Reuse the proposal unless the flag changed since it was made.
    if existing and not force and existing["created_at"] >= flag["updated_at"]:
        return _out(existing)

    order = await pool.fetchrow("select * from orders where id=$1", order_id)
    context = await risk.build_context(order_id)
    prompt = json.dumps({
        "flag": {"risk_level": flag["risk_level"], "signal": flag["signal"], "reason": flag["reason"]},
        "order": context,
        "buyer_name": order["buyer_name"],
        "seller_has_tracking_number_not_on_paypal": bool(order["tracking_number"]) and not order["tracking_attached_at"],
        "max_refund": round(float(order["amount"]) * MAX_REFUND_SHARE, 2),
    }, indent=2, default=str)
    system = SYSTEM.replace("{max_share}", str(int(MAX_REFUND_SHARE * 100)))

    rec = await ai.chat_json(system, "Recommend one fix for this flagged order:\n" + prompt, Recommendation)
    try:
        params = _build_params(rec, order)
    except ValueError as e:
        # Tell the model what was wrong once, then accept only a valid answer.
        rec = await ai.chat_json(system, f"Recommend one fix for this flagged order:\n{prompt}\n\n"
                                 f"Your previous answer was not possible: {e}. Choose again.", Recommendation)
        params = _build_params(rec, order)
    params["paypal_call"] = _paypal_call(rec.type, order)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "update actions set status='superseded', updated_at=now() where flag_id=$1 and status='proposed'", flag["id"])
        row = await conn.fetchrow(
            """insert into actions (flag_id, order_id, type, params, justification, status, model)
               values ($1,$2,$3,$4,$5,'proposed',$6) returning *""",
            flag["id"], order_id, rec.type, json.dumps(params), rec.justification, ai.last_model_used or ai.model_name())
    return _out(row)


def _out(row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d["params"]) if isinstance(d["params"], str) else d["params"]
    return {k: (str(v) if k in ("id", "flag_id", "order_id") and v is not None else v) for k, v in d.items()}


async def supersede_for_order(conn, order_id) -> None:
    """Risk cleared: any pending proposal for this order is no longer needed."""
    await conn.execute(
        "update actions set status='superseded', updated_at=now() where order_id=$1 and status='proposed'", order_id)
