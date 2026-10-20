"""Thin HTTP adapter for policy-driven action workflows."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from incident_ops.api.dependencies.application import IdempotencyKey, enforce_rate_limit, request_id
from incident_ops.api.dependencies.auth import require_scope, require_scope_for_actor
from incident_ops.api.schemas.actions import (
    ActionListResponse,
    ActionReject,
    ActionRequestCreate,
    ActionResponse,
    DemoJobAttach,
)
from incident_ops.api.schemas.incidents import DemoJobResponse
from incident_ops.application.auth import Principal
from incident_ops.application.serialization import action_body
from incident_ops.application.services.actions import (
    ActionCommandService,
    ActionQueryService,
    serialize_action_page,
)
from incident_ops.domain.enums import ActionStatus, ActorType

router = APIRouter(prefix="/api/v1", tags=["actions"])
demo_router = APIRouter(prefix="/api/v1/demo", tags=["demo"])

ReadPrincipal = Annotated[Principal, Depends(require_scope("incidents:read"))]
RequestPrincipal = Annotated[Principal, Depends(require_scope("actions:request"))]
ApproverPrincipal = Annotated[
    Principal,
    Depends(require_scope_for_actor("actions:approve", ActorType.HUMAN)),
]
FixturePrincipal = Annotated[
    Principal,
    Depends(require_scope_for_actor("fixtures:write", ActorType.HUMAN)),
]


def _query_service(request: Request) -> ActionQueryService:
    return request.app.state.action_query_service


def _command_service(request: Request) -> ActionCommandService:
    return request.app.state.action_command_service


@router.post(
    "/incidents/{incident_id}/actions",
    response_model=ActionResponse,
    status_code=201,
)
async def request_action(
    incident_id: UUID,
    payload: ActionRequestCreate,
    request: Request,
    principal: RequestPrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="action_commands")
    result = await _command_service(request).request_action(
        principal=principal,
        incident_id=incident_id,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
        expected_incident_version=payload.expected_incident_version,
        action_type=payload.action_type,
        parameters=payload.normalized_parameters(),
        rationale=payload.rationale,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.get("/actions", response_model=ActionListResponse)
async def list_actions(
    request: Request,
    principal: ApproverPrincipal,
    status: ActionStatus | None = None,
    incident_id: UUID | None = None,
    minimum_age_minutes: Annotated[int | None, Query(ge=1, le=10_080)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="action_queries")
    page = await _query_service(request).list_actions(
        principal=principal,
        status=status,
        incident_id=incident_id,
        minimum_age_minutes=minimum_age_minutes,
        cursor=cursor,
        limit=limit,
    )
    return serialize_action_page(page)


@router.get("/actions/by-request/{idempotency_key}", response_model=ActionResponse)
async def get_requested_action(
    idempotency_key: UUID,
    request: Request,
    principal: RequestPrincipal,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="action_queries")
    action = await _query_service(request).get_requested_action(
        principal=principal, idempotency_key=idempotency_key
    )
    return action_body(action)


@router.get("/actions/{action_id}", response_model=ActionResponse)
async def get_action(
    action_id: UUID,
    request: Request,
    principal: ReadPrincipal,
) -> dict[str, object]:
    await enforce_rate_limit(request, principal, bucket="action_queries")
    action = await _query_service(request).get_action(principal=principal, action_id=action_id)
    return action_body(action)


@router.post("/actions/{action_id}/approve", response_model=ActionResponse)
async def approve_action(
    action_id: UUID,
    request: Request,
    principal: ApproverPrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="approval_commands")
    result = await _command_service(request).approve_action(
        principal=principal,
        action_id=action_id,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@router.post("/actions/{action_id}/reject", response_model=ActionResponse)
async def reject_action(
    action_id: UUID,
    payload: ActionReject,
    request: Request,
    principal: ApproverPrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="approval_commands")
    result = await _command_service(request).reject_action(
        principal=principal,
        action_id=action_id,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
        reason=payload.reason,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@demo_router.post(
    "/incidents/{incident_id}/job",
    response_model=DemoJobResponse,
    status_code=201,
)
async def attach_demo_job(
    incident_id: UUID,
    payload: DemoJobAttach,
    request: Request,
    principal: FixturePrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="demo_commands")
    result = await _command_service(request).attach_demo_job(
        principal=principal,
        incident_id=incident_id,
        expected_incident_version=payload.expected_incident_version,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)


@demo_router.post(
    "/jobs/{job_id}/simulate-failure",
    response_model=DemoJobResponse,
    include_in_schema=True,
)
async def simulate_job_failure(
    job_id: UUID,
    request: Request,
    principal: FixturePrincipal,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    await enforce_rate_limit(request, principal, bucket="demo_commands")
    result = await _command_service(request).simulate_job_failure(
        principal=principal,
        job_id=job_id,
        idempotency_key=idempotency_key,
        request_id=request_id(request),
    )
    return JSONResponse(status_code=result.status_code, content=result.body)
