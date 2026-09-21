"""Signed monitoring intake and normalized incident alert history."""

import asyncio
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from incident_ops.api.dependencies.auth import require_scope
from incident_ops.api.routers.slack import _body, _secure_transport, _single_header
from incident_ops.api.schemas.alerts import AlertListResponse
from incident_ops.application.auth import Principal
from incident_ops.application.dto import AlertEventView
from incident_ops.application.services.alert_ingestion import (
    AlertIngestionService,
    AlertQueryService,
)
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import (
    AuthorizationDeniedError,
    IngestionUnavailableError,
    ResourceNotFoundError,
    UnsupportedWebhookMediaError,
    WebhookAuthenticationError,
)
from incident_ops.infrastructure.auth.alert_webhook import AlertWebhookVerifier
from incident_ops.infrastructure.monitoring.generic import GenericAlertAdapter
from incident_ops.infrastructure.rate_limit import FixedWindowRateLimiter

router = APIRouter(tags=["alerts"])
ReadPrincipal = Annotated[Principal, Depends(require_scope("incidents:read"))]


@router.post("/api/v1/alerts/webhook", include_in_schema=False)
async def receive_alert(request: Request) -> JSONResponse:
    settings = cast(Settings, request.app.state.settings)
    if not settings.alert_intake.enabled:
        raise ResourceNotFoundError()
    ingress = cast(FixedWindowRateLimiter, request.app.state.alert_ingress_limiter)
    await ingress.check(
        Principal(ActorType.SYSTEM, "alerts:unverified", frozenset()), bucket="alerts:unverified"
    )
    if (
        not _secure_transport(request, settings)
        or "authorization" in request.headers
        or "cookie" in request.headers
    ):
        raise WebhookAuthenticationError()
    key_id = _single_header(request, "x-alert-key-id", limit=100)
    timestamp = _single_header(request, "x-alert-timestamp", limit=12)
    key = _single_header(request, "idempotency-key", limit=36)
    signature = _single_header(request, "x-alert-signature", limit=67)
    content_type = _single_header(request, "content-type", limit=100).lower().replace(" ", "")
    if content_type not in {"application/json", "application/json;charset=utf-8"}:
        raise UnsupportedWebhookMediaError()
    if "content-encoding" in request.headers:
        raise UnsupportedWebhookMediaError()
    try:
        async with asyncio.timeout(5):
            body = await _body(request)
            verifier = cast(AlertWebhookVerifier, request.app.state.alert_signature_verifier)
            verified = verifier.verify(
                key_id=key_id,
                timestamp=timestamp,
                idempotency_key=key,
                signature=signature,
                body=body,
            )
            alert = GenericAlertAdapter().normalize(body)
            if alert.source != verified.source or alert.service not in verified.services:
                raise AuthorizationDeniedError()
            source_limiter = cast(FixedWindowRateLimiter, request.app.state.alert_source_limiter)
            await source_limiter.check(verified.principal, bucket="alerts:ingest")
            service = cast(AlertIngestionService, request.app.state.alert_ingestion_service)
            result, replayed = await service.ingest(
                verified=verified,
                alert=alert,
                request_id=UUID(request.state.request_id),
            )
            return JSONResponse(
                status_code=result.status_code,
                content=result.body,
                headers={"Idempotent-Replayed": "true"} if replayed else None,
            )
    except TimeoutError, SQLAlchemyError:
        raise IngestionUnavailableError() from None


@router.get("/api/v1/incidents/{incident_id}/alerts", response_model=AlertListResponse)
async def get_incident_alerts(
    incident_id: UUID,
    request: Request,
    principal: ReadPrincipal,
    cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, object]:
    limiter = cast(FixedWindowRateLimiter, request.app.state.rate_limiter)
    await limiter.check(principal, bucket="incident_queries")
    service = cast(AlertQueryService, request.app.state.alert_query_service)
    page = await service.for_incident(
        principal=principal, incident_id=incident_id, cursor=cursor, limit=limit
    )
    items = cast(tuple[AlertEventView, ...], page.items)
    return {
        "items": [
            {
                "id": str(item.id),
                "alert_id": str(item.alert_id),
                "incident_id": str(item.incident_id),
                "source": item.source,
                "started_at": item.started_at.isoformat(),
                "event_version": item.event_version,
                "status": item.status,
                "severity": item.severity,
                "title": item.title,
                "outcome": item.outcome,
                "received_at": item.received_at.isoformat(),
            }
            for item in items
        ],
        "next_cursor": page.next_cursor,
    }
