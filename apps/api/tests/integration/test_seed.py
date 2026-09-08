"""Deterministic additive seed behavior."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    IncidentModel,
)
from incident_ops.seed import seed_database


async def test_seed_is_deterministic_and_additive(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    await seed_database(database_engine, reset=False)
    await seed_database(database_engine, reset=False)

    async with database_engine.connect() as connection:
        counts = {
            "incidents": await connection.scalar(select(func.count()).select_from(IncidentModel)),
            "jobs": await connection.scalar(select(func.count()).select_from(DemoJobModel)),
            "actions": await connection.scalar(
                select(func.count()).select_from(ActionRequestModel)
            ),
        }
        states = set((await connection.execute(select(ActionRequestModel.status))).scalars())

    assert counts == {"incidents": 3, "jobs": 2, "actions": 3}
    assert states == {"pending", "applied", "rejected"}
