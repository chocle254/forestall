import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

app = FastAPI(title="Forestall API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _check_supabase() -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        return "not_configured"
    headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{settings.supabase_url}/rest/v1/orders?select=id&limit=1", headers=headers
            )
        return "ok" if r.status_code == 200 else f"error_{r.status_code}"
    except httpx.HTTPError:
        return "unreachable"


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "forestall-api",
        "supabase": await _check_supabase(),
        "paypal": "configured" if settings.paypal_client_id else "not_configured",
    }
