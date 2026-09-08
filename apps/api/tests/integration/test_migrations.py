"""Core schema migrations are deterministic and reversible."""

import asyncio

from alembic import command
from conftest import make_alembic_config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


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

    await asyncio.to_thread(command.downgrade, config, "base")
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
        "ix_incidents_service",
        "ix_incidents_severity",
        "ix_incidents_status",
        "ix_incidents_status_severity_service_updated_id",
        "ix_incidents_updated_id",
    } <= second_schema["incidents"]
    assert "ix_incident_events_timeline" in second_schema["incident_events"]
    assert "ix_action_requests_pending" in second_schema["action_requests"]
    assert "ix_outbox_events_claimable" in second_schema["outbox_events"]
    await asyncio.to_thread(command.check, config)


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
        vector_indexes = await connection.exec_driver_sql(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE tablename = 'historical_incidents'
            """
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
    assert all(
        "hnsw" not in definition.lower() and "ivfflat" not in definition.lower()
        for definition in vector_indexes.scalars()
    )


async def test_all_named_constraints_and_restrictive_foreign_keys_exist(
    database_engine,
) -> None:
    expected_checks = {
        "incidents": {
            "ck_incidents_title",
            "ck_incidents_description",
            "ck_incidents_service",
            "ck_incidents_severity",
            "ck_incidents_status",
            "ck_incidents_version",
            "ck_incidents_actor_type",
            "ck_incidents_actor_id",
            "ck_incidents_lifecycle",
            "ck_incidents_timestamp_order",
        },
        "action_requests": {
            "ck_action_requests_type",
            "ck_action_requests_status",
            "ck_action_requests_execution_mode",
            "ck_action_requests_actor_type",
            "ck_action_requests_actor_id",
            "ck_action_requests_base_version",
            "ck_action_requests_fingerprint",
            "ck_action_requests_params",
            "ck_action_requests_policy_version",
            "ck_action_requests_policy_snapshot",
            "ck_action_requests_rationale",
            "ck_action_requests_outcome",
            "ck_action_requests_decider_pair",
            "ck_action_requests_decider_type",
            "ck_action_requests_decision_time",
            "ck_action_requests_outcome_state",
            "ck_action_requests_timestamp_order",
        },
        "incident_events": {
            "ck_incident_events_type",
            "ck_incident_events_actor_type",
            "ck_incident_events_actor_id",
            "ck_incident_events_payload",
        },
        "demo_jobs": {
            "ck_demo_jobs_stable_key",
            "ck_demo_jobs_name",
            "ck_demo_jobs_status",
            "ck_demo_jobs_retry_count",
            "ck_demo_jobs_version",
            "ck_demo_jobs_error",
            "ck_demo_jobs_timestamp_order",
        },
        "historical_incidents": {
            "ck_historical_source_id",
            "ck_historical_title",
            "ck_historical_description",
            "ck_historical_resolution_summary",
            "ck_historical_service",
            "ck_historical_severity",
            "ck_historical_resolution_steps",
            "ck_historical_embedding_provider",
            "ck_historical_embedding_model",
            "ck_historical_timestamp_order",
        },
        "outbox_events": {
            "ck_outbox_event_type",
            "ck_outbox_aggregate_type",
            "ck_outbox_dedupe_key",
            "ck_outbox_payload",
            "ck_outbox_status",
            "ck_outbox_attempt_count",
            "ck_outbox_error",
            "ck_outbox_lifecycle",
            "ck_outbox_timestamp_order",
        },
        "idempotency_records": {
            "ck_idempotency_actor_type",
            "ck_idempotency_actor_id",
            "ck_idempotency_operation",
            "ck_idempotency_fingerprint",
            "ck_idempotency_response_status",
            "ck_idempotency_response_body",
            "ck_idempotency_resource_pair",
            "ck_idempotency_completion",
            "ck_idempotency_timestamp_order",
        },
    }

    async with database_engine.connect() as connection:
        actual_checks = await connection.run_sync(
            lambda sync_connection: {
                table_name: {
                    constraint["name"]
                    for constraint in inspect(sync_connection).get_check_constraints(table_name)
                }
                for table_name in expected_checks
            }
        )
        unique_constraints = await connection.run_sync(
            lambda sync_connection: {
                constraint["name"]
                for table_name in (
                    "action_requests",
                    "demo_jobs",
                    "historical_incidents",
                    "outbox_events",
                    "idempotency_records",
                )
                for constraint in inspect(sync_connection).get_unique_constraints(table_name)
            }
        )
        foreign_key_actions = await connection.exec_driver_sql(
            """
            SELECT conname, confdeltype::text
            FROM pg_constraint
            WHERE contype = 'f'
              AND conrelid::regclass::text = ANY($1::text[])
            """,
            (
                [
                    "action_requests",
                    "demo_jobs",
                    "incident_events",
                    "outbox_events",
                ],
            ),
        )

    assert actual_checks == expected_checks
    assert unique_constraints == {
        "uq_action_requests_principal_idempotency",
        "uq_demo_jobs_incident_stable_key",
        "uq_historical_source_id",
        "uq_idempotency_principal_operation_key",
        "uq_outbox_dedupe_key",
    }
    foreign_key_rows = foreign_key_actions.all()
    assert foreign_key_rows
    assert all(delete_action == "r" for _, delete_action in foreign_key_rows)
