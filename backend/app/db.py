"""Postgres (Neon) pool and a tiny migration runner.

The pool is created on first use rather than at startup, because on Vercel the
app runs as a serverless function and may start cold on any request.
"""

import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from .config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


def _dsn_and_ssl() -> tuple[str, str | bool]:
    # Neon URLs carry ?sslmode=require&channel_binding=require; asyncpg rejects
    # channel_binding, so drop the query string and pass ssl explicitly.
    parts = urlsplit(settings.database_url)
    no_tls = "sslmode=disable" in parts.query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), (False if no_tls else "require")


async def get_pool() -> asyncpg.Pool | None:
    global _pool
    if not settings.database_url:
        return None
    if _pool is None:
        async with _lock:
            if _pool is None:
                dsn, ssl = _dsn_and_ssl()
                # statement_cache_size=0 keeps asyncpg compatible with Neon's pooler.
                pool = await asyncpg.create_pool(
                    dsn, ssl=ssl, min_size=0, max_size=5, statement_cache_size=0
                )
                await _migrate(pool)
                _pool = pool
    return _pool


async def close() -> None:
    if _pool:
        await _pool.close()


async def _migrate(pool: asyncpg.Pool) -> None:
    """Apply each migrations/*.sql file once, in name order.

    Serverless: several fresh instances may start at once after a deploy. A
    transaction-scoped advisory lock makes them take turns (it is released at
    commit, which also works through Neon's connection pooler), so each
    migration runs exactly once.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(7461001)")
        await conn.execute(
            "create table if not exists schema_migrations ("
            " name text primary key, applied_at timestamptz not null default now())"
        )
        applied = {r["name"] for r in await conn.fetch("select name from schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in applied:
                continue
            await conn.execute(f.read_text())
            await conn.execute("insert into schema_migrations (name) values ($1)", f.name)


async def check() -> str:
    if not settings.database_url:
        return "not_configured"
    try:
        pool = await get_pool()
        assert pool
        async with pool.acquire() as conn:
            await conn.fetchval("select 1")
        return "ok"
    except Exception as e:  # surfaced on /api/health to make setup issues obvious
        return f"error: {type(e).__name__}"
