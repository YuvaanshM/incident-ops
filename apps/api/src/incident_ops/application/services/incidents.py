"""Incident query and command orchestration."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal
from incident_ops.application.dto import (
    CommandResult,
    IdempotencyReservation,
    IncidentEventView,
    IncidentView,
    Page,
)
from incident_ops.application.idempotency import canonical_request_fingerprint
from incident_ops.application.pagination import decode_cursor, encode_cursor
from incident_ops.application.ports import CommandObserver
from incident_ops.application.serialization import error_body, event_body, incident_body
from incident_ops.domain.advisory import recommend_severity
from incident_ops.domain.enums import ActorType, IncidentSeverity, IncidentStatus
from incident_ops.domain.errors import (
    AuthorizationDeniedError,
    IncidentOpsError,
    NoChangesRequestedError,
    ResourceNotFoundError,
    VersionConflictError,
)
from incident_ops.domain.incidents import transition_lifecycle
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


class NullCommandObserver:
    async def reached(self, point: str) -> None:
        del point


def _replay_result(reservation: IdempotencyReservation) -> CommandResult | None:
    if reservation.created:
        return None
    status = reservation.response_status
    body = reservation.response_body
    if not isinstance(status, int) or not isinstance(body, Mapping):
        raise RuntimeError("idempotency record is incomplete")
    return CommandResult(status_code=status, body=dict(body))


class IncidentQueryService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_incidents(
        self,
        *,
        principal: Principal,
        query: str | None,
        service: str | None,
        severity: IncidentSeverity | None,
        status: IncidentStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page:
        cursor_timestamp = None
        cursor_id = None
        if cursor is not None:
            cursor_timestamp, raw_id = decode_cursor(cursor)
            cursor_id = UUID(raw_id)
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            items = await uow.incidents.list_page(
                query=query,
                service=service,
                severity=None if severity is None else severity.value,
                status=None if status is None else status.value,
                cursor_timestamp=cursor_timestamp,
                cursor_id=cursor_id,
                limit=limit + 1,
            )
        has_more = len(items) > limit
        visible = items[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = encode_cursor(timestamp=last.updated_at, resource_id=last.id)
        return Page(items=visible, next_cursor=next_cursor)

    async def get_incident(self, *, principal: Principal, incident_id: UUID) -> IncidentView:
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            incident = await uow.incidents.get_with_jobs(incident_id)
        if incident is None:
            raise ResourceNotFoundError()
        return incident

    async def get_timeline(
        self,
        *,
        principal: Principal,
        incident_id: UUID,
        cursor: str | None,
        limit: int,
    ) -> Page:
        cursor_timestamp = None
        cursor_id = None
        if cursor is not None:
            cursor_timestamp, raw_id = decode_cursor(cursor)
            cursor_id = UUID(raw_id)
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            if await uow.incidents.get(incident_id) is None:
                raise ResourceNotFoundError()
            items = await uow.events.list_for_incident(
                incident_id=incident_id,
                cursor_timestamp=cursor_timestamp,
                cursor_id=cursor_id,
                limit=limit + 1,
            )
        has_more = len(items) > limit
        visible = items[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = encode_cursor(timestamp=last.occurred_at, resource_id=last.id)
        return Page(items=visible, next_cursor=next_cursor)

    async def severity_recommendation(
        self, *, principal: Principal, incident_id: UUID
    ) -> dict[str, object]:
        incident = await self.get_incident(principal=principal, incident_id=incident_id)
        recommendation = recommend_severity(
            title=incident.title,
            description=incident.description,
        )
        return {
            "advisory": recommendation.advisory,
            "recommended_severity": recommendation.severity.value,
            "reasons": list(recommendation.reasons),
        }

    async def postmortem_draft(
        self, *, principal: Principal, incident_id: UUID
    ) -> dict[str, object]:
        incident = await self.get_incident(principal=principal, incident_id=incident_id)
        timeline = await self.get_timeline(
            principal=principal,
            incident_id=incident_id,
            cursor=None,
            limit=100,
        )
        events = tuple(item for item in timeline.items if isinstance(item, IncidentEventView))
        return {
            "advisory": True,
            "incident_id": str(incident.id),
            "title": f"Postmortem draft: {incident.title}",
            "summary": incident.description,
            "impact": (
                f"The {incident.service} service experienced a "
                f"{incident.severity}-severity incident."
            ),
            "timeline": [
                f"{event.occurred_at.isoformat()}: {event.event_type}" for event in events
            ],
            "action_items": [
                "Confirm the contributing factors with the incident responders.",
                "Assign owners and due dates before publishing this draft.",
            ],
        }


async def create_incident_in_transaction(
    uow: SqlAlchemyUnitOfWork,
    *,
    principal: Principal,
    request_id: UUID,
    title: str,
    description: str,
    service: str,
    severity: IncidentSeverity,
    observer: CommandObserver,
    source: Mapping[str, object] | None = None,
) -> IncidentView:
    """Write creation, audit, and notification intent without committing the caller's UoW."""
    incident = await uow.incidents.add_incident(
        title=title,
        description=description,
        service=service,
        severity=severity.value,
        principal=principal,
    )
    event = await uow.events.append(
        incident_id=incident.id,
        event_type="incident.created",
        principal=principal,
        request_id=request_id,
        payload={
            "title": incident.title,
            "service": incident.service,
            "severity": incident.severity,
            "status": incident.status,
            **({"source": dict(source)} if source is not None else {}),
        },
    )
    await observer.reached("incident.create.after_event")
    await uow.outbox.enqueue(
        source_event_id=event.id,
        event_type="slack.incident.created",
        aggregate_type="incident",
        aggregate_id=incident.id,
        dedupe_key=f"slack.incident.created:{event.id}",
        payload={
            "incident_id": str(incident.id),
            "event_id": str(event.id),
            "severity": incident.severity,
            "title": incident.title,
        },
    )
    return incident


class IncidentCommandService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        observer: CommandObserver | None = None,
    ) -> None:
        self._engine = engine
        self._observer = observer or NullCommandObserver()

    async def create_incident(
        self,
        *,
        principal: Principal,
        idempotency_key: UUID,
        request_id: UUID,
        title: str,
        description: str,
        service: str,
        severity: IncidentSeverity,
    ) -> CommandResult:
        fingerprint = canonical_request_fingerprint(
            operation="incidents.create",
            path_parameters={},
            body={
                "title": title,
                "description": description,
                "service": service,
                "severity": severity,
            },
        )
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation="incidents.create",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(reservation)
            if replay is not None:
                await uow.rollback()
                return replay

            incident = await create_incident_in_transaction(
                uow,
                principal=principal,
                request_id=request_id,
                title=title,
                description=description,
                service=service,
                severity=severity,
                observer=self._observer,
            )
            body = incident_body(incident)
            await uow.idempotency.complete(
                record_id=reservation.record_id,
                response_status=201,
                response_body=body,
                resource_type="incident",
                resource_id=incident.id,
            )
            await uow.commit()
            return CommandResult(status_code=201, body=body)

    async def update_incident(
        self,
        *,
        principal: Principal,
        incident_id: UUID,
        idempotency_key: UUID,
        request_id: UUID,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> CommandResult:
        fingerprint = canonical_request_fingerprint(
            operation="incidents.update",
            path_parameters={"incident_id": incident_id},
            body={"expected_version": expected_version, **changes},
        )
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation="incidents.update",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(reservation)
            if replay is not None:
                await uow.rollback()
                return replay

            current = await uow.incidents.get(incident_id)
            if current is None:
                return await self._complete_error(uow, reservation, ResourceNotFoundError())
            if current.version != expected_version:
                return await self._complete_version_conflict(uow, reservation, current)

            target_status = changes.get("status")
            target_severity = changes.get("severity")
            if (
                target_status == IncidentStatus.CLOSED
                and principal.actor_type is not ActorType.HUMAN
            ):
                return await self._complete_error(uow, reservation, AuthorizationDeniedError())
            if (
                target_severity == IncidentSeverity.CRITICAL
                and current.severity != IncidentSeverity.CRITICAL.value
                and principal.actor_type is not ActorType.HUMAN
            ):
                return await self._complete_error(uow, reservation, AuthorizationDeniedError())

            update_values: dict[str, object] = {}
            field_changes: dict[str, dict[str, object | None]] = {}
            for field in ("title", "description", "service"):
                requested = changes.get(field)
                existing = getattr(current, field)
                if requested is not None and requested != existing:
                    update_values[field] = requested
                    field_changes[field] = {"from": existing, "to": requested}

            if isinstance(target_severity, IncidentSeverity):
                if target_severity.value != current.severity:
                    update_values["severity"] = target_severity.value
                    field_changes["severity"] = {
                        "from": current.severity,
                        "to": target_severity.value,
                    }

            if isinstance(target_status, IncidentStatus) and target_status.value != current.status:
                now = await uow.incidents.database_now()
                lifecycle = transition_lifecycle(
                    current=IncidentStatus(current.status),
                    target=target_status,
                    now=now,
                    resolved_at=current.resolved_at,
                    closed_at=current.closed_at,
                )
                update_values.update(
                    {
                        "status": target_status.value,
                        "resolved_at": lifecycle.resolved_at,
                        "closed_at": lifecycle.closed_at,
                    }
                )
                field_changes["status"] = {
                    "from": current.status,
                    "to": target_status.value,
                }

            if not update_values:
                return await self._complete_error(uow, reservation, NoChangesRequestedError())

            await self._observer.reached("incident.update.before_conditional_write")
            updated = await uow.incidents.conditional_update(
                incident_id=incident_id,
                expected_version=expected_version,
                values=update_values,
            )
            if updated is None:
                latest = await uow.incidents.get(incident_id)
                if latest is None:
                    return await self._complete_error(uow, reservation, ResourceNotFoundError())
                return await self._complete_version_conflict(uow, reservation, latest)

            event = await uow.events.append(
                incident_id=incident_id,
                event_type="incident.updated",
                principal=principal,
                request_id=request_id,
                payload={"changes": field_changes, "version": updated.version},
            )
            severity_order = list(IncidentSeverity)
            if "severity" in field_changes and severity_order.index(
                IncidentSeverity(updated.severity)
            ) > severity_order.index(IncidentSeverity(current.severity)):
                await uow.outbox.enqueue(
                    source_event_id=event.id,
                    event_type="slack.incident.escalated",
                    aggregate_type="incident",
                    aggregate_id=incident_id,
                    dedupe_key=f"slack.incident.escalated:{event.id}",
                    payload={
                        "incident_id": str(incident_id),
                        "event_id": str(event.id),
                        "severity": updated.severity,
                        "title": updated.title,
                    },
                )
            body = incident_body(updated)
            await uow.idempotency.complete(
                record_id=reservation.record_id,
                response_status=200,
                response_body=body,
                resource_type="incident",
                resource_id=incident_id,
            )
            await uow.commit()
            return CommandResult(status_code=200, body=body)

    async def _complete_version_conflict(
        self,
        uow: SqlAlchemyUnitOfWork,
        reservation: IdempotencyReservation,
        current: IncidentView,
    ) -> CommandResult:
        return await self._complete_error(
            uow,
            reservation,
            VersionConflictError(
                details={
                    "current_version": current.version,
                    "refetch": f"/api/v1/incidents/{current.id}",
                }
            ),
        )

    async def _complete_error(
        self,
        uow: SqlAlchemyUnitOfWork,
        reservation: IdempotencyReservation,
        error: IncidentOpsError,
    ) -> CommandResult:
        body = error_body(error)
        await uow.idempotency.complete(
            record_id=reservation.record_id,
            response_status=error.status_code,
            response_body=body,
        )
        await uow.commit()
        return CommandResult(status_code=error.status_code, body=body)


def serialize_incident_page(page: Page) -> dict[str, object]:
    incidents = tuple(item for item in page.items if isinstance(item, IncidentView))
    return {
        "items": [incident_body(incident) for incident in incidents],
        "next_cursor": page.next_cursor,
    }


def serialize_timeline_page(page: Page) -> dict[str, object]:
    events = tuple(item for item in page.items if isinstance(item, IncidentEventView))
    return {
        "items": [event_body(event) for event in events],
        "next_cursor": page.next_cursor,
    }
