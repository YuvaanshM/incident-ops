"""Source-bound alert lifecycle with one durable transaction per delivery."""

import re
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.dto import CommandResult, Page
from incident_ops.application.idempotency import canonical_request_fingerprint
from incident_ops.application.pagination import decode_cursor, encode_cursor
from incident_ops.application.ports import CommandObserver
from incident_ops.application.services.incidents import (
    NullCommandObserver,
    _replay_result,
    create_incident_in_transaction,
)
from incident_ops.domain.alerts import AlertStatus, NormalizedAlert
from incident_ops.domain.errors import (
    AlertVersionConflictError,
    AuthorizationDeniedError,
    ResourceNotFoundError,
    VersionConflictError,
)
from incident_ops.infrastructure.auth.alert_webhook import VerifiedAlertSource
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from incident_ops.infrastructure.monitoring_config import AlertIntakeSettings


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _content(
    alert: NormalizedAlert, *, include_version: bool, include_raw: bool
) -> dict[str, object]:
    body: dict[str, object] = {
        "source": alert.source,
        "external_alert_id": alert.external_alert_id,
        "service": alert.service,
        "severity": alert.severity.value,
        "status": alert.status.value,
        "title": alert.title,
        "description": alert.description,
        "started_at": alert.started_at,
        "resolved_at": alert.resolved_at,
    }
    if include_version:
        body["event_version"] = alert.event_version
    if include_raw:
        body["metadata"] = _plain(alert.metadata)
        body["raw_payload"] = _plain(alert.raw_payload)
    return body


def _fingerprint(alert: NormalizedAlert, *, include_version: bool, include_raw: bool) -> str:
    return canonical_request_fingerprint(
        operation="alerts.ingest",
        path_parameters={},
        body=_content(alert, include_version=include_version, include_raw=include_raw),
    )


def _safe_metadata(alert: NormalizedAlert) -> dict[str, object]:
    # Retain only bounded operational labels. Raw envelopes and arbitrary provider
    # keys can contain credentials or personal data and are never persisted.
    allowed = {"environment", "region", "cluster", "namespace"}
    return {
        key: value
        for key, value in alert.metadata.items()
        if key in allowed
        and isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", value) is not None
    }


class AlertIngestionService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: AlertIntakeSettings,
        *,
        observer: CommandObserver | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._observer = observer or NullCommandObserver()

    async def ingest(
        self,
        *,
        verified: VerifiedAlertSource,
        alert: NormalizedAlert,
        request_id: UUID,
    ) -> tuple[CommandResult, bool]:
        if (
            verified.principal.subject != f"monitoring:{verified.source}"
            or verified.principal.scopes != frozenset({"alerts:ingest"})
            or verified.source != alert.source
            or alert.service not in verified.services
        ):
            raise AuthorizationDeniedError()
        source_config = next(
            (source for source in self._settings.sources if source.source == verified.source), None
        )
        if (
            not self._settings.enabled
            or source_config is None
            or not source_config.enabled
            or alert.service not in source_config.services
        ):
            raise AuthorizationDeniedError()

        request_hash = _fingerprint(alert, include_version=True, include_raw=True)
        state_hash = _fingerprint(alert, include_version=False, include_raw=False)
        principal = verified.principal
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            # These are transaction-local and never supplied by the caller without
            # the verified source/service checks above.
            for setting, value in (
                ("app.monitor_source", alert.source),
                ("app.monitor_service", alert.service),
                ("app.monitor_severity", alert.severity.value),
            ):
                await uow.session.execute(select(func.set_config(setting, value, True)))
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation="alerts.ingest",
                key=verified.idempotency_key,
                fingerprint=request_hash,
            )
            replay = _replay_result(reservation)
            if replay is not None:
                await uow.rollback()
                return replay, True

            current = await uow.alerts.lock_identity(*alert.logical_identity)
            await self._observer.reached("alert.ingest.before_incident_lock")
            incident = None
            if current is not None and current.incident_id is not None:
                incident = await uow.incidents.get(current.incident_id, for_update=True)
            outcome = "unchanged"
            notification = "none"
            linked_id: UUID | None = current.incident_id if current is not None else None
            advance = False
            update_monitoring = False
            timeline_type: str | None = None

            if current is None:
                if alert.status is AlertStatus.RESOLVED:
                    outcome = "orphan_resolution"
                    linked_id = None
                else:
                    incident = await create_incident_in_transaction(
                        uow,
                        principal=principal,
                        request_id=request_id,
                        title=alert.title,
                        description=alert.description,
                        service=alert.service,
                        severity=alert.severity,
                        observer=self._observer,
                        source={
                            "monitoring_source": alert.source,
                            "external_alert_id": alert.external_alert_id,
                        },
                        monitoring_status="FIRING",
                    )
                    linked_id = incident.id
                    outcome = "incident_created"
                    notification = "queued"
                current = await uow.alerts.create(
                    alert=alert,
                    incident_id=linked_id,
                    state_fingerprint=state_hash,
                    metadata=_safe_metadata(alert),
                )
            else:
                prior_resolved, prior_incident_id = await uow.alerts.occurrence_history(
                    alert_id=current.id, started_at=alert.started_at
                )
                if alert.started_at < current.started_at:
                    linked_id = prior_incident_id
                    outcome = "stale_ignored"
                elif alert.started_at == current.started_at:
                    if alert.event_version < current.event_version or (
                        prior_resolved and alert.status is AlertStatus.FIRING
                    ):
                        outcome = "stale_ignored"
                    elif alert.event_version == current.event_version:
                        if current.state_fingerprint != state_hash:
                            raise AlertVersionConflictError()
                        outcome = (
                            "suppressed_terminal"
                            if alert.status is AlertStatus.FIRING
                            and incident is not None
                            and incident.status in {"resolved", "closed"}
                            else "unchanged"
                        )
                    else:
                        advance = True
                        if current.state_fingerprint != state_hash:
                            if alert.status is AlertStatus.RESOLVED:
                                outcome = (
                                    "resolution_recorded" if linked_id else "orphan_resolution"
                                )
                                timeline_type = "alert.resolved" if linked_id else None
                            elif incident is not None and incident.status in {"resolved", "closed"}:
                                outcome = "suppressed_terminal"
                            else:
                                outcome = "incident_updated" if linked_id else "stale_ignored"
                                timeline_type = "alert.updated" if linked_id else None
                            update_monitoring = linked_id is not None and outcome in {
                                "resolution_recorded",
                                "incident_updated",
                            }
                        elif (
                            alert.status is AlertStatus.FIRING
                            and incident is not None
                            and incident.status in {"resolved", "closed"}
                        ):
                            outcome = "suppressed_terminal"
                else:
                    if prior_resolved:
                        linked_id = prior_incident_id
                        outcome = "stale_ignored"
                    elif alert.status is AlertStatus.RESOLVED:
                        linked_id = None
                        outcome = "orphan_resolution"
                    else:
                        advance = True
                        if incident is None or incident.status in {"resolved", "closed"}:
                            incident = await create_incident_in_transaction(
                                uow,
                                principal=principal,
                                request_id=request_id,
                                title=alert.title,
                                description=alert.description,
                                service=alert.service,
                                severity=alert.severity,
                                observer=self._observer,
                                source={
                                    "monitoring_source": alert.source,
                                    "external_alert_id": alert.external_alert_id,
                                },
                                monitoring_status="FIRING",
                            )
                            linked_id = incident.id
                            outcome = "incident_created"
                            notification = "queued"
                        else:
                            outcome = "incident_updated"
                            timeline_type = "alert.refired"
                            update_monitoring = True
                if advance:
                    advanced = await uow.alerts.advance(
                        alert_id=current.id,
                        expected_version=current.version,
                        values={
                            "incident_id": linked_id,
                            "status": alert.status.value,
                            "severity": alert.severity.value,
                            "title": alert.title,
                            "description": alert.description,
                            "started_at": alert.started_at,
                            "resolved_at": alert.resolved_at,
                            "event_version": alert.event_version,
                            "metadata_": _safe_metadata(alert),
                            "state_fingerprint": state_hash,
                        },
                    )
                    if advanced is None:
                        raise VersionConflictError()
                    current = advanced
                if update_monitoring and incident is not None:
                    updated = await uow.incidents.conditional_update(
                        incident_id=incident.id,
                        expected_version=incident.version,
                        values={
                            "monitoring_status": alert.status.value,
                            "monitoring_updated_at": func.clock_timestamp(),
                        },
                    )
                    if updated is None:
                        raise VersionConflictError()
                    incident = updated
                if timeline_type is not None and linked_id is not None:
                    await uow.events.append(
                        incident_id=linked_id,
                        event_type=timeline_type,
                        principal=principal,
                        request_id=request_id,
                        payload={
                            "alert_id": str(current.id),
                            "source": alert.source,
                            "external_alert_id": alert.external_alert_id,
                            "reported_severity": alert.severity.value,
                            "monitoring_status": alert.status.value,
                        },
                    )

                severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
                if (
                    outcome == "incident_updated"
                    and alert.status is AlertStatus.FIRING
                    and incident is not None
                    and severity_rank[alert.severity.value] > severity_rank[incident.severity]
                    and not await uow.alerts.review_signaled(
                        current.id, alert.started_at, alert.severity.value
                    )
                ):
                    review = await uow.events.append(
                        incident_id=incident.id,
                        event_type="alert.severity_review_required",
                        principal=principal,
                        request_id=request_id,
                        payload={
                            "alert_id": str(current.id),
                            "source": alert.source,
                            "started_at": alert.started_at.isoformat(),
                            "stored_severity": incident.severity,
                            "reported_severity": alert.severity.value,
                        },
                    )
                    await uow.outbox.enqueue(
                        source_event_id=review.id,
                        event_type="slack.alert.severity_review_required",
                        aggregate_type="incident",
                        aggregate_id=incident.id,
                        dedupe_key=(
                            f"slack.alert.severity_review_required:{current.id}:"
                            f"{alert.started_at.isoformat()}:{alert.severity.value}"
                        ),
                        payload={
                            "incident_id": str(incident.id),
                            "event_id": str(review.id),
                            "alert_id": str(current.id),
                            "stored_severity": incident.severity,
                            "reported_severity": alert.severity.value,
                            "monitoring_source": alert.source,
                            "service": alert.service,
                        },
                    )
                    notification = "queued"

            if outcome == "incident_created" and linked_id is not None:
                await uow.events.append(
                    incident_id=linked_id,
                    event_type="alert.linked",
                    principal=principal,
                    request_id=request_id,
                    payload={
                        "alert_id": str(current.id),
                        "source": alert.source,
                        "external_alert_id": alert.external_alert_id,
                        "started_at": alert.started_at.isoformat(),
                    },
                )

            receipt = await uow.alerts.append_event(
                alert_id=current.id,
                incident_id=linked_id,
                principal=principal,
                idempotency_key=verified.idempotency_key,
                request_id=request_id,
                alert=alert,
                metadata=_safe_metadata(alert),
                request_fingerprint=request_hash,
                state_fingerprint=state_hash,
                outcome=outcome,
            )
            await self._observer.reached("alert.ingest.after_receipt")
            if linked_id is None:
                incident = None
            elif incident is None or incident.id != linked_id:
                incident = await uow.incidents.get(linked_id)
            body = {
                "alert_id": str(current.id),
                "alert_event_id": str(receipt.id),
                "incident_id": None if linked_id is None else str(linked_id),
                "outcome": outcome,
                "alert_status": alert.status.value,
                "incident_status": None if incident is None else incident.status,
                "incident_version": None if incident is None else incident.version,
                "notification": notification,
            }
            status = 201 if outcome == "incident_created" else 200
            await uow.idempotency.complete(
                record_id=reservation.record_id,
                response_status=status,
                response_body=body,
                resource_type="alert",
                resource_id=current.id,
            )
            await uow.commit()
            return CommandResult(status_code=status, body=body), False


class AlertQueryService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def for_incident(
        self, *, principal, incident_id: UUID, cursor: str | None, limit: int
    ) -> Page:
        timestamp: datetime | None = None
        item_id: UUID | None = None
        if cursor is not None:
            timestamp, raw_id = decode_cursor(cursor)
            item_id = UUID(raw_id)
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            if await uow.incidents.get(incident_id) is None:
                raise ResourceNotFoundError()
            items = await uow.alerts.list_for_incident(
                incident_id=incident_id,
                cursor_timestamp=timestamp,
                cursor_id=item_id,
                limit=limit + 1,
            )
        visible = items[:limit]
        next_cursor = None
        if len(items) > limit and visible:
            last = visible[-1]
            next_cursor = encode_cursor(timestamp=last.received_at, resource_id=last.id)
        return Page(items=visible, next_cursor=next_cursor)
