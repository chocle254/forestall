"""Store PayPal captures as Forestall orders."""

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
    }


async def upsert(o: dict[str, Any]) -> dict:
    pool = await db.get_pool()
    assert pool, "database not configured"
    row = await pool.fetchrow(
        """
        insert into orders (paypal_order_id, capture_id, buyer_name, buyer_email, amount, currency, captured_at)
        values ($1, $2, $3, $4, $5::numeric, $6, coalesce($7, now()))
        on conflict (paypal_order_id) do update set
          capture_id  = coalesce(excluded.capture_id, orders.capture_id),
          buyer_name  = coalesce(excluded.buyer_name, orders.buyer_name),
          buyer_email = coalesce(excluded.buyer_email, orders.buyer_email)
        returning *
        """,
        o["paypal_order_id"], o["capture_id"], o["buyer_name"], o["buyer_email"],
        str(o["amount"]), o["currency"], o["captured_at"],
    )
    return dict(row)
