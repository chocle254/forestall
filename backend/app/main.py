import json
import random
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from pydantic import BaseModel

from . import actions, ai, db, easypost, investigate, orders, paypal, recommend, risk, simulator, watcher
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
        "easypost": await easypost.check(),
        "easypost_mode": "test" if easypost.test_mode() else ("live" if easypost.configured() else None),
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
               o.carrier_source, o.tracking_public_url, o.carrier,
               f.risk_level, f.signal, f.reason as flag_reason, f.updated_at as flagged_at,
               a.id as action_id, a.type as action_type, a.params as action_params,
               a.justification as action_justification, a.status as action_status,
               a.edited as action_edited, a.parent_action_id is not null as action_is_followup,
               a.paypal_response as action_result, o.resolution, o.refunded_amount,
               i.findings as findings, i.tool_calls as tool_calls
        from orders o
        left join flags f on f.order_id = o.id and not f.resolved
        left join lateral (
          select * from actions x where x.order_id = o.id and x.status <> 'superseded'
          order by x.created_at desc limit 1
        ) a on true
        left join investigations i on i.id = a.investigation_id
        order by o.captured_at desc limit 100
        """
    )
    out = []
    for r in rows:
        d = dict(r)
        d["action_params"] = json.loads(d["action_params"]) if d["action_params"] else None
        d["findings"] = json.loads(d["findings"]) if d["findings"] else None
        d["action_result"] = json.loads(d["action_result"]) if d["action_result"] else None
        calls = json.loads(d.pop("tool_calls")) if d["tool_calls"] else []
        d["tools_used"] = [c["tool"] for c in calls]
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


# --- Slice 7: approve / edit / dismiss, and the buyer's reply ------------------

def _action_errors(e: Exception):
    if isinstance(e, LookupError):
        raise HTTPException(404, str(e))
    if isinstance(e, ValueError):
        raise HTTPException(409, str(e))
    if isinstance(e, ai.AIUnavailable):
        raise HTTPException(503, str(e))
    if isinstance(e, easypost.EasyPostError):
        raise HTTPException(502, {"easypost_status": e.status, "easypost_error": e.body})
    raise e


@api.post("/actions/{action_id}/approve")
async def approve_action(action_id: str):
    """The seller approves a fix; Forestall executes it (PayPal fixes are real sandbox API calls)."""
    try:
        return await actions.approve(action_id)
    except Exception as e:
        _action_errors(e)


class ActionEdit(BaseModel):
    message: str | None = None
    note_to_payer: str | None = None
    amount: float | None = None


@api.patch("/actions/{action_id}")
async def edit_action(action_id: str, body: ActionEdit):
    try:
        return await actions.edit(action_id, body.message, body.note_to_payer, body.amount)
    except Exception as e:
        _action_errors(e)


@api.post("/actions/{action_id}/dismiss")
async def dismiss_action(action_id: str):
    try:
        return await actions.dismiss(action_id)
    except Exception as e:
        _action_errors(e)


class ReplyIn(BaseModel):
    text: str


@api.post("/orders/{order_id}/buyer-reply")
async def buyer_reply(order_id: str, body: ReplyIn):
    """Demo: the buyer replies to the seller's message. A chosen option becomes a fix awaiting approval."""
    if not body.text.strip():
        raise HTTPException(400, "empty reply")
    try:
        return await actions.buyer_reply(order_id, body.text.strip()[:1000])
    except Exception as e:
        _action_errors(e)


@api.post("/orders/{order_id}/investigate")
async def investigate_order(order_id: str):
    """The agent investigates the order with tools and reports findings (with its tool-call trace)."""
    try:
        return await investigate.investigate(order_id)
    except LookupError:
        raise HTTPException(404, "order not found")
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e))


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
    scenario: str | None = None          # demo: play a scripted delivery story (simulated carrier)
    easypost_code: str | None = None     # ship with a real EasyPost tracker (test codes EZ1000000001...)
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
async def ship_order(order_id: str, body: ShipIn, request: Request):
    """Mark shipped and start watching. Forestall follows the order from here."""
    if body.easypost_code:
        if not easypost.configured():
            raise HTTPException(503, "EasyPost is not configured")
        try:
            return await watcher.ship_with_easypost(
                order_id, body.easypost_code, body.carrier,
                attach_to_paypal=body.attach_to_paypal if body.attach_to_paypal is not None else True,
                webhook_url=f"{_origin(request)}/api/webhooks/easypost")
        except easypost.EasyPostError as e:
            raise HTTPException(502, {"easypost_status": e.status, "easypost_error": e.body})
        except Exception as e:
            _watch_errors(e)
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


@api.get("/easypost/test-codes")
async def easypost_test_codes():
    return [{"code": k, "label": v} for k, v in easypost.TEST_CODES.items()] if easypost.test_mode() else []


@api.get("/webhooks/recent")
async def recent_webhooks():
    pool = await db.get_pool()
    if not pool:
        return []
    rows = await pool.fetch("select source, event, valid, reference, received_at from webhook_log"
                            " order by received_at desc limit 20")
    return [dict(r) for r in rows]


@api.post("/easypost/webhook")
async def easypost_register_webhook(request: Request):
    """Register (or confirm) Forestall's EasyPost webhook URL. Idempotent."""
    if not easypost.configured():
        raise HTTPException(503, "EasyPost is not configured")
    try:
        return await easypost.ensure_webhook(f"{_origin(request)}/api/webhooks/easypost")
    except easypost.EasyPostError as e:
        raise HTTPException(502, {"easypost_status": e.status, "easypost_error": e.body})


@api.post("/webhooks/easypost")
async def easypost_webhook(request: Request):
    """EasyPost pushes carrier updates here. Signed with HMAC; we store new scans and schedule a check."""
    raw = await request.body()
    pool = await db.get_pool()
    if not pool:
        raise HTTPException(503, "database not configured")
    valid = easypost.verify_signature(raw, request.headers.get("x-hmac-signature"))
    try:
        event = json.loads(raw)
    except ValueError:
        event = {}
    tracker = event.get("result") or {}
    await pool.execute("insert into webhook_log (source, event, valid, reference) values ('easypost',$1,$2,$3)",
                       event.get("description"), valid, tracker.get("id"))
    if not valid:
        raise HTTPException(401, "bad signature")
    if tracker.get("object") != "Tracker":
        return {"ignored": event.get("description")}
    oid = await pool.fetchval("select id from orders where easypost_tracker_id=$1", tracker.get("id"))
    if not oid:
        return {"ignored": "unknown tracker"}
    new = await watcher.ingest_easypost(oid, tracker)
    async with pool.acquire() as conn:
        await watcher.log(conn, oid, "webhook", f"EasyPost pushed a carrier update ({event.get('description')})",
                          {"new_scans": new, "status": tracker.get("status")})
    # Answer EasyPost fast; the watcher checks the order on its next sweep (or when the app opens).
    return {"order_id": str(oid), "new_scans": new}


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
          (select count(*) from disputes)                                            as disputes,
          (select count(*) from actions where status = 'executed')                   as fixes_executed,
          (select coalesce(sum(refunded_amount), 0) from orders)                     as refunded_total,
          (select count(*) from orders where watch_status = 'closed')                as resolved_by_refund
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
    ship_to = random.choice(orders.DEMO_ADDRESSES)
    try:
        order = await paypal.create_card_order(item, buyer, ship_to)
        if order.get("status") != "COMPLETED":
            order = await paypal.capture_order(order["id"])
    except paypal.PayPalError as e:
        raise HTTPException(502, {"paypal_status": e.status, "paypal_error": e.body})
    summary = orders.summarize_capture(order)
    summary["buyer_name"] = summary["buyer_name"] or buyer
    summary["ship_to"] = summary.get("ship_to") or ship_to
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
