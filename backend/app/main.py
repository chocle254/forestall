import random
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import db, orders, paypal
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
    }


@api.get("/orders")
async def list_orders():
    pool = await db.get_pool()
    if not pool:
        return []
    rows = await pool.fetch(
        "select id, paypal_order_id, capture_id, buyer_name, buyer_email, amount, currency,"
        " captured_at, tracking_number from orders order by captured_at desc limit 100"
    )
    return [dict(r) for r in rows]


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
