"""FastAPI dependencies for database-backed application services."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from incident_ops.application.auth import Principal
from incident_ops.application.ports import RateLimiter

IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


async def enforce_rate_limit(
    request: Request,
    principal: Principal,
    *,
    bucket: str,
) -> None:
    limiter: RateLimiter = get_rate_limiter(request)
    await limiter.check(principal, bucket=bucket)


def request_id(request: Request) -> UUID:
    return UUID(request.state.request_id)


RateLimiterDependency = Annotated[RateLimiter, Depends(get_rate_limiter)]
