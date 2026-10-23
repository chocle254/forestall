"""Store PayPal captures as Forestall orders."""

import json
from datetime import datetime
from typing import Any

from . import db

# Small demo catalog so test orders look like a real small seller's shop.
CATALOG = [
    {"sku": "WALLET-BRN", "name": "Handstitched leather wallet", "price": 42.00},
    {"sku": "MUG-CER-2", "name": "Ceramic pour-over mug (set of 2)", "price": 36.50},
    {"sku": "TOTE-KIKOY", "name": "Kikoy cotton tote bag", "price": 24.00},
    {"sku": "SOAP-SHEA-3", "name": "Shea butter soap trio", "price": 18.75},
]


# Demo buyers' shipping addresses (sent to PayPal on test-card orders, so PayPal holds them).
DEMO_ADDRESSES = [
    {"address_line_1": "1420 N Clark St", "address_line_2": "Apt 5B", "admin_area_2": "Chicago", "admin_area_1": "IL",
     "postal_code": "60610", "country_code": "US"},
    {"address_line_1": "2876 Pearl St", "admin_area_2": "Denver", "admin_area_1": "CO",
     "postal_code": "80205", "country_code": "US"},
    {"address_line_1": "410 S 16th St", "admin_area_2": "Omaha", "admin_area_1": "NE",
     "postal_code": "68102", "country_code": "US"},
    {"address_line_1": "77 Peachtree Pl NW", "admin_area_2": "Atlanta", "admin_area_1": "GA",
     "postal_code": "30309", "country_code": "US"},
]


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def summarize_capture(order: dict) -> dict[str, Any]:
    """Pull the fields we store from a captured Orders v2 response."""
    unit = (order.get("purchase_units") or [{}])[0]
    capture = ((unit.get("payments") or {}).get("captures") or [{}])[0]
    payer = order.get("payer") or {}
    name = payer.get("name") or {}
    shipping = unit.get("shipping") or {}
    amount = capture.get("amount") or unit.get("amount") or {}
    return {
        "paypal_order_id": order["id"],
        "capture_id": capture.get("id"),
        "buyer_name": " ".join(p for p in (name.get("given_name"), name.get("surname")) if p)
        or (shipping.get("name") or {}).get("full_name"),
        "buyer_email": payer.get("email_address"),
        "amount": amount.get("value", "0"),
        "currency": amount.get("currency_code", "USD"),
        "captured_at": _parse_time(capture.get("create_time")),
        "status": capture.get("status") or order.get("status"),
        "ship_to": shipping.get("address"),
    }


async def upsert(o: dict[str, Any]) -> dict:
    pool = await db.get_pool()
    assert pool, "database not configured"
    row = await pool.fetchrow(
        """
        insert into orders (paypal_order_id, capture_id, buyer_name, buyer_email, amount, currency, captured_at, ship_to)
        values ($1, $2, $3, $4, $5::numeric, $6, coalesce($7, now()), $8)
        on conflict (paypal_order_id) do update set
          ship_to     = coalesce(excluded.ship_to, orders.ship_to),
          capture_id  = coalesce(excluded.capture_id, orders.capture_id),
          buyer_name  = coalesce(excluded.buyer_name, orders.buyer_name),
          buyer_email = coalesce(excluded.buyer_email, orders.buyer_email)
        returning *, (xmax = 0) as inserted
        """,
        o["paypal_order_id"], o["capture_id"], o["buyer_name"], o["buyer_email"],
        str(o["amount"]), o["currency"], o["captured_at"],
        json.dumps(o["ship_to"]) if o.get("ship_to") else None,
    )
    if row["inserted"]:
        await pool.execute(
            "insert into order_timeline (order_id, kind, title, detail, occurred_at) values ($1,'paid',$2,$3,$4)",
            row["id"], "Payment captured by PayPal",
            json.dumps({"capture_id": row["capture_id"], "amount": str(row["amount"]), "currency": row["currency"]}),
            row["captured_at"],
        )
    return {k: v for k, v in dict(row).items() if k != "inserted"}
