"""Narrow adapter over the existing authorized incident/action application services."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal
from incident_ops.application.dto import AlertEventView, IncidentEventView
from incident_ops.application.investigation import Decision, Evidence, Observation, ReadTool
from incident_ops.application.retrieval import RetrievalRequest, _bounded_plain_text
from incident_ops.application.services.actions import ActionCommandService
from incident_ops.application.services.alert_ingestion import AlertQueryService
from incident_ops.application.services.incidents import IncidentQueryService
from incident_ops.application.services.retrieval import RetrievalService
from incident_ops.domain.enums import IncidentStatus
from incident_ops.domain.errors import RetrievalUnavailableError, VersionConflictError
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


class InvestigationToolAdapter:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        principal: Principal,
        incident_id: UUID,
        expected_version: int,
        deadline: datetime,
        action_key: UUID,
        request_id: UUID,
        verify: Callable[[], Awaitable[None]],
        retrieval: RetrievalService,
        actions: ActionCommandService,
    ) -> None:
        self._engine = engine
        self._principal = principal
        self._incident_id = incident_id
        self._version = expected_version
        self._deadline = deadline
        self._action_key = action_key
        self._request_id = request_id
        self._verify = verify
        self._retrieval = retrieval
        self._actions = actions
        self._incidents = IncidentQueryService(engine)
        self._alerts = AlertQueryService(engine)

    async def check(self) -> None:
        await self._verify()
        async with SqlAlchemyUnitOfWork(self._engine, principal=self._principal) as uow:
            if await uow.incidents.database_now() >= self._deadline:
                raise TimeoutError()
            current = await uow.incidents.get(self._incident_id)
            if current is None or current.version != self._version:
                raise VersionConflictError()

    async def observe(self, name: ReadTool) -> Observation:
        evidence: list[Evidence] = []
        jobs: list[UUID] = []
        truncated = False
        if name == "incident":
            incident = await self._incidents.get_incident(
                principal=self._principal, incident_id=self._incident_id
            )
            evidence.append(
                Evidence(
                    source_id=f"incident:{incident.id}",
                    text=_bounded_plain_text(
                        f"{incident.title}. {incident.description}\nService: {incident.service}; "
                        f"severity: {incident.severity}; status: {incident.status}; "
                        f"version: {incident.version}; "
                        f"monitoring: {incident.monitoring_status or 'unknown'}.",
                        1000,
                    ),
                )
            )
            failed = [job for job in incident.jobs if job.status == "failed"]
            jobs = [job.id for job in failed[:3]]
            truncated = len(failed) > 3
            evidence.extend(
                Evidence(
                    source_id=f"job:{job.id}",
                    text=_bounded_plain_text(
                        f"Failed job: {job.name}; retries: {job.retry_count}; "
                        f"version: {job.version}.",
                        200,
                    ),
                )
                for job in failed[:3]
            )
        elif name == "timeline":
            page = await self._incidents.get_timeline(
                principal=self._principal, incident_id=self._incident_id, cursor=None, limit=5
            )
            evidence = [
                Evidence(
                    source_id=f"event:{event.id}",
                    text=f"{event.occurred_at.isoformat()}: {event.event_type}",
                )
                for event in page.items
                if isinstance(event, IncidentEventView)
            ]
            truncated = page.next_cursor is not None
        elif name == "alerts":
            page = await self._alerts.for_incident(
                principal=self._principal, incident_id=self._incident_id, cursor=None, limit=5
            )
            evidence = [
                Evidence(
                    source_id=f"alert:{event.alert_id}:{event.id}",
                    text=_bounded_plain_text(
                        f"{event.title}; status: {event.status}; severity: {event.severity}; "
                        f"event version: {event.event_version}; outcome: {event.outcome}.",
                        300,
                    ),
                )
                for event in page.items
                if isinstance(event, AlertEventView)
            ]
            truncated = page.next_cursor is not None
        elif name == "precedent":
            try:
                result = await self._retrieval.suggest(
                    principal=self._principal,
                    incident_id=self._incident_id,
                    request=RetrievalRequest(),
                )
            except RetrievalUnavailableError:
                return Observation(status="unavailable", evidence=[], job_ids=[])
            evidence = [
                Evidence(
                    source_id=f"source:{item.source_incident_id}:v{item.source_version}",
                    text=_bounded_plain_text(
                        f"Historical synthetic precedent (version {item.source_version}): "
                        f"{item.title}. {item.resolution_summary}",
                        600,
                    ),
                )
                for item in result.suggestions
            ]
        return Observation(
            status="ok" if evidence else "empty",
            evidence=evidence,
            job_ids=jobs,
            truncated=truncated,
        )

    async def request_action(self, command: Decision) -> dict[str, object]:
        await self.check()
        parameters: dict[str, object]
        if command.tool == "request_retry_failed_job" and command.job_id is not None:
            action_type, parameters = "retry_failed_job", {"job_id": command.job_id}
        elif command.tool == "request_status_update" and command.target_status is not None:
            action_type, parameters = (
                "request_status_update",
                {"target_status": IncidentStatus(command.target_status)},
            )
        else:
            raise ValueError("unsupported investigation action")
        result = await self._actions.request_action(
            principal=self._principal,
            incident_id=self._incident_id,
            expected_incident_version=self._version,
            idempotency_key=self._action_key,
            request_id=self._request_id,
            action_type=action_type,
            parameters=parameters,
            rationale="Optional investigation request; review cited evidence. Sources: "
            + ", ".join(f.source_id for f in command.findings),
        )
        if result.status_code >= 400:
            return {"status": "not_applied", "http_status": result.status_code}
        return {
            key: result.body[key]
            for key in ("id", "status", "action_type", "outcome_code")
            if key in result.body
        }
