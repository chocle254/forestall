import json
import random
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from pydantic import BaseModel

from . import ai, db, orders, paypal, risk, simulator
from .config import settings

app = FastAPI(title="Forestall API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# On Vercel, every request under /api/* is routed to this app with its full path.
api = APIRouter(prefix="/api")


def _origin(request: Request) -> str:
    """Public origin of this deployment (Vercel sets the forwarded headers)."""
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


@api.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "forestall-api",
        "database": await db.check(),
        "paypal": await paypal.check(),
        "paypal_env": "sandbox" if "sandbox" in settings.paypal_base_url else "live",
        "ai": await ai.check(),
        "ai_model": ai.model_name(),
    }


@api.get("/orders")
async def list_orders():
    pool = await db.get_pool()
    if not pool:
        return []
    rows = await pool.fetch(
        """
        select o.id, o.paypal_order_id, o.capture_id, o.buyer_name, o.buyer_email, o.amount, o.currency,
               o.captured_at, o.tracking_number, o.tracking_attached_at, o.demo_shift_hours,
               f.risk_level, f.signal, f.reason as flag_reason, f.updated_at as flagged_at
        from orders o
        left join flags f on f.order_id = o.id and not f.resolved
        order by o.captured_at desc limit 100
        """
    )
    return [dict(r) for r in rows]


@api.get("/orders/{order_id}")
async def order_detail(order_id: str):
    pool = await db.get_pool()
    if not pool:
        raise HTTPException(503, "database not configured")
    o = await pool.fetchrow("select * from orders where id::text = $1 or paypal_order_id = $1", order_id)
    if not o:
        raise HTTPException(404, "order not found")
    oid = o["id"]
    return {
        "order": dict(o),
        "shipment_events": [dict(r) for r in await pool.fetch(
            "select status, location, occurred_at, source from shipment_events where order_id=$1 order by occurred_at", oid)],
        "buyer_messages": [dict(r) for r in await pool.fetch(
            "select body, received_at from buyer_messages where order_id=$1 order by received_at", oid)],
        "flag": (lambda r: dict(r) if r else None)(await pool.fetchrow(
            "select id, risk_level, signal, reason, created_at, updated_at from flags where order_id=$1 and not resolved", oid)),
        "latest_assessment": (lambda r: {**dict(r), "result": json.loads(r["result"])} if r else None)(await pool.fetchrow(
            "select id, result, model, created_at from assessments where order_id=$1 order by created_at desc limit 1", oid)),
    }


# --- AI risk assessment (slice 5) --------------------------------------------

@api.post("/orders/{order_id}/assess")
async def assess_order(order_id: str, force: bool = False):
    try:
        return await risk.assess(order_id, force=force)
    except LookupError:
        raise HTTPException(404, "order not found")
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e))


class ScenarioIn(BaseModel):
    scenario: str


@api.get("/demo/scenarios")
async def list_scenarios():
    return [{"id": k, "label": v["label"]} for k, v in simulator.SCENARIOS.items()]


@api.post("/orders/{order_id}/simulate")
async def simulate(order_id: str, body: ScenarioIn):
    """Replay a delivery story on this order, then have the AI assess it."""
    try:
        sim = await simulator.apply(order_id, body.scenario)
    except KeyError:
        raise HTTPException(400, f"unknown scenario; choose one of {list(simulator.SCENARIOS)}")
    except LookupError:
        raise HTTPException(404, "order not found")
    try:
        assessment = await risk.assess(order_id)
    except ai.AIUnavailable as e:
        return {"simulation": sim, "assessment": None, "ai_error": str(e)}
    return {"simulation": sim, "assessment": assessment}


# --- Demo checkout: a sandbox buyer pays for a test order ---------------------

@api.post("/demo/test-order")
async def create_test_order(request: Request):
    if not paypal.configured():
        raise HTTPException(503, "PayPal is not configured")
    origin = _origin(request)
    item = random.choice(orders.CATALOG)
    try:
        order = await paypal.create_order(
            item,
            return_url=f"{origin}/api/paypal/return",
            cancel_url=f"{origin}/?checkout=cancelled",
        )
    except paypal.PayPalError as e:
        raise HTTPException(502, {"paypal_status": e.status, "paypal_error": e.body})
    link = paypal.approve_link(order)
    if not link:
        raise HTTPException(502, "PayPal did not return an approval link")
    return {"paypal_order_id": order["id"], "item": item, "approve_url": link}


@api.get("/paypal/return")
async def paypal_return(token: str):
    """PayPal sends the buyer here after approval; `token` is the order ID."""
    try:
        captured = await paypal.capture_order(token)
    except paypal.PayPalError as e:
        # Already captured (e.g. page refresh): read it back instead of failing.
        issue = str(e.body)
        if "ORDER_ALREADY_CAPTURED" in issue:
            captured = await paypal.get_order(token)
        else:
            return RedirectResponse(f"/?checkout=error&detail={quote(issue[:200])}", status_code=303)
    row = await orders.upsert(orders.summarize_capture(captured))
    return RedirectResponse(f"/?checkout=paid&order={row['paypal_order_id']}", status_code=303)


app.include_router(api)
