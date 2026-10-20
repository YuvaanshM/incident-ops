"""Durable investigation ownership, replay, concurrency and policy on PostgreSQL."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from test_action_api import headers
from test_agent_authorization import client_for, credential
from test_agent_authorization import delegated_settings as delegated_settings
from test_alert_ingestion import alert_settings as alert_settings
from test_monitoring_remediation import monitored_job
from test_rls import _ensure_runtime_role

from incident_ops.application.auth import Principal
from incident_ops.application.investigation import Decision, ModelTurn
from incident_ops.domain.enums import ActorType
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    IdempotencyRecordModel,
    IncidentEventModel,
    InvestigationRunModel,
)
from incident_ops.infrastructure.database.session import apply_principal_context

pytestmark = pytest.mark.integration


class Investigator:
    provider = "test"
    model = "scripted"

    def __init__(self, *, action=False, wait=False):
        self.calls = []
        self.action = action
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not wait:
            self.release.set()

    async def next(self, context):
        self.calls.append(context)
        self.entered.set()
        await self.release.wait()
        observation = json.loads(context)["untrusted_observations"]["incident"]
        evidence = observation["evidence"][0]
        return ModelTurn(
            Decision.model_validate(
                {
                    "tool": "request_retry_failed_job" if self.action else "finish",
                    "findings": [
                        {"source_id": evidence["source_id"], "quote": evidence["text"][:100]}
                    ],
                    "job_id": observation["job_ids"][0] if self.action else None,
                    "target_status": None,
                }
            ),
            100,
            100,
        )


async def start(client, token, incident_id, key, **changes):
    return await client.post(
        f"/api/v1/incidents/{incident_id}/investigations",
        headers=headers(token, key=key),
        json={"expected_incident_version": 2, **changes},
    )


async def test_complete_run_is_replayed_without_provider_work_and_is_owner_scoped(
    delegated_settings, database_engine, clean_database
):
    model = Investigator()
    async with client_for(delegated_settings, database_engine, investigation_model=model) as client:
        _, incident_id, _ = await monitored_job(client)
        token = await credential(client)
        key = uuid4()
        result = await start(client, token, incident_id, key)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["outcome"]["status"] == "completed"
        assert body["version"] == 2
        replay = await start(client, await credential(client), incident_id, key)
        assert replay.json() == body
        assert len(model.calls) == 1
        mismatch = await start(client, token, incident_id, key, allow_action_requests=True)
        assert mismatch.status_code == 422
        other = await credential(client, "other-human")
        recovery = await client.get(
            f"/api/v1/investigations/by-request/{key}", headers=headers(other)
        )
        assert recovery.status_code == 404
        async with database_engine.connect() as connection:
            events = (
                (
                    await connection.execute(
                        select(IncidentEventModel.event_type).where(
                            IncidentEventModel.event_type.like("investigation.%")
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert sorted(events) == ["investigation.completed", "investigation.started"]
            assert (
                await connection.scalar(select(func.count()).select_from(InvestigationRunModel))
                == 1
            )


async def test_concurrent_duplicate_returns_running_receipt_and_another_key_is_limited(
    delegated_settings, database_engine, clean_database
):
    model = Investigator(wait=True)
    async with client_for(delegated_settings, database_engine, investigation_model=model) as client:
        _, incident_id, _ = await monitored_job(client)
        token, key = await credential(client), uuid4()
        task = asyncio.create_task(start(client, token, incident_id, key))
        try:
            await asyncio.wait_for(model.entered.wait(), 3)
            replay = await start(client, token, incident_id, key)
            assert replay.status_code == 200
            assert replay.json()["outcome"]["status"] == "running"
            extra = await start(client, token, incident_id, uuid4())
            assert extra.status_code == 429
        finally:
            model.release.set()
        result = await asyncio.wait_for(task, 3)
        assert result.json()["id"] == replay.json()["id"]
        assert len(model.calls) == 1


async def test_action_uses_existing_policy_and_only_one_request_after_replay(
    delegated_settings, database_engine, clean_database
):
    model = Investigator(action=True)
    async with client_for(delegated_settings, database_engine, investigation_model=model) as client:
        _, incident_id, _ = await monitored_job(client)
        token, key = await credential(client), uuid4()
        result = await start(client, token, incident_id, key, allow_action_requests=True)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["outcome"]["action"]["status"] == "applied"
        recovered = await client.get(
            f"/api/v1/actions/by-request/{body['action_request_key']}", headers=headers(token)
        )
        assert recovered.json()["id"] == body["outcome"]["action"]["id"]
        replay = await start(client, token, incident_id, key, allow_action_requests=True)
        assert replay.json() == body
        assert len(model.calls) == 1


async def test_stale_start_and_disabled_feature_do_not_call_provider(
    delegated_settings, database_engine, clean_database
):
    model = Investigator()
    async with client_for(delegated_settings, database_engine, investigation_model=model) as client:
        _, incident_id, _ = await monitored_job(client)
        token = await credential(client)
        response = await start(client, token, incident_id, uuid4(), expected_incident_version=1)
        assert response.status_code == 409
        assert not model.calls
    async with client_for(delegated_settings, database_engine) as client:
        token = await credential(client)
        response = await start(client, token, incident_id, uuid4())
        assert response.status_code == 503


async def test_forced_rls_denies_another_principal_and_unscoped_access(
    delegated_settings, database_engine, clean_database
):
    async with client_for(
        delegated_settings, database_engine, investigation_model=Investigator()
    ) as client:
        _, incident_id, _ = await monitored_job(client)
        token = await credential(client)
        result = await start(client, token, incident_id, uuid4())
        identifier = UUID(result.json()["id"])
    await _ensure_runtime_role(database_engine)
    from sqlalchemy.ext.asyncio import AsyncSession

    async with database_engine.connect() as owner_connection:
        owner_id = await owner_connection.scalar(
            select(InvestigationRunModel.principal_id).where(InvestigationRunModel.id == identifier)
        )
    async with database_engine.connect() as connection, connection.begin():
        await connection.execute(text("SET LOCAL ROLE incident_ops_phase2_runtime"))
        async with AsyncSession(bind=connection) as session:
            await apply_principal_context(
                session,
                Principal(
                    ActorType.AGENT, "other", frozenset({"incidents:read", "actions:request"})
                ),
            )
            assert (
                await session.scalar(
                    select(InvestigationRunModel).where(InvestigationRunModel.id == identifier)
                )
                is None
            )
            cross_write = await session.execute(
                update(InvestigationRunModel)
                .where(InvestigationRunModel.id == identifier)
                .values(version=1)
            )
            assert cross_write.rowcount == 0
            await apply_principal_context(
                session,
                Principal(
                    ActorType.AGENT, owner_id, frozenset({"incidents:read", "actions:request"})
                ),
            )
            assert (
                await session.scalar(
                    select(InvestigationRunModel.id).where(InvestigationRunModel.id == identifier)
                )
                == identifier
            )
            untrusted_write = await session.execute(
                update(InvestigationRunModel)
                .where(InvestigationRunModel.id == identifier)
                .values(version=1)
            )
            assert untrusted_write.rowcount == 0
            for principal in (
                Principal(
                    ActorType.HUMAN, owner_id, frozenset({"incidents:read", "actions:request"})
                ),
                Principal(ActorType.AGENT, owner_id, frozenset()),
            ):
                await apply_principal_context(session, principal)
                assert (
                    await session.scalar(
                        select(InvestigationRunModel.id).where(
                            InvestigationRunModel.id == identifier
                        )
                    )
                    is None
                )


async def test_status_action_reaches_policy_with_expected_version(
    delegated_settings, database_engine, clean_database
):
    class StatusInvestigator(Investigator):
        async def next(self, context):
            turn = await super().next(context)
            return ModelTurn(
                turn.decision.model_copy(
                    update={"tool": "request_status_update", "target_status": "investigating"}
                ),
                100,
                100,
            )

    async with client_for(
        delegated_settings, database_engine, investigation_model=StatusInvestigator()
    ) as client:
        _, incident_id, _ = await monitored_job(client)
        result = await start(
            client, await credential(client), incident_id, uuid4(), allow_action_requests=True
        )
        assert result.json()["outcome"]["action"]["action_type"] == "request_status_update"
        assert result.json()["outcome"]["action"]["status"] in {"applied", "pending"}


async def test_model_proposal_cannot_skip_human_approval(
    delegated_settings, database_engine, clean_database
):
    async with client_for(
        delegated_settings, database_engine, investigation_model=Investigator(action=True)
    ) as client:
        _, incident_id, _ = await monitored_job(client, severity="critical")
        result = await start(
            client, await credential(client), incident_id, uuid4(), allow_action_requests=True
        )
        assert result.json()["outcome"]["action"]["status"] == "pending"


async def test_version_change_during_provider_call_stops_before_action(
    delegated_settings, database_engine, clean_database
):
    model = Investigator(action=True, wait=True)
    async with client_for(delegated_settings, database_engine, investigation_model=model) as client:
        _, incident_id, _ = await monitored_job(client)
        token, key = await credential(client), uuid4()
        task = asyncio.create_task(
            start(client, token, incident_id, key, allow_action_requests=True)
        )
        try:
            await asyncio.wait_for(model.entered.wait(), 3)
            changed = await client.patch(
                f"/api/v1/incidents/{incident_id}",
                headers=headers("human-token", key=uuid4()),
                json={"expected_version": 2, "description": "Changed during investigation"},
            )
            assert changed.status_code == 200, changed.text
        finally:
            model.release.set()
        result = await asyncio.wait_for(task, 3)
        assert result.json()["outcome"]["reason"] == "version_conflict"
        async with database_engine.connect() as connection:
            assert (
                await connection.scalar(select(func.count()).select_from(ActionRequestModel)) == 0
            )


async def test_start_rollback_leaves_no_receipt_audit_or_provider_call(
    delegated_settings, database_engine, clean_database
):
    class FailStart:
        async def reached(self, point):
            if point == "investigation.start.before_commit":
                raise RuntimeError("synthetic transaction failure")

    model = Investigator()
    async with client_for(
        delegated_settings, database_engine, investigation_model=model, command_observer=FailStart()
    ) as client:
        _, incident_id, _ = await monitored_job(client)
        with pytest.raises(RuntimeError, match="synthetic transaction failure"):
            await start(client, await credential(client), incident_id, uuid4())
    assert not model.calls
    async with database_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(InvestigationRunModel)) == 0
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(IdempotencyRecordModel)
                .where(IdempotencyRecordModel.operation == "investigations.start")
            )
            == 0
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(IncidentEventModel)
                .where(IncidentEventModel.event_type.like("investigation.%"))
            )
            == 0
        )


async def test_cancelled_run_is_durable_and_replay_does_not_restart_it(
    delegated_settings, database_engine, clean_database
):
    model = Investigator(wait=True)
    async with client_for(delegated_settings, database_engine, investigation_model=model) as client:
        _, incident_id, _ = await monitored_job(client)
        token, key = await credential(client), uuid4()
        task = asyncio.create_task(start(client, token, incident_id, key))
        await asyncio.wait_for(model.entered.wait(), 3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        recovered = await client.get(
            f"/api/v1/investigations/by-request/{key}", headers=headers(token)
        )
        assert recovered.json()["outcome"]["status"] == "cancelled"
        replay = await start(client, token, incident_id, key)
        assert replay.json() == recovered.json()
        assert len(model.calls) == 1


async def test_lost_finish_is_reported_interrupted_after_deadline_without_resume(
    delegated_settings, database_engine, clean_database
):
    class FailFinish:
        async def reached(self, point):
            if point == "investigation.finish.before_commit":
                raise RuntimeError("synthetic lost finish")

    model = Investigator()
    async with client_for(
        delegated_settings,
        database_engine,
        investigation_model=model,
        command_observer=FailFinish(),
    ) as client:
        _, incident_id, _ = await monitored_job(client)
        token, key = await credential(client), uuid4()
        with pytest.raises(RuntimeError, match="synthetic lost finish"):
            await start(client, token, incident_id, key)
        async with database_engine.begin() as connection:
            await connection.execute(
                update(InvestigationRunModel)
                .where(InvestigationRunModel.request_key == key)
                .values(
                    created_at=func.now() - text("interval '1 minute'"),
                    deadline=func.now() - text("interval '1 second'"),
                )
            )
        response = await start(client, token, incident_id, key)
        assert response.json()["outcome"]["status"] == "interrupted"
        assert len(model.calls) == 1
        async with database_engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IncidentEventModel)
                    .where(IncidentEventModel.event_type == "investigation.completed")
                )
                == 0
            )
