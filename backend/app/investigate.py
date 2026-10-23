"""AI job 2 of 4: investigate a flagged order with tools, then report findings.

The agent decides which tools to call. Carrier tools read the simulated carrier
(the sandbox has none); the PayPal tool is a live PayPal API call.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, db, easypost, paypal, simulator

# Default seller policy. It decides what Forestall may offer a buyer *today*,
# so a message can resolve the problem instead of promising to "get back to you".
SELLER_POLICY = {
    "replacement": "Can ship a replacement the same day by USPS Priority Mail (1 to 3 days).",
    "max_goodwill_refund_pct": 25,
    "full_refund_if_no_carrier_movement_days": 7,
    "delivered_not_received": "Share the carrier's delivery proof, file a USPS misdelivery request, and offer a "
                              "replacement or refund at once if the proof does not match the buyer's address.",
    "deadline_at_risk": "If the buyer has a deadline the carrier delay will miss, offer a same-day replacement "
                        "or a full refund, buyer's choice.",
}


class Evidence(BaseModel):
    source: Literal["carrier", "paypal", "buyer", "forestall"]
    fact: str


class Findings(BaseModel):
    summary: str = Field(description="Two sentences max for the seller: what is going on and why.")
    parcel_status: Literal["moving_normally", "delayed_at_carrier", "possibly_lost", "delivered_confirmed",
                           "delivered_possibly_misdelivered", "not_on_paypal", "unknown"]
    likely_cause: str
    evidence: list[Evidence] = Field(description="Specific facts from tool results, each with its source.")
    buyer_need: str | None = Field(default=None, description="Any deadline or need the buyer stated, else null.")
    resolution_options: list[str] = Field(
        description="Concrete things the seller can offer the buyer TODAY under the seller policy.")
    confidence: Literal["low", "medium", "high"]


SYSTEM = """You are Forestall's investigator. A PayPal order has been flagged as at risk of an "item not received"
dispute. Before anyone contacts the buyer, find out what actually happened, using the tools.

How to investigate:
- Always check the carrier tracking and the PayPal order.
- If the parcel is stuck at a facility, check that facility's service alerts.
- If the carrier says delivered, compare the proof of delivery (GPS distance, photo) with the shipping address
  on the PayPal order. More than about 100 m away, or a photo that doesn't match the address, suggests
  misdelivery.
- Read the buyer's messages for needs or deadlines.
- Read the seller policy and list only resolution options it allows, concrete enough to offer today.

Report facts only from tool results, each tagged with its source. Never invent."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_carrier_tracking",
        "description": "Carrier tracking for this order's parcel: scans, exceptions, expected delivery, proof of delivery.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_carrier_facility_status",
        "description": "Service alerts (backlogs, outages) at a carrier facility, e.g. 'Memphis, TN hub'.",
        "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}},
    {"type": "function", "function": {
        "name": "get_paypal_order",
        "description": "Live PayPal order: status, amount, buyer shipping address, tracking on the transaction.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_buyer_messages",
        "description": "Messages the buyer has sent about this order.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_seller_policy",
        "description": "What the seller allows Forestall to offer buyers (replacements, refunds).",
        "parameters": {"type": "object", "properties": {}}}},
]


async def investigate(order_id: str) -> dict:
    pool = await db.get_pool()
    assert pool
    order = await pool.fetchrow("select * from orders where id=$1", order_id)
    if not order:
        raise LookupError("order not found")
    flag = await pool.fetchrow("select * from flags where order_id=$1 and not resolved", order_id)
    now = datetime.now(timezone.utc) + timedelta(hours=order["demo_shift_hours"])

    async def get_carrier_tracking():
        if order["carrier_source"] == "easypost" and order["easypost_tracker_id"]:
            return easypost.summarize(await easypost.get_tracker(order["easypost_tracker_id"]))
        scans = await pool.fetch(
            "select status, location, occurred_at from shipment_events where order_id=$1 order by occurred_at", order_id)
        return simulator.carrier_tracking(order, scans, now)

    async def get_carrier_facility_status(location: str = ""):
        if order["carrier_source"] == "easypost":
            return {"source": "EasyPost", "location": location,
                    "alerts": "EasyPost does not publish facility alerts; rely on the tracker's status_detail."}
        return simulator.network_status(order, location, now)

    async def get_paypal_order():
        po = await paypal.get_order(order["paypal_order_id"])
        unit = (po.get("purchase_units") or [{}])[0]
        return {
            "source": "PayPal Orders API (live sandbox call)",
            "status": po.get("status"),
            "amount": unit.get("amount"),
            "ship_to": (unit.get("shipping") or {}).get("address"),
            "ship_to_name": ((unit.get("shipping") or {}).get("name") or {}).get("full_name"),
            "trackers_on_transaction": paypal.trackers_on(po),
        }

    async def get_buyer_messages():
        rows = await pool.fetch("select body, received_at from buyer_messages where order_id=$1 order by received_at",
                                order_id)
        return {"messages": [{"text": r["body"], "time": r["received_at"].isoformat()} for r in rows]}

    async def get_seller_policy():
        return SELLER_POLICY

    handlers = {f.__name__: f for f in (get_carrier_tracking, get_carrier_facility_status, get_paypal_order,
                                        get_buyer_messages, get_seller_policy)}
    brief = {
        "flag": {"risk_level": flag["risk_level"], "signal": flag["signal"], "reason": flag["reason"]} if flag else None,
        "buyer_name": order["buyer_name"],
        "order_value": f"{order['amount']} {order['currency']}",
        "now": now.isoformat(),
    }
    findings, trace = await ai.run_agent(SYSTEM, "Investigate this flagged order:\n" + json.dumps(brief, default=str),
                                         TOOLS, handlers, Findings)
    row = await pool.fetchrow(
        "insert into investigations (order_id, flag_id, findings, tool_calls, model) values ($1,$2,$3,$4,$5) returning *",
        order_id, flag["id"] if flag else None, json.dumps(findings.model_dump()), json.dumps(trace, default=str),
        ai.last_model_used or ai.model_name())
    return out(row)


def out(row) -> dict:
    d = dict(row)
    for k in ("findings", "tool_calls"):
        d[k] = json.loads(d[k]) if isinstance(d[k], str) else d[k]
    return {k: (str(v) if k in ("id", "order_id", "flag_id") and v is not None else v) for k, v in d.items()}


async def latest_for_flag(order_id: str, flag_id) -> dict | None:
    pool = await db.get_pool()
    assert pool
    row = await pool.fetchrow(
        "select * from investigations where order_id=$1 and flag_id=$2 order by created_at desc limit 1", order_id, flag_id)
    return out(row) if row else None
