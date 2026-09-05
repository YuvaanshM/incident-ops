"""Stable HTTP error envelope for domain and application failures."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from incident_ops.domain.errors import AuthenticationRequiredError, IncidentOpsError


async def incident_ops_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, IncidentOpsError):
        raise error

    error_body: dict[str, Any] = {
        "code": error.code,
        "message": error.public_message,
    }
    if error.details is not None:
        error_body["details"] = error.details

    headers: dict[str, str] = {}
    if isinstance(error, AuthenticationRequiredError):
        headers["WWW-Authenticate"] = "Bearer"
    request_id = getattr(request.state, "request_id", None)
    if request_id is not None:
        headers["X-Request-ID"] = request_id

    return JSONResponse(
        status_code=error.status_code,
        content={"error": error_body},
        headers=headers,
    )
