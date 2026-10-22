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


async def request(method: str, path: str, json: dict | None = None, idempotency_key: str | None = None) -> Any:
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
