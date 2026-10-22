"""AI job 1 of 3: assess an order's risk of an "item not received" dispute."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db

Signal = Literal["no_tracking", "stalled", "buyer_concern", "delivered_not_received", "none"]


class Assessment(BaseModel):
    risk_level: Literal["low", "medium", "high"]
    signal: Signal = Field(description="The single strongest risk signal, or 'none'.")
    reason: str = Field(description="One or two plain sentences a busy seller understands, citing the facts.")
    facts_used: list[str] = Field(description="The specific facts from the input that drove the decision.")


SYSTEM = """You are Forestall's risk analyst. Forestall protects small online sellers who take PayPal
payments from "item not received" (INR) disputes. A seller wins an INR dispute only with proof of
shipment and delivery, and that proof must be on the PayPal transaction (tracking attached).

Assess ONE order and decide how likely the buyer is to open an INR dispute soon, and why.

Signals (pick the single strongest one):
- no_tracking: the seller has not attached tracking to the PayPal transaction and it has been more than
  48 hours since payment. Without it the seller has no proof on record.
- stalled: the carrier has shown no new scan for 72 hours or more and the parcel is not delivered.
- buyer_concern: the buyer has written in a worried or frustrated way about where their order is.
- delivered_not_received: the carrier says delivered but the buyer says they did not get it.
- none: nothing worrying yet.

Risk level: high = a dispute is likely without action now; medium = action recommended soon;
low = no action needed. Use the precomputed hour counts; do not do your own date arithmetic.
Write the reason for the seller: specific, calm, no jargon, under 45 words. Never invent facts."""


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600, 1)


async def build_context(order_id: str) -> dict:
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id = $1", order_id)
    if not o:
        raise LookupError("order not found")
    events = await pool.fetch(
        "select status, location, occurred_at from shipment_events where order_id = $1 order by occurred_at", order_id
    )
    msgs = await pool.fetch(
        "select body, received_at from buyer_messages where order_id = $1 order by received_at", order_id
    )
    # Simulated "now" for demo scenarios (orders aged by the simulator).
    now = datetime.now(timezone.utc) + timedelta(hours=o["demo_shift_hours"])
    captured = o["captured_at"]
    last_scan = events[-1] if events else None
    return {
        "order": {
            "amount": f"{o['amount']} {o['currency']}",
            "hours_since_payment": _hours(captured, now),
            "seller_has_tracking_number": bool(o["tracking_number"]),
            "carrier": o["carrier"],
            "tracking_attached_to_paypal": o["tracking_attached_at"] is not None,
        },
        "carrier_scans": [
            {"status": e["status"], "location": e["location"], "hours_after_payment": _hours(captured, e["occurred_at"])}
            for e in events
        ],
        "hours_since_last_carrier_scan": _hours(last_scan["occurred_at"], now) if last_scan else None,
        "latest_carrier_status": last_scan["status"] if last_scan else None,
        "buyer_messages": [
            {"text": m["body"], "hours_after_payment": _hours(captured, m["received_at"])} for m in msgs
        ],
    }


async def assess(order_id: str, force: bool = False) -> dict:
    """Assess an order, reusing the last answer if nothing about the order changed."""
    pool = await db.get_pool()
    assert pool
    context = await build_context(order_id)
    # Round hours for the cache key so a page refresh a minute later doesn't cost a new AI call.
    key_ctx = json.loads(json.dumps(context))
    key_ctx["order"]["hours_since_payment"] = int(context["order"]["hours_since_payment"] // 6)
    if key_ctx["hours_since_last_carrier_scan"] is not None:
        key_ctx["hours_since_last_carrier_scan"] = int(key_ctx["hours_since_last_carrier_scan"] // 6)
    input_hash = hashlib.sha256(json.dumps(key_ctx, sort_keys=True).encode()).hexdigest()[:16]

    cached = None if force else await pool.fetchrow(
        "select id, result, model, created_at from assessments where order_id = $1 and input_hash = $2"
        " order by created_at desc limit 1",
        order_id, input_hash,
    )
    if cached:
        assessment_id, result = cached["id"], json.loads(cached["result"])
    else:
        a = await ai.chat_json(SYSTEM, "Assess this order:\n" + json.dumps(context, indent=2), Assessment)
        result = a.model_dump()
        assessment_id = await pool.fetchval(
            "insert into assessments (order_id, input_hash, input, result, model) values ($1,$2,$3,$4,$5) returning id",
            order_id, input_hash, json.dumps(context), json.dumps(result), ai.model_name(),
        )

    flag = await _sync_flag(order_id, assessment_id, result)
    return {"assessment_id": str(assessment_id), "cached": cached is not None, "context": context, **result, "flag": flag}


async def _sync_flag(order_id: str, assessment_id, r: dict) -> dict | None:
    """Open/update the order's flag when risk is medium+, close it when risk drops."""
    pool = await db.get_pool()
    assert pool
    if r["risk_level"] == "low" or r["signal"] == "none":
        await pool.execute(
            "update flags set resolved = true, updated_at = now() where order_id = $1 and not resolved", order_id
        )
        return None
    row = await pool.fetchrow(
        """
        insert into flags (order_id, risk_level, reason, signal, assessment_id)
        values ($1, $2, $3, $4, $5)
        on conflict (order_id) where not resolved do update set
          risk_level = excluded.risk_level, reason = excluded.reason, signal = excluded.signal,
          assessment_id = excluded.assessment_id, updated_at = now()
        returning id, risk_level, reason, signal, created_at, updated_at
        """,
        order_id, r["risk_level"], r["reason"], r["signal"], assessment_id,
    )
    return {k: (str(v) if k == "id" else v) for k, v in dict(row).items()}
