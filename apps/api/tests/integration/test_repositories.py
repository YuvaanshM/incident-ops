"""Repository and unit-of-work transaction/concurrency behavior."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import IdempotencyKeyMismatchError
from incident_ops.infrastructure.database.models import IdempotencyRecordModel, IncidentModel
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


async def test_unit_of_work_rolls_back_without_explicit_commit(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident_id = uuid4()
    async with SqlAlchemyUnitOfWork(database_engine) as uow:
        uow.session.add(
            IncidentModel(
                id=incident_id,
                title="Rollback proof",
                description="This row must not commit.",
                service="api",
                severity="low",
                status="open",
                version=1,
                created_by_type="human",
                created_by_id="operator-1",
            )
        )

    async with database_engine.connect() as connection:
        found = await connection.scalar(
            select(IncidentModel.id).where(IncidentModel.id == incident_id)
        )
    assert found is None


async def test_unit_of_work_commits_intention_revealing_repository_writes(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    incident_id = uuid4()
    async with SqlAlchemyUnitOfWork(database_engine) as uow:
        uow.session.add(
            IncidentModel(
                id=incident_id,
                title="Commit proof",
                description="This row must commit.",
                service="api",
                severity="low",
                status="open",
                version=1,
                created_by_type="human",
                created_by_id="operator-1",
            )
        )
        changed = await uow.incidents.transition_status(
            incident_id=incident_id,
            expected_version=1,
            target_status="investigating",
            resolved_at=None,
            closed_at=None,
        )
        assert changed is True
        await uow.commit()

    async with database_engine.connect() as connection:
        row = (
            await connection.execute(
                select(IncidentModel.status, IncidentModel.version).where(
                    IncidentModel.id == incident_id
                )
            )
        ).one()
    assert row == ("investigating", 2)


async def test_concurrent_idempotency_reservations_have_one_winner_and_one_replay(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    principal = Principal(ActorType.HUMAN, "operator-1", frozenset({"incidents:write"}))
    key = uuid4()
    first_reserved = asyncio.Event()
    second_attempting_reservation = asyncio.Event()

    async def first_writer() -> bool:
        async with SqlAlchemyUnitOfWork(database_engine) as uow:
            result = await uow.idempotency.reserve(
                principal=principal,
                operation="incidents.create",
                key=key,
                fingerprint="a" * 64,
            )
            first_reserved.set()
            await second_attempting_reservation.wait()
            await uow.commit()
            return result.created

    async def second_writer() -> bool:
        await first_reserved.wait()
        second_attempting_reservation.set()
        async with SqlAlchemyUnitOfWork(database_engine) as uow:
            result = await uow.idempotency.reserve(
                principal=principal,
                operation="incidents.create",
                key=key,
                fingerprint="a" * 64,
            )
            await uow.commit()
            return result.created

    first_task = asyncio.create_task(first_writer())
    second_task = asyncio.create_task(second_writer())
    assert await asyncio.gather(first_task, second_task) == [True, False]

    async with database_engine.connect() as connection:
        count = await connection.scalar(select(IdempotencyRecordModel.id))
    assert isinstance(count, UUID)


async def test_idempotency_replay_with_different_fingerprint_is_rejected(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    principal = Principal(ActorType.AGENT, "agent-1", frozenset({"actions:request"}))
    key = uuid4()
    async with SqlAlchemyUnitOfWork(database_engine) as uow:
        await uow.idempotency.reserve(
            principal=principal,
            operation="actions.request",
            key=key,
            fingerprint="a" * 64,
        )
        await uow.commit()

    async with SqlAlchemyUnitOfWork(database_engine) as uow:
        with pytest.raises(IdempotencyKeyMismatchError):
            await uow.idempotency.reserve(
                principal=principal,
                operation="actions.request",
                key=key,
                fingerprint="b" * 64,
            )
