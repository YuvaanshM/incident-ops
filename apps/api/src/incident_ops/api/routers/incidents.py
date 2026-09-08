"""Thin HTTP adapter for incident commands and queries."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from incident_ops.api.dependencies.application import IdempotencyKey, enforce_rate_limit, request_id
from incident_ops.api.dependencies.auth import require_scope
from incident_ops.api.schemas.incidents import (
    IncidentCreate,
    IncidentListResponse,
    IncidentResponse,
    IncidentUpdate,
    PostmortemDraftResponse,
    SeverityRecommendationResponse,
    TimelineResponse,
)
from incident_ops.application.auth import Principal
from incident_ops.application.serialization import incident_body
from incident_ops.application.services.incidents import (
    IncidentCommandService,
    IncidentQueryService,
    serialize_incident_page,
    serialize_timeline_page,
)
from incident_ops.domain.enums import IncidentSeverity, IncidentStatus

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

ReadPrincipal = Annotated[Principal, Depends(require_scope("incidents:read"))]
WritePrincipal = Annotated[Principal, Depends(require_scope("incidents:write"))]


def _query_service(request: Request) -> IncidentQueryService:
    return request.app.state.incident_query_service


def _command_service(request: Request) -> IncidentCommandService:
    return request.app.state.incident_command_service


@router.post("", response_model=IncidentResponse, status_code=201)
async def create_incident(
    payload: IncidentCreate,
    request: Request,
    principal: WritePrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="incident_commands")
    result = await _command_service(request).create_incident(
        principal=principal,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
        title=payload.title,
        description=payload.description,
        service=payload.service,
        severity=payload.severity,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("", response_model=IncidentListResponse)
async def list_incidents(
    request: Request,
    principal: ReadPrincipal,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    service: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    severity: IncidentSeverity | None = None,
    status: IncidentStatus | None = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="incident_queries")
    page = await _query_service(request).list_incidents(
        principal=principal,
        query=None if q is None else q.strip(),
        service=None if service is None else service.strip(),
        severity=severity,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return serialize_incident_page(page)


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: UUID,
    request: Request,
    principal: ReadPrincipal,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="incident_queries")
    incident = await _query_service(request).get_incident(
        principal=principal,
        incident_id=incident_id,
    )
    return incident_body(incident)


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: UUID,
    payload: IncidentUpdate,
    request: Request,
    principal: WritePrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="incident_commands")
    changes = payload.model_dump(
        exclude={"expected_version"},
        exclude_unset=True,
    )
    result = await _command_service(request).update_incident(
        principal=principal,
        incident_id=incident_id,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
        expected_version=payload.expected_version,
        changes=changes,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/{incident_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    incident_id: UUID,
    request: Request,
    principal: ReadPrincipal,
    cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="incident_queries")
    page = await _query_service(request).get_timeline(
        principal=principal,
        incident_id=incident_id,
        cursor=cursor,
        limit=limit,
    )
    return serialize_timeline_page(page)


@router.post(
    "/{incident_id}/severity-recommendation",
    response_model=SeverityRecommendationResponse,
)
async def severity_recommendation(
    incident_id: UUID,
    request: Request,
    principal: ReadPrincipal,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="advisory_queries")
    return await _query_service(request).severity_recommendation(
        principal=principal,
        incident_id=incident_id,
    )


@router.post(
    "/{incident_id}/postmortem-draft",
    response_model=PostmortemDraftResponse,
)
async def postmortem_draft(
    incident_id: UUID,
    request: Request,
    principal: ReadPrincipal,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="advisory_queries")
    return await _query_service(request).postmortem_draft(
        principal=principal,
        incident_id=incident_id,
    )
