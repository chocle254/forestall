from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings

app = FastAPI(title="Forestall API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# On Vercel, every request under /api/* is routed to this app with its full path.
api = APIRouter(prefix="/api")


@api.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "forestall-api",
        "database": await db.check(),
        "paypal": "configured" if settings.paypal_client_id else "not_configured",
    }


@api.get("/orders")
async def list_orders():
    pool = await db.get_pool()
    if not pool:
        return []
    rows = await pool.fetch(
        "select id, paypal_order_id, buyer_name, amount, currency, captured_at, tracking_number"
        " from orders order by captured_at desc limit 100"
    )
    return [dict(r) for r in rows]


app.include_router(api)
