from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="Forestall API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "forestall-api",
        "database": await db.check(),
        "paypal": "configured" if settings.paypal_client_id else "not_configured",
    }


@app.get("/api/orders")
async def list_orders():
    if not db.pool:
        return []
    rows = await db.pool.fetch(
        "select id, paypal_order_id, buyer_name, amount, currency, captured_at, tracking_number"
        " from orders order by captured_at desc limit 100"
    )
    return [dict(r) for r in rows]
