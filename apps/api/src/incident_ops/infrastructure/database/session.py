"""Async PostgreSQL engine, sessions, and trusted RLS context."""

import json

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from incident_ops.application.auth import Principal


def normalize_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


def create_database_engine(database_url: SecretStr | str) -> AsyncEngine:
    raw_url = (
        database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    )
    return create_async_engine(
        normalize_async_database_url(raw_url),
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


async def apply_principal_context(session: AsyncSession, principal: Principal) -> None:
    """Set transaction-local RLS inputs derived only from verified identity."""

    await session.execute(
        text("SELECT set_config('app.actor_type', :value, true)"),
        {"value": principal.actor_type.value},
    )
    await session.execute(
        text("SELECT set_config('app.actor_id', :value, true)"),
        {"value": principal.subject},
    )
    await session.execute(
        text("SELECT set_config('app.scopes', :value, true)"),
        {"value": json.dumps(sorted(principal.scopes), separators=(",", ":"))},
    )


async def probe_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            return bool(await connection.scalar(text("SELECT 1")))
    except Exception:  # The health contract deliberately hides driver/network details.
        return False
