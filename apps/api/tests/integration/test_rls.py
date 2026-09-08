"""Deny-by-default and principal-aware RLS behavior."""

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _ensure_runtime_role(database_engine: AsyncEngine) -> None:
    async with database_engine.begin() as owner:
        await owner.execute(
            text(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = 'incident_ops_phase2_runtime'
                    ) THEN
                        CREATE ROLE incident_ops_phase2_runtime
                            NOLOGIN NOSUPERUSER NOBYPASSRLS;
                    END IF;
                END $$
                """
            )
        )
        await owner.execute(text("GRANT USAGE ON SCHEMA public TO incident_ops_phase2_runtime"))
        await owner.execute(
            text(
                """
                GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public
                    TO incident_ops_phase2_runtime
                """
            )
        )


async def test_unset_security_context_cannot_read_domain_rows(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    await _ensure_runtime_role(database_engine)
    async with database_engine.begin() as owner:
        await owner.execute(
            text(
                """
                INSERT INTO incidents (
                    id, title, description, service, severity, status, version,
                    created_by_type, created_by_id
                ) VALUES (
                    :id, 'RLS proof', 'Synthetic row', 'api', 'low', 'open', 1,
                    'human', 'operator-1'
                )
                """
            ),
            {"id": uuid4()},
        )

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("SET LOCAL ROLE incident_ops_phase2_runtime"))
        assert await connection.scalar(text("SELECT count(*) FROM incidents")) == 0
        await connection.execute(text("SELECT set_config('app.actor_type', 'human', true)"))
        await connection.execute(text("SELECT set_config('app.actor_id', 'operator-2', true)"))
        await connection.execute(
            text("SELECT set_config('app.scopes', '[\"incidents:read\"]', true)")
        )
        assert await connection.scalar(text("SELECT count(*) FROM incidents")) == 1
        await transaction.rollback()


async def test_idempotency_records_are_isolated_by_verified_principal(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    await _ensure_runtime_role(database_engine)
    record_id = uuid4()
    async with database_engine.begin() as owner:
        await owner.execute(
            text(
                """
                INSERT INTO idempotency_records (
                    id, principal_type, principal_id, operation, idempotency_key,
                    request_fingerprint
                ) VALUES (
                    :id, 'human', 'operator-1', 'incidents.create', :key, :fingerprint
                )
                """
            ),
            {"id": record_id, "key": uuid4(), "fingerprint": "a" * 64},
        )

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("SET LOCAL ROLE incident_ops_phase2_runtime"))
        await connection.execute(text("SELECT set_config('app.actor_type', 'human', true)"))
        await connection.execute(text("SELECT set_config('app.scopes', '[]', true)"))
        await connection.execute(text("SELECT set_config('app.actor_id', 'operator-2', true)"))
        assert await connection.scalar(text("SELECT count(*) FROM idempotency_records")) == 0
        await connection.execute(text("SELECT set_config('app.actor_id', 'operator-1', true)"))
        assert await connection.scalar(text("SELECT count(*) FROM idempotency_records")) == 0
        await connection.execute(
            text("SELECT set_config('app.scopes', '[\"incidents:write\"]', true)")
        )
        assert await connection.scalar(text("SELECT count(*) FROM idempotency_records")) == 1
        await transaction.rollback()


async def test_incident_read_scope_can_inspect_action_status_without_mutating_it(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    await _ensure_runtime_role(database_engine)
    incident_id = uuid4()
    action_id = uuid4()
    async with database_engine.begin() as owner:
        await owner.execute(
            text(
                """
                INSERT INTO incidents (
                    id, title, description, service, severity, status, version,
                    created_by_type, created_by_id
                ) VALUES (
                    :incident_id, 'Action read proof', 'Synthetic row', 'api',
                    'medium', 'open', 1, 'human', 'operator-1'
                )
                """
            ),
            {"incident_id": incident_id},
        )
        await owner.execute(
            text(
                """
                INSERT INTO action_requests (
                    id, incident_id, action_type, status, execution_mode,
                    requested_by_type, requested_by_id, idempotency_key,
                    request_fingerprint, base_incident_version, parameters,
                    policy_version, policy_snapshot
                ) VALUES (
                    :action_id, :incident_id, 'request_status_update', 'pending',
                    'human_approved', 'agent', 'agent-1', :idempotency_key,
                    :fingerprint, 1, '{"target_status":"investigating"}'::jsonb,
                    '2026-09-05.v1', '{"decision":"require_approval"}'::jsonb
                )
                """
            ),
            {
                "action_id": action_id,
                "incident_id": incident_id,
                "idempotency_key": uuid4(),
                "fingerprint": "a" * 64,
            },
        )

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("SET LOCAL ROLE incident_ops_phase2_runtime"))
        await connection.execute(text("SELECT set_config('app.actor_type', 'human', true)"))
        await connection.execute(text("SELECT set_config('app.actor_id', 'reader-1', true)"))
        await connection.execute(
            text("SELECT set_config('app.scopes', '[\"incidents:read\"]', true)")
        )
        assert await connection.scalar(text("SELECT count(*) FROM action_requests")) == 1
        updated = await connection.execute(
            text("UPDATE action_requests SET rationale = 'forbidden' WHERE id = :action_id"),
            {"action_id": action_id},
        )
        assert updated.rowcount == 0
        await transaction.rollback()
