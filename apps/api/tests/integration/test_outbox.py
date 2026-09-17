"""Real PostgreSQL leases, delivery atomicity, RLS, and network separation."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx2 import AsyncClient, MockTransport, Response
from pydantic import SecretStr
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_rls import _ensure_runtime_role

from incident_ops.application.auth import Principal
from incident_ops.application.services.outbox import OutboxWorker
from incident_ops.domain.delivery import DeliveryResult, RetryPolicy
from incident_ops.domain.enums import ActorType
from incident_ops.infrastructure.database.models import (
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)
from incident_ops.infrastructure.database.outbox import (
    WORKER_PRINCIPAL,
    SqlAlchemyOutboxDeliveryRepository,
)
from incident_ops.infrastructure.database.session import apply_principal_context
from incident_ops.infrastructure.slack import SlackWebhookSender


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


async def enqueue(engine, count=1):
    ids = []
    async with AsyncSession(engine) as session, session.begin():
        incident = IncidentModel(
            title="Synthetic",
            description="Do not forward",
            service="demo",
            severity="low",
            status="open",
            created_by_type="human",
            created_by_id="demo",
        )
        session.add(incident)
        await session.flush()
        for _ in range(count):
            source = IncidentEventModel(
                incident_id=incident.id,
                event_type="incident.created",
                actor_type="human",
                actor_id="demo",
                request_id=uuid4(),
                payload={},
            )
            session.add(source)
            await session.flush()
            row = OutboxEventModel(
                source_event_id=source.id,
                event_type="slack.incident.created",
                aggregate_type="incident",
                aggregate_id=incident.id,
                dedupe_key=str(uuid4()),
                payload={
                    "incident_id": str(incident.id),
                    "event_id": str(source.id),
                    "severity": "low",
                    "title": "private",
                },
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
    return ids


async def snapshot(engine, outbox_id):
    async with AsyncSession(engine) as session:
        return await session.get(OutboxEventModel, outbox_id)


async def test_concurrent_workers_claim_disjoint_batches_and_skip_locked(
    database_engine, clean_database
):
    ids = await enqueue(database_engine, 6)
    repo = SqlAlchemyOutboxDeliveryRepository(database_engine)
    async with AsyncSession(database_engine) as locked, locked.begin():
        await locked.execute(
            select(OutboxEventModel).where(OutboxEventModel.id == ids[0]).with_for_update()
        )
        a, b = await asyncio.wait_for(
            asyncio.gather(
                repo.claim(batch_size=3, lease_seconds=60),
                repo.claim(batch_size=3, lease_seconds=60),
            ),
            3,
        )
        assert len(a) + len(b) == 5
        assert {item.id for item in a}.isdisjoint({item.id for item in b})
        assert ids[0] not in {item.id for item in (*a, *b)}
    remaining = await repo.claim(batch_size=3, lease_seconds=60)
    assert len(remaining) == 1 and remaining[0].id == ids[0]
    assert await repo.claim(batch_size=3, lease_seconds=60) == ()


async def test_lease_recovery_fences_stale_completions_and_preserves_event_ids(
    database_engine, clean_database
):
    ids = await enqueue(database_engine)
    clock = Clock()
    clock.now = (await snapshot(database_engine, ids[0])).available_at + timedelta(seconds=1)
    repo = SqlAlchemyOutboxDeliveryRepository(database_engine, clock=clock)
    (first,) = await repo.claim(batch_size=1, lease_seconds=60)
    clock.advance(60)
    assert not await repo.finalize(first, DeliveryResult("delivered"), retry_delay=None)
    (recovered,) = await repo.claim(batch_size=1, lease_seconds=60)
    assert recovered.id == first.id and recovered.source_event_id == first.source_event_id
    assert recovered.lease_token != first.lease_token and recovered.attempt_count == 2
    assert not await repo.finalize(first, DeliveryResult("provider_rejected"), retry_delay=None)
    assert await repo.finalize(recovered, DeliveryResult("delivered"), retry_delay=None)
    assert not await repo.finalize(recovered, DeliveryResult("delivered"), retry_delay=None)
    row = await snapshot(database_engine, first.id)
    assert row.status == "delivered" and row.locked_by is None
    async with AsyncSession(database_engine) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IncidentEventModel)
                .where(IncidentEventModel.event_type == "slack.delivered")
            )
            == 1
        )


async def test_scripted_retries_commit_before_http_and_deliver_once(
    database_engine, clean_database
):
    ids = await enqueue(database_engine)
    clock = Clock()
    clock.now = (await snapshot(database_engine, ids[0])).available_at + timedelta(seconds=1)
    repo = SqlAlchemyOutboxDeliveryRepository(database_engine, clock=clock)
    codes = iter((500, 429, 200))
    messages = []

    async def respond(request):
        # A separate transaction can lock the claimed row while HTTP is in flight.
        async with AsyncSession(database_engine) as session, session.begin():
            row = await session.scalar(
                select(OutboxEventModel)
                .where(OutboxEventModel.id == ids[0])
                .with_for_update(nowait=True)
            )
            assert row.status == "processing"
        messages.append(request.content)
        return Response(next(codes), text="ok", headers={"Retry-After": "19"})

    async with AsyncClient(transport=MockTransport(respond)) as client:
        sender = SlackWebhookSender(
            client,
            webhook_url=SecretStr("http://localhost/mock"),
            web_url="https://console.example",
        )
        worker = OutboxWorker(repo, sender, retry_policy=RetryPolicy(), random_value=lambda: 1)
        await worker.run_once()
        row = await snapshot(database_engine, ids[0])
        assert row.status == "pending" and row.available_at == clock.now + timedelta(seconds=5)
        assert await worker.run_once() == 0
        clock.advance(5)
        await worker.run_once()
        row = await snapshot(database_engine, ids[0])
        assert row.available_at == clock.now + timedelta(seconds=19)
        clock.advance(19)
        await worker.run_once()
    row = await snapshot(database_engine, ids[0])
    assert row.status == "delivered" and row.attempt_count == 3
    assert messages[0] == messages[1] == messages[2]


async def test_terminal_failure_and_audit_are_atomic_and_do_not_enqueue_again(
    database_engine, clean_database
):
    ids = await enqueue(database_engine)
    repo = SqlAlchemyOutboxDeliveryRepository(database_engine)
    (item,) = await repo.claim(batch_size=1, lease_seconds=60)

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO incident_events"):
            raise RuntimeError("synthetic audit write failure")

    event.listen(database_engine.sync_engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(RuntimeError):
            await repo.finalize(item, DeliveryResult("provider_rejected"), retry_delay=None)
    finally:
        event.remove(database_engine.sync_engine, "before_cursor_execute", fail_audit)
    assert (await snapshot(database_engine, ids[0])).status == "processing"
    outcomes = await asyncio.gather(
        *[
            repo.finalize(item, DeliveryResult("provider_rejected"), retry_delay=None)
            for _ in range(2)
        ]
    )
    assert sorted(outcomes) == [False, True]
    async with AsyncSession(database_engine) as session:
        failures = (
            await session.scalars(
                select(IncidentEventModel).where(
                    IncidentEventModel.event_type == "slack.delivery_failed"
                )
            )
        ).all()
        assert len(failures) == 1
        assert failures[0].payload["outbox_event_id"] == str(ids[0])
        assert failures[0].actor_type == "system"
        assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 1


async def test_crashes_at_attempt_limit_are_terminal_without_more_http(
    database_engine, clean_database
):
    ids = await enqueue(database_engine)
    async with database_engine.begin() as connection:
        await connection.execute(
            update(OutboxEventModel).where(OutboxEventModel.id == ids[0]).values(attempt_count=25)
        )
    repo = SqlAlchemyOutboxDeliveryRepository(database_engine)

    class NoNetwork:
        async def send(self, item):
            pytest.fail("exhausted event sent again")

    await OutboxWorker(
        repo, NoNetwork(), retry_policy=RetryPolicy(max_attempts=25), random_value=lambda: 1
    ).run_once()
    row = await snapshot(database_engine, ids[0])
    assert row.status == "dead_letter" and row.attempt_count == 25


async def test_rls_worker_can_only_deliver_and_append_matching_audit(
    database_engine, postgres_test_url, clean_database
):
    await _ensure_runtime_role(database_engine)
    ids = await enqueue(database_engine)
    runtime = create_async_engine(
        postgres_test_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": "incident_ops_phase2_runtime"}},
    )
    try:
        repo = SqlAlchemyOutboxDeliveryRepository(runtime)
        assert await repo.ready()
        (item,) = await repo.claim(batch_size=1, lease_seconds=60)
        assert await repo.finalize(item, DeliveryResult("provider_rejected"), retry_delay=None)
        for principal in (
            Principal(ActorType.HUMAN, "human", frozenset({"outbox:deliver"})),
            Principal(ActorType.AGENT, "agent", frozenset({"outbox:deliver"})),
            Principal(ActorType.SYSTEM, "system", frozenset()),
        ):
            async with AsyncSession(runtime) as session, session.begin():
                await apply_principal_context(session, principal)
                assert await session.scalar(select(func.count()).select_from(OutboxEventModel)) == 0
                result = await session.execute(update(OutboxEventModel).values(status="pending"))
                assert result.rowcount == 0
        async with AsyncSession(runtime) as session, session.begin():
            await apply_principal_context(session, WORKER_PRINCIPAL)
            assert await session.scalar(select(func.count()).select_from(IncidentModel)) == 0
            result = await session.execute(update(IncidentModel).values(title="forbidden"))
            assert result.rowcount == 0
        for values in (
            {"event_type": "incident.closed"},
            {"actor_id": "other-worker"},
            {"incident_id": uuid4()},
        ):
            async with AsyncSession(runtime) as session:
                await apply_principal_context(session, WORKER_PRINCIPAL)
                with pytest.raises(DBAPIError):
                    await session.execute(
                        IncidentEventModel.__table__.insert().values(
                            **{
                                "incident_id": item.incident_id,
                                "event_type": "slack.delivery_failed",
                                "actor_type": "system",
                                "actor_id": WORKER_PRINCIPAL.subject,
                                "request_id": item.request_id,
                                "payload": {
                                    "outbox_event_id": str(ids[0]),
                                    "source_event_id": str(item.source_event_id),
                                },
                                **values,
                            }
                        )
                    )
                await session.rollback()
    finally:
        await runtime.dispose()
