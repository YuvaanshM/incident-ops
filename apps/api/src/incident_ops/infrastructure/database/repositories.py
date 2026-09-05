"""Intention-revealing SQLAlchemy repositories."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from incident_ops.application.auth import Principal
from incident_ops.domain.errors import IdempotencyKeyMismatchError
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    IdempotencyRecordModel,
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)


class SqlAlchemyIncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def transition_status(
        self,
        *,
        incident_id: UUID,
        expected_version: int,
        target_status: str,
        resolved_at: datetime | None,
        closed_at: datetime | None,
    ) -> bool:
        result = await self._session.execute(
            update(IncidentModel)
            .where(
                IncidentModel.id == incident_id,
                IncidentModel.version == expected_version,
            )
            .values(
                status=target_status,
                resolved_at=resolved_at,
                closed_at=closed_at,
                version=IncidentModel.version + 1,
                updated_at=func.now(),
            )
        )
        return result.rowcount == 1  # type: ignore[attr-defined]


class SqlAlchemyActionRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_pending(self, action_id: UUID) -> ActionRequestModel | None:
        statement = (
            select(ActionRequestModel)
            .where(
                ActionRequestModel.id == action_id,
                ActionRequestModel.status == "pending",
            )
            .with_for_update()
        )
        return await self._session.scalar(statement)


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    created: bool
    record_id: UUID
    response_status: int | None
    response_body: Mapping[str, object] | None
    resource_type: str | None
    resource_id: UUID | None


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self,
        *,
        principal: Principal,
        operation: str,
        key: UUID,
        fingerprint: str,
    ) -> IdempotencyReservation:
        statement = (
            insert(IdempotencyRecordModel)
            .values(
                principal_type=principal.actor_type.value,
                principal_id=principal.subject,
                operation=operation,
                idempotency_key=key,
                request_fingerprint=fingerprint,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_principal_operation_key")
            .returning(IdempotencyRecordModel)
        )
        created_record = (await self._session.execute(statement)).scalar_one_or_none()
        if created_record is not None:
            return self._to_reservation(created_record, created=True)

        existing = await self._session.scalar(
            select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.principal_type == principal.actor_type.value,
                IdempotencyRecordModel.principal_id == principal.subject,
                IdempotencyRecordModel.operation == operation,
                IdempotencyRecordModel.idempotency_key == key,
            )
        )
        if existing is None:
            raise RuntimeError("idempotency conflict record was not visible")
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyKeyMismatchError()
        return self._to_reservation(existing, created=False)

    @staticmethod
    def _to_reservation(
        record: IdempotencyRecordModel,
        *,
        created: bool,
    ) -> IdempotencyReservation:
        return IdempotencyReservation(
            created=created,
            record_id=record.id,
            response_status=record.response_status,
            response_body=record.response_body,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
        )


class SqlAlchemyEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        incident_id: UUID,
        event_type: str,
        principal: Principal,
        request_id: UUID,
        payload: Mapping[str, object],
        action_request_id: UUID | None = None,
    ) -> IncidentEventModel:
        event = IncidentEventModel(
            incident_id=incident_id,
            action_request_id=action_request_id,
            event_type=event_type,
            actor_type=principal.actor_type.value,
            actor_id=principal.subject,
            request_id=request_id,
            payload=dict(payload),
        )
        self._session.add(event)
        await self._session.flush()
        return event


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        source_event_id: UUID,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        dedupe_key: str,
        payload: Mapping[str, object],
    ) -> OutboxEventModel:
        event = OutboxEventModel(
            source_event_id=source_event_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            dedupe_key=dedupe_key,
            payload=dict(payload),
            status="pending",
        )
        self._session.add(event)
        await self._session.flush()
        return event
