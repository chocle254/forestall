"""The AI check: an agent investigates the order with tools and decides everything about it.

On every check the agent itself pulls the carrier tracking, the live PayPal order, the buyer's messages
and the seller's policy, then decides: how likely an "item not received" dispute is and why, what it
found (each fact tagged with its source), when to look again, and whether the order is safe to close.
There are no hard-coded risk rules. Code only enforces safety rails (see watcher.py and recommend.py).
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db, investigate

Signal = Literal["no_tracking", "stalled", "buyer_concern", "delivered_not_received", "carrier_exception", "none"]


class Step(BaseModel):
    source: Literal["carrier", "paypal", "buyer", "seller_policy", "forestall"]
    checked: str = Field(description="What you looked at, e.g. 'Carrier tracking' or 'Buyer's last message'.")
    learned: str = Field(description="What it told you about THIS order, in one plain sentence.")


class Assessment(BaseModel):
    steps: list[Step] = Field(description="Your investigation, in order: each source you checked and what it showed.")
    risk_level: Literal["low", "medium", "high"]
    signal: Signal = Field(description="The single strongest risk signal, or 'none'.")
    reason: str = Field(description="One or two plain sentences a busy seller understands, citing the facts.")
    summary: str = Field(description="Two sentences max for the seller: what is going on and why.")
    parcel_status: Literal["not_shipped_yet", "moving_normally", "delayed_at_carrier", "possibly_lost", "delivery_failed",
                           "returning_to_sender", "delivered_confirmed", "delivered_possibly_misdelivered",
                           "not_on_paypal", "unknown"]
    likely_cause: str
    evidence: list[investigate.Evidence] = Field(description="Specific facts from tool results, each with its source.")
    buyer_mood: Literal["no_messages", "calm", "curious", "worried", "frustrated", "angry"]
    buyer_need: str | None = Field(default=None, description="Any deadline or need the buyer stated, else null.")
    resolution_options: list[str] = Field(
        default_factory=list, description="Concrete things the seller can offer the buyer TODAY under the seller policy.")
    confidence: Literal["low", "medium", "high"]
    next_check_in_hours: int = Field(description="When to look at this order again (1 to 24).")
    next_check_reason: str = Field(description="Why then, in a short plain sentence.")
    safe_to_close: bool = Field(description="True only if the order is delivered, the proof is on PayPal, and nothing "
                                            "suggests the buyer will dispute. Forestall then stops watching it.")


SYSTEM = """You are Forestall, an AI agent that protects small online sellers who take PayPal payments from
"item not received" (INR) disputes. You are checking ONE shipped order. Investigate it with your tools, then
judge how likely the buyer is to open an INR dispute soon, and why.

What you know about INR disputes:
- PayPal decides them on evidence: proof the parcel shipped and was delivered to the buyer's address, with the
  tracking on the PayPal transaction. Tracking the seller has but PayPal doesn't is no proof at all.
- Buyers dispute when the parcel is late against what they were promised, when tracking shows no progress,
  when the carrier reports a problem, when it says "delivered" but they have nothing, or when their questions go
  unanswered. A buyer with a deadline (a trip, a gift, an event) is far more likely to escalate.
- A dispute costs the seller the refund, the item, fees and account standing. A calm, specific fix sent early
  prevents most of them.

How to investigate (call several tools at once when you can):
- Always call get_carrier_tracking and get_paypal_order first, even if the brief seems to answer it: the brief
  is Forestall's copy, the tools are the live sources. Is the tracking on the transaction? Where is the buyer?
- Judge delay against the carrier's own expected delivery date and the scan pattern, not a fixed number of
  days. If the parcel sits at a facility, call get_carrier_facility_status for it.
- Tag each step with the source you actually got it from (the tool's source, not "forestall").
- If the carrier says delivered, compare the proof of delivery (GPS distance, photo) with the PayPal shipping
  address.
- Read the buyer's messages for tone, needs and deadlines, and whether they were answered.
- If there is risk, read the seller policy and list only resolution options it allows, concrete enough to offer
  today.

Then decide:
- signal: the single strongest risk: no_tracking (tracking missing from PayPal), stalled (no carrier progress
  that the expected date can absorb), buyer_concern (the buyer is worried or has a need the delay threatens),
  delivered_not_received, carrier_exception (the carrier reports failure, return, loss or damage), or none.
- risk_level: high = a dispute is likely without action now; medium = act soon; low = no action needed.
- next_check_in_hours (1 to 24): when something could change, e.g. just after the expected delivery date, the
  next scan you'd expect, or a buyer's deadline. Look sooner when risk is high or a buyer is waiting.
- safe_to_close: only when delivered, the tracking is on PayPal, and nothing points to a dispute. If delivery
  only just happened, it is fine to wait a day for the buyer to speak up.

Write for the seller: specific, calm, plain words. Say times as "about 3 days", "yesterday", never raw hours.
Report facts only from the order brief and tool results. Never invent."""


def _hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600, 1)


async def build_context(order_id: str) -> dict:
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where id = $1", order_id)
    if not o:
        raise LookupError("order not found")
    events = await pool.fetch(
        "select status, location, occurred_at, message from shipment_events where order_id = $1 order by occurred_at",
        order_id,
    )
    msgs = await pool.fetch(
        "select body, received_at from buyer_messages where order_id = $1"
        " and ($2::timestamptz is null or received_at > $2) order by received_at",
        order_id, o["replacement_shipped_at"],
    )
    # Simulated "now" for demo scenarios (orders aged by the simulator).
    now = datetime.now(timezone.utc) + timedelta(hours=o["demo_shift_hours"])
    captured = o["captured_at"]
    last_scan = events[-1] if events else None
    return {
        "order": {
            "amount": f"{o['amount']} {o['currency']}",
            "hours_since_payment": _hours(captured, now),
            "shipped": o["shipped_at"] is not None,
            "hours_since_shipped": _hours(o["shipped_at"], now) if o["shipped_at"] else None,
            "seller_has_tracking_number": bool(o["tracking_number"]),
            "carrier": o["carrier"],
            "tracking_attached_to_paypal": o["tracking_attached_at"] is not None,
            **({"replacement_shipped": True,
                "note": "The first parcel failed and a replacement was shipped; scans and messages below are "
                        "for the replacement only."} if o["replacement_shipped_at"] else {}),
        },
        "carrier_scans": [
            {"status": e["status"], "location": e["location"], "hours_after_payment": _hours(captured, e["occurred_at"]),
             **({"carrier_message": e["message"]} if e["message"] else {})}
            for e in events
        ],
        "hours_since_last_carrier_scan": _hours(last_scan["occurred_at"], now) if last_scan else None,
        "latest_carrier_status": last_scan["status"] if last_scan else None,
        "buyer_messages": [
            {"text": m["body"], "hours_after_payment": _hours(captured, m["received_at"])} for m in msgs
        ],
    }


async def assess(order_id: str, force: bool = False) -> dict:
    """The agent investigates and assesses the order. Unchanged orders reuse the last answer (no new AI call)."""
    pool = await db.get_pool()
    assert pool
    context = await build_context(order_id)
    # Round hours for the cache key so a page refresh a minute later doesn't cost a new AI run.
    key_ctx = {**json.loads(json.dumps(context)), "check_version": 2}  # bump to invalidate old cached answers
    key_ctx["order"]["hours_since_payment"] = int(context["order"]["hours_since_payment"] // 6)
    if key_ctx["hours_since_last_carrier_scan"] is not None:
        key_ctx["hours_since_last_carrier_scan"] = int(key_ctx["hours_since_last_carrier_scan"] // 6)
    input_hash = hashlib.sha256(json.dumps(key_ctx, sort_keys=True).encode()).hexdigest()[:16]

    cached = None if force else await pool.fetchrow(
        "select id, result, model, created_at from assessments where order_id = $1 and input_hash = $2"
        " order by created_at desc limit 1",
        order_id, input_hash,
    )
    trace: list[dict] = []
    if cached:
        assessment_id, result, model = cached["id"], json.loads(cached["result"]), cached["model"]
    else:
        order = await pool.fetchrow("select * from orders where id=$1", order_id)
        now = datetime.now(timezone.utc) + timedelta(hours=order["demo_shift_hours"])
        brief = {"buyer_name": order["buyer_name"], "now": now.isoformat(), **context}
        a, trace = await ai.run_agent(SYSTEM, "Check this order:\n" + json.dumps(brief, indent=2, default=str),
                                      investigate.TOOLS, investigate.tool_handlers(order, pool, now), Assessment,
                                      required_tools=("get_carrier_tracking", "get_paypal_order"))
        a.next_check_in_hours = max(1, min(24, a.next_check_in_hours))
        result = a.model_dump()
        result["tools_used"] = [t["tool"] for t in trace]
        model = ai.last_model_used or ai.model_name()
        assessment_id = await pool.fetchval(
            "insert into assessments (order_id, input_hash, input, result, model) values ($1,$2,$3,$4,$5) returning id",
            order_id, input_hash, json.dumps(context), json.dumps(result), model,
        )

    flag = await _sync_flag(order_id, assessment_id, result)
    if not cached:
        # Store the investigation (findings + full tool-call trace). The fix recommender builds on it.
        findings = {k: result.get(k) for k in investigate.Findings.model_fields}
        findings["parcel_status"] = findings["parcel_status"] if findings["parcel_status"] != "not_shipped_yet" else "unknown"
        await pool.execute(
            "insert into investigations (order_id, flag_id, findings, tool_calls, model) values ($1,$2,$3,$4,$5)",
            order_id, flag["id"] if flag else None, json.dumps(findings), json.dumps(trace, default=str), model)
    return {"assessment_id": str(assessment_id), "cached": cached is not None, "model": model, "context": context,
            **result, "flag": flag}


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
          updated_at = case when flags.signal is distinct from excluded.signal
                              or flags.risk_level is distinct from excluded.risk_level
                            then now() else flags.updated_at end,
          risk_level = excluded.risk_level, reason = excluded.reason, signal = excluded.signal,
          assessment_id = excluded.assessment_id
        returning id, risk_level, reason, signal, created_at, updated_at
        """,
        order_id, r["risk_level"], r["reason"], r["signal"], assessment_id,
    )
    return {k: (str(v) if k == "id" else v) for k, v in dict(row).items()}
