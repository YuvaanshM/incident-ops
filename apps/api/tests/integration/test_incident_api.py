"""Phase 3 incident HTTP contracts against real PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError
from incident_ops.infrastructure.database.models import (
    IdempotencyRecordModel,
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)
from incident_ops.main import create_app


class ApiTokenVerifier:
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
            "reader-token": Principal(
                ActorType.HUMAN,
                "reader-1",
                frozenset({"incidents:read"}),
            ),
            "agent-writer-token": Principal(
                ActorType.AGENT,
                "agent-writer-1",
                frozenset({"incidents:read", "incidents:write", "actions:request"}),
            ),
        }
        try:
            return principals[token]
        except KeyError as error:
            raise AuthenticationRequiredError() from error


class RaisingObserver:
    async def reached(self, point: str) -> None:
        if point == "incident.create.after_event":
            raise RuntimeError("synthetic observer failure containing private detail")


class TwoWriterBarrierObserver:
    def __init__(self) -> None:
        self._arrivals = 0
        self._release = asyncio.Event()

    async def reached(self, point: str) -> None:
        if point != "incident.update.before_conditional_write":
            return
        self._arrivals += 1
        if self._arrivals == 2:
            self._release.set()
        await asyncio.wait_for(self._release.wait(), timeout=5)


@pytest.fixture
def api_settings(postgres_test_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=postgres_test_url,
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["testserver"],
    )


@pytest.fixture
def api_app(api_settings: Settings, database_engine: AsyncEngine) -> FastAPI:
    return create_app(
        api_settings,
        token_verifier=ApiTokenVerifier(),
        database_engine=database_engine,
    )


@pytest_asyncio.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=api_app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield client


def auth_headers(
    token: str | None = None, *, idempotency_key: UUID | None = None
) -> dict[str, str]:
    credential = token or "human-token"
    headers = {"Authorization": f"Bearer {credential}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = str(idempotency_key)
    return headers


async def create_incident(
    client: AsyncClient,
    *,
    title: str = "Checkout latency",
    description: str | None = None,
    service: str = "checkout",
    severity: str = "low",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/incidents",
        headers=auth_headers(idempotency_key=uuid4()),
        json={
            "title": title,
            "description": description or f"Synthetic description for {title}.",
            "service": service,
            "severity": severity,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.integration
async def test_create_derives_actor_and_atomically_writes_event_outbox_and_replay(
    api_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    key = uuid4()
    payload = {
        "title": "Checkout latency",
        "description": "Synthetic checkout requests are timing out.",
        "service": "checkout",
        "severity": "low",
    }

    first = await api_client.post(
        "/api/v1/incidents",
        headers=auth_headers(idempotency_key=key),
        json=payload,
    )
    replay = await api_client.post(
        "/api/v1/incidents",
        headers=auth_headers(idempotency_key=key),
        json=payload,
    )

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert first.json()["created_by_type"] == "human"
    assert first.json()["created_by_id"] == "operator-1"
    assert first.json()["version"] == 1

    async with database_engine.connect() as connection:
        counts = [
            await connection.scalar(select(func.count()).select_from(model))
            for model in (
                IncidentModel,
                IncidentEventModel,
                OutboxEventModel,
                IdempotencyRecordModel,
            )
        ]
        event_type = await connection.scalar(select(IncidentEventModel.event_type))
    assert counts == [1, 1, 1, 1]
    assert event_type == "incident.created"

    mismatch = await api_client.post(
        "/api/v1/incidents",
        headers=auth_headers(idempotency_key=key),
        json={**payload, "title": "Different title"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "idempotency_key_reused_with_different_payload"


@pytest.mark.integration
async def test_incident_validation_auth_and_server_owned_fields_are_enforced(
    api_client: AsyncClient,
    clean_database: None,
) -> None:
    payload = {
        "title": "API errors",
        "description": "Synthetic failures.",
        "service": "api",
        "severity": "medium",
    }

    missing_auth = await api_client.post(
        "/api/v1/incidents",
        headers={"Idempotency-Key": str(uuid4())},
        json=payload,
    )
    missing_scope = await api_client.post(
        "/api/v1/incidents",
        headers=auth_headers("reader-token", idempotency_key=uuid4()),
        json=payload,
    )
    spoofed_actor = await api_client.post(
        "/api/v1/incidents",
        headers=auth_headers(idempotency_key=uuid4()),
        json={**payload, "created_by_id": "attacker-selected"},
    )
    blank_title = await api_client.post(
        "/api/v1/incidents",
        headers=auth_headers(idempotency_key=uuid4()),
        json={**payload, "title": "   "},
    )

    assert missing_auth.status_code == 401
    assert missing_scope.status_code == 403
    assert spoofed_actor.status_code == blank_title.status_code == 422
    assert spoofed_actor.json()["error"]["code"] == "validation_error"
    assert "attacker-selected" not in spoofed_actor.text


@pytest.mark.integration
async def test_list_detail_and_timeline_use_stable_cursor_pagination_and_filters(
    api_client: AsyncClient,
    clean_database: None,
) -> None:
    created = [
        await create_incident(api_client, title="Checkout latency", service="checkout"),
        await create_incident(api_client, title="Checkout errors", service="checkout"),
        await create_incident(api_client, title="Billing delay", service="billing"),
    ]

    first_page = await api_client.get(
        "/api/v1/incidents",
        headers=auth_headers("reader-token"),
        params={"service": "checkout", "q": "checkout", "limit": 1},
    )
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["next_cursor"] is not None

    second_page = await api_client.get(
        "/api/v1/incidents",
        headers=auth_headers("reader-token"),
        params={
            "service": "checkout",
            "q": "checkout",
            "limit": 1,
            "cursor": first_page.json()["next_cursor"],
        },
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["items"][0]["id"] != first_page.json()["items"][0]["id"]

    incident_id = created[0]["id"]
    detail = await api_client.get(
        f"/api/v1/incidents/{incident_id}", headers=auth_headers("reader-token")
    )
    timeline = await api_client.get(
        f"/api/v1/incidents/{incident_id}/timeline",
        headers=auth_headers("reader-token"),
        params={"limit": 1},
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == incident_id
    assert detail.json()["jobs"] == []
    assert timeline.status_code == 200
    assert timeline.json()["items"][0]["event_type"] == "incident.created"

    invalid_cursor = await api_client.get(
        "/api/v1/incidents",
        headers=auth_headers("reader-token"),
        params={"cursor": "not-a-cursor"},
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.integration
async def test_patch_enforces_versions_idempotency_and_human_only_risk_boundaries(
    api_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(api_client)
    incident_id = incident["id"]
    key = uuid4()
    payload = {"expected_version": 1, "status": "investigating"}

    changed = await api_client.patch(
        f"/api/v1/incidents/{incident_id}",
        headers=auth_headers(idempotency_key=key),
        json=payload,
    )
    replay = await api_client.patch(
        f"/api/v1/incidents/{incident_id}",
        headers=auth_headers(idempotency_key=key),
        json=payload,
    )
    stale = await api_client.patch(
        f"/api/v1/incidents/{incident_id}",
        headers=auth_headers(idempotency_key=uuid4()),
        json={"expected_version": 1, "description": "Stale overwrite attempt"},
    )
    critical_by_agent = await api_client.patch(
        f"/api/v1/incidents/{incident_id}",
        headers=auth_headers("agent-writer-token", idempotency_key=uuid4()),
        json={"expected_version": 2, "severity": "critical"},
    )

    assert changed.status_code == replay.status_code == 200
    assert changed.json() == replay.json()
    assert changed.json()["version"] == 2
    assert changed.json()["status"] == "investigating"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert stale.json()["error"]["details"] == {
        "current_version": 2,
        "refetch": f"/api/v1/incidents/{incident_id}",
    }
    assert critical_by_agent.status_code == 403

    async with database_engine.connect() as connection:
        persisted = (
            await connection.execute(
                select(IncidentModel.version, IncidentModel.status).where(
                    IncidentModel.id == UUID(str(incident_id))
                )
            )
        ).one()
    assert persisted == (2, "investigating")


@pytest.mark.integration
async def test_two_simultaneous_patches_have_one_winner_and_one_persisted_conflict(
    api_settings: Settings,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    api_app = create_app(
        api_settings,
        token_verifier=ApiTokenVerifier(),
        database_engine=database_engine,
        command_observer=TwoWriterBarrierObserver(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=api_app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as setup_client:
        incident = await create_incident(setup_client, title="Concurrent write")
    incident_id = UUID(str(incident["id"]))

    async with (
        AsyncClient(
            transport=ASGITransport(app=api_app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as first_client,
        AsyncClient(
            transport=ASGITransport(app=api_app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as second_client,
    ):
        first_task = asyncio.create_task(
            first_client.patch(
                f"/api/v1/incidents/{incident_id}",
                headers=auth_headers(idempotency_key=uuid4()),
                json={"expected_version": 1, "description": "First writer"},
            )
        )
        second_task = asyncio.create_task(
            second_client.patch(
                f"/api/v1/incidents/{incident_id}",
                headers=auth_headers(idempotency_key=uuid4()),
                json={"expected_version": 1, "description": "Second writer"},
            )
        )
        responses = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()["error"][
            "code"
        ]
        == "version_conflict"
    )

    async with database_engine.connect() as connection:
        version = await connection.scalar(
            select(IncidentModel.version).where(IncidentModel.id == incident_id)
        )
        update_events = await connection.scalar(
            select(func.count())
            .select_from(IncidentEventModel)
            .where(IncidentEventModel.event_type == "incident.updated")
        )
        idempotency_records = await connection.scalar(
            select(func.count()).select_from(IdempotencyRecordModel)
        )
    assert version == 2
    assert update_events == 1
    assert idempotency_records == 3


@pytest.mark.integration
async def test_command_failure_rolls_back_state_audit_idempotency_and_outbox(
    api_settings: Settings,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    app = create_app(
        api_settings,
        token_verifier=ApiTokenVerifier(),
        database_engine=database_engine,
        command_observer=RaisingObserver(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/incidents",
            headers=auth_headers(idempotency_key=uuid4()),
            json={
                "title": "Rollback proof",
                "description": "The injected failure must remain private.",
                "service": "api",
                "severity": "low",
            },
        )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "The request could not be completed.",
    }
    assert "private detail" not in response.text

    async with database_engine.connect() as connection:
        counts = [
            await connection.scalar(select(func.count()).select_from(model))
            for model in (
                IncidentModel,
                IncidentEventModel,
                OutboxEventModel,
                IdempotencyRecordModel,
            )
        ]
    assert counts == [0, 0, 0, 0]


@pytest.mark.integration
async def test_advisory_endpoints_are_read_only_and_explicitly_labeled(
    api_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(
        api_client,
        title="Checkout outage",
        description="All users cannot submit checkout requests.",
        severity="high",
    )
    incident_id = incident["id"]

    async with database_engine.connect() as connection:
        events_before = await connection.scalar(
            select(func.count()).select_from(IncidentEventModel)
        )

    severity = await api_client.post(
        f"/api/v1/incidents/{incident_id}/severity-recommendation",
        headers=auth_headers("reader-token"),
    )
    postmortem = await api_client.post(
        f"/api/v1/incidents/{incident_id}/postmortem-draft",
        headers=auth_headers("reader-token"),
    )

    assert severity.status_code == postmortem.status_code == 200
    assert severity.json()["advisory"] is True
    assert severity.json()["recommended_severity"] == "critical"
    assert postmortem.json()["advisory"] is True
    assert postmortem.json()["incident_id"] == incident_id

    async with database_engine.connect() as connection:
        events_after = await connection.scalar(select(func.count()).select_from(IncidentEventModel))
        version = await connection.scalar(
            select(IncidentModel.version).where(IncidentModel.id == UUID(str(incident_id)))
        )
    assert events_after == events_before
    assert version == 1


async def test_each_severity_increase_enqueues_once_and_replays_do_not_duplicate(
    api_client: AsyncClient,
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident = await create_incident(api_client)
    for version, severity in enumerate(("medium", "high", "low", "critical"), start=1):
        key = uuid4()
        body = {"expected_version": version, "severity": severity}
        response = await api_client.patch(
            f"/api/v1/incidents/{incident['id']}",
            headers=auth_headers(idempotency_key=key),
            json=body,
        )
        assert response.status_code == 200
        replay = await api_client.patch(
            f"/api/v1/incidents/{incident['id']}",
            headers=auth_headers(idempotency_key=key),
            json=body,
        )
        assert replay.status_code == 200
    async with database_engine.connect() as connection:
        rows = (
            await connection.execute(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == "slack.incident.escalated"
                )
            )
        ).all()
    assert len(rows) == 3
    assert {row.payload["severity"] for row in rows} == {"medium", "high", "critical"}
    assert len({row.dedupe_key for row in rows}) == 3


async def test_runtime_rls_commands_can_enqueue_without_delivery_authority(
    api_settings: Settings,
    database_engine: AsyncEngine,
    postgres_test_url: str,
    clean_database: None,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from test_rls import _ensure_runtime_role

    await _ensure_runtime_role(database_engine)
    runtime = create_async_engine(
        postgres_test_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": "incident_ops_phase2_runtime"}},
    )
    try:
        app = create_app(api_settings, token_verifier=ApiTokenVerifier(), database_engine=runtime)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/v1/incidents",
                headers=auth_headers(idempotency_key=uuid4()),
                json={
                    "title": "Synthetic",
                    "description": "Synthetic",
                    "service": "demo",
                    "severity": "low",
                },
            )
        assert response.status_code == 201
    finally:
        await runtime.dispose()
