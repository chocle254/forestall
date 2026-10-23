import json
import random
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from pydantic import BaseModel

from . import ai, db, orders, paypal, recommend, risk, simulator, watcher
from .config import settings

app = FastAPI(title="Forestall API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Always answer with JSON, so the app can show what went wrong.
    return JSONResponse(status_code=500, content={"detail": f"Server error: {type(exc).__name__}: {str(exc)[:200]}"})


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
               o.shipped_at, o.watch_status, o.next_check_at, o.demo_scenario,
               f.risk_level, f.signal, f.reason as flag_reason, f.updated_at as flagged_at,
               a.id as action_id, a.type as action_type, a.params as action_params,
               a.justification as action_justification, a.status as action_status
        from orders o
        left join flags f on f.order_id = o.id and not f.resolved
        left join lateral (
          select * from actions x where x.order_id = o.id and x.status = 'proposed'
          order by x.created_at desc limit 1
        ) a on true
        order by o.captured_at desc limit 100
        """
    )
    out = []
    for r in rows:
        d = dict(r)
        d["action_params"] = json.loads(d["action_params"]) if d["action_params"] else None
        out.append(d)
    return out


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


@api.get("/ai/models")
async def ai_models():
    if not ai.configured():
        raise HTTPException(503, "AI is not configured")
    return {"current": ai.model_name(), "available": await ai.list_models()}


@api.post("/orders/{order_id}/recommend")
async def recommend_fix(order_id: str, force: bool = False):
    """AI proposes one fix for the order's open flag (slice 6)."""
    try:
        p = await recommend.recommend(order_id, force=force)
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, f"AI could not produce a valid fix: {e}")
    if p is None:
        raise HTTPException(409, "order has no open flag")
    return p


# --- AI risk assessment (slice 5) --------------------------------------------

@api.post("/orders/{order_id}/assess")
async def assess_order(order_id: str, force: bool = False):
    try:
        return await risk.assess(order_id, force=force)
    except LookupError:
        raise HTTPException(404, "order not found")
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e))


# --- Watching orders from "shipped" to "protected" ---------------------------

@api.get("/demo/scenarios")
async def list_scenarios():
    return [{"id": k, "label": v["label"]} for k, v in simulator.SCENARIOS.items()]


class ShipIn(BaseModel):
    scenario: str | None = None          # demo: play a scripted delivery story
    carrier: str = "USPS"
    tracking_number: str | None = None
    attach_to_paypal: bool | None = None  # default: scenario decides; real shipments attach


def _watch_errors(e: Exception):
    if isinstance(e, LookupError):
        raise HTTPException(404, "order not found")
    if isinstance(e, KeyError):
        raise HTTPException(400, f"unknown scenario; choose one of {list(simulator.SCENARIOS)}")
    if isinstance(e, ValueError):
        raise HTTPException(409, str(e))
    if isinstance(e, paypal.PayPalError):
        raise HTTPException(502, {"paypal_status": e.status, "paypal_error": e.body})
    raise e


@api.post("/orders/{order_id}/ship")
async def ship_order(order_id: str, body: ShipIn):
    """Mark shipped and start watching. Forestall follows the order from here."""
    try:
        attach = body.attach_to_paypal if body.attach_to_paypal is not None else (None if body.scenario else True)
        return await watcher.ship(order_id, body.scenario, body.carrier, body.tracking_number, attach)
    except Exception as e:
        _watch_errors(e)


@api.post("/orders/{order_id}/advance")
async def advance_order(order_id: str, hours: int = 24):
    """Demo time travel: move this order's clock forward and let the watcher react."""
    if not 1 <= hours <= 72:
        raise HTTPException(400, "hours must be 1-72")
    try:
        return await watcher.advance(order_id, hours)
    except Exception as e:
        _watch_errors(e)


@api.post("/orders/{order_id}/check")
async def check_order(order_id: str):
    try:
        return await watcher.check(order_id, trigger="manual")
    except Exception as e:
        _watch_errors(e)


@api.get("/orders/{order_id}/timeline")
async def order_timeline(order_id: str):
    pool = await db.get_pool()
    if not pool:
        raise HTTPException(503, "database not configured")
    rows = await pool.fetch(
        "select kind, title, detail, occurred_at, recorded_at from order_timeline where order_id::text=$1"
        " order by occurred_at, recorded_at", order_id)
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


@api.api_route("/watch/tick", methods=["GET", "POST"])
async def watch_tick():
    """Check every watched order that is due.

    Public on purpose: called by Vercel Cron (daily), by the app whenever it is
    opened, and optionally by any pinger. The sweep itself is throttled in the
    database (once per 5 minutes) and only touches orders that are due, so extra
    calls cost nothing.
    """
    return await watcher.sweep()


@api.get("/report")
async def protection_report():
    """Evidence that Forestall followed every order: coverage, checks, catches, fixes, proof."""
    pool = await db.get_pool()
    if not pool:
        raise HTTPException(503, "database not configured")
    r = await pool.fetchrow(
        """
        select
          (select count(*) from orders)                                              as orders,
          (select count(*) from orders where shipped_at is not null)                 as shipped,
          (select count(*) from orders where watch_status = 'watching')              as watching,
          (select count(*) from orders where watch_status = 'protected')             as protected,
          (select count(*) from order_timeline where kind = 'check')                 as checks_run,
          (select count(distinct order_id) from order_timeline where kind = 'flag')  as orders_flagged,
          (select count(*) from order_timeline where kind = 'paypal')                as paypal_confirmations,
          (select count(*) from orders where shipped_at is not null and tracking_attached_at is not null) as shipped_with_proof,
          (select count(*) from disputes)                                            as disputes
        """
    )
    d = dict(r)
    d["proof_coverage_pct"] = round(100 * d["shipped_with_proof"] / d["shipped"], 1) if d["shipped"] else None
    by_signal = await pool.fetch(
        "select detail->>'signal' as signal, count(distinct order_id) as orders from order_timeline"
        " where kind='flag' group by 1 order by 2 desc")
    d["risks_caught_by_signal"] = {x["signal"]: x["orders"] for x in by_signal}
    return d


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


DEMO_BUYERS = ["Amina Wanjiru", "Jordan Ruiz", "Priya Nair", "Tom Becker", "Grace Mensah", "Leo Tanaka"]


@api.post("/demo/card-order")
async def create_card_test_order():
    """One tap: create + capture a sandbox order paid with PayPal's test card."""
    if not paypal.configured():
        raise HTTPException(503, "PayPal is not configured")
    item = random.choice(orders.CATALOG)
    buyer = random.choice(DEMO_BUYERS)
    try:
        order = await paypal.create_card_order(item, buyer)
        if order.get("status") != "COMPLETED":
            order = await paypal.capture_order(order["id"])
    except paypal.PayPalError as e:
        raise HTTPException(502, {"paypal_status": e.status, "paypal_error": e.body})
    summary = orders.summarize_capture(order)
    summary["buyer_name"] = summary["buyer_name"] or buyer
    if summary.get("status") not in ("COMPLETED", "PENDING"):
        raise HTTPException(502, {"paypal_status": summary.get("status"), "paypal_order": order.get("id")})
    row = await orders.upsert(summary)
    return {"order": row, "item": item, "paypal_status": summary.get("status")}


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
