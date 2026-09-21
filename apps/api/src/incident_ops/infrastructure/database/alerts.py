"""PostgreSQL alert identity, ordering history, and immutable receipts."""

import json
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from incident_ops.application.auth import Principal
from incident_ops.application.dto import AlertEventView, AlertRecord
from incident_ops.domain.alerts import NormalizedAlert
from incident_ops.infrastructure.database.models import (
    AlertEventModel,
    AlertModel,
    IncidentEventModel,
)


def _record(model: AlertModel) -> AlertRecord:
    return AlertRecord(
        id=model.id,
        source=model.source,
        external_alert_id=model.external_alert_id,
        service=model.service,
        incident_id=model.incident_id,
        status=model.status,
        severity=model.severity,
        title=model.title,
        description=model.description,
        started_at=model.started_at,
        resolved_at=model.resolved_at,
        event_version=model.event_version,
        state_fingerprint=model.state_fingerprint,
        version=model.version,
    )


def _event(model: AlertEventModel) -> AlertEventView:
    return AlertEventView(
        id=model.id,
        alert_id=model.alert_id,
        incident_id=model.incident_id,
        source=model.source,
        started_at=model.started_at,
        event_version=model.event_version,
        status=model.status,
        severity=model.severity,
        title=model.title,
        outcome=model.outcome,
        received_at=model.received_at,
    )


class SqlAlchemyAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_identity(
        self, source: str, external_alert_id: str, service: str
    ) -> AlertRecord | None:
        identity = json.dumps([source, external_alert_id, service], separators=(",", ":"))
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(identity, 0)))
        )
        model = await self._session.scalar(
            select(AlertModel)
            .where(
                AlertModel.source == source,
                AlertModel.external_alert_id == external_alert_id,
                AlertModel.service == service,
            )
            .with_for_update()
        )
        return None if model is None else _record(model)

    async def create(
        self,
        *,
        alert: NormalizedAlert,
        incident_id: UUID | None,
        state_fingerprint: str,
        metadata: Mapping[str, object],
    ) -> AlertRecord:
        model = AlertModel(
            source=alert.source,
            external_alert_id=alert.external_alert_id,
            service=alert.service,
            incident_id=incident_id,
            status=alert.status.value,
            severity=alert.severity.value,
            title=alert.title,
            description=alert.description,
            started_at=alert.started_at,
            resolved_at=alert.resolved_at,
            event_version=alert.event_version,
            metadata_=dict(metadata),
            state_fingerprint=state_fingerprint,
            version=1,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _record(model)

    async def advance(
        self, *, alert_id: UUID, expected_version: int, values: Mapping[str, object]
    ) -> AlertRecord | None:
        result = await self._session.execute(
            update(AlertModel)
            .where(AlertModel.id == alert_id, AlertModel.version == expected_version)
            .values(
                **values,
                version=AlertModel.version + 1,
                last_received_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
            )
            .returning(AlertModel)
        )
        model = result.scalar_one_or_none()
        return None if model is None else _record(model)

    async def occurrence_history(
        self, *, alert_id: UUID, started_at: datetime
    ) -> tuple[bool, UUID | None]:
        events = (
            await self._session.scalars(
                select(AlertEventModel)
                .where(
                    AlertEventModel.alert_id == alert_id, AlertEventModel.started_at == started_at
                )
                .order_by(AlertEventModel.received_at, AlertEventModel.id)
            )
        ).all()
        return (
            any(item.status == "RESOLVED" for item in events),
            next((item.incident_id for item in events if item.incident_id is not None), None),
        )

    async def append_event(
        self,
        *,
        alert_id: UUID,
        incident_id: UUID | None,
        principal: Principal,
        idempotency_key: UUID,
        request_id: UUID,
        alert: NormalizedAlert,
        metadata: Mapping[str, object],
        request_fingerprint: str,
        state_fingerprint: str,
        outcome: str,
    ) -> AlertEventView:
        model = AlertEventModel(
            alert_id=alert_id,
            incident_id=incident_id,
            source=alert.source,
            actor_id=principal.subject,
            idempotency_key=idempotency_key,
            request_id=request_id,
            started_at=alert.started_at,
            event_version=alert.event_version,
            status=alert.status.value,
            severity=alert.severity.value,
            title=alert.title,
            description=alert.description,
            metadata_=dict(metadata),
            request_fingerprint=request_fingerprint,
            state_fingerprint=state_fingerprint,
            outcome=outcome,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return _event(model)

    async def list_for_incident(
        self,
        *,
        incident_id: UUID,
        cursor_timestamp: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> tuple[AlertEventView, ...]:
        statement = select(AlertEventModel).where(AlertEventModel.incident_id == incident_id)
        if cursor_timestamp is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    AlertEventModel.received_at < cursor_timestamp,
                    and_(
                        AlertEventModel.received_at == cursor_timestamp,
                        AlertEventModel.id < cursor_id,
                    ),
                )
            )
        models = (
            await self._session.scalars(
                statement.order_by(
                    AlertEventModel.received_at.desc(), AlertEventModel.id.desc()
                ).limit(limit)
            )
        ).all()
        return tuple(_event(item) for item in models)

    async def has_higher_firing_risk(self, incident_id: UUID, severity: str) -> bool:
        levels = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        higher = tuple(level for level, rank in levels.items() if rank > levels[severity])
        if not higher:
            return False
        found = await self._session.scalar(
            select(AlertModel.id)
            .where(
                AlertModel.incident_id == incident_id,
                AlertModel.status == "FIRING",
                AlertModel.severity.in_(higher),
            )
            .limit(1)
        )
        return found is not None

    async def review_signaled(self, alert_id: UUID, started_at: datetime, severity: str) -> bool:
        found = await self._session.scalar(
            select(IncidentEventModel.id)
            .where(
                IncidentEventModel.event_type == "alert.severity_review_required",
                IncidentEventModel.payload["alert_id"].as_string() == str(alert_id),
                IncidentEventModel.payload["started_at"].as_string() == started_at.isoformat(),
                IncidentEventModel.payload["reported_severity"].as_string() == severity,
            )
            .limit(1)
        )
        return found is not None
