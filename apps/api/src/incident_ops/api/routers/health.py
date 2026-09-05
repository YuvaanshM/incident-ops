"""Non-sensitive health endpoints."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["alive"] = "alive"


class ReadinessChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration: Literal["ok"] = "ok"
    database: Literal["deferred_to_phase_2"] = "deferred_to_phase_2"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"] = "ready"
    checks: ReadinessChecks = ReadinessChecks()


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """Confirm that the API process can serve requests."""

    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def ready() -> ReadinessResponse:
    """Confirm validated configuration; database probing arrives with Phase 2."""

    return ReadinessResponse()
