"""Phase 3 action, approval, and demo-executor contracts against PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    IdempotencyRecordModel,
    IncidentEventModel,
    OutboxEventModel,
)
from incident_ops.main import create_app


class ActionTokenVerifier:
    async def verify(self, token: str) -> Principal:
        principals = {
            "human-token": Principal(
                ActorType.HUMAN,
                "operator-1",
                frozenset(
                    {
                        "incidents:read",
                        "incidents:write",
                        "actions:read",
                        "actions:request",
                        "actions:approve",
                        "fixtures:write",
                    }
                ),
            ),
            "agent-token": Principal(
                ActorType.AGENT,
                "agent-1",
                frozenset({"incidents:read", "actions:request"}),
            ),
            "agent-approver-token": Principal(
                ActorType.AGENT,
                "agent-approver-1",
                frozenset({"incidents:read", "actions:approve"}),
            ),
            "reader-token": Principal(
                ActorType.HUMAN,
                "reader-1",
                frozenset({"incidents:read"}),
            ),
        }
        try:
            return principals[token]
        except KeyError as error:
            raise AuthenticationRequiredError() from error


class ApprovalBarrierObserver:
    def __init__(self) -> None:
        self._arrivals = 0
        self._release = asyncio.Event()

    async def reached(self, point: str) -> None:
        if point != "action.review.before_lock":
            return
        self._arrivals += 1
        if self._arrivals == 2:
            self._release.set()
        await asyncio.wait_for(self._release.wait(), timeout=5)


class ActionFailureObserver:
    async def reached(self, point: str) -> None:
        if point == "action.request.after_event":
            raise RuntimeError("synthetic private rollback detail")


@pytest.fixture
def action_settings(postgres_test_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=postgres_test_url,
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["testserver"],
    )


@pytest.fixture
def action_app(action_settings: Settings, database_engine: AsyncEngine) -> FastAPI:
    return create_app(
        action_settings,
        token_verifier=ActionTokenVerifier(),
        database_engine=database_engine,
    )


@pytest_asyncio.fixture
async def action_client(action_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=action_app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield client


def headers(token: str, *, key: UUID | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if key is not None:
        result["Idempotency-Key"] = str(key)
    return result


async def create_incident(
    client: AsyncClient,
    *,
    severity: str = "low",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/incidents",
        headers=headers("human-token", key=uuid4()),
        json={
            "title": f"Synthetic {severity} incident",
            "description": "A deterministic action API test incident.",
            "service": "checkout",
            "severity": severity,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_failed_job(
    engine: AsyncEngine,
    incident_id: UUID,
    *,
    retry_count: int = 0,
    status: str = "failed",
) -> UUID:
    job_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            DemoJobModel.__table__.insert().values(
                id=job_id,
                incident_id=incident_id,
                stable_key=f"checkout-job-{job_id}",
                name="Synthetic checkout job",
                status=status,
                retry_count=retry_count,
                version=1,
                last_error="Synthetic timeout",
            )
        )
    return job_id


async def request_retry(
    client: AsyncClient,
    incident_id: UUID,
    job_id: UUID,
    *,
    expected_version: int = 1,
    key: UUID | None = None,
    credential: str = "agent-token",
) -> Response:
    return await client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers(credential, key=key or uuid4()),
        json={
            "expected_incident_version": expected_version,
            "action_type": "retry_failed_job",
            "parameters": {"job_id": str(job_id)},
            "rationale": "Retry one known failed demo job.",
        },
    )


@pytest.mark.integration
async def test_low_severity_first_retry_auto_applies_atomically_and_replays(
    action_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(action_client)
    incident_id = UUID(str(incident["id"]))
    job_id = await add_failed_job(database_engine, incident_id)
    key = uuid4()

    first = await request_retry(action_client, incident_id, job_id, key=key)
    replay = await request_retry(action_client, incident_id, job_id, key=key)

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    body = first.json()
    assert body["status"] == "applied"
    assert body["execution_mode"] == "auto"
    assert body["outcome_code"] == "job_queued"
    assert body["requested_by_type"] == "agent"
    assert body["requested_by_id"] == "agent-1"
    assert body["policy_snapshot"]["decision"] == "auto_apply"
    assert body["policy_snapshot"]["policy_version"]

    mismatch = await action_client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers("agent-token", key=key),
        json={
            "expected_incident_version": 1,
            "action_type": "retry_failed_job",
            "parameters": {"job_id": str(job_id)},
            "rationale": "A different request body.",
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "idempotency_key_reused_with_different_payload"

    async with database_engine.connect() as connection:
        job = (
            await connection.execute(
                select(DemoJobModel.status, DemoJobModel.retry_count, DemoJobModel.version).where(
                    DemoJobModel.id == job_id
                )
            )
        ).one()
        action_count = await connection.scalar(
            select(func.count()).select_from(ActionRequestModel)
        )
        action_events = await connection.scalar(
            select(func.count())
            .select_from(IncidentEventModel)
            .where(IncidentEventModel.action_request_id.is_not(None))
        )
    assert job == ("queued", 1, 2)
    assert action_count == 1
    assert action_events == 2


@pytest.mark.integration
async def test_action_inputs_policy_and_pending_queries_are_strict(
    action_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(action_client, severity="high")
    incident_id = UUID(str(incident["id"]))
    job_id = await add_failed_job(database_engine, incident_id)

    pending = await request_retry(action_client, incident_id, job_id)
    assert pending.status_code == 201
    assert pending.json()["status"] == "pending"
    assert pending.json()["execution_mode"] == "human_approved"
    assert pending.json()["policy_snapshot"]["decision"] == "require_approval"

    detail = await action_client.get(
        f"/api/v1/actions/{pending.json()['id']}",
        headers=headers("reader-token"),
    )
    queue = await action_client.get(
        "/api/v1/actions",
        headers=headers("human-token"),
        params={"status": "pending", "limit": 1},
    )
    assert detail.status_code == 200
    assert detail.json() == pending.json()
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json()["items"]] == [pending.json()["id"]]

    unknown = await action_client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers("agent-token", key=uuid4()),
        json={
            "expected_incident_version": 1,
            "action_type": "run_shell_command",
            "parameters": {"command": "whoami"},
        },
    )
    spoofed = await action_client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers("agent-token", key=uuid4()),
        json={
            "expected_incident_version": 1,
            "action_type": "retry_failed_job",
            "parameters": {"job_id": str(job_id)},
            "status": "applied",
            "requested_by_id": "attacker-selected",
        },
    )
    extra_parameter = await action_client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers("agent-token", key=uuid4()),
        json={
            "expected_incident_version": 1,
            "action_type": "retry_failed_job",
            "parameters": {"job_id": str(job_id), "command": "whoami"},
        },
    )

    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "unsupported_action_type"
    assert spoofed.status_code == extra_parameter.status_code == 422
    assert "attacker-selected" not in spoofed.text

    async with database_engine.connect() as connection:
        human_alerts = await connection.scalar(
            select(func.count())
            .select_from(OutboxEventModel)
            .where(OutboxEventModel.event_type == "slack.action.human_required")
        )
    assert human_alerts == 1


@pytest.mark.integration
async def test_demo_second_retry_approval_and_retry_cap(
    action_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(action_client)
    incident_id = UUID(str(incident["id"]))
    job_id = await add_failed_job(database_engine, incident_id)
    first = await request_retry(action_client, incident_id, job_id)
    assert first.status_code == 201
    assert first.json()["status"] == "applied"

    simulated = await action_client.post(
        f"/api/v1/demo/jobs/{job_id}/simulate-failure",
        headers=headers("human-token", key=uuid4()),
    )
    assert simulated.status_code == 200
    assert simulated.json()["status"] == "failed"
    assert simulated.json()["retry_count"] == 1

    second = await request_retry(action_client, incident_id, job_id)
    assert second.status_code == 201
    assert second.json()["status"] == "pending"

    approval_key = uuid4()
    approved = await action_client.post(
        f"/api/v1/actions/{second.json()['id']}/approve",
        headers=headers("human-token", key=approval_key),
    )
    replay = await action_client.post(
        f"/api/v1/actions/{second.json()['id']}/approve",
        headers=headers("human-token", key=approval_key),
    )
    assert approved.status_code == replay.status_code == 200
    assert approved.json() == replay.json()
    assert approved.json()["status"] == "applied"
    assert approved.json()["execution_mode"] == "human_approved"

    await action_client.post(
        f"/api/v1/demo/jobs/{job_id}/simulate-failure",
        headers=headers("human-token", key=uuid4()),
    )
    third = await request_retry(action_client, incident_id, job_id)
    assert third.status_code == 201
    assert third.json()["status"] == "rejected"
    assert third.json()["outcome_code"] == "retry_limit_reached"

    async with database_engine.connect() as connection:
        job = (
            await connection.execute(
                select(DemoJobModel.status, DemoJobModel.retry_count).where(
                    DemoJobModel.id == job_id
                )
            )
        ).one()
    assert job == ("failed", 2)


@pytest.mark.integration
async def test_status_update_becomes_stale_and_reject_is_idempotent(
    action_client: AsyncClient,
    clean_database: None,
) -> None:
    incident = await create_incident(action_client)
    incident_id = UUID(str(incident["id"]))
    requested = await action_client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers("agent-token", key=uuid4()),
        json={
            "expected_incident_version": 1,
            "action_type": "request_status_update",
            "parameters": {"target_status": "resolved"},
            "rationale": "The synthetic checks recovered.",
        },
    )
    assert requested.status_code == 201
    assert requested.json()["status"] == "pending"

    changed = await action_client.patch(
        f"/api/v1/incidents/{incident_id}",
        headers=headers("human-token", key=uuid4()),
        json={"expected_version": 1, "status": "investigating"},
    )
    assert changed.status_code == 200

    stale = await action_client.post(
        f"/api/v1/actions/{requested.json()['id']}/approve",
        headers=headers("human-token", key=uuid4()),
    )
    assert stale.status_code == 200
    assert stale.json()["status"] == "stale"
    assert stale.json()["outcome_code"] == "incident_version_changed"

    another = await action_client.post(
        f"/api/v1/incidents/{incident_id}/actions",
        headers=headers("agent-token", key=uuid4()),
        json={
            "expected_incident_version": 2,
            "action_type": "request_status_update",
            "parameters": {"target_status": "resolved"},
        },
    )
    key = uuid4()
    rejected = await action_client.post(
        f"/api/v1/actions/{another.json()['id']}/reject",
        headers=headers("human-token", key=key),
        json={"reason": "Keep investigating the synthetic incident."},
    )
    replay = await action_client.post(
        f"/api/v1/actions/{another.json()['id']}/reject",
        headers=headers("human-token", key=key),
        json={"reason": "Keep investigating the synthetic incident."},
    )
    assert rejected.status_code == replay.status_code == 200
    assert rejected.json() == replay.json()
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["outcome_code"] == "rejected_by_human"


@pytest.mark.integration
async def test_two_simultaneous_approvals_execute_once(
    action_settings: Settings,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    app = create_app(
        action_settings,
        token_verifier=ActionTokenVerifier(),
        database_engine=database_engine,
        command_observer=ApprovalBarrierObserver(),
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as setup:
        incident = await create_incident(setup, severity="medium")
        incident_id = UUID(str(incident["id"]))
        job_id = await add_failed_job(database_engine, incident_id)
        pending = await request_retry(setup, incident_id, job_id)
        action_id = pending.json()["id"]

    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as first_client,
        AsyncClient(transport=transport, base_url="http://testserver") as second_client,
    ):
        first_task = asyncio.create_task(
            first_client.post(
                f"/api/v1/actions/{action_id}/approve",
                headers=headers("human-token", key=uuid4()),
            )
        )
        second_task = asyncio.create_task(
            second_client.post(
                f"/api/v1/actions/{action_id}/approve",
                headers=headers("human-token", key=uuid4()),
            )
        )
        responses = await asyncio.gather(first_task, second_task)

    assert sorted(response.status_code for response in responses) == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["error"]["code"] == "action_not_pending"

    async with database_engine.connect() as connection:
        job = (
            await connection.execute(
                select(DemoJobModel.status, DemoJobModel.retry_count).where(
                    DemoJobModel.id == job_id
                )
            )
        ).one()
        applied_events = await connection.scalar(
            select(func.count())
            .select_from(IncidentEventModel)
            .where(
                IncidentEventModel.action_request_id == UUID(str(action_id)),
                IncidentEventModel.event_type == "action.applied",
            )
        )
    assert job == ("queued", 1)
    assert applied_events == 1


@pytest.mark.integration
async def test_approval_auth_simulator_scope_and_action_rollback_boundaries(
    action_settings: Settings,
    action_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(action_client, severity="medium")
    incident_id = UUID(str(incident["id"]))
    job_id = await add_failed_job(database_engine, incident_id)
    pending = await request_retry(action_client, incident_id, job_id)
    action_id = pending.json()["id"]

    agent_denied = await action_client.post(
        f"/api/v1/actions/{action_id}/approve",
        headers=headers("agent-approver-token", key=uuid4()),
    )
    fixture_denied = await action_client.post(
        f"/api/v1/demo/jobs/{job_id}/simulate-failure",
        headers=headers("reader-token", key=uuid4()),
    )
    assert agent_denied.status_code == fixture_denied.status_code == 403

    production = Settings(
        _env_file=None,
        app_env="production",
        database_url=action_settings.database_url,
        api_public_url="https://api.example.test",
        web_public_url="https://app.example.test",
        cors_origins=["https://app.example.test"],
        allowed_hosts=["api.example.test"],
    )
    production_app = create_app(
        production,
        token_verifier=ActionTokenVerifier(),
        database_engine=database_engine,
    )
    assert not any(
        getattr(route, "path", "") == "/api/v1/demo/jobs/{job_id}/simulate-failure"
        for route in production_app.routes
    )

    rollback_app = create_app(
        action_settings,
        token_verifier=ActionTokenVerifier(),
        database_engine=database_engine,
        command_observer=ActionFailureObserver(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=rollback_app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as rollback_client:
        failed = await action_client.post(
            f"/api/v1/demo/jobs/{job_id}/simulate-failure",
            headers=headers("human-token", key=uuid4()),
        )
        assert failed.status_code == 422  # pending job is still failed, not queued
        before = {}
        async with database_engine.connect() as connection:
            for model in (ActionRequestModel, IncidentEventModel, IdempotencyRecordModel):
                before[model.__tablename__] = await connection.scalar(
                    select(func.count()).select_from(model)
                )
        rolled_back = await rollback_client.post(
            f"/api/v1/incidents/{incident_id}/actions",
            headers=headers("agent-token", key=uuid4()),
            json={
                "expected_incident_version": 1,
                "action_type": "request_status_update",
                "parameters": {"target_status": "investigating"},
            },
        )
        assert rolled_back.status_code == 500
        assert "synthetic private" not in rolled_back.text

    async with database_engine.connect() as connection:
        for model in (ActionRequestModel, IncidentEventModel, IdempotencyRecordModel):
            assert await connection.scalar(select(func.count()).select_from(model)) == before[
                model.__tablename__
            ]
