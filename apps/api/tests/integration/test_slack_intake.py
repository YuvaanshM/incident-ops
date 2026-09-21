"""Slack receipt/incident/outbox atomicity and creation-only RLS on real PostgreSQL."""

import asyncio
from dataclasses import replace
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
from incident_ops.application.services.slack_intake import SlackIntakeService, slack_principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthorizationDeniedError, IdempotencyKeyMismatchError
from incident_ops.domain.source_issues import ChannelIssue
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

PATH = "/api/v1/integrations/slack/events"
PRINCIPAL = slack_principal("T123ABC", "C123ABC")


def issue():
    return ChannelIssue(
        team_id="T123ABC",
        app_id="A123ABC",
        channel_id="C123ABC",
        event_id="Ev123ABC",
        message_ts="1789668000.000001",
        user_id="U123ABC",
        text="Checkout errors\nSynthetic issue.",
    )


async def counts(engine):
    async with AsyncSession(engine) as session:
        return [
            await session.scalar(select(func.count()).select_from(model))
            for model in (
                IncidentModel,
                IncidentEventModel,
                OutboxEventModel,
                IdempotencyRecordModel,
                ActionRequestModel,
                DemoJobModel,
            )
        ]


def service(engine, config, observer=None):
    return SlackIntakeService(engine, config, observer=observer)


async def ingest(target, value=None, principal=PRINCIPAL):
    return await target.ingest(principal=principal, issue=value or issue(), request_id=uuid4())


async def test_signed_route_creates_one_incident_readable_in_existing_console(
    database_engine,
    clean_database,
    test_settings,
    slack_config,
    slack_event,
    sign_slack,
):
    app = create_app(
        test_settings.model_copy(update={"slack_intake": slack_config}),
        database_engine=database_engine,
        token_verifier=ApiTokenVerifier(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        body, headers = sign_slack(slack_event)
        response = await client.post(PATH, content=body, headers=headers)
        assert response.status_code == 200, response.text
        replay = await client.post(
            PATH, content=body, headers={**headers, "X-Slack-Retry-Num": "1"}
        )
        assert replay.status_code == 200
        assert replay.headers["Idempotent-Replayed"] == "true"
        listing = await client.get(
            "/api/v1/incidents", headers={"Authorization": "Bearer human-token"}
        )
        incident = listing.json()["items"][0]
        assert incident["title"] == "Checkout errors"
        assert incident["severity"] == "medium"
        assert incident["service"] == "checkout"
        assert incident["status"] == "open"
        assert incident["version"] == 1
        timeline = await client.get(
            f"/api/v1/incidents/{incident['id']}/timeline",
            headers={"Authorization": "Bearer human-token"},
        )
        source = timeline.json()["items"][0]["payload"]["source"]
        assert source["event_id"] == "Ev123ABC"
        assert source["channel_id"] == "C123ABC"
        assert "token" not in str(timeline.json())
        # An unsigned request cannot create or reserve any rows.
        assert (await client.post(PATH, json=slack_event)).status_code == 401
    assert await counts(database_engine) == [1, 1, 1, 2, 0, 0]


async def test_distinct_event_id_for_same_message_dedupes_but_changed_text_conflicts(
    database_engine,
    clean_database,
    slack_config,
):
    target = service(database_engine, slack_config)
    assert not (await ingest(target)).replayed
    assert (await ingest(target, replace(issue(), event_id="EvOTHER"))).replayed
    assert await counts(database_engine) == [1, 1, 1, 3, 0, 0]
    with pytest.raises(IdempotencyKeyMismatchError):
        await ingest(target, replace(issue(), text="Changed payload"))
    with pytest.raises(IdempotencyKeyMismatchError):
        await ingest(target, replace(issue(), event_id="EvTHIRD", text="Changed payload"))
    assert await counts(database_engine) == [1, 1, 1, 3, 0, 0]


async def test_replay_does_not_overwrite_human_edit_or_closed_incident(
    database_engine,
    clean_database,
    slack_config,
):
    target = service(database_engine, slack_config)
    await ingest(target)
    async with database_engine.begin() as conn:
        await conn.execute(
            update(IncidentModel).values(title="Human edited title", severity="high", version=2)
        )
    assert (await ingest(target)).replayed
    async with AsyncSession(database_engine) as session:
        row = await session.scalar(select(IncidentModel))
        assert (row.title, row.severity, row.version) == ("Human edited title", "high", 2)
    assert await counts(database_engine) == [1, 1, 1, 2, 0, 0]


@pytest.mark.parametrize("point", ["incident.create.after_event", "slack.before_commit"])
async def test_failure_rolls_back_everything_and_retry_can_create(
    database_engine,
    clean_database,
    slack_config,
    point,
):
    class Failing:
        async def reached(self, current):
            if current == point:
                raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError):
        await ingest(service(database_engine, slack_config, Failing()))
    assert await counts(database_engine) == [0, 0, 0, 0, 0, 0]
    await ingest(service(database_engine, slack_config))
    assert await counts(database_engine) == [1, 1, 1, 2, 0, 0]


@pytest.mark.parametrize("different_event_id", [False, True])
async def test_concurrent_connections_create_once(
    database_engine, clean_database, slack_config, different_event_id
):
    class Barrier:
        def __init__(self):
            self.arrivals = 0
            self.release = asyncio.Event()

        async def reached(self, point):
            if point == "slack.before_reserve":
                self.arrivals += 1
                if self.arrivals == 2:
                    self.release.set()
                await asyncio.wait_for(self.release.wait(), 3)

    target = service(database_engine, slack_config, Barrier())
    other = replace(issue(), event_id="EvOTHER") if different_event_id else issue()
    results = await asyncio.gather(ingest(target), ingest(target, other))
    assert sorted(item.replayed for item in results) == [False, True]
    assert await counts(database_engine) == [1, 1, 1, 3 if different_event_id else 2, 0, 0]


@pytest.mark.parametrize(
    "principal",
    [
        Principal(ActorType.HUMAN, "operator", frozenset({"slack:ingest"})),
        Principal(ActorType.AGENT, "agent", frozenset({"slack:ingest"})),
        Principal(ActorType.SYSTEM, PRINCIPAL.subject, frozenset()),
        slack_principal("TOTHER", "C123ABC"),
        slack_principal("T123ABC", "COTHER"),
        Principal(
            ActorType.SYSTEM, PRINCIPAL.subject, frozenset({"slack:ingest", "actions:approve"})
        ),
    ],
)
async def test_service_rechecks_actor_scope_and_source_even_on_owner_connection(
    database_engine,
    clean_database,
    slack_config,
    principal,
):
    with pytest.raises(AuthorizationDeniedError):
        await ingest(service(database_engine, slack_config), principal=principal)
    assert await counts(database_engine) == [0, 0, 0, 0, 0, 0]


async def test_disabled_source_is_rechecked_at_service_boundary(
    database_engine, clean_database, slack_config
):
    with pytest.raises(AuthorizationDeniedError):
        await ingest(service(database_engine, slack_config.model_copy(update={"enabled": False})))
    assert await counts(database_engine) == [0, 0, 0, 0, 0, 0]


async def test_full_ingestion_works_with_non_bypass_role_and_cross_source_denial(
    database_engine,
    postgres_test_url,
    clean_database,
    slack_config,
):
    await _ensure_runtime_role(database_engine)
    runtime_engine = create_async_engine(postgres_test_url, poolclass=NullPool)

    @event.listens_for(runtime_engine.sync_engine, "connect")
    def set_role(connection, _record):
        cursor = connection.cursor()
        cursor.execute("SET ROLE incident_ops_phase2_runtime")
        cursor.close()

    try:
        target = service(runtime_engine, slack_config)
        await ingest(target)
        assert (await ingest(target)).replayed
        async with AsyncSession(runtime_engine) as session, session.begin():
            await apply_principal_context(session, PRINCIPAL)
            await session.execute(
                text(
                    "SELECT set_config('app.slack_service', 'checkout', true), "
                    "set_config('app.slack_severity', 'medium', true)"
                )
            )
            assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(IdempotencyRecordModel)) == 2
            )
            for model in (IncidentModel, OutboxEventModel, ActionRequestModel, DemoJobModel):
                result = await session.execute(
                    update(model).values(updated_at=func.clock_timestamp())
                )
                assert result.rowcount == 0
            await apply_principal_context(session, slack_principal("T123ABC", "COTHER"))
            for model in (
                IncidentModel,
                IncidentEventModel,
                IdempotencyRecordModel,
                OutboxEventModel,
            ):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            await apply_principal_context(
                session, Principal(ActorType.HUMAN, PRINCIPAL.subject, PRINCIPAL.scopes)
            )
            assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 0
            await apply_principal_context(
                session, Principal(ActorType.SYSTEM, PRINCIPAL.subject, frozenset())
            )
            assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 0
    finally:
        await runtime_engine.dispose()


@pytest.mark.parametrize(
    "override",
    [
        {"service": "forbidden-service"},
        {"created_by_id": "slack:TOTHER:C123ABC"},
        {"severity": "critical"},
        {"version": 2},
    ],
)
async def test_rls_creation_must_match_server_binding(database_engine, clean_database, override):
    await _ensure_runtime_role(database_engine)
    async with AsyncSession(database_engine) as session, session.begin():
        await session.execute(text("SET LOCAL ROLE incident_ops_phase2_runtime"))
        await apply_principal_context(session, PRINCIPAL)
        await session.execute(
            text(
                "SELECT set_config('app.slack_service', 'checkout', true), "
                "set_config('app.slack_severity', 'medium', true)"
            )
        )
        row = IncidentModel(
            **{
                "title": "Synthetic",
                "description": "Synthetic",
                "service": "checkout",
                "severity": "medium",
                "created_by_type": "system",
                "created_by_id": PRINCIPAL.subject,
                **override,
            }
        )
        session.add(row)
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()
