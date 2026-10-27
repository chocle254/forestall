"""Store connection: WooCommerce tells Forestall when an order ships.

The store sends a signed webhook when the seller fulfils an order. Forestall
finds the matching PayPal order, marks it shipped with the store's tracking
number, attaches that tracking to PayPal and starts watching it. The seller no
longer copies tracking by hand.

WooCommerce signs the raw body: base64(HMAC-SHA256(secret, body)).
Shopify can be added later as another parser (see git history).
"""

import base64
import hashlib
import hmac
import json
import random
from dataclasses import dataclass, field

from . import db, easypost, watcher
from .config import settings

PLATFORMS = ("woocommerce",)
SIGNATURE_HEADER = {"woocommerce": "x-wc-webhook-signature"}
TOPIC_HEADER = {"woocommerce": "x-wc-webhook-topic"}


def secret(platform: str) -> str:
    return settings.woocommerce_webhook_secret


def sign(platform: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret(platform).encode(), body, hashlib.sha256).digest()).decode()


def verify(platform: str, body: bytes, header: str | None) -> bool:
    if not secret(platform) or not header:
        return False
    return hmac.compare_digest(sign(platform, body), header.strip())


@dataclass
class Fulfillment:
    """One shipped store order, in a platform-neutral shape."""
    platform: str
    store_order_id: str
    tracking_number: str
    carrier: str = "USPS"
    tracking_url: str | None = None
    paypal_order_id: str | None = None
    capture_id: str | None = None
    email: str | None = None
    total: str | None = None
    currency: str | None = None
    buyer_name: str | None = None
    refs: list[str] = field(default_factory=list)  # any other value that might be a PayPal ID


CARRIERS = {"usps": "USPS", "ups": "UPS", "fedex": "FEDEX", "dhl": "DHL", "dhl express": "DHL"}


def _carrier(name: str | None) -> str:
    return CARRIERS.get((name or "").strip().lower(), (name or "USPS").strip().upper() or "USPS")


def parse_woocommerce(p: dict) -> Fulfillment | None:
    """order.updated / order.created. Tracking comes from the Shipment Tracking plugin's order meta."""
    if "id" not in p:
        return None
    meta = {m.get("key"): m.get("value") for m in p.get("meta_data") or []}
    items = meta.get("_wc_shipment_tracking_items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError:
            items = []
    item = items[-1] if items else {}
    number = item.get("tracking_number") or meta.get("_tracking_number")
    if not number:
        return None
    billing = p.get("billing") or {}
    return Fulfillment(
        platform="woocommerce", store_order_id=str(p["id"]), tracking_number=str(number),
        carrier=_carrier(item.get("tracking_provider") or item.get("custom_tracking_provider") or meta.get("_tracking_provider")),
        tracking_url=item.get("custom_tracking_link") or None,
        # WooCommerce PayPal Payments stores the PayPal order ID in meta; transaction_id is the capture ID.
        paypal_order_id=meta.get("_ppcp_paypal_order_id") or meta.get("_paypal_order_id"),
        capture_id=p.get("transaction_id") or None,
        email=billing.get("email"), total=p.get("total"), currency=p.get("currency"),
        buyer_name=" ".join(x for x in (billing.get("first_name"), billing.get("last_name")) if x) or None,
    )


PARSERS = {"woocommerce": parse_woocommerce}


async def match(f: Fulfillment):
    """Find the Forestall order for a store order. Returns (order row, how it matched)."""
    pool = await db.get_pool()
    assert pool
    o = await pool.fetchrow("select * from orders where store_platform=$1 and store_order_id=$2", f.platform, f.store_order_id)
    if o:
        return o, "store order already linked"
    for ref, how in ((f.paypal_order_id, "PayPal order ID"), (f.capture_id, "PayPal capture ID"),
                     *((r, "PayPal reference") for r in f.refs)):
        if ref:
            o = await pool.fetchrow("select * from orders where paypal_order_id=$1 or capture_id=$1", ref)
            if o:
                return o, how
    if f.email and f.total:
        o = await pool.fetchrow(
            """select * from orders where lower(buyer_email)=lower($1) and amount=$2::numeric
               and watch_status='not_shipped' and store_order_id is null order by captured_at desc limit 1""",
            f.email, str(f.total))
        if o:
            return o, "buyer email and amount"
    if f.buyer_name and f.total:
        o = await pool.fetchrow(
            """select * from orders where lower(buyer_name)=lower($1) and amount=$2::numeric
               and watch_status='not_shipped' and store_order_id is null order by captured_at desc limit 1""",
            f.buyer_name, str(f.total))
        if o:
            return o, "buyer name and amount"
    return None, None


async def handle(platform: str, payload: dict, topic: str | None, webhook_url: str | None = None) -> dict:
    """A verified store webhook: link the order and, if it shipped, start watching with the store's tracking."""
    f = PARSERS[platform](payload)
    if not f:
        return {"ignored": "no tracking number in this event", "topic": topic}
    pool = await db.get_pool()
    assert pool
    o, how = await match(f)
    if not o:
        return {"ignored": "no matching PayPal order", "store_order_id": f.store_order_id}

    label = "WooCommerce"
    async with pool.acquire() as conn:
        await conn.execute("update orders set store_platform=$2, store_order_id=$3 where id=$1",
                           o["id"], platform, f.store_order_id)
        await watcher.log(conn, o["id"], "store", f"{label} order #{f.store_order_id} linked ({how})",
                          {"platform": platform, "store_order_id": f.store_order_id, "matched_by": how, "topic": topic},
                          watcher._now(o))

    if o["watch_status"] != "not_shipped":
        if o["tracking_number"] != f.tracking_number:
            async with pool.acquire() as conn:
                await watcher.log(conn, o["id"], "store",
                                  f"{label} reports a different tracking number ({f.carrier} {f.tracking_number})",
                                  {"store_tracking": f.tracking_number, "forestall_tracking": o["tracking_number"]},
                                  watcher._now(o))
        return {"order_id": str(o["id"]), "linked": True, "shipped": False, "reason": "already shipped"}

    async with pool.acquire() as conn:
        await watcher.log(conn, o["id"], "store", f"{label} marked the order shipped · {f.carrier} {f.tracking_number}",
                          {"tracking_number": f.tracking_number, "carrier": f.carrier, "tracking_url": f.tracking_url},
                          watcher._now(o))
    # Real carrier data when EasyPost can follow this number; otherwise a plain shipment.
    result = None
    if easypost.configured() and (not easypost.test_mode() or f.tracking_number in easypost.TEST_CODES):
        try:
            result = await watcher.ship_with_easypost(o["id"], f.tracking_number, f.carrier, attach_to_paypal=True,
                                                      webhook_url=webhook_url)
        except easypost.EasyPostError:
            result = None
    if result is None:
        result = await watcher.ship(o["id"], None, f.carrier, f.tracking_number, attach_to_paypal=True)
    return {"order_id": str(o["id"]), "linked": True, "shipped": True, "matched_by": how, "check": result}


async def status() -> list[dict]:
    """What the Connect page shows for each store."""
    pool = await db.get_pool()
    out = []
    for p in PLATFORMS:
        last = await pool.fetchrow("select event, valid, received_at from webhook_log where source=$1"
                                   " order by received_at desc limit 1", p) if pool else None
        linked = await pool.fetchval("select count(*) from orders where store_platform=$1", p) if pool else 0
        out.append({"platform": p, "secret_set": bool(secret(p)), "linked_orders": linked,
                    "last_event": dict(last) if last else None})
    return out


def sample_payload(platform: str, o) -> dict:
    """A realistic webhook body for this order, as the store would send it (for the demo button)."""
    code = random.choice([c for c in easypost.TEST_CODES if c != "EZ4000000004"]) if easypost.test_mode() else None
    number = code or f"9400 1000 0000 {random.randint(1000, 9999)} {random.randint(1000, 9999)} {random.randint(10, 99)}".replace(" ", "")
    name = (o["buyer_name"] or "Test Buyer").split(" ", 1)
    store_id = str(random.randint(1000, 9999))
    return {
        "id": int(store_id), "status": "completed", "currency": o["currency"], "total": f"{o['amount']:.2f}",
        "payment_method": "ppcp-gateway", "transaction_id": o["capture_id"],
        "billing": {"first_name": name[0], "last_name": name[1] if len(name) > 1 else "", "email": o["buyer_email"]},
        "meta_data": [
            {"key": "_ppcp_paypal_order_id", "value": o["paypal_order_id"]},
            {"key": "_wc_shipment_tracking_items", "value": [
                {"tracking_provider": "usps", "tracking_number": number, "date_shipped": None}]},
        ],
    }
