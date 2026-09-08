"""Stable HTTP error envelope for domain and application failures."""

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from incident_ops.domain.errors import AuthenticationRequiredError, IncidentOpsError

logger = logging.getLogger("incident_ops.http")


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


async def validation_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    fields = [
        {
            "location": [str(part) for part in item["loc"]],
            "type": item["type"],
            "message": item["msg"],
        }
        for item in error.errors()
    ]
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-ID": request_id} if request_id is not None else None
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request is invalid.",
                "details": {"fields": fields},
            }
        },
        headers=headers,
    )


async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "unhandled request error",
        extra={
            "request_id": request_id,
            "error_type": type(error).__name__,
        },
    )
    headers = {"X-Request-ID": request_id} if request_id is not None else None
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "The request could not be completed.",
            }
        },
        headers=headers,
    )
