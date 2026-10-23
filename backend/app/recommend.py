"""AI job 3 of 4: recommend ONE fix for a flagged order, based on the investigation.

The model picks the action and writes the words; the backend fills in every
PayPal parameter from real order data and enforces limits, so the model can't
invent a tracking number or refund more than allowed.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db, investigate, risk

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
    offers: list[Literal["replacement", "full_refund", "partial_refund", "info_only"]] = Field(
        default_factory=list,
        description="What the buyer message offers. Must match the message text exactly.")


SYSTEM = """You are Forestall's resolution agent. A PayPal order is at risk of an "item not received" (INR)
dispute, and Forestall's investigator has already found out what happened (the findings). Recommend exactly ONE
fix the seller can approve with one tap. The goal is to resolve the buyer's problem today, so they have no
reason to open a dispute.

Available fixes:
- attach_tracking: put the seller's existing tracking number on the PayPal transaction (PayPal also emails the
  buyer the tracking). Only possible if the seller has a tracking number that is not yet on PayPal. Always the
  right fix when tracking is missing from PayPal: it gives the seller proof for any dispute.
- message_buyer: a message from the seller that (1) tells the buyer what Forestall found, with specifics
  (where the parcel is, what the carrier reports), and (2) gives a concrete resolution TODAY, taken from the
  findings' resolution_options; if the buyer has a deadline the delay will miss, offer a choice
  (for example "a replacement shipped today by Priority Mail, or a full refund; just reply 1 or 2").
- partial_refund: a small goodwill refund (at most {max_share}% of the order) with a short note, when the buyer
  was inconvenienced but the item is still coming.

Message rules:
- Lead with the finding, then the resolution. Never end with only "I'll look into it" or "I'll update you
  tomorrow": the buyer must leave the message with an answer or a choice they can act on now.
- Be specific to THIS order: buyer's first name, the facility or city, what the carrier reported, the
  buyer's stated need. No generic filler.
- Times in plain words ("since Tuesday", "a few days"), never raw hours.
- Never claim the seller already did something that hasn't happened. Offering to do it is fine.
- Offer only what "offers_allowed_now" lists (the seller's policy, decided from the facts), and declare in
  "offers" exactly what your message offers. Never mention a full refund or a replacement unless it is allowed.
  When only information and a small goodwill refund are allowed, explain the situation and what happens next
  with a specific, fact-based expectation (for example the carrier's stated delay). Under 90 words.
The justification is for the seller: one plain sentence citing the key finding."""


def _paypal_call(rtype: str, order) -> str:
    if rtype == "attach_tracking":
        return f"POST /v2/checkout/orders/{order['paypal_order_id']}/track"
    if rtype == "partial_refund":
        return f"POST /v2/payments/captures/{order['capture_id']}/refund"
    return "none (PayPal has no pre-dispute messaging API; the seller sends it, Forestall records it)"


def allowed_offers(findings: dict, context: dict) -> set[str]:
    """What the seller's policy allows Forestall to offer, decided in code from the facts (not by the model)."""
    allowed = {"info_only", "partial_refund"}
    status = findings.get("parcel_status")
    stuck_days = (context.get("hours_since_last_carrier_scan") or 0) / 24
    serious = (
        bool(findings.get("buyer_need"))
        or status in ("delivered_possibly_misdelivered", "possibly_lost", "delivery_failed", "returning_to_sender")
        or (status != "delivered_confirmed" and stuck_days >= 7)
    )
    if serious:
        allowed |= {"replacement", "full_refund"}
    elif status == "delayed_at_carrier" and context.get("order", {}).get("hours_since_payment", 0) >= 72:
        allowed.add("replacement")  # past the expected delivery window: a replacement may be offered
    return allowed


def check_offers(rec: Recommendation, allowed: set[str]) -> None:
    """Reject messages that offer more than the policy allows (declared or written)."""
    if rec.type != "message_buyer":
        return
    text = (rec.buyer_message or "").lower()
    offered = set(rec.offers)
    if "full refund" in text or "refund in full" in text:
        offered.add("full_refund")
    if "replacement" in text or "send another" in text or "ship another" in text:
        offered.add("replacement")
    too_much = offered - allowed
    if too_much:
        raise ValueError(f"the message offers {sorted(too_much)}, which the seller policy does not allow here; "
                         f"allowed: {sorted(allowed)}")


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
    # Investigate first (or reuse this flag's investigation if nothing changed since).
    inv = await investigate.latest_for_flag(order_id, flag["id"])
    if not inv or force or inv["created_at"] < flag["updated_at"]:
        inv = await investigate.investigate(order_id)
    allowed = allowed_offers(inv["findings"], context)
    prompt = json.dumps({
        "flag": {"risk_level": flag["risk_level"], "signal": flag["signal"], "reason": flag["reason"]},
        "findings": inv["findings"],
        "order": context,
        "buyer_name": order["buyer_name"],
        "seller_has_tracking_number_not_on_paypal": bool(order["tracking_number"]) and not order["tracking_attached_at"],
        "max_refund": round(float(order["amount"]) * MAX_REFUND_SHARE, 2),
        "offers_allowed_now": sorted(allowed),
    }, indent=2, default=str)
    system = SYSTEM.replace("{max_share}", str(int(MAX_REFUND_SHARE * 100)))

    rec = await ai.chat_json(system, "Recommend one fix for this flagged order:\n" + prompt, Recommendation)
    try:
        check_offers(rec, allowed)
        params = _build_params(rec, order)
    except ValueError as e:
        # Tell the model what was wrong once, then accept only a valid answer.
        rec = await ai.chat_json(system, f"Recommend one fix for this flagged order:\n{prompt}\n\n"
                                 f"Your previous answer was not possible: {e}. Choose again.", Recommendation)
        check_offers(rec, allowed)
        params = _build_params(rec, order)
    if rec.type == "message_buyer":
        params["offers"] = sorted(set(rec.offers) or {"info_only"})
        params["offers_allowed"] = sorted(allowed)
    params["paypal_call"] = _paypal_call(rec.type, order)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "update actions set status='superseded', updated_at=now() where flag_id=$1 and status='proposed'", flag["id"])
        row = await conn.fetchrow(
            """insert into actions (flag_id, order_id, type, params, justification, status, model, investigation_id)
               values ($1,$2,$3,$4,$5,'proposed',$6,$7) returning *""",
            flag["id"], order_id, rec.type, json.dumps(params), rec.justification, ai.last_model_used or ai.model_name(),
            inv["id"])
    return _out(row)


def _out(row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d["params"]) if isinstance(d["params"], str) else d["params"]
    return {k: (str(v) if k in ("id", "flag_id", "order_id", "investigation_id") and v is not None else v)
            for k, v in d.items()}


async def supersede_for_order(conn, order_id) -> None:
    """Risk cleared: any pending proposal for this order is no longer needed."""
    await conn.execute(
        "update actions set status='superseded', updated_at=now() where order_id=$1 and status='proposed'", order_id)
