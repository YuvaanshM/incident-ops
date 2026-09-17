"""Short, fenced outbox transactions. HTTP never runs inside this adapter."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from incident_ops.application.auth import Principal
from incident_ops.application.outbox import OutboxClaim
from incident_ops.domain.delivery import DeliveryResult
from incident_ops.domain.enums import ActorType
from incident_ops.infrastructure.database.models import IncidentEventModel, OutboxEventModel
from incident_ops.infrastructure.database.session import (
    apply_principal_context,
    create_session_factory,
)

WORKER_PRINCIPAL = Principal(ActorType.SYSTEM, "slack-outbox-worker", frozenset({"outbox:deliver"}))


class SqlAlchemyOutboxDeliveryRepository:
    def __init__(self, engine: AsyncEngine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._sessions = create_session_factory(engine)
        self._clock = clock

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            await apply_principal_context(session, WORKER_PRINCIPAL)
            # Bound lock waits and statements independently of the HTTP attempt deadline.
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '10s'"))
            yield session

    async def _now(self, session: AsyncSession) -> datetime:
        if self._clock is not None:
            return self._clock()
        value = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(value, datetime)
        return value

    async def ready(self) -> bool:
        try:
            async with self._transaction() as session:
                await session.execute(select(OutboxEventModel.id).limit(1))
            return True
        except Exception:
            return False

    async def claim(self, *, batch_size: int, lease_seconds: float) -> tuple[OutboxClaim, ...]:
        if not 1 <= batch_size <= 20 or not 30 <= lease_seconds <= 600:
            raise ValueError("invalid claim bounds")
        claims = []
        async with self._transaction() as session:
            now = await self._now(session)
            rows = (
                await session.execute(
                    select(OutboxEventModel, IncidentEventModel)
                    .join(
                        IncidentEventModel,
                        OutboxEventModel.source_event_id == IncidentEventModel.id,
                    )
                    .where(
                        or_(
                            and_(
                                OutboxEventModel.status == "pending",
                                OutboxEventModel.available_at <= now,
                            ),
                            and_(
                                OutboxEventModel.status == "processing",
                                OutboxEventModel.lease_expires_at <= now,
                            ),
                        )
                    )
                    .order_by(OutboxEventModel.available_at, OutboxEventModel.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True, of=OutboxEventModel)
                )
            ).all()
            for row, source in rows:
                row.status = "processing"
                # Unique per claim, not per process: stale completions cannot match a new lease.
                row.locked_by = str(uuid4())
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                attempt = row.attempt_count + 1
                row.attempt_count = min(attempt, 25)
                row.updated_at = now
                claims.append(
                    OutboxClaim(
                        id=row.id,
                        source_event_id=source.id,
                        incident_id=source.incident_id,
                        action_id=source.action_request_id,
                        request_id=source.request_id,
                        event_type=row.event_type,
                        payload=dict(row.payload),
                        attempt_count=attempt,
                        lease_token=row.locked_by,
                        lease_expires_at=row.lease_expires_at,
                    )
                )
        return tuple(claims)

    async def finalize(
        self,
        claim: OutboxClaim,
        result: DeliveryResult,
        *,
        retry_delay: float | None,
    ) -> bool:
        if retry_delay is not None and (not result.retryable or not 0 <= retry_delay <= 3600):
            raise ValueError("invalid retry delay")
        async with self._transaction() as session:
            row = await session.scalar(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.id == claim.id,
                    OutboxEventModel.status == "processing",
                    OutboxEventModel.locked_by == claim.lease_token,
                )
                .with_for_update()
            )
            now = await self._now(session)
            if row is None or row.lease_expires_at is None or row.lease_expires_at <= now:
                return False
            delivered = result.code == "delivered"
            row.status = (
                "delivered"
                if delivered
                else "pending"
                if retry_delay is not None
                else "dead_letter"
            )
            row.last_error = None if delivered else result.code
            row.delivered_at = now if delivered else None
            row.locked_by = None
            row.lease_expires_at = None
            row.updated_at = now
            if retry_delay is not None:
                row.available_at = now + timedelta(seconds=retry_delay)
            await session.flush()
            if row.status in {"delivered", "dead_letter"}:
                await session.execute(
                    insert(IncidentEventModel)
                    .inline()
                    .values(
                        incident_id=claim.incident_id,
                        action_request_id=claim.action_id,
                        event_type="slack.delivered" if delivered else "slack.delivery_failed",
                        actor_type=WORKER_PRINCIPAL.actor_type.value,
                        actor_id=WORKER_PRINCIPAL.subject,
                        request_id=claim.request_id,
                        payload={
                            "outbox_event_id": str(claim.id),
                            "source_event_id": str(claim.source_event_id),
                            "attempt_count": row.attempt_count,
                            "result": result.code,
                        },
                    )
                )
        return True
