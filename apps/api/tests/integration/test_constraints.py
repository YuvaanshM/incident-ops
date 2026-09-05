"""Database-enforced Phase 2 invariants."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    HistoricalIncidentModel,
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)


async def _insert_incident(session: AsyncSession, **overrides: object) -> IncidentModel:
    values: dict[str, object] = {
        "title": "API latency",
        "description": "Synthetic incident used by the database test suite.",
        "service": "checkout",
        "severity": "low",
        "status": "open",
        "version": 1,
        "created_by_type": "human",
        "created_by_id": "operator-1",
    }
    values.update(overrides)
    incident = IncidentModel(**values)
    session.add(incident)
    await session.flush()
    return incident


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", " "),
        ("title", "x" * 201),
        ("service", "x" * 101),
        ("severity", "urgent"),
        ("status", "deleted"),
        ("version", 0),
        ("created_by_type", "browser"),
    ],
)
async def test_incident_checks_reject_invalid_values(
    database_session: AsyncSession,
    field: str,
    value: object,
) -> None:
    with pytest.raises(IntegrityError):
        await _insert_incident(database_session, **{field: value})


async def test_incident_lifecycle_timestamps_must_match_status(
    database_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError):
        await _insert_incident(
            database_session,
            status="closed",
            resolved_at=None,
            closed_at=datetime.now(UTC),
        )


async def test_action_and_job_retry_constraints(database_session: AsyncSession) -> None:
    incident = await _insert_incident(database_session)
    database_session.add(
        DemoJobModel(
            incident_id=incident.id,
            name="rebuild-search-index",
            status="failed",
            retry_count=3,
            version=1,
        )
    )
    with pytest.raises(IntegrityError):
        await database_session.flush()


async def test_unknown_action_and_invalid_fingerprint_are_rejected(
    database_session: AsyncSession,
) -> None:
    incident = await _insert_incident(database_session)
    database_session.add(
        ActionRequestModel(
            incident_id=incident.id,
            action_type="run_shell",
            status="pending",
            execution_mode="human",
            requested_by_type="agent",
            requested_by_id="agent-1",
            idempotency_key=uuid4(),
            request_fingerprint="not-sha256",
            base_incident_version=1,
            parameters={},
            policy_version="2026-09-05.v1",
            policy_snapshot={},
        )
    )
    with pytest.raises(IntegrityError):
        await database_session.flush()


async def test_event_rows_are_immutable(database_session: AsyncSession) -> None:
    incident = await _insert_incident(database_session)
    event = IncidentEventModel(
        incident_id=incident.id,
        event_type="incident.created",
        actor_type="human",
        actor_id="operator-1",
        request_id=uuid4(),
        payload={"source": "test"},
    )
    database_session.add(event)
    await database_session.flush()

    with pytest.raises(IntegrityError, match="immutable"):
        async with database_session.begin_nested():
            await database_session.execute(
                update(IncidentEventModel)
                .where(IncidentEventModel.id == event.id)
                .values(payload={})
            )

    with pytest.raises(IntegrityError, match="immutable"):
        async with database_session.begin_nested():
            await database_session.execute(
                text("DELETE FROM incident_events WHERE id = :event_id"), {"event_id": event.id}
            )


async def test_embedding_is_exactly_1536_dimensions(database_session: AsyncSession) -> None:
    valid = HistoricalIncidentModel(
        source_id="hist-001",
        title="Synthetic latency precedent",
        summary="A safe synthetic precedent.",
        service="checkout",
        severity="low",
        resolution_steps=["restart synthetic worker"],
        embedding_provider="google",
        embedding_model="gemini-embedding-2",
        embedding=[0.0] * 1536,
    )
    database_session.add(valid)
    await database_session.flush()

    with pytest.raises((IntegrityError, ValueError)):
        await database_session.execute(
            insert(HistoricalIncidentModel).values(
                source_id="hist-002",
                title="Wrong vector",
                summary="Wrong vector size.",
                service="checkout",
                severity="low",
                resolution_steps=[],
                embedding_provider="google",
                embedding_model="gemini-embedding-2",
                embedding=[0.0] * 3,
            )
        )


async def test_outbox_lease_fields_are_coherent(database_session: AsyncSession) -> None:
    incident = await _insert_incident(database_session)
    event = IncidentEventModel(
        incident_id=incident.id,
        event_type="incident.created",
        actor_type="human",
        actor_id="operator-1",
        request_id=uuid4(),
        payload={},
    )
    database_session.add(event)
    await database_session.flush()
    database_session.add(
        OutboxEventModel(
            source_event_id=event.id,
            event_type="slack.incident.created",
            aggregate_type="incident",
            aggregate_id=incident.id,
            dedupe_key=f"incident.created:{event.id}",
            payload={},
            status="processing",
            locked_by=None,
            lease_expires_at=None,
        )
    )

    with pytest.raises(IntegrityError):
        await database_session.flush()
