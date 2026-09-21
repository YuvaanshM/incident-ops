"""Creation-only source authorization and durable Slack delivery/message deduplication."""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.application.auth import Principal
from incident_ops.application.idempotency import canonical_request_fingerprint
from incident_ops.application.ports import CommandObserver
from incident_ops.application.services.incidents import (
    NullCommandObserver,
    create_incident_in_transaction,
)
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthorizationDeniedError
from incident_ops.domain.source_issues import ChannelIssue
from incident_ops.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from incident_ops.infrastructure.slack_config import SlackIntakeSettings


def slack_principal(team_id: str, channel_id: str) -> Principal:
    return Principal(
        actor_type=ActorType.SYSTEM,
        subject=f"slack:{team_id}:{channel_id}",
        scopes=frozenset({"slack:ingest"}),
    )


@dataclass(frozen=True, slots=True)
class IntakeResult:
    replayed: bool


class SlackIntakeService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: SlackIntakeSettings,
        *,
        observer: CommandObserver | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._observer = observer or NullCommandObserver()

    async def ingest(
        self, *, principal: Principal, issue: ChannelIssue, request_id: UUID
    ) -> IntakeResult:
        binding = self._settings.channel(issue.channel_id)
        if (
            not self._settings.enabled
            or issue.team_id != self._settings.team_id
            or issue.app_id != self._settings.app_id
            or binding is None
            or principal != slack_principal(issue.team_id, issue.channel_id)
            or not issue.text.strip()
            or len(issue.text) > 5000
        ):
            raise AuthorizationDeniedError()
        fingerprint = canonical_request_fingerprint(
            operation="slack.message",
            path_parameters=issue.source_fields(),
            body={"text": issue.text},
        )
        async with SqlAlchemyUnitOfWork(self._engine, principal=principal) as uow:
            # Transaction-local source bindings are server-owned, never request fields.
            await uow.session.execute(
                text(
                    "SELECT set_config('app.slack_service', :service, true), "
                    "set_config('app.slack_severity', :severity, true), "
                    "set_config('lock_timeout', '1000ms', true), "
                    "set_config('statement_timeout', '1500ms', true)"
                ),
                {"service": binding.service, "severity": binding.severity.value},
            )
            await self._observer.reached("slack.before_reserve")
            delivery = await uow.idempotency.reserve(
                principal=principal,
                operation="slack.delivery",
                key=uuid5(NAMESPACE_URL, f"slack:{issue.app_id}:{issue.team_id}:{issue.event_id}"),
                fingerprint=fingerprint,
            )
            if not delivery.created:
                await uow.rollback()
                return IntakeResult(replayed=True)
            # A second event ID for the same immutable message also cannot create twice.
            message = await uow.idempotency.reserve(
                principal=principal,
                operation="slack.message",
                key=uuid5(
                    NAMESPACE_URL, f"slack:{issue.team_id}:{issue.channel_id}:{issue.message_ts}"
                ),
                fingerprint=fingerprint,
            )
            if message.created:
                incident = await create_incident_in_transaction(
                    uow,
                    principal=principal,
                    request_id=request_id,
                    title=issue.text.strip().splitlines()[0][:200],
                    description=issue.text.strip(),
                    service=binding.service,
                    severity=binding.severity,
                    observer=self._observer,
                    source={**issue.source_fields(), "event_id": issue.event_id},
                )
                await uow.idempotency.complete(
                    record_id=message.record_id,
                    response_status=200,
                    response_body={"ok": True},
                    resource_type="incident",
                    resource_id=incident.id,
                )
                resource_id = incident.id
            else:
                if message.resource_id is None:
                    raise RuntimeError("incomplete Slack message receipt")
                resource_id = message.resource_id
            await uow.idempotency.complete(
                record_id=delivery.record_id,
                response_status=200,
                response_body={"ok": True},
                resource_type="incident",
                resource_id=resource_id,
            )
            await self._observer.reached("slack.before_commit")
            await uow.commit()
            return IntakeResult(replayed=not message.created)
