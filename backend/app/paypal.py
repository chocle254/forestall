"""Thin PayPal REST client (sandbox by default).

Only the backend talks to PayPal; the AI layer never sees these credentials.
"""

import time
import uuid
from typing import Any

import httpx

from .config import settings


class PayPalError(Exception):
    def __init__(self, status: int, body: Any):
        super().__init__(f"PayPal returned {status}")
        self.status = status
        self.body = body


_token: str | None = None
_token_expires_at: float = 0.0


def configured() -> bool:
    return bool(settings.paypal_client_id and settings.paypal_client_secret)


async def _access_token() -> str:
    """OAuth2 client-credentials token, cached until shortly before it expires."""
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{settings.paypal_base_url}/v1/oauth2/token",
            auth=(settings.paypal_client_id, settings.paypal_client_secret),
            data={"grant_type": "client_credentials"},
        )
    if r.status_code != 200:
        raise PayPalError(r.status_code, _json(r))
    body = r.json()
    _token = body["access_token"]
    _token_expires_at = time.time() + int(body.get("expires_in", 3600))
    return _token


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return r.text


async def request(method: str, path: str, json: dict | list | None = None, idempotency_key: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {await _access_token()}", "Content-Type": "application/json"}
    if idempotency_key:
        headers["PayPal-Request-Id"] = idempotency_key
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, f"{settings.paypal_base_url}{path}", json=json, headers=headers)
    if r.status_code >= 400:
        raise PayPalError(r.status_code, _json(r))
    return _json(r) if r.content else {}


async def check() -> str:
    if not configured():
        return "not_configured"
    try:
        await _access_token()
        return "ok"
    except PayPalError as e:
        return f"error_{e.status}"
    except httpx.HTTPError:
        return "unreachable"


# --- Orders v2 ---------------------------------------------------------------

async def create_order(item: dict, return_url: str, cancel_url: str) -> dict:
    amount = f"{item['price']:.2f}"
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": item["sku"],
                "description": item["name"],
                "amount": {
                    "currency_code": "USD",
                    "value": amount,
                    "breakdown": {"item_total": {"currency_code": "USD", "value": amount}},
                },
                "items": [
                    {
                        "name": item["name"],
                        "sku": item["sku"],
                        "quantity": "1",
                        "category": "PHYSICAL_GOODS",
                        "unit_amount": {"currency_code": "USD", "value": amount},
                    }
                ],
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "brand_name": "Forestall Demo Store",
                    "shipping_preference": "GET_FROM_FILE",
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }
    return await request("POST", "/v2/checkout/orders", json=body, idempotency_key=str(uuid.uuid4()))


async def capture_order(order_id: str) -> dict:
    # Same key per order, so a retried capture can't charge twice.
    return await request("POST", f"/v2/checkout/orders/{order_id}/capture", json={}, idempotency_key=f"capture-{order_id}")


async def get_order(order_id: str) -> dict:
    return await request("GET", f"/v2/checkout/orders/{order_id}")


def approve_link(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    return None


# Sandbox-only test card, used by the "quick test order" demo button so judges
# can create a captured order without logging in to a sandbox buyer account.
SANDBOX_TEST_CARD = {
    "number": "4111111111111111",
    "expiry": "2030-12",
    "security_code": "123",
}


async def create_card_order(item: dict, buyer_name: str, ship_to: dict | None = None) -> dict:
    """Create an order paid by the sandbox test card (no buyer redirect)."""
    if "sandbox" not in settings.paypal_base_url:
        raise PayPalError(400, "test-card orders are sandbox-only")
    amount = f"{item['price']:.2f}"
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": item["sku"],
                "description": item["name"],
                "amount": {"currency_code": "USD", "value": amount},
                **({"shipping": {"type": "SHIPPING", "name": {"full_name": buyer_name}, "address": ship_to}}
                   if ship_to else {}),
            }
        ],
        "payment_source": {
            "card": {
                **SANDBOX_TEST_CARD,
                "name": buyer_name,
                "billing_address": {
                    "address_line_1": "2211 N First St",
                    "admin_area_2": "San Jose",
                    "admin_area_1": "CA",
                    "postal_code": "95131",
                    "country_code": "US",
                },
            }
        },
    }
    return await request("POST", "/v2/checkout/orders", json=body, idempotency_key=str(uuid.uuid4()))


async def add_tracking(order_id: str, capture_id: str, tracking_number: str, carrier: str = "USPS",
                       notify_payer: bool = False) -> dict:
    """Attach shipment tracking to the PayPal transaction (Orders v2 track)."""
    body = {
        "capture_id": capture_id,
        "tracking_number": tracking_number,
        "carrier": carrier,
        "notify_payer": notify_payer,
    }
    return await request("POST", f"/v2/checkout/orders/{order_id}/track", json=body,
                         idempotency_key=f"track-{order_id}-{tracking_number}")


def trackers_on(order: dict) -> list[dict]:
    """Tracking entries PayPal holds for an order (read back as proof)."""
    out = []
    for unit in order.get("purchase_units", []):
        for t in (unit.get("shipping") or {}).get("trackers", []) or []:
            out.append({"id": t.get("id"), "status": t.get("status"), "created_time": t.get("create_time")})
    return out


async def refund_capture(capture_id: str, amount: str | None, currency: str, note_to_payer: str | None,
                         idempotency_key: str) -> dict:
    """Refund a capture (Payments v2). amount=None refunds whatever is left in full."""
    body: dict = {}
    if amount is not None:
        body["amount"] = {"value": amount, "currency_code": currency}
    if note_to_payer:
        body["note_to_payer"] = note_to_payer[:255]
    return await request("POST", f"/v2/payments/captures/{capture_id}/refund", json=body,
                         idempotency_key=idempotency_key)


async def cancel_tracker(order_id: str, tracker_id: str) -> dict:
    """Mark a tracker on the PayPal order as CANCELLED (e.g. the failed parcel after a replacement)."""
    return await request("PATCH", f"/v2/checkout/orders/{order_id}/trackers/{tracker_id}",
                         json=[{"op": "replace", "path": "/status", "value": "CANCELLED"}])


# --- Webhooks ------------------------------------------------------------------

WEBHOOK_EVENTS = ["PAYMENT.CAPTURE.COMPLETED", "CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED",
                  "CUSTOMER.DISPUTE.RESOLVED"]


async def ensure_webhook(url: str) -> dict:
    """Register our webhook URL with PayPal for the events we handle (idempotent)."""
    hooks = (await request("GET", "/v1/notifications/webhooks")).get("webhooks", [])
    for h in hooks:
        if h.get("url") == url:
            have = {e["name"] for e in h.get("event_types", [])}
            if not set(WEBHOOK_EVENTS) <= have:
                await request("PATCH", f"/v1/notifications/webhooks/{h['id']}", json=[
                    {"op": "replace", "path": "/event_types", "value": [{"name": n} for n in WEBHOOK_EVENTS]}])
            return {"id": h["id"], "url": url, "created": False}
    h = await request("POST", "/v1/notifications/webhooks",
                      json={"url": url, "event_types": [{"name": n} for n in WEBHOOK_EVENTS]})
    return {"id": h["id"], "url": url, "created": True}


async def verify_webhook(headers: dict, event: dict, webhook_id: str) -> bool:
    """Ask PayPal whether this webhook delivery is genuine."""
    body = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": webhook_id,
        "webhook_event": event,
    }
    if not all(body[k] for k in ("auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time")):
        return False
    try:
        r = await request("POST", "/v1/notifications/verify-webhook-signature", json=body)
    except PayPalError:
        return False
    return r.get("verification_status") == "SUCCESS"


# --- Disputes ------------------------------------------------------------------

async def list_disputes() -> list[dict]:
    return (await request("GET", "/v1/customer/disputes?page_size=50")).get("items", [])


async def get_dispute(dispute_id: str) -> dict:
    return await request("GET", f"/v1/customer/disputes/{dispute_id}")


async def provide_evidence(dispute_id: str, evidences: list[dict], files: list[tuple[str, bytes, str]]) -> dict:
    """Submit evidence (multipart: 'input' JSON + optional documents)."""
    headers = {"Authorization": f"Bearer {await _access_token()}"}
    multipart = [("input", ("input.json", __import__("json").dumps({"evidences": evidences}), "application/json"))]
    multipart += [(name, (name, data, ctype)) for name, data, ctype in files]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{settings.paypal_base_url}/v1/customer/disputes/{dispute_id}/provide-evidence",
                              headers=headers, files=multipart)
    if r.status_code >= 400:
        raise PayPalError(r.status_code, _json(r))
    return _json(r) if r.content else {}


async def send_dispute_message(dispute_id: str, message: str) -> dict:
    return await request("POST", f"/v1/customer/disputes/{dispute_id}/send-message", json={"message": message[:2000]})


async def accept_claim(dispute_id: str, note: str) -> dict:
    return await request("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim", json={"note": note[:2000]})
