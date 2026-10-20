"""Phase 9 monitoring/remediation transaction and authorization guarantees."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from test_action_api import ActionTokenVerifier, headers, request_retry
from test_alert_ingestion import alert_payload, post, signed
from test_alert_ingestion import alert_settings as alert_settings
from test_rls import _ensure_runtime_role

from incident_ops.application.auth import Principal
from incident_ops.application.services.actions import ActionCommandService, ActionQueryService
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthorizationDeniedError, ResourceNotFoundError
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    IdempotencyRecordModel,
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)
from incident_ops.infrastructure.database.session import apply_principal_context
from incident_ops.main import create_app

pytestmark = pytest.mark.integration


def client_for(settings, engine, observer=None):
    app = create_app(
        settings,
        database_engine=engine,
        token_verifier=ActionTokenVerifier(),
        command_observer=observer,
    )
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    )


async def attach(client, incident_id, version=1, key=None, credential="human-token"):
    return await client.post(
        f"/api/v1/demo/incidents/{incident_id}/job",
        headers=headers(credential, key=key or uuid4()),
        json={"expected_incident_version": version},
    )


async def monitored_job(client, severity="low"):
    payload = alert_payload(severity=severity)
    created = await post(client, payload)
    assert created.status_code == 201, created.text
    incident_id = UUID(created.json()["incident_id"])
    job = await attach(client, incident_id)
    assert job.status_code == 201, job.text
    return payload, incident_id, UUID(job.json()["id"])


async def review(client, action_id, key=None):
    return await client.post(
        f"/api/v1/actions/{action_id}/approve",
        headers=headers("human-token", key=key or uuid4()),
    )


async def job_state(engine, job_id):
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(DemoJobModel.status, DemoJobModel.retry_count, DemoJobModel.version).where(
                    DemoJobModel.id == job_id
                )
            )
        ).one()
        return tuple(row)


@pytest.mark.parametrize("change", ["higher_risk", "recovery", "description"])
async def test_changed_monitor_evidence_stales_retry_without_executing(
    alert_settings, database_engine, clean_database, change
):
    async with client_for(alert_settings, database_engine) as client:
        payload, incident_id, job_id = await monitored_job(client, "medium")
        pending = await request_retry(client, incident_id, job_id, expected_version=2)
        assert pending.status_code == 201, pending.text
        changed = {**payload, "event_version": 2}
        if change == "higher_risk":
            changed["severity"] = "critical"
        elif change == "recovery":
            changed.update(status="RESOLVED", resolved_at=datetime.now(UTC).isoformat())
        else:
            changed["description"] = "New source evidence"
        update_result = await post(client, changed)
        assert update_result.status_code == 200, update_result.text
        action_id = pending.json()["id"]
        still_pending = await client.get(
            f"/api/v1/actions/{action_id}", headers=headers("human-token")
        )
        assert still_pending.json()["status"] == "pending"
        key = uuid4()
        result = await review(client, action_id, key)
        replay = await review(client, action_id, key)
        assert result.status_code == replay.status_code == 200
        assert result.json() == replay.json()
        assert result.json()["status"] == "stale"
        assert result.json()["outcome_code"] == "incident_version_changed"
        assert await job_state(database_engine, job_id) == ("failed", 0, 1)
        detail = await client.get(
            f"/api/v1/incidents/{incident_id}", headers=headers("human-token")
        )
        assert detail.json()["severity"] == "medium"
        assert detail.json()["status"] == "open"
        async with database_engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IncidentEventModel)
                    .where(
                        IncidentEventModel.action_request_id == UUID(action_id),
                        IncidentEventModel.event_type == "action.applied",
                    )
                )
                == 0
            )


async def test_higher_risk_review_records_current_policy_and_heartbeat_preserves_proposal(
    alert_settings, database_engine, clean_database
):
    async with client_for(alert_settings, database_engine) as client:
        payload, incident_id, job_id = await monitored_job(client)
        high = {**payload, "severity": "critical", "event_version": 2}
        assert (await post(client, high)).status_code == 200
        pending = await request_retry(client, incident_id, job_id, expected_version=3)
        assert pending.status_code == 201, pending.text
        assert pending.json()["status"] == "pending"
        snapshot = pending.json()["policy_snapshot"]
        assert "higher_monitoring_risk" in snapshot["reasons"]
        assert snapshot["facts"]["incident_severity"] == "low"
        heartbeat = await post(client, {**high, "event_version": 3})
        assert heartbeat.json()["incident_version"] == 3
        result = await review(client, pending.json()["id"])
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "applied"
        assert result.json()["policy_snapshot"] == snapshot
        assert await job_state(database_engine, job_id) == ("queued", 1, 2)
        async with database_engine.connect() as connection:
            audit = await connection.scalar(
                select(IncidentEventModel.payload).where(
                    IncidentEventModel.action_request_id == UUID(pending.json()["id"]),
                    IncidentEventModel.event_type == "action.approved",
                )
            )
            assert audit["revalidated_policy"]["facts"]["higher_monitoring_risk"] is True
            assert audit["incident_version"] == 3
            assert audit["job_version"] == 1
            assert (
                await connection.scalar(
                    select(IncidentModel.severity).where(IncidentModel.id == incident_id)
                )
                == "low"
            )


class RaceObserver:
    def __init__(self, winner):
        self.winner = winner
        self.armed = False
        self.locked = asyncio.Event()
        self.contender = asyncio.Event()
        self.release = asyncio.Event()

    async def reached(self, point):
        if not self.armed:
            return
        held, contending = (
            ("alert.ingest.after_receipt", "action.review.before_lock")
            if self.winner == "alert"
            else ("action.review.after_incident_lock", "alert.ingest.before_incident_lock")
        )
        if point == held:
            self.locked.set()
            await asyncio.wait_for(self.release.wait(), 10)
        if point == contending:
            self.contender.set()


@pytest.mark.parametrize("winner", ["alert", "approval"])
async def test_ingestion_and_retry_approval_serialize_in_both_lock_orders(
    alert_settings, database_engine, clean_database, winner
):
    observer = RaceObserver(winner)
    async with client_for(alert_settings, database_engine, observer) as client:
        payload, incident_id, job_id = await monitored_job(client, "medium")
        pending = await request_retry(client, incident_id, job_id, expected_version=2)
        assert pending.status_code == 201, pending.text
        observer.armed = True
        changed = {**payload, "severity": "critical", "event_version": 2}
        tasks = []
        try:
            first = asyncio.create_task(
                post(client, changed) if winner == "alert" else review(client, pending.json()["id"])
            )
            tasks.append(first)
            await asyncio.wait_for(observer.locked.wait(), 5)
            second = asyncio.create_task(
                review(client, pending.json()["id"]) if winner == "alert" else post(client, changed)
            )
            tasks.append(second)
            await asyncio.wait_for(observer.contender.wait(), 5)
            # Observe a real PostgreSQL lock wait, not merely task scheduling.
            async with asyncio.timeout(5), database_engine.connect() as connection:
                while not await connection.scalar(  # noqa: ASYNC110 - PostgreSQL has no in-process event
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                        "AND wait_event_type = 'Lock' AND query LIKE '%incidents%')"
                    )
                ):
                    await asyncio.sleep(0.01)
            assert not second.done()
            observer.release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 10)
        finally:
            observer.release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        alert_result, reviewed = results if winner == "alert" else results[::-1]
        assert alert_result.status_code == reviewed.status_code == 200
        assert reviewed.json()["status"] == ("stale" if winner == "alert" else "applied")
        assert await job_state(database_engine, job_id) == (
            ("failed", 0, 1) if winner == "alert" else ("queued", 1, 2)
        )
        detail = await client.get(
            f"/api/v1/incidents/{incident_id}", headers=headers("human-token")
        )
        assert detail.json()["version"] == 3
        assert detail.json()["severity"] == "medium"


async def test_approval_fault_rolls_back_execution_audit_and_idempotency(
    alert_settings, database_engine, clean_database
):
    class FailReview:
        async def reached(self, point):
            if point == "action.review.after_event":
                raise RuntimeError("synthetic private approval failure")

    async with client_for(alert_settings, database_engine, FailReview()) as client:
        _, incident_id, job_id = await monitored_job(client, "medium")
        pending = await request_retry(client, incident_id, job_id, expected_version=2)
        key = uuid4()
        response = await review(client, pending.json()["id"], key)
        assert response.status_code == 500
        assert "private" not in response.text
        assert await job_state(database_engine, job_id) == ("failed", 0, 1)
        async with database_engine.connect() as connection:
            assert await connection.scalar(select(ActionRequestModel.status)) == "pending"
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IncidentEventModel)
                    .where(IncidentEventModel.event_type.in_(["action.approved", "action.applied"]))
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IdempotencyRecordModel)
                    .where(IdempotencyRecordModel.idempotency_key == key)
                )
                == 0
            )
            assert await connection.scalar(select(func.count()).select_from(OutboxEventModel)) == 2
    async with client_for(alert_settings, database_engine) as client:
        retry = await review(client, pending.json()["id"], key)
        assert retry.json()["status"] == "applied"


async def test_fixture_is_versioned_idempotent_and_never_resets_retry_counter(
    alert_settings, database_engine, clean_database
):
    async with client_for(alert_settings, database_engine) as client:
        created = await post(client, alert_payload(severity="low"))
        incident_id = UUID(created.json()["incident_id"])
        assert await job_count(database_engine) == 0
        key = uuid4()
        first = await attach(client, incident_id, key=key)
        replay = await attach(client, incident_id, key=key)
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        assert (await attach(client, incident_id, version=2, key=key)).status_code == 422
        assert (await attach(client, incident_id)).status_code == 409
        job_id = UUID(first.json()["id"])
        requested = await request_retry(client, incident_id, job_id, expected_version=2)
        assert requested.json()["status"] == "applied"
        repeated = await attach(client, incident_id, version=2)
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "queued"
        assert repeated.json()["retry_count"] == 1
        assert await job_count(database_engine) == 1
        async with database_engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IncidentEventModel)
                    .where(IncidentEventModel.event_type == "demo_job.attached")
                )
                == 1
            )


async def job_count(engine):
    async with engine.connect() as connection:
        return await connection.scalar(select(func.count()).select_from(DemoJobModel))


@pytest.mark.parametrize("token", ["agent-token", "reader-token"])
async def test_fixture_requires_human_fixture_scope(
    alert_settings, database_engine, clean_database, token
):
    async with client_for(alert_settings, database_engine) as client:
        created = await post(client, alert_payload())
        result = await attach(client, created.json()["incident_id"], credential=token)
        assert result.status_code == 403
        assert await job_count(database_engine) == 0


async def test_fixture_strict_input_auth_and_atomic_rollback(
    alert_settings, database_engine, clean_database
):
    class FailFixture:
        async def reached(self, point):
            if point == "demo_job.attach.after_event":
                raise RuntimeError("synthetic fixture failure")

    async with client_for(alert_settings, database_engine, FailFixture()) as client:
        created = await post(client, alert_payload())
        incident_id = created.json()["incident_id"]
        path = f"/api/v1/demo/incidents/{incident_id}/job"
        assert (await client.post(path, json={"expected_incident_version": 1})).status_code == 401
        for body in [
            {},
            {"expected_incident_version": 0},
            {"expected_incident_version": True},
            {"expected_incident_version": 1, "command": "arbitrary"},
        ]:
            result = await client.post(path, headers=headers("human-token", key=uuid4()), json=body)
            assert result.status_code == 422
        assert (await attach(client, uuid4())).status_code == 404
        key = uuid4()
        failed = await attach(client, incident_id, key=key)
        assert failed.status_code == 500
        assert await job_count(database_engine) == 0
        async with database_engine.connect() as connection:
            assert await connection.scalar(select(IncidentModel.version)) == 1
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IdempotencyRecordModel)
                    .where(IdempotencyRecordModel.idempotency_key == key)
                )
                == 0
            )
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(IncidentEventModel)
                    .where(IncidentEventModel.event_type == "demo_job.attached")
                )
                == 0
            )


@pytest.mark.parametrize("overprivileged", [False, True])
async def test_application_services_deny_monitoring_principals_before_storage(
    database_engine, overprivileged
):
    scopes = {"alerts:ingest"}
    if overprivileged:
        scopes |= {"incidents:read", "actions:request", "actions:approve", "fixtures:write"}
    principal = Principal(ActorType.SYSTEM, "monitoring:monitoring-demo-prod", frozenset(scopes))
    service = ActionCommandService(database_engine, demo_enabled=True)
    queries = ActionQueryService(database_engine)
    common = {"principal": principal, "idempotency_key": uuid4(), "request_id": uuid4()}
    calls = [
        lambda: service.request_action(
            **common,
            incident_id=uuid4(),
            expected_incident_version=1,
            action_type="retry_failed_job",
            parameters={"job_id": uuid4()},
            rationale=None,
        ),
        lambda: service.approve_action(**common, action_id=uuid4()),
        lambda: service.reject_action(**common, action_id=uuid4(), reason=None),
        lambda: service.attach_demo_job(**common, incident_id=uuid4(), expected_incident_version=1),
        lambda: service.simulate_job_failure(**common, job_id=uuid4()),
        lambda: queries.get_action(principal=principal, action_id=uuid4()),
        lambda: queries.list_actions(
            principal=principal,
            status=None,
            incident_id=None,
            minimum_age_minutes=None,
            cursor=None,
            limit=20,
        ),
    ]
    for call in calls:
        with pytest.raises(AuthorizationDeniedError):
            await call()


async def test_all_action_routes_refuse_monitoring_signatures(
    alert_settings, database_engine, clean_database
):
    async with client_for(alert_settings, database_engine) as client:
        payload, incident_id, job_id = await monitored_job(client, "medium")
        pending = await request_retry(client, incident_id, job_id, expected_version=2)
        action_id = pending.json()["id"]
        endpoints = [
            ("POST", f"/api/v1/incidents/{incident_id}/actions"),
            ("GET", "/api/v1/actions"),
            ("GET", f"/api/v1/actions/{action_id}"),
            ("POST", f"/api/v1/actions/{action_id}/approve"),
            ("POST", f"/api/v1/actions/{action_id}/reject"),
            ("POST", f"/api/v1/demo/jobs/{job_id}/simulate-failure"),
            ("POST", f"/api/v1/demo/incidents/{incident_id}/job"),
        ]
        body = json.dumps(payload).encode()
        for method, path in endpoints:
            result = await client.request(
                method, path, content=body, headers=signed(body, key=str(uuid4()))
            )
            assert result.status_code == 401, (path, result.text)
        assert await job_state(database_engine, job_id) == ("failed", 0, 1)


async def test_disabled_fixture_service_denies_even_authorized_human(database_engine):
    principal = await ActionTokenVerifier().verify("human-token")
    service = ActionCommandService(database_engine)
    with pytest.raises(ResourceNotFoundError):
        await service.attach_demo_job(
            principal=principal,
            incident_id=uuid4(),
            expected_incident_version=1,
            idempotency_key=uuid4(),
            request_id=uuid4(),
        )
    with pytest.raises(ResourceNotFoundError):
        await service.simulate_job_failure(
            principal=principal, job_id=uuid4(), idempotency_key=uuid4(), request_id=uuid4()
        )


async def test_changed_policy_marks_approval_stale(alert_settings, database_engine, clean_database):
    async with client_for(alert_settings, database_engine) as client:
        _, incident_id, job_id = await monitored_job(client, "medium")
        pending = await request_retry(client, incident_id, job_id, expected_version=2)
        async with database_engine.begin() as connection:
            await connection.execute(
                update(ActionRequestModel).values(policy_version="prior-policy")
            )
        result = await review(client, pending.json()["id"])
        assert result.status_code == 200
        assert result.json()["status"] == "stale"
        assert result.json()["outcome_code"] == "policy_changed"
        assert await job_state(database_engine, job_id) == ("failed", 0, 1)


@pytest.mark.parametrize("requester", ["human-token", "agent-token"])
async def test_non_bypass_human_demo_and_monitoring_rls_denial(
    alert_settings, postgres_test_url, database_engine, clean_database, requester
):
    await _ensure_runtime_role(database_engine)
    runtime = create_async_engine(postgres_test_url, poolclass=NullPool)

    @event.listens_for(runtime.sync_engine, "connect")
    def set_role(connection, _record):
        cursor = connection.cursor()
        cursor.execute("SET ROLE incident_ops_phase2_runtime")
        cursor.close()

    try:
        async with client_for(alert_settings, runtime) as client:
            payload, incident_id, job_id = await monitored_job(client)
            first = await request_retry(
                client, incident_id, job_id, expected_version=2, credential=requester
            )
            assert first.status_code == 201, first.text
            assert first.json()["status"] == "applied"
            failed = await client.post(
                f"/api/v1/demo/jobs/{job_id}/simulate-failure",
                headers=headers("human-token", key=uuid4()),
            )
            assert failed.status_code == 200, failed.text
            second = await request_retry(
                client, incident_id, job_id, expected_version=2, credential=requester
            )
            assert second.json()["status"] == "pending"
            approved = await review(client, second.json()["id"])
            assert approved.json()["status"] == "applied"
            await client.post(
                f"/api/v1/demo/jobs/{job_id}/simulate-failure",
                headers=headers("human-token", key=uuid4()),
            )
            third = await request_retry(
                client, incident_id, job_id, expected_version=2, credential=requester
            )
            assert third.json()["status"] == "rejected"
            assert third.json()["outcome_code"] == "retry_limit_reached"
            recovered = await post(
                client,
                {
                    **payload,
                    "event_version": 2,
                    "status": "RESOLVED",
                    "resolved_at": datetime.now(UTC).isoformat(),
                },
            )
            assert recovered.json()["incident_status"] == "open"
            for status, version in [("resolved", 3), ("closed", 4)]:
                result = await client.patch(
                    f"/api/v1/incidents/{incident_id}",
                    headers=headers("human-token", key=uuid4()),
                    json={"expected_version": version, "status": status},
                )
                assert result.status_code == 200, result.text
            async with runtime.connect() as connection, connection.begin():
                await apply_principal_context(
                    connection,
                    Principal(
                        ActorType.SYSTEM,
                        "monitoring:monitoring-demo-prod",
                        frozenset({"alerts:ingest"}),
                    ),
                )
                for model in (DemoJobModel, ActionRequestModel):
                    assert await connection.scalar(select(func.count()).select_from(model)) == 0
                mutation = await connection.execute(
                    update(DemoJobModel).where(DemoJobModel.id == job_id).values(status="queued")
                )
                assert mutation.rowcount == 0
    finally:
        await runtime.dispose()


async def test_application_commands_require_scope_even_without_http_adapter(database_engine):
    principal = Principal(ActorType.HUMAN, "read-only-operator", frozenset({"incidents:read"}))
    service = ActionCommandService(database_engine, demo_enabled=True)
    common = {"principal": principal, "idempotency_key": uuid4(), "request_id": uuid4()}
    for call in (
        lambda: service.request_action(
            **common,
            incident_id=uuid4(),
            expected_incident_version=1,
            action_type="retry_failed_job",
            parameters={"job_id": uuid4()},
            rationale=None,
        ),
        lambda: service.approve_action(**common, action_id=uuid4()),
        lambda: service.reject_action(**common, action_id=uuid4(), reason=None),
        lambda: service.attach_demo_job(**common, incident_id=uuid4(), expected_incident_version=1),
        lambda: service.simulate_job_failure(**common, job_id=uuid4()),
    ):
        with pytest.raises(AuthorizationDeniedError):
            await call()


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_fixture_attachment_cannot_create_extra_jobs(
    alert_settings, database_engine, clean_database, same_key
):
    async with client_for(alert_settings, database_engine) as client:
        created = await post(client, alert_payload())
        incident_id = created.json()["incident_id"]
        key = uuid4()
        results = await asyncio.gather(
            attach(client, incident_id, key=key),
            attach(client, incident_id, key=key if same_key else uuid4()),
        )
        assert sorted(result.status_code for result in results) == (
            [201, 201] if same_key else [201, 409]
        )
        if same_key:
            assert results[0].json() == results[1].json()
        assert await job_count(database_engine) == 1


async def test_fresh_seed_pending_action_can_still_be_reviewed(
    alert_settings, database_engine, clean_database
):
    from incident_ops.seed import ACTION_IDS, seed_database

    await seed_database(database_engine, reset=False)
    async with client_for(alert_settings, database_engine) as client:
        result = await review(client, ACTION_IDS[0])
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "applied"
        assert result.json()["outcome_code"] == "incident_status_updated"
