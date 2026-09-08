"""Non-sensitive health endpoints."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["alive"] = "alive"


class ReadinessChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration: Literal["ok"] = "ok"
    database: Literal["ok", "unavailable"]


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """Confirm that the API process can serve requests."""

    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def ready(request: Request) -> ReadinessResponse | JSONResponse:
    """Confirm validated configuration and database reachability."""

    database_ready = await request.app.state.readiness_probe()
    result = ReadinessResponse(
        status="ready" if database_ready else "not_ready",
        checks=ReadinessChecks(database="ok" if database_ready else "unavailable"),
    )
    if not database_ready:
        return JSONResponse(status_code=503, content=result.model_dump())
    return result
