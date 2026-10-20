"""Durable start/recovery and bounded execution for the optional investigator."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal, require_actor_type, require_scope
from incident_ops.application.idempotency import canonical_request_fingerprint
from incident_ops.application.investigation import (
    PROMPT_VERSION,
    InvestigationModel,
    InvestigationOutcome,
    InvestigationRequest,
    InvestigationRunner,
    Limits,
)
from incident_ops.application.ports import CommandObserver
from incident_ops.application.retrieval import StrictContract
from incident_ops.application.services.actions import ActionCommandService
from incident_ops.application.services.investigation_tools import InvestigationToolAdapter
from incident_ops.application.services.retrieval import RetrievalService
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import (
    AuthorizationDeniedError,
    InvestigationUnavailableError,
    RateLimitExceededError,
    ResourceNotFoundError,
    VersionConflictError,
)
from incident_ops.infrastructure.database.models import InvestigationRunModel
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

INTERNAL_SCOPES = frozenset({"investigations:write", "incidents:write"})


def authorize(principal: Principal) -> None:
    require_actor_type(principal, ActorType.AGENT)
    require_scope(principal, "incidents:read")
    require_scope(principal, "actions:request")
    if principal.delegated_by is None or principal.credential_id is None:
        raise AuthorizationDeniedError()


class InvestigationResponse(StrictContract):
    id: UUID
    incident_id: UUID
    incident_version: int
    version: int
    action_request_key: UUID
    provider: str
    model: str
    prompt_version: str
    deadline: datetime
    outcome: InvestigationOutcome
    limits: dict[str, int | float]


def _response(row: InvestigationRunModel, now: datetime) -> InvestigationResponse:
    outcome = InvestigationOutcome.model_validate(row.outcome)
    if outcome.status == "running" and row.deadline <= now:
        # Read-only recovery projection; never resume a possibly committed action.
        outcome.status, outcome.reason = "interrupted", "deadline_passed_check_action"
    return InvestigationResponse(
        id=row.id,
        incident_id=row.incident_id,
        incident_version=row.incident_version,
        version=row.version,
        action_request_key=uuid5(row.id, "action"),
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        deadline=row.deadline,
        outcome=outcome,
        limits=asdict(Limits()),
    )


class InvestigationService:
    def __init__(
        self,
        engine: AsyncEngine,
        model: InvestigationModel | None,
        *,
        retrieval: RetrievalService,
        actions: ActionCommandService,
        observer: CommandObserver | None = None,
    ) -> None:
        self._engine, self._model = engine, model
        self._retrieval, self._actions, self._observer = retrieval, actions, observer

    async def recover(self, *, principal: Principal, key: UUID) -> InvestigationResponse:
        authorize(principal)
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            row = await uow.session.scalar(
                select(InvestigationRunModel).where(
                    InvestigationRunModel.principal_id == principal.subject,
                    InvestigationRunModel.request_key == key,
                )
            )
            if row is None:
                raise ResourceNotFoundError()
            return _response(row, await uow.incidents.database_now())

    async def run(
        self,
        *,
        principal: Principal,
        incident_id: UUID,
        key: UUID,
        request_id: UUID,
        request: InvestigationRequest,
        verify: Callable[[], Awaitable[None]],
    ) -> InvestigationResponse:
        authorize(principal)
        request = InvestigationRequest.model_validate(request.model_dump())
        if self._model is None:
            raise InvestigationUnavailableError()
        await verify()
        receipt, created = await self._start(principal, incident_id, key, request_id, request)
        if not created:
            return receipt
        adapter = InvestigationToolAdapter(
            engine=self._engine,
            principal=principal,
            incident_id=incident_id,
            expected_version=request.expected_incident_version,
            deadline=receipt.deadline,
            action_key=receipt.action_request_key,
            request_id=request_id,
            verify=verify,
            retrieval=self._retrieval,
            actions=self._actions,
        )
        try:
            outcome = await InvestigationRunner(self._model).run(
                adapter, allow_actions=request.allow_action_requests
            )
        except asyncio.CancelledError:
            outcome = InvestigationOutcome(status="cancelled", reason="cancelled")
        except Exception:
            # Do not persist/log exception strings, prompts or provider bodies.
            outcome = InvestigationOutcome(status="stopped", reason="internal_error")
        return await self._finish(principal, receipt, request_id, outcome)

    async def _start(
        self,
        principal: Principal,
        incident_id: UUID,
        key: UUID,
        request_id: UUID,
        request: InvestigationRequest,
    ) -> tuple[InvestigationResponse, bool]:
        assert self._model is not None
        fingerprint = canonical_request_fingerprint(
            operation="investigations.start",
            path_parameters={"incident_id": incident_id},
            body=request.model_dump(),
        )
        async with SqlAlchemyUnitOfWork(
            self._engine, principal=principal, trusted_scopes=INTERNAL_SCOPES
        ) as uow:
            await uow.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 110017))"),
                {"subject": principal.subject},
            )
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation="investigations.start",
                key=key,
                fingerprint=fingerprint,
            )
            if not reservation.created:
                row = await uow.session.scalar(
                    select(InvestigationRunModel).where(
                        InvestigationRunModel.principal_id == principal.subject,
                        InvestigationRunModel.request_key == key,
                    )
                )
                if row is None:
                    raise ResourceNotFoundError()
                return _response(row, await uow.incidents.database_now()), False
            now = await uow.incidents.database_now()
            active = await uow.session.scalar(
                select(InvestigationRunModel.id)
                .where(
                    InvestigationRunModel.principal_id == principal.subject,
                    InvestigationRunModel.version == 1,
                    InvestigationRunModel.deadline > now,
                )
                .limit(1)
            )
            if active is not None:
                raise RateLimitExceededError()
            incident = await uow.incidents.get(incident_id, for_update=True)
            if incident is None:
                raise ResourceNotFoundError()
            if incident.version != request.expected_incident_version:
                raise VersionConflictError()
            row = InvestigationRunModel(
                id=uuid4(),
                principal_id=principal.subject,
                request_key=key,
                incident_id=incident_id,
                incident_version=incident.version,
                version=1,
                created_at=now,
                updated_at=now,
                deadline=now + timedelta(seconds=Limits().seconds),
                provider=self._model.provider,
                model=self._model.model,
                prompt_version=PROMPT_VERSION,
                outcome=InvestigationOutcome().model_dump(mode="json"),
            )
            uow.session.add(row)
            await uow.session.flush()
            await uow.idempotency.complete(
                record_id=reservation.record_id,
                response_status=202,
                response_body={"run_id": str(row.id)},
                resource_type="investigation_run",
                resource_id=row.id,
            )
            await uow.events.append(
                incident_id=incident_id,
                event_type="investigation.started",
                principal=principal,
                request_id=request_id,
                payload={
                    "run_id": str(row.id),
                    "incident_version": incident.version,
                    "allow_action_requests": request.allow_action_requests,
                    "provider": row.provider,
                    "model": row.model,
                    "prompt_version": PROMPT_VERSION,
                    "limits": asdict(Limits()),
                },
            )
            if self._observer is not None:
                await self._observer.reached("investigation.start.before_commit")
            response = _response(row, now)
            await uow.commit()
            return response, True

    async def _finish(
        self,
        principal: Principal,
        receipt: InvestigationResponse,
        request_id: UUID,
        outcome: InvestigationOutcome,
    ) -> InvestigationResponse:
        async with SqlAlchemyUnitOfWork(
            self._engine, principal=principal, trusted_scopes=INTERNAL_SCOPES
        ) as uow:
            incident = await uow.incidents.get(receipt.incident_id, for_update=True)
            now = await uow.incidents.database_now()
            if (
                outcome.status == "completed"
                and outcome.action is None
                and (incident is None or incident.version != receipt.incident_version)
            ):
                outcome.status, outcome.reason, outcome.findings = "stopped", "version_conflict", []
            if outcome.status == "completed" and now >= receipt.deadline:
                outcome.status, outcome.reason = "stopped", "deadline_exceeded"
            statement = (
                update(InvestigationRunModel)
                .where(
                    InvestigationRunModel.id == receipt.id,
                    InvestigationRunModel.principal_id == principal.subject,
                    InvestigationRunModel.version == 1,
                )
                .values(
                    version=2,
                    outcome=outcome.model_dump(mode="json"),
                    updated_at=func.clock_timestamp(),
                )
                .returning(InvestigationRunModel)
            )
            row = (await uow.session.execute(statement)).scalar_one_or_none()
            if row is None:
                raise VersionConflictError()
            await uow.events.append(
                incident_id=receipt.incident_id,
                event_type=f"investigation.{outcome.status}",
                principal=principal,
                request_id=request_id,
                payload={
                    "run_id": str(receipt.id),
                    "reason": outcome.reason,
                    "trace": [step.model_dump() for step in outcome.trace],
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                    "reserved_cost_microusd": outcome.reserved_cost_microusd,
                    "action_request_key": str(receipt.action_request_key),
                },
            )
            if self._observer is not None:
                await self._observer.reached("investigation.finish.before_commit")
            response = _response(row, now)
            await uow.commit()
            return response
