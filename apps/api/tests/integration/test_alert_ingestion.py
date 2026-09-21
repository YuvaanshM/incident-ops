"""Signed generic alert deliveries drive one durable incident lifecycle."""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_incident_api import ApiTokenVerifier
from test_rls import _ensure_runtime_role

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.infrastructure.database.models import (
    AlertEventModel,
    AlertModel,
    DemoJobModel,
    IdempotencyRecordModel,
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)
from incident_ops.infrastructure.database.session import apply_principal_context
from incident_ops.infrastructure.monitoring_config import AlertIntakeSettings
from incident_ops.main import create_app

PATH = "/api/v1/alerts/webhook"
SECRET = "synthetic-alert-signing-secret-12345"
KEY_ID = "synthetic-key"


def alert_payload(**changes):
    started = datetime.now(UTC) - timedelta(minutes=5)
    payload = {
        "schema_version": 1,
        "source": "monitoring-demo-prod",
        "external_alert_id": "checkout-health",
        "service": "checkout",
        "severity": "high",
        "status": "FIRING",
        "title": "Checkout unavailable",
        "description": "Synthetic monitor reports checkout failures.",
        "started_at": started.isoformat(),
        "resolved_at": None,
        "event_version": 1,
        "metadata": {"environment": "demo"},
    }
    payload.update(changes)
    return payload


def signed(body: bytes, *, key: str, timestamp: str | None = None) -> dict[str, str]:
    stamp = timestamp or str(int(datetime.now(UTC).timestamp()))
    base = f"v1\nPOST\n{PATH}\n{KEY_ID}\n{stamp}\n{key}\n".encode() + body
    signature = hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Alert-Key-Id": KEY_ID,
        "X-Alert-Timestamp": stamp,
        "Idempotency-Key": key,
        "X-Alert-Signature": f"v1={signature}",
    }


async def post(client: AsyncClient, payload: dict, *, key: str | None = None):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return await client.post(PATH, content=body, headers=signed(body, key=key or str(uuid4())))


@pytest.fixture
def alert_settings(test_settings, postgres_test_url):
    return test_settings.model_copy(
        update={
            "database_url": postgres_test_url,
            "alert_intake": AlertIntakeSettings.model_validate(
                {
                    "enabled": True,
                    "sources": [
                        {
                            "source": "monitoring-demo-prod",
                            "services": ["checkout"],
                            "keys": [{"key_id": KEY_ID, "signing_secret": SECRET}],
                        }
                    ],
                }
            ),
        }
    )


@pytest.fixture
def alert_app(alert_settings, database_engine):
    return create_app(
        alert_settings,
        database_engine=database_engine,
        token_verifier=ApiTokenVerifier(),
    )


async def test_first_firing_is_atomic_and_exact_replay_has_no_new_effects(
    alert_app, database_engine, clean_database
):
    payload = alert_payload()
    key = str(uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, payload, key=key)
        assert created.status_code == 201, created.text
        assert created.json()["outcome"] == "incident_created"
        assert created.json()["notification"] == "queued"
        replay = await post(client, payload, key=key)
        assert replay.status_code == 201
        assert replay.json() == created.json()
        assert replay.headers["Idempotent-Replayed"] == "true"
        listing = await client.get(
            "/api/v1/incidents", headers={"Authorization": "Bearer human-token"}
        )
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 1
        assert listing.json()["items"][0]["severity"] == "high"
        assert listing.json()["items"][0]["created_by_id"] == "monitoring:monitoring-demo-prod"
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEventModel)) >= 1
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 1
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 1


async def test_update_and_recovery_preserve_human_incident_lifecycle(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        assert created.status_code == 201
        incident_id = created.json()["incident_id"]
        update = await post(
            client,
            {**first, "event_version": 2, "severity": "critical", "title": "Wider outage"},
        )
        assert update.status_code == 200, update.text
        assert update.json()["outcome"] == "incident_updated"
        recovery = await post(
            client,
            {
                **first,
                "event_version": 3,
                "status": "RESOLVED",
                "resolved_at": datetime.now(UTC).isoformat(),
            },
        )
        assert recovery.status_code == 200, recovery.text
        assert recovery.json()["outcome"] == "resolution_recorded"
        current = await client.get(
            f"/api/v1/incidents/{incident_id}",
            headers={"Authorization": "Bearer human-token"},
        )
        assert current.json()["status"] == "open"
        assert current.json()["severity"] == "high"
        assert current.json()["monitoring_status"] == "RESOLVED"
        history = await client.get(
            f"/api/v1/incidents/{incident_id}/alerts",
            headers={"Authorization": "Bearer human-token"},
        )
        assert history.status_code == 200
        assert [item["outcome"] for item in history.json()["items"]] == [
            "resolution_recorded",
            "incident_updated",
            "incident_created",
        ]
        assert "raw_payload" not in str(history.json())
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 1


async def test_recovery_before_firing_creates_no_phantom_incident(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    resolved = {
        **first,
        "status": "RESOLVED",
        "resolved_at": datetime.now(UTC).isoformat(),
        "event_version": 2,
    }
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        orphan = await post(client, resolved)
        assert orphan.status_code == 200, orphan.text
        assert orphan.json()["outcome"] == "orphan_resolution"
        assert orphan.json()["incident_id"] is None
        late = await post(client, first)
        assert late.status_code == 200
        assert late.json()["outcome"] == "stale_ignored"
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 0


async def test_new_key_same_state_and_conflicting_version(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        assert created.status_code == 201
        unchanged = await post(client, first)
        assert unchanged.status_code == 200
        assert unchanged.json()["outcome"] == "unchanged"
        assert unchanged.json()["incident_version"] == 1
        conflict = await post(client, {**first, "title": "Changed at same version"})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "alert_version_conflict"
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(AlertEventModel)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 1


async def test_metadata_only_delivery_is_receipted_without_invalidating_incident_version(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        assert created.status_code == 201
        heartbeat = await post(
            client,
            {
                **first,
                "event_version": 2,
                "metadata": {
                    "environment": "demo",
                    "region": "us-west",
                    "secret": "do-not-persist-me",
                },
                "raw_payload": {"token": "raw-secret"},
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["outcome"] == "unchanged"
        assert heartbeat.json()["incident_version"] == 1
    async with AsyncSession(database_engine) as session:
        alert = await session.scalar(select(AlertModel))
        assert alert is not None
        assert alert.metadata_ == {"environment": "demo", "region": "us-west"}
        receipts = (await session.scalars(select(AlertEventModel))).all()
        assert len(receipts) == 2
        assert all("secret" not in str(receipt.metadata_) for receipt in receipts)
        assert await session.scalar(select(func.count()).select_from(IncidentEventModel)) == 2


async def test_old_recovery_cannot_clear_new_occurrence(alert_app, database_engine, clean_database):
    first = alert_payload()
    later = {
        **first,
        "started_at": (
            datetime.fromisoformat(first["started_at"]) + timedelta(minutes=1)
        ).isoformat(),
    }
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        refired = await post(client, later)
        assert refired.status_code == 200, refired.text
        assert refired.json()["incident_id"] == created.json()["incident_id"]
        old_recovery = await post(
            client,
            {
                **first,
                "status": "RESOLVED",
                "resolved_at": datetime.now(UTC).isoformat(),
                "event_version": 2,
            },
        )
        assert old_recovery.status_code == 200, old_recovery.text
        assert old_recovery.json()["outcome"] == "stale_ignored"
        detail = await client.get(
            f"/api/v1/incidents/{created.json()['incident_id']}",
            headers={"Authorization": "Bearer human-token"},
        )
        assert detail.json()["monitoring_status"] == "FIRING"
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 1
        assert await session.scalar(select(func.count()).select_from(AlertEventModel)) == 3


async def test_authentication_binding_and_payload_limits(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    body = json.dumps(first).encode()
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        unsigned = await client.post(PATH, content=body)
        assert unsigned.status_code == 401
        tampered = await client.post(
            PATH, content=body + b" ", headers=signed(body, key=str(uuid4()))
        )
        assert tampered.status_code == 401
        stale = await client.post(
            PATH,
            content=body,
            headers=signed(body, key=str(uuid4()), timestamp="1000000000"),
        )
        assert stale.status_code == 401
        wrong_source = await post(client, {**first, "source": "another-source"})
        assert wrong_source.status_code == 403
        wrong_service = await post(client, {**first, "service": "billing"})
        assert wrong_service.status_code == 403
        invalid = await post(client, {**first, "unexpected": "field"})
        assert invalid.status_code == 422
        oversized = await client.post(
            PATH,
            content=b"{" + b"x" * (65 * 1024) + b"}",
            headers=signed(b"{}", key=str(uuid4())),
        )
        assert oversized.status_code == 413
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(AlertModel)) == 0
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 0


async def test_higher_monitor_risk_requires_human_review_for_retry(
    alert_app, database_engine, clean_database
):
    first = alert_payload(severity="low")
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        assert created.status_code == 201, created.text
        incident_id = created.json()["incident_id"]
        job_id = uuid4()
        async with database_engine.begin() as connection:
            await connection.execute(
                DemoJobModel.__table__.insert().values(
                    id=job_id,
                    incident_id=incident_id,
                    stable_key=f"checkout-job-{job_id}",
                    name="Synthetic failed job",
                    status="failed",
                    retry_count=0,
                    version=1,
                    last_error="Synthetic timeout",
                )
            )
        update = await post(client, {**first, "event_version": 2, "severity": "high"})
        assert update.status_code == 200, update.text
        assert update.json()["notification"] == "queued"
        assert update.json()["incident_version"] == 2
        request = await client.post(
            f"/api/v1/incidents/{incident_id}/actions",
            headers={
                "Authorization": "Bearer agent-writer-token",
                "Idempotency-Key": str(uuid4()),
            },
            json={
                "expected_incident_version": 2,
                "action_type": "retry_failed_job",
                "parameters": {"job_id": str(job_id)},
                "rationale": "Request a bounded retry.",
            },
        )
        assert request.status_code == 201, request.text
        assert request.json()["status"] == "pending"
        assert request.json()["execution_mode"] == "human_approved"
        assert request.json()["policy_snapshot"]["facts"]["higher_monitoring_risk"] is True
    async with AsyncSession(database_engine) as session:
        notifications = (await session.scalars(select(OutboxEventModel.event_type))).all()
        assert "slack.alert.severity_review_required" in notifications


async def test_concurrent_first_deliveries_create_one_incident(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        responses = await asyncio.gather(post(client, first), post(client, first))
        assert sorted(response.status_code for response in responses) == [200, 201]
        assert {response.json()["incident_id"] for response in responses} == {
            responses[0].json()["incident_id"]
        }
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 1
        assert await session.scalar(select(func.count()).select_from(AlertEventModel)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 1


async def test_terminal_incident_suppresses_same_occurrence_and_new_occurrence_creates(
    alert_app, database_engine, clean_database
):
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=alert_app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        incident_id = created.json()["incident_id"]
        resolved = await client.patch(
            f"/api/v1/incidents/{incident_id}",
            headers={
                "Authorization": "Bearer human-token",
                "Idempotency-Key": str(uuid4()),
            },
            json={"expected_version": 1, "status": "resolved"},
        )
        assert resolved.status_code == 200, resolved.text
        repeated = await post(client, first)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["outcome"] == "suppressed_terminal"
        assert repeated.json()["incident_version"] == 2
        same = await post(client, {**first, "event_version": 2, "title": "Still firing"})
        assert same.status_code == 200, same.text
        assert same.json()["outcome"] == "suppressed_terminal"
        later = await post(
            client,
            {
                **first,
                "started_at": (
                    datetime.fromisoformat(first["started_at"]) + timedelta(minutes=1)
                ).isoformat(),
            },
        )
        assert later.status_code == 201, later.text
        assert later.json()["incident_id"] != incident_id
        old_history = await client.get(
            f"/api/v1/incidents/{incident_id}/alerts",
            headers={"Authorization": "Bearer human-token"},
        )
        assert {item["outcome"] for item in old_history.json()["items"]} == {
            "incident_created",
            "suppressed_terminal",
        }
    async with AsyncSession(database_engine) as session:
        assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 2


async def test_non_bypass_runtime_enforces_source_isolation(
    alert_settings, postgres_test_url, database_engine, clean_database
):
    await _ensure_runtime_role(database_engine)
    runtime_engine = create_async_engine(postgres_test_url, poolclass=NullPool)

    @event.listens_for(runtime_engine.sync_engine, "connect")
    def set_role(connection, _record):
        cursor = connection.cursor()
        cursor.execute("SET ROLE incident_ops_phase2_runtime")
        cursor.close()

    try:
        app = create_app(
            alert_settings,
            database_engine=runtime_engine,
            token_verifier=ApiTokenVerifier(),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://testserver",
        ) as client:
            created = await post(client, alert_payload())
            assert created.status_code == 201, created.text
            history = await client.get(
                f"/api/v1/incidents/{created.json()['incident_id']}/alerts",
                headers={"Authorization": "Bearer human-token"},
            )
            assert history.status_code == 200, history.text
            assert len(history.json()["items"]) == 1
        async with AsyncSession(runtime_engine) as session, session.begin():
            await apply_principal_context(
                session,
                Principal(
                    ActorType.SYSTEM,
                    "monitoring:monitoring-demo-prod",
                    frozenset({"alerts:ingest"}),
                ),
            )
            await session.execute(
                text("SELECT set_config('app.monitor_source', 'monitoring-demo-prod', true)")
            )
            await session.execute(
                text("SELECT set_config('app.monitor_service', 'checkout', true)")
            )
            assert await session.scalar(select(func.count()).select_from(AlertModel)) == 1
            await apply_principal_context(
                session,
                Principal(
                    ActorType.SYSTEM, "monitoring:other-source", frozenset({"alerts:ingest"})
                ),
            )
            await session.execute(
                text("SELECT set_config('app.monitor_source', 'other-source', true)")
            )
            assert await session.scalar(select(func.count()).select_from(AlertModel)) == 0
            assert await session.scalar(select(func.count()).select_from(AlertEventModel)) == 0
            await apply_principal_context(
                session,
                Principal(
                    ActorType.AGENT, "monitoring:monitoring-demo-prod", frozenset({"alerts:ingest"})
                ),
            )
            await session.execute(
                text("SELECT set_config('app.monitor_source', 'monitoring-demo-prod', true)")
            )
            assert await session.scalar(select(func.count()).select_from(AlertModel)) == 0
        async with AsyncSession(runtime_engine) as session, session.begin():
            await apply_principal_context(
                session,
                Principal(
                    ActorType.SYSTEM,
                    "monitoring:monitoring-demo-prod",
                    frozenset({"alerts:ingest"}),
                ),
            )
            await session.execute(
                text("SELECT set_config('app.monitor_source', 'monitoring-demo-prod', true)")
            )
            await session.execute(
                text("SELECT set_config('app.monitor_service', 'checkout', true)")
            )
            with pytest.raises(DBAPIError):
                await session.execute(update(IncidentModel).values(title="Unauthorized rewrite"))
            await session.rollback()
        async with AsyncSession(runtime_engine) as session, session.begin():
            await apply_principal_context(
                session,
                Principal(
                    ActorType.SYSTEM,
                    "monitoring:monitoring-demo-prod",
                    frozenset({"alerts:ingest"}),
                ),
            )
            await session.execute(
                text("SELECT set_config('app.monitor_source', 'monitoring-demo-prod', true)")
            )
            await session.execute(
                text("SELECT set_config('app.monitor_service', 'checkout', true)")
            )
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(AlertModel).values(external_alert_id="unauthorized-renaming")
                )
            await session.rollback()
    finally:
        await runtime_engine.dispose()


async def test_fault_after_receipt_rolls_back_incident_audit_outbox_and_idempotency(
    alert_settings, database_engine, clean_database
):
    class ExplodingObserver:
        async def reached(self, point: str) -> None:
            if point == "alert.ingest.after_receipt":
                raise RuntimeError("synthetic post-receipt failure")

    app = create_app(
        alert_settings,
        database_engine=database_engine,
        token_verifier=ApiTokenVerifier(),
        command_observer=ExplodingObserver(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await post(client, alert_payload())
        assert response.status_code == 500
        assert "synthetic" not in response.text
    async with AsyncSession(database_engine) as session:
        for model in (
            AlertModel,
            AlertEventModel,
            IncidentModel,
            IncidentEventModel,
            OutboxEventModel,
            IdempotencyRecordModel,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_monitor_update_wins_race_with_stale_human_draft(
    alert_settings, database_engine, clean_database
):
    class PauseHumanWrite:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.release = asyncio.Event()

        async def reached(self, point: str) -> None:
            if point == "incident.update.before_conditional_write":
                self.ready.set()
                await asyncio.wait_for(self.release.wait(), timeout=5)

    observer = PauseHumanWrite()
    app = create_app(
        alert_settings,
        database_engine=database_engine,
        token_verifier=ApiTokenVerifier(),
        command_observer=observer,
    )
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        assert created.status_code == 201
        incident_id = created.json()["incident_id"]
        human = asyncio.create_task(
            client.patch(
                f"/api/v1/incidents/{incident_id}",
                headers={
                    "Authorization": "Bearer human-token",
                    "Idempotency-Key": str(uuid4()),
                },
                json={"expected_version": 1, "title": "Human draft"},
            )
        )
        await asyncio.wait_for(observer.ready.wait(), timeout=5)
        monitor = await post(client, {**first, "event_version": 2, "title": "Source update"})
        observer.release.set()
        stale_human = await asyncio.wait_for(human, timeout=5)
        assert monitor.status_code == 200, monitor.text
        assert monitor.json()["incident_version"] == 2
        assert stale_human.status_code == 409, stale_human.text
        detail = await client.get(
            f"/api/v1/incidents/{incident_id}",
            headers={"Authorization": "Bearer human-token"},
        )
        assert detail.json()["title"] == first["title"]
        assert detail.json()["monitoring_status"] == "FIRING"


async def test_monitor_update_stales_concurrent_action_approval(
    alert_settings, database_engine, clean_database
):
    class PauseApproval:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.release = asyncio.Event()

        async def reached(self, point: str) -> None:
            if point == "action.review.before_lock":
                self.ready.set()
                await asyncio.wait_for(self.release.wait(), timeout=5)

    observer = PauseApproval()
    app = create_app(
        alert_settings,
        database_engine=database_engine,
        token_verifier=ApiTokenVerifier(),
        command_observer=observer,
    )
    first = alert_payload()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        created = await post(client, first)
        assert created.status_code == 201, created.text
        incident_id = created.json()["incident_id"]
        requested = await client.post(
            f"/api/v1/incidents/{incident_id}/actions",
            headers={
                "Authorization": "Bearer agent-writer-token",
                "Idempotency-Key": str(uuid4()),
            },
            json={
                "expected_incident_version": 1,
                "action_type": "request_status_update",
                "parameters": {"target_status": "resolved"},
                "rationale": "Synthetic monitor looked healthy.",
            },
        )
        assert requested.status_code == 201, requested.text
        assert requested.json()["status"] == "pending"
        approval = asyncio.create_task(
            client.post(
                f"/api/v1/actions/{requested.json()['id']}/approve",
                headers={
                    "Authorization": "Bearer human-token",
                    "Idempotency-Key": str(uuid4()),
                },
            )
        )
        await asyncio.wait_for(observer.ready.wait(), timeout=5)
        monitor = await post(client, {**first, "event_version": 2, "title": "Still failing"})
        observer.release.set()
        reviewed = await asyncio.wait_for(approval, timeout=5)
        assert monitor.status_code == 200, monitor.text
        assert monitor.json()["incident_version"] == 2
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "stale"
        assert reviewed.json()["outcome_code"] == "incident_version_changed"
        detail = await client.get(
            f"/api/v1/incidents/{incident_id}",
            headers={"Authorization": "Bearer human-token"},
        )
        assert detail.json()["status"] == "open"
        assert detail.json()["monitoring_status"] == "FIRING"
