"""Intention-revealing SQLAlchemy repositories."""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from incident_ops.application.auth import Principal
from incident_ops.application.dto import (
    DemoJobView,
    IdempotencyReservation,
    IncidentEventView,
    IncidentView,
)
from incident_ops.domain.errors import IdempotencyKeyMismatchError
from incident_ops.infrastructure.database.models import (
    ActionRequestModel,
    DemoJobModel,
    IdempotencyRecordModel,
    IncidentEventModel,
    IncidentModel,
    OutboxEventModel,
)


def _job_view(model: DemoJobModel) -> DemoJobView:
    return DemoJobView(
        id=model.id,
        incident_id=model.incident_id,
        stable_key=model.stable_key,
        name=model.name,
        status=model.status,
        retry_count=model.retry_count,
        version=model.version,
        last_error=model.last_error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _incident_view(
    model: IncidentModel,
    *,
    jobs: tuple[DemoJobView, ...] = (),
) -> IncidentView:
    return IncidentView(
        id=model.id,
        title=model.title,
        description=model.description,
        service=model.service,
        severity=model.severity,
        status=model.status,
        version=model.version,
        created_by_type=model.created_by_type,
        created_by_id=model.created_by_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
        resolved_at=model.resolved_at,
        closed_at=model.closed_at,
        jobs=jobs,
    )


def _event_view(model: IncidentEventModel) -> IncidentEventView:
    return IncidentEventView(
        id=model.id,
        incident_id=model.incident_id,
        action_request_id=model.action_request_id,
        event_type=model.event_type,
        actor_type=model.actor_type,
        actor_id=model.actor_id,
        request_id=model.request_id,
        payload=dict(model.payload),
        occurred_at=model.occurred_at,
    )


class SqlAlchemyIncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_incident(
        self,
        *,
        title: str,
        description: str,
        service: str,
        severity: str,
        principal: Principal,
    ) -> IncidentView:
        model = IncidentModel(
            title=title,
            description=description,
            service=service,
            severity=severity,
            status="open",
            version=1,
            created_by_type=principal.actor_type.value,
            created_by_id=principal.subject,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _incident_view(model)

    async def get(self, incident_id: UUID, *, for_update: bool = False) -> IncidentView | None:
        statement = select(IncidentModel).where(IncidentModel.id == incident_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return None if model is None else _incident_view(model)

    async def get_with_jobs(self, incident_id: UUID) -> IncidentView | None:
        model = await self._session.scalar(
            select(IncidentModel).where(IncidentModel.id == incident_id)
        )
        if model is None:
            return None
        job_models = (
            await self._session.scalars(
                select(DemoJobModel)
                .where(DemoJobModel.incident_id == incident_id)
                .order_by(DemoJobModel.updated_at.desc(), DemoJobModel.id.desc())
            )
        ).all()
        return _incident_view(model, jobs=tuple(_job_view(job) for job in job_models))

    async def list_page(
        self,
        *,
        query: str | None,
        service: str | None,
        severity: str | None,
        status: str | None,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[IncidentView, ...]:
        statement = select(IncidentModel)
        if query is not None:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    IncidentModel.title.ilike(pattern),
                    IncidentModel.description.ilike(pattern),
                    IncidentModel.service.ilike(pattern),
                )
            )
        if service is not None:
            statement = statement.where(IncidentModel.service == service)
        if severity is not None:
            statement = statement.where(IncidentModel.severity == severity)
        if status is not None:
            statement = statement.where(IncidentModel.status == status)
        if cursor_timestamp is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    IncidentModel.updated_at < cursor_timestamp,
                    and_(
                        IncidentModel.updated_at == cursor_timestamp,
                        IncidentModel.id < cursor_id,
                    ),
                )
            )
        models = (
            await self._session.scalars(
                statement.order_by(IncidentModel.updated_at.desc(), IncidentModel.id.desc()).limit(
                    limit
                )
            )
        ).all()
        return tuple(_incident_view(model) for model in models)

    async def conditional_update(
        self,
        *,
        incident_id: UUID,
        expected_version: int,
        values: Mapping[str, object],
    ) -> IncidentView | None:
        result = await self._session.execute(
            update(IncidentModel)
            .where(
                IncidentModel.id == incident_id,
                IncidentModel.version == expected_version,
            )
            .values(
                **values,
                version=IncidentModel.version + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(IncidentModel)
        )
        model = result.scalar_one_or_none()
        return None if model is None else _incident_view(model)

    async def database_now(self) -> datetime:
        value = await self._session.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            raise RuntimeError("database did not return a timestamp")
        return value

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

    async def complete(
        self,
        *,
        record_id: UUID,
        response_status: int,
        response_body: Mapping[str, object],
        resource_type: str | None = None,
        resource_id: UUID | None = None,
    ) -> None:
        result = await self._session.execute(
            update(IdempotencyRecordModel)
            .where(
                IdempotencyRecordModel.id == record_id,
                IdempotencyRecordModel.completed_at.is_(None),
            )
            .values(
                response_status=response_status,
                response_body=dict(response_body),
                resource_type=resource_type,
                resource_id=resource_id,
                completed_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("idempotency record was already completed")

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
            response_body=None if record.response_body is None else dict(record.response_body),
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

    async def list_for_incident(
        self,
        *,
        incident_id: UUID,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[IncidentEventView, ...]:
        statement = select(IncidentEventModel).where(IncidentEventModel.incident_id == incident_id)
        if cursor_timestamp is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    IncidentEventModel.occurred_at < cursor_timestamp,
                    and_(
                        IncidentEventModel.occurred_at == cursor_timestamp,
                        IncidentEventModel.id < cursor_id,
                    ),
                )
            )
        models = (
            await self._session.scalars(
                statement.order_by(
                    IncidentEventModel.occurred_at.desc(), IncidentEventModel.id.desc()
                ).limit(limit)
            )
        ).all()
        return tuple(_event_view(model) for model in models)


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
