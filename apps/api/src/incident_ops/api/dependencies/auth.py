"""Bearer authentication and authorization dependencies."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from incident_ops.application.auth import Principal
from incident_ops.application.auth import require_actor_type as enforce_actor_type
from incident_ops.application.auth import require_scope as enforce_scope
from incident_ops.application.ports import TokenVerifier
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthenticationRequiredError

_bearer = HTTPBearer(auto_error=False)


def get_token_verifier(request: Request) -> TokenVerifier:
    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise AuthenticationRequiredError()
    return verifier


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationRequiredError()
    return await verifier.verify(credentials.credentials)


def require_scope(scope: str) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        enforce_scope(principal, scope)
        return principal

    return dependency


def require_actor_type(actor_type: ActorType) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        enforce_actor_type(principal, actor_type)
        return principal

    return dependency


def require_scope_for_actor(
    scope: str,
    actor_type: ActorType,
) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        enforce_scope(principal, scope)
        enforce_actor_type(principal, actor_type)
        return principal

    return dependency
