"""Server-to-server exchange of verified human authority for capped agent authority."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from incident_ops.api.dependencies.auth import require_scope_for_actor
from incident_ops.api.schemas.incidents import StrictSchema
from incident_ops.application.auth import Principal
from incident_ops.application.services.agent_sessions import AgentSessionService
from incident_ops.domain.enums import ActorType

router = APIRouter(prefix="/api/v1/agent-sessions", tags=["agent authentication"])


class AgentSessionRequest(StrictSchema):
    """No caller-controlled permissions or identities."""


@router.post("")
async def exchange_agent_session(
    payload: AgentSessionRequest,
    request: Request,
    principal: Annotated[
        Principal, Depends(require_scope_for_actor("actions:request", ActorType.HUMAN))
    ],
) -> JSONResponse:
    del payload
    await request.app.state.agent_exchange_limiter.check(principal, bucket="agent_exchange")
    service: AgentSessionService = request.app.state.agent_session_service
    credential = service.exchange(principal)
    return JSONResponse(
        {
            "access_token": credential.access_token.get_secret_value(),
            "token_type": "Bearer",
            "expires_in": credential.expires_in,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
