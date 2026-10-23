"""EasyPost: real carrier tracking API (USPS, UPS, FedEx, DHL...).

In test mode (EZTK... key) EasyPost provides test tracking codes that return
realistic carrier data and send webhooks:
  EZ1000000001 pre_transit · EZ2000000002 in_transit · EZ3000000003 out_for_delivery
  EZ4000000004 delivered   · EZ5000000005 return_to_sender · EZ6000000006 failure
  EZ7000000007 unknown
"""

import hashlib
import hmac
import unicodedata
from typing import Any

import httpx

from .config import settings

BASE = "https://api.easypost.com/v2"

TEST_CODES = {
    "EZ1000000001": "Label created, not yet scanned (pre-transit)",
    "EZ2000000002": "In transit",
    "EZ3000000003": "Out for delivery",
    "EZ4000000004": "Delivered",
    "EZ5000000005": "Returned to sender",
    "EZ6000000006": "Delivery failure",
    "EZ7000000007": "Unknown status",
}


class EasyPostError(Exception):
    def __init__(self, status: int, body: Any):
        super().__init__(f"EasyPost returned {status}")
        self.status = status
        self.body = body


def configured() -> bool:
    return bool(settings.easypost_api_key)


def test_mode() -> bool:
    return settings.easypost_api_key.startswith("EZTK")


async def _request(method: str, path: str, json: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, f"{BASE}{path}", json=json, auth=(settings.easypost_api_key, ""))
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        raise EasyPostError(r.status_code, body)
    return r.json()


async def check() -> str:
    if not configured():
        return "not_configured"
    try:
        await _request("GET", "/webhooks")
        return "ok"
    except EasyPostError as e:
        return f"error_{e.status}"
    except httpx.HTTPError:
        return "unreachable"


async def create_tracker(tracking_code: str, carrier: str = "USPS") -> dict:
    return await _request("POST", "/trackers", {"tracker": {"tracking_code": tracking_code, "carrier": carrier}})


async def get_tracker(tracker_id: str) -> dict:
    return await _request("GET", f"/trackers/{tracker_id}")


def details_to_events(tracker: dict) -> list[dict]:
    """EasyPost tracking_details -> Forestall shipment events."""
    out = []
    for d in tracker.get("tracking_details") or []:
        loc = d.get("tracking_location") or {}
        place = ", ".join(p for p in (loc.get("city"), loc.get("state")) if p) or None
        out.append({
            "status": d.get("status") or "unknown",
            "location": place,
            "occurred_at": d.get("datetime"),
            "message": d.get("message"),
        })
    return out


def summarize(tracker: dict) -> dict:
    """What the investigating agent needs from a live tracker."""
    cd = tracker.get("carrier_detail") or {}
    return {
        "source": "EasyPost API" + (" (test mode)" if test_mode() else ""),
        "carrier": tracker.get("carrier"),
        "tracking_number": tracker.get("tracking_code"),
        "status": tracker.get("status"),
        "status_detail": tracker.get("status_detail"),
        "est_delivery_date": tracker.get("est_delivery_date"),
        "signed_by": tracker.get("signed_by"),
        "carrier_service": cd.get("service"),
        "origin": cd.get("origin_location"),
        "destination": cd.get("destination_location"),
        "public_url": tracker.get("public_url"),
        "scans": [{"status": e["status"], "location": e["location"], "time": e["occurred_at"], "message": e["message"]}
                  for e in details_to_events(tracker)],
    }


# --- Webhooks ----------------------------------------------------------------

def webhook_secret() -> str:
    """Derived from the API key, so no extra secret has to be configured."""
    return hmac.new(settings.easypost_api_key.encode(), b"forestall-easypost-webhook", hashlib.sha256).hexdigest()


def verify_signature(body: bytes, header: str | None) -> bool:
    """EasyPost signs webhooks with HMAC-SHA256 of the raw body: 'hmac-sha256-hex=<digest>'."""
    if not header:
        return False
    key = unicodedata.normalize("NFKD", webhook_secret()).encode()
    expected = "hmac-sha256-hex=" + hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def ensure_webhook(url: str) -> dict:
    """Register our webhook URL once (idempotent)."""
    hooks = (await _request("GET", "/webhooks")).get("webhooks", [])
    for h in hooks:
        if h.get("url") == url:
            return {"id": h["id"], "url": url, "created": False}
    h = await _request("POST", "/webhooks", {"webhook": {"url": url, "webhook_secret": webhook_secret()}})
    return {"id": h["id"], "url": url, "created": True}
