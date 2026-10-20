"""HTTP lifecycle adapter; the application owns authority, limits and persistence."""

import asyncio
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Request
from pydantic import SecretStr

from incident_ops.api.dependencies.application import IdempotencyKey, request_id
from incident_ops.api.dependencies.auth import get_current_principal
from incident_ops.application.auth import Principal
from incident_ops.application.investigation import InvestigationRequest
from incident_ops.application.services.investigations import (
    InvestigationResponse,
    InvestigationService,
)
from incident_ops.domain.errors import AuthenticationRequiredError

router = APIRouter(prefix="/api/v1", tags=["investigations"])
AgentPrincipal = Annotated[Principal, Depends(get_current_principal)]


@router.post("/incidents/{incident_id}/investigations", response_model=InvestigationResponse)
async def investigate(
    incident_id: UUID,
    payload: InvestigationRequest,
    request: Request,
    principal: AgentPrincipal,
    idempotency_key: IdempotencyKey,
) -> InvestigationResponse:
    await request.app.state.investigation_limiter.check(principal, bucket="investigations")
    service: InvestigationService = request.app.state.investigation_service
    bearer = SecretStr(request.headers.get("authorization", "").partition(" ")[2])

    async def verify() -> None:
        current = await request.app.state.token_verifier.verify(bearer.get_secret_value())
        if current != principal:
            raise AuthenticationRequiredError()

    task = asyncio.create_task(
        service.run(
            principal=principal,
            incident_id=incident_id,
            key=idempotency_key,
            request_id=request_id(request),
            request=payload,
            verify=verify,
        )
    )
    try:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                break
            await asyncio.wait({task}, timeout=0.1)
        return await task
    except asyncio.CancelledError:
        task.cancel()
        # Finish its cancellation receipt even when the response can no longer be sent.
        # Starlette cancels a whole AnyIO scope on disconnect. asyncio.shield alone
        # does not shield this cleanup from repeated cancellation at each await.
        with anyio.CancelScope(shield=True):
            await asyncio.shield(task)
        raise


@router.get("/investigations/by-request/{key}", response_model=InvestigationResponse)
async def recover_investigation(
    key: UUID, request: Request, principal: AgentPrincipal
) -> InvestigationResponse:
    await request.app.state.rate_limiter.check(principal, bucket="investigation_recovery")
    service: InvestigationService = request.app.state.investigation_service
    return await service.recover(principal=principal, key=key)
