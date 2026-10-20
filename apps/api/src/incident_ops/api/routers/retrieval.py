"""Read-only HTTP adapters for cited historical precedent."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from incident_ops.api.dependencies.auth import require_scope
from incident_ops.application.auth import Principal
from incident_ops.application.retrieval import (
    HistoricalSource,
    RemediationResponse,
    RetrievalRequest,
)
from incident_ops.application.services.retrieval import RetrievalService

router = APIRouter(prefix="/api/v1", tags=["retrieval"])
ReadPrincipal = Annotated[Principal, Depends(require_scope("incidents:read"))]


@router.post("/incidents/{incident_id}/remediation-suggestions", response_model=RemediationResponse)
async def suggest_remediation(
    incident_id: UUID, payload: RetrievalRequest, request: Request, principal: ReadPrincipal
) -> RemediationResponse:
    await request.app.state.retrieval_limiter.check(principal, bucket="retrieval")
    service: RetrievalService = request.app.state.retrieval_service
    return await service.suggest(principal=principal, incident_id=incident_id, request=payload)


@router.get("/historical-incidents/{source_id}", response_model=HistoricalSource)
async def historical_source(
    source_id: UUID, request: Request, principal: ReadPrincipal
) -> HistoricalSource:
    await request.app.state.rate_limiter.check(principal, bucket="historical_sources")
    service: RetrievalService = request.app.state.retrieval_service
    return await service.source(principal=principal, source_id=source_id)
