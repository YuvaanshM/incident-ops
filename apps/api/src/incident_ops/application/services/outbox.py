"""Deliver committed facts, then atomically record each leased attempt."""

import asyncio
import logging
from collections.abc import Callable

from incident_ops.application.outbox import (
    NotificationSender,
    OutboxClaim,
    OutboxDeliveryRepository,
)
from incident_ops.domain.delivery import DeliveryResult, RetryPolicy

logger = logging.getLogger("incident_ops.worker")


class OutboxWorker:
    def __init__(
        self,
        repository: OutboxDeliveryRepository,
        sender: NotificationSender,
        *,
        retry_policy: RetryPolicy,
        random_value: Callable[[], float],
        batch_size: int = 5,
        lease_seconds: float = 60,
        poll_seconds: float = 2,
    ) -> None:
        if not 1 <= batch_size <= 20 or not 1 <= poll_seconds <= 60 or lease_seconds < 30:
            raise ValueError("invalid worker bounds")
        self.repository = repository
        self.sender = sender
        self.retry_policy = retry_policy
        self.random_value = random_value
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds

    async def deliver(self, claim: OutboxClaim) -> None:
        # Crashed attempts count toward the same finite budget, even if no HTTP result was stored.
        if claim.attempt_count > self.retry_policy.max_attempts:
            result = DeliveryResult("attempts_exhausted")
        else:
            result = await self.sender.send(claim)
        delay = None
        if result.retryable and claim.attempt_count < self.retry_policy.max_attempts:
            delay = self.retry_policy.delay(
                claim.attempt_count,
                self.random_value(),
                result.retry_after,
            )
        finalized = await self.repository.finalize(claim, result, retry_delay=delay)
        logger.info(
            "outbox_attempt",
            extra={
                "event_id": str(claim.id),
                "source_event_id": str(claim.source_event_id),
                "request_id": str(claim.request_id),
                "attempt": claim.attempt_count,
                "result": result.code,
                "finalized": finalized,
            },
        )

    async def run_once(self) -> int:
        claims = await self.repository.claim(
            batch_size=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        # A bounded batch starts together so waiting siblings cannot exhaust their leases.
        async with asyncio.TaskGroup() as group:
            for claim in claims:
                group.create_task(self.deliver(claim))
        return len(claims)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                # Driver and provider exceptions can contain credentials: never log their text.
                logger.error("worker_iteration_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass
