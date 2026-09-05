"""Core schema migrations are deterministic and reversible."""

import asyncio

from alembic import command
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from conftest import make_alembic_config


def _inspect_schema(connection: Connection) -> dict[str, set[str]]:
    inspector = inspect(connection)
    return {
        table_name: {index["name"] for index in inspector.get_indexes(table_name)}
        for table_name in inspector.get_table_names()
        if table_name != "alembic_version"
    }


async def test_upgrade_downgrade_upgrade_cycle_is_deterministic(
    postgres_test_url: str,
    database_engine,
) -> None:
    config = make_alembic_config(postgres_test_url)

    await asyncio.to_thread(command.upgrade, config, "head")
    async with database_engine.connect() as connection:
        first_schema = await connection.run_sync(_inspect_schema)

    await asyncio.to_thread(command.downgrade, config, "base")
    async with database_engine.connect() as connection:
        tables_after_downgrade = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
    assert tables_after_downgrade <= {"alembic_version"}

    await asyncio.to_thread(command.upgrade, config, "head")
    async with database_engine.connect() as connection:
        second_schema = await connection.run_sync(_inspect_schema)

    assert second_schema == first_schema
    assert set(second_schema) == {
        "action_requests",
        "demo_jobs",
        "historical_incidents",
        "idempotency_records",
        "incident_events",
        "incidents",
        "outbox_events",
    }
    assert {
        "ix_incidents_status_severity_service_updated_id",
        "ix_incidents_updated_id",
    } <= second_schema["incidents"]
    assert "ix_incident_events_timeline" in second_schema["incident_events"]
    assert "ix_action_requests_pending" in second_schema["action_requests"]
    assert "ix_outbox_events_claimable" in second_schema["outbox_events"]


async def test_pgvector_and_forced_rls_are_enabled(database_engine) -> None:
    async with database_engine.connect() as connection:
        vector_enabled = await connection.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
        )
        protected_tables = await connection.exec_driver_sql(
            """
            SELECT relname
            FROM pg_class
            WHERE relname = ANY($1::text[])
              AND relrowsecurity
              AND relforcerowsecurity
            ORDER BY relname
            """,
            (
                [
                    "action_requests",
                    "demo_jobs",
                    "historical_incidents",
                    "idempotency_records",
                    "incident_events",
                    "incidents",
                    "outbox_events",
                ],
            ),
        )

    assert vector_enabled.scalar_one() is True
    assert protected_tables.scalars().all() == [
        "action_requests",
        "demo_jobs",
        "historical_incidents",
        "idempotency_records",
        "incident_events",
        "incidents",
        "outbox_events",
    ]
