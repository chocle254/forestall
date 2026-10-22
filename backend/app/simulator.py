"""Delivery simulator: the PayPal sandbox has no carrier data, so we script it.

Each scenario rewrites an order's shipment history and ages the order
(demo_shift_hours) so a multi-day story plays out instantly.
"""

import random
from datetime import timedelta

from . import db

# (hours after payment, status, location)
SCENARIOS: dict[str, dict] = {
    "on_track": {
        "label": "On track: shipped, tracking on PayPal, delivered",
        "age_hours": 74,
        "attach_tracking": True,
        "scans": [(5, "label_created", "Austin, TX"), (20, "in_transit", "Dallas, TX hub"),
                  (52, "out_for_delivery", "Denver, CO"), (70, "delivered", "Denver, CO")],
        "messages": [],
    },
    "no_tracking": {
        "label": "Shipped 3 days ago, tracking never added to PayPal",
        "age_hours": 74,
        "attach_tracking": False,
        "scans": [(6, "label_created", "Austin, TX"), (22, "in_transit", "Dallas, TX hub"),
                  (49, "in_transit", "Kansas City, MO hub")],
        "messages": [],
    },
    "stalled": {
        "label": "Parcel stuck at a hub for 4 days",
        "age_hours": 124,
        "attach_tracking": True,
        "scans": [(5, "label_created", "Austin, TX"), (21, "in_transit", "Memphis, TN hub"),
                  (27, "in_transit", "Memphis, TN hub")],
        "messages": [],
    },
    "buyer_concern": {
        "label": "Buyer asking where their order is",
        "age_hours": 98,
        "attach_tracking": True,
        "scans": [(6, "label_created", "Austin, TX"), (23, "in_transit", "Memphis, TN hub")],
        "messages": [(92, "Hi, I ordered 4 days ago and the tracking hasn't changed since Tuesday. "
                          "Is my package lost? I needed it for a birthday this weekend.")],
    },
    "delivered_not_received": {
        "label": "Carrier says delivered, buyer says no",
        "age_hours": 86,
        "attach_tracking": True,
        "scans": [(5, "label_created", "Austin, TX"), (20, "in_transit", "Dallas, TX hub"),
                  (60, "out_for_delivery", "Chicago, IL"), (68, "delivered", "Chicago, IL")],
        "messages": [(81, "Tracking says delivered yesterday but there is nothing here. "
                          "I checked with my neighbours and the building's mailroom.")],
    },
}


def _tracking_number() -> str:
    return "9400" + "".join(random.choice("0123456789") for _ in range(18))


async def apply(order_id: str, scenario: str) -> dict:
    s = SCENARIOS.get(scenario)
    if not s:
        raise KeyError(scenario)
    pool = await db.get_pool()
    assert pool
    async with pool.acquire() as conn, conn.transaction():
        o = await conn.fetchrow("select captured_at, tracking_number from orders where id = $1", order_id)
        if not o:
            raise LookupError("order not found")
        captured = o["captured_at"]
        # How far "now" must move forward so the order is age_hours old.
        real_age = await conn.fetchval("select extract(epoch from now() - $1::timestamptz) / 3600", captured)
        shift = max(0, round(s["age_hours"] - float(real_age)))

        await conn.execute("delete from shipment_events where order_id = $1", order_id)
        await conn.execute("delete from buyer_messages where order_id = $1", order_id)
        await conn.executemany(
            "insert into shipment_events (order_id, status, location, occurred_at) values ($1,$2,$3,$4)",
            [(order_id, st, loc, captured + timedelta(hours=h)) for h, st, loc in s["scans"]],
        )
        await conn.executemany(
            "insert into buyer_messages (order_id, body, received_at) values ($1,$2,$3)",
            [(order_id, body, captured + timedelta(hours=h)) for h, body in s["messages"]],
        )
        tracking = o["tracking_number"] or _tracking_number()
        await conn.execute(
            """update orders set tracking_number = $2, carrier = 'USPS', demo_shift_hours = $3,
               tracking_attached_at = case when $4 then $5::timestamptz else null end
               where id = $1""",
            order_id, tracking, shift, s["attach_tracking"], captured + timedelta(hours=7),
        )
    return {"scenario": scenario, "label": s["label"], "shift_hours": shift}
