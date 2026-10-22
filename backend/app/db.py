"""Postgres (Neon) pool and a tiny migration runner."""

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from .config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

pool: asyncpg.Pool | None = None


def _dsn_and_ssl() -> tuple[str, str | bool]:
    # Neon URLs carry ?sslmode=require&channel_binding=require; asyncpg rejects
    # channel_binding, so drop the query string and pass ssl explicitly.
    # Railway's private network (*.railway.internal) has no TLS.
    parts = urlsplit(settings.database_url)
    host = parts.hostname or ""
    no_tls = host.endswith(".railway.internal") or "sslmode=disable" in parts.query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), (False if no_tls else "require")


async def connect() -> None:
    global pool
    if not settings.database_url:
        return
    dsn, ssl = _dsn_and_ssl()
    # statement_cache_size=0 keeps asyncpg compatible with Neon's pgbouncer pooler.
    pool = await asyncpg.create_pool(
        dsn, ssl=ssl, min_size=1, max_size=5, statement_cache_size=0
    )
    await migrate()


async def close() -> None:
    if pool:
        await pool.close()


async def migrate() -> None:
    """Apply each migrations/*.sql file once, in name order."""
    assert pool
    async with pool.acquire() as conn:
        await conn.execute(
            "create table if not exists schema_migrations ("
            " name text primary key, applied_at timestamptz not null default now())"
        )
        applied = {r["name"] for r in await conn.fetch("select name from schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in applied:
                continue
            async with conn.transaction():
                await conn.execute(f.read_text())
                await conn.execute("insert into schema_migrations (name) values ($1)", f.name)


async def check() -> str:
    if not settings.database_url:
        return "not_configured"
    if not pool:
        return "unreachable"
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("select 1")
        return "ok"
    except Exception:
        return "unreachable"
