"""Policy-driven action queries, requests, reviews, and demo execution."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal, require_actor_type
from incident_ops.application.dto import (
    ActionRequestView,
    CommandResult,
    IdempotencyReservation,
    Page,
)
from incident_ops.application.idempotency import canonical_request_fingerprint
from incident_ops.application.pagination import decode_cursor, encode_cursor
from incident_ops.application.ports import CommandObserver
from incident_ops.application.serialization import action_body, error_body, job_body
from incident_ops.application.services.incidents import NullCommandObserver
from incident_ops.domain.actions import get_action_definition
from incident_ops.domain.enums import (
    ActionStatus,
    ActionType,
    ActorType,
    ExecutionMode,
    IncidentSeverity,
    IncidentStatus,
    PolicyDecision,
)
from incident_ops.domain.errors import (
    ActionNotPendingError,
    IncidentOpsError,
    InvalidActionParametersError,
    ResourceNotFoundError,
    VersionConflictError,
)
from incident_ops.domain.incidents import transition_lifecycle
from incident_ops.domain.policies import ActionPolicyInput, evaluate_action_policy
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

_REQUEST_INTERNAL_SCOPES = frozenset({"incidents:read", "actions:execute"})
_REVIEW_INTERNAL_SCOPES = frozenset({"incidents:read", "incidents:write", "actions:execute"})
_DEMO_INTERNAL_SCOPES = frozenset({"incidents:read", "actions:execute"})


def _replay_result(reservation: IdempotencyReservation) -> CommandResult | None:
    if reservation.created:
        return None
    status = reservation.response_status
    body = reservation.response_body
    if not isinstance(status, int) or not isinstance(body, Mapping):
        raise RuntimeError("idempotency record is incomplete")
    return CommandResult(status_code=status, body=dict(body))


class ActionQueryService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_action(self, *, principal: Principal, action_id: UUID) -> ActionRequestView:
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            action = await uow.actions.get(action_id)
        if action is None:
            raise ResourceNotFoundError()
        return action

    async def list_actions(
        self,
        *,
        principal: Principal,
        status: ActionStatus | None,
        incident_id: UUID | None,
        minimum_age_minutes: int | None,
        cursor: str | None,
        limit: int,
    ) -> Page:
        cursor_timestamp = None
        cursor_id = None
        if cursor is not None:
            cursor_timestamp, raw_id = decode_cursor(cursor)
            cursor_id = UUID(raw_id)
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            actions = await uow.actions.list_page(
                status=None if status is None else status.value,
                incident_id=incident_id,
                created_before=(
                    datetime.now(UTC) - timedelta(minutes=minimum_age_minutes)
                    if minimum_age_minutes is not None
                    else None
                ),
                cursor_timestamp=cursor_timestamp,
                cursor_id=cursor_id,
                limit=limit + 1,
            )
        has_more = len(actions) > limit
        visible = actions[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = encode_cursor(timestamp=last.created_at, resource_id=last.id)
        return Page(items=visible, next_cursor=next_cursor)


class ActionCommandService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        observer: CommandObserver | None = None,
    ) -> None:
        self._engine = engine
        self._observer = observer or NullCommandObserver()

    async def request_action(
        self,
        *,
        principal: Principal,
        incident_id: UUID,
        idempotency_key: UUID,
        request_id: UUID,
        expected_incident_version: int,
        action_type: str,
        parameters: Mapping[str, object],
        rationale: str | None,
    ) -> CommandResult:
        fingerprint = canonical_request_fingerprint(
            operation="actions.request",
            path_parameters={"incident_id": incident_id},
            body={
                "expected_incident_version": expected_incident_version,
                "action_type": action_type,
                "parameters": parameters,
                "rationale": rationale,
            },
        )
        async with SqlAlchemyUnitOfWork(
            self._engine,
            principal=principal,
            trusted_scopes=_REQUEST_INTERNAL_SCOPES,
        ) as uow:
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation="actions.request",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(reservation)
            if replay is not None:
                await uow.rollback()
                return replay

            try:
                definition = get_action_definition(action_type)
            except IncidentOpsError as error:
                return await self._complete_error(uow, reservation, error)

            incident = await uow.incidents.get(incident_id, for_update=True)
            if incident is None:
                return await self._complete_error(uow, reservation, ResourceNotFoundError())
            if incident.version != expected_incident_version:
                return await self._complete_error(
                    uow,
                    reservation,
                    VersionConflictError(
                        details={
                            "current_version": incident.version,
                            "refetch": f"/api/v1/incidents/{incident.id}",
                        }
                    ),
                )

            retry_count = 0
            persisted_parameters: dict[str, object]
            job = None
            if definition.action_type is ActionType.RETRY_FAILED_JOB:
                raw_job_id = parameters.get("job_id")
                if not isinstance(raw_job_id, UUID):
                    return await self._complete_error(
                        uow, reservation, InvalidActionParametersError()
                    )
                job = await uow.jobs.get(raw_job_id, for_update=True)
                if job is None or job.incident_id != incident_id or job.status != "failed":
                    return await self._complete_error(
                        uow, reservation, InvalidActionParametersError()
                    )
                retry_count = job.retry_count
                persisted_parameters = {
                    "job_id": str(job.id),
                    "expected_job_version": job.version,
                }
            else:
                target_status = parameters.get("target_status")
                if not isinstance(target_status, IncidentStatus):
                    return await self._complete_error(
                        uow, reservation, InvalidActionParametersError()
                    )
                now = await uow.incidents.database_now()
                try:
                    transition_lifecycle(
                        current=IncidentStatus(incident.status),
                        target=target_status,
                        now=now,
                        resolved_at=incident.resolved_at,
                        closed_at=incident.closed_at,
                    )
                except IncidentOpsError as error:
                    return await self._complete_error(uow, reservation, error)
                persisted_parameters = {"target_status": target_status.value}

            policy = evaluate_action_policy(
                ActionPolicyInput(
                    action_type=definition.action_type,
                    incident_severity=IncidentSeverity(incident.severity),
                    requested_by=principal.actor_type,
                    parameters_valid=True,
                    retry_count=retry_count,
                    higher_monitoring_risk=await uow.alerts.has_higher_firing_risk(
                        incident.id, incident.severity
                    ),
                )
            )
            policy_snapshot = policy.snapshot
            if job is not None:
                policy_facts = policy_snapshot.get("facts")
                if not isinstance(policy_facts, dict):
                    raise RuntimeError("policy snapshot facts must be an object")
                policy_snapshot["facts"] = {
                    **policy_facts,
                    "job_version": job.version,
                }

            now = await uow.incidents.database_now()
            status = ActionStatus.PENDING
            execution_mode = ExecutionMode.HUMAN_APPROVED
            outcome_code = None
            outcome_payload = None
            decided_by_type = None
            decided_by_id = None
            decided_at = None

            if policy.decision is PolicyDecision.AUTO_APPLY:
                if job is None:
                    raise RuntimeError("automatic retry has no validated job")
                queued = await uow.jobs.queue_failed(
                    job_id=job.id,
                    incident_id=incident_id,
                    expected_version=job.version,
                    expected_retry_count=job.retry_count,
                )
                if queued is None:
                    raise RuntimeError("locked demo job changed before execution")
                status = ActionStatus.APPLIED
                execution_mode = ExecutionMode.AUTO
                outcome_code = "job_queued"
                outcome_payload = {
                    "job_id": str(queued.id),
                    "job_version": queued.version,
                    "retry_count": queued.retry_count,
                }
                decided_by_type = ActorType.SYSTEM.value
                decided_by_id = "policy-engine"
                decided_at = now
            elif policy.decision is PolicyDecision.REJECT:
                status = ActionStatus.REJECTED
                execution_mode = ExecutionMode.AUTO
                outcome_code = policy.reasons[0]
                outcome_payload = {"reasons": list(policy.reasons)}
                decided_by_type = ActorType.SYSTEM.value
                decided_by_id = "policy-engine"
                decided_at = now

            action = await uow.actions.add_action(
                incident_id=incident_id,
                action_type=definition.action_type.value,
                status=status.value,
                execution_mode=execution_mode.value,
                principal=principal,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                base_incident_version=incident.version,
                parameters=persisted_parameters,
                policy_version=policy.policy_version,
                policy_snapshot=policy_snapshot,
                rationale=rationale,
                outcome_code=outcome_code,
                outcome_payload=outcome_payload,
                decided_by_type=decided_by_type,
                decided_by_id=decided_by_id,
                decided_at=decided_at,
            )
            await uow.events.append(
                incident_id=incident_id,
                action_request_id=action.id,
                event_type="action.requested",
                principal=principal,
                request_id=request_id,
                payload={
                    "action_type": action.action_type,
                    "decision": policy.decision.value,
                    "policy_version": policy.policy_version,
                    "reasons": list(policy.reasons),
                },
            )
            await self._observer.reached("action.request.after_event")

            if status is ActionStatus.PENDING:
                pending_event = await uow.events.append(
                    incident_id=incident_id,
                    action_request_id=action.id,
                    event_type="action.pending_approval",
                    principal=principal,
                    request_id=request_id,
                    payload={"policy_version": policy.policy_version},
                )
                await uow.outbox.enqueue(
                    source_event_id=pending_event.id,
                    event_type="slack.action.human_required",
                    aggregate_type="action_request",
                    aggregate_id=action.id,
                    dedupe_key=f"slack.action.human_required:{pending_event.id}",
                    payload={
                        "action_id": str(action.id),
                        "incident_id": str(incident_id),
                        "event_id": str(pending_event.id),
                        "action_type": action.action_type,
                        "severity": incident.severity,
                    },
                )
            else:
                event_type = (
                    "action.applied" if status is ActionStatus.APPLIED else "action.rejected"
                )
                await uow.events.append(
                    incident_id=incident_id,
                    action_request_id=action.id,
                    event_type=event_type,
                    principal=principal,
                    request_id=request_id,
                    payload={
                        "outcome_code": action.outcome_code,
                        "execution_mode": action.execution_mode,
                    },
                )
            body = action_body(action)
            await uow.idempotency.complete(
                record_id=reservation.record_id,
                response_status=201,
                response_body=body,
                resource_type="action_request",
                resource_id=action.id,
            )
            await uow.commit()
            return CommandResult(status_code=201, body=body)

    async def approve_action(
        self,
        *,
        principal: Principal,
        action_id: UUID,
        idempotency_key: UUID,
        request_id: UUID,
    ) -> CommandResult:
        require_actor_type(principal, ActorType.HUMAN)
        return await self._review_action(
            principal=principal,
            action_id=action_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            approve=True,
            reason=None,
        )

    async def reject_action(
        self,
        *,
        principal: Principal,
        action_id: UUID,
        idempotency_key: UUID,
        request_id: UUID,
        reason: str | None,
    ) -> CommandResult:
        require_actor_type(principal, ActorType.HUMAN)
        return await self._review_action(
            principal=principal,
            action_id=action_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            approve=False,
            reason=reason,
        )

    async def _review_action(
        self,
        *,
        principal: Principal,
        action_id: UUID,
        idempotency_key: UUID,
        request_id: UUID,
        approve: bool,
        reason: str | None,
    ) -> CommandResult:
        operation = "actions.approve" if approve else "actions.reject"
        fingerprint = canonical_request_fingerprint(
            operation=operation,
            path_parameters={"action_id": action_id},
            body={} if approve else {"reason": reason},
        )
        async with SqlAlchemyUnitOfWork(
            self._engine,
            principal=principal,
            trusted_scopes=_REVIEW_INTERNAL_SCOPES,
        ) as uow:
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation=operation,
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(reservation)
            if replay is not None:
                await uow.rollback()
                return replay

            await self._observer.reached("action.review.before_lock")
            action = await uow.actions.lock_pending(action_id)
            if action is None:
                existing = await uow.actions.get(action_id)
                if existing is None:
                    return await self._complete_error(uow, reservation, ResourceNotFoundError())
                return await self._complete_error(
                    uow,
                    reservation,
                    ActionNotPendingError(details={"current_status": existing.status}),
                )

            if not approve:
                finalized = await uow.actions.finalize(
                    action_id=action.id,
                    status=ActionStatus.REJECTED.value,
                    outcome_code="rejected_by_human",
                    outcome_payload={"reason": reason},
                    principal=principal,
                )
                if finalized is None:
                    raise RuntimeError("locked action could not be rejected")
                await uow.events.append(
                    incident_id=action.incident_id,
                    action_request_id=action.id,
                    event_type="action.rejected",
                    principal=principal,
                    request_id=request_id,
                    payload={"reason": reason},
                )
                return await self._complete_action(uow, reservation, finalized)

            incident = await uow.incidents.get(action.incident_id, for_update=True)
            if incident is None or incident.version != action.base_incident_version:
                return await self._mark_stale(
                    uow,
                    reservation,
                    action,
                    principal,
                    request_id,
                    "incident_version_changed",
                )

            if action.action_type == ActionType.RETRY_FAILED_JOB.value:
                job_id = UUID(str(action.parameters["job_id"]))
                expected_job_version = action.parameters.get("expected_job_version")
                job = await uow.jobs.get(job_id, for_update=True)
                if (
                    job is None
                    or job.incident_id != action.incident_id
                    or job.status != "failed"
                    or job.version != expected_job_version
                    or job.retry_count >= 2
                ):
                    return await self._mark_stale(
                        uow,
                        reservation,
                        action,
                        principal,
                        request_id,
                        "job_state_changed",
                    )
                queued = await uow.jobs.queue_failed(
                    job_id=job.id,
                    incident_id=action.incident_id,
                    expected_version=job.version,
                    expected_retry_count=job.retry_count,
                )
                if queued is None:
                    return await self._mark_stale(
                        uow,
                        reservation,
                        action,
                        principal,
                        request_id,
                        "job_state_changed",
                    )
                outcome_code = "job_queued"
                outcome_payload: dict[str, object] = {
                    "job_id": str(queued.id),
                    "job_version": queued.version,
                    "retry_count": queued.retry_count,
                }
            else:
                target_status = IncidentStatus(str(action.parameters["target_status"]))
                now = await uow.incidents.database_now()
                try:
                    lifecycle = transition_lifecycle(
                        current=IncidentStatus(incident.status),
                        target=target_status,
                        now=now,
                        resolved_at=incident.resolved_at,
                        closed_at=incident.closed_at,
                    )
                except IncidentOpsError:
                    return await self._mark_stale(
                        uow,
                        reservation,
                        action,
                        principal,
                        request_id,
                        "incident_state_changed",
                    )
                updated = await uow.incidents.conditional_update(
                    incident_id=incident.id,
                    expected_version=incident.version,
                    values={
                        "status": target_status.value,
                        "resolved_at": lifecycle.resolved_at,
                        "closed_at": lifecycle.closed_at,
                    },
                )
                if updated is None:
                    return await self._mark_stale(
                        uow,
                        reservation,
                        action,
                        principal,
                        request_id,
                        "incident_state_changed",
                    )
                outcome_code = "incident_status_updated"
                outcome_payload = {
                    "incident_id": str(updated.id),
                    "incident_version": updated.version,
                    "status": updated.status,
                }

            await uow.events.append(
                incident_id=action.incident_id,
                action_request_id=action.id,
                event_type="action.approved",
                principal=principal,
                request_id=request_id,
                payload={"action_type": action.action_type},
            )
            finalized = await uow.actions.finalize(
                action_id=action.id,
                status=ActionStatus.APPLIED.value,
                outcome_code=outcome_code,
                outcome_payload=outcome_payload,
                principal=principal,
            )
            if finalized is None:
                raise RuntimeError("locked action could not be applied")
            await uow.events.append(
                incident_id=action.incident_id,
                action_request_id=action.id,
                event_type="action.applied",
                principal=principal,
                request_id=request_id,
                payload={"outcome_code": outcome_code},
            )
            return await self._complete_action(uow, reservation, finalized)

    async def simulate_job_failure(
        self,
        *,
        principal: Principal,
        job_id: UUID,
        idempotency_key: UUID,
        request_id: UUID,
    ) -> CommandResult:
        require_actor_type(principal, ActorType.HUMAN)
        fingerprint = canonical_request_fingerprint(
            operation="demo_jobs.simulate_failure",
            path_parameters={"job_id": job_id},
            body={},
        )
        async with SqlAlchemyUnitOfWork(
            self._engine,
            principal=principal,
            trusted_scopes=_DEMO_INTERNAL_SCOPES,
        ) as uow:
            reservation = await uow.idempotency.reserve(
                principal=principal,
                operation="demo_jobs.simulate_failure",
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            replay = _replay_result(reservation)
            if replay is not None:
                await uow.rollback()
                return replay
            existing = await uow.jobs.get(job_id, for_update=True)
            if existing is None:
                return await self._complete_error(uow, reservation, ResourceNotFoundError())
            failed = await uow.jobs.simulate_failure(job_id=job_id)
            if failed is None:
                return await self._complete_error(uow, reservation, InvalidActionParametersError())
            await uow.events.append(
                incident_id=failed.incident_id,
                event_type="demo_job.failed_again",
                principal=principal,
                request_id=request_id,
                payload={
                    "job_id": str(failed.id),
                    "states": ["queued", "running", "failed"],
                    "job_version": failed.version,
                },
            )
            body = job_body(failed)
            await uow.idempotency.complete(
                record_id=reservation.record_id,
                response_status=200,
                response_body=body,
                resource_type="demo_job",
                resource_id=failed.id,
            )
            await uow.commit()
            return CommandResult(status_code=200, body=body)

    async def _mark_stale(
        self,
        uow: SqlAlchemyUnitOfWork,
        reservation: IdempotencyReservation,
        action: ActionRequestView,
        principal: Principal,
        request_id: UUID,
        outcome_code: str,
    ) -> CommandResult:
        stale = await uow.actions.finalize(
            action_id=action.id,
            status=ActionStatus.STALE.value,
            outcome_code=outcome_code,
            outcome_payload={"refetch": f"/api/v1/incidents/{action.incident_id}"},
            principal=principal,
        )
        if stale is None:
            raise RuntimeError("locked action could not be marked stale")
        await uow.events.append(
            incident_id=action.incident_id,
            action_request_id=action.id,
            event_type="action.stale",
            principal=principal,
            request_id=request_id,
            payload={"outcome_code": outcome_code},
        )
        return await self._complete_action(uow, reservation, stale)

    async def _complete_action(
        self,
        uow: SqlAlchemyUnitOfWork,
        reservation: IdempotencyReservation,
        action: ActionRequestView,
    ) -> CommandResult:
        body = action_body(action)
        await uow.idempotency.complete(
            record_id=reservation.record_id,
            response_status=200,
            response_body=body,
            resource_type="action_request",
            resource_id=action.id,
        )
        await uow.commit()
        return CommandResult(status_code=200, body=body)

    async def _complete_error(
        self,
        uow: SqlAlchemyUnitOfWork,
        reservation: IdempotencyReservation,
        error: IncidentOpsError,
    ) -> CommandResult:
        body = error_body(error)
        await uow.idempotency.complete(
            record_id=reservation.record_id,
            response_status=error.status_code,
            response_body=body,
        )
        await uow.commit()
        return CommandResult(status_code=error.status_code, body=body)


def serialize_action_page(page: Page) -> dict[str, object]:
    actions = tuple(item for item in page.items if isinstance(item, ActionRequestView))
    return {
        "items": [action_body(action) for action in actions],
        "next_cursor": page.next_cursor,
    }
