"""Worker orchestration, finite crash budgets, and graceful shutdown."""

import asyncio
from dataclasses import replace

from test_slack import claim

from incident_ops.application.services.outbox import OutboxWorker
from incident_ops.domain.delivery import DeliveryResult, RetryPolicy


class Repository:
    def __init__(self):
        self.item = claim()
        self.calls = 0
        self.finalizations = []

    async def claim(self, **kwargs):
        self.calls += 1
        return (self.item,)

    async def finalize(self, item, result, *, retry_delay):
        self.finalizations.append((result, retry_delay))
        return True


class Sender:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def send(self, item):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return DeliveryResult("provider_unavailable", True)


def worker(repo, sender):
    return OutboxWorker(repo, sender, retry_policy=RetryPolicy(), random_value=lambda: 1)


async def test_shutdown_drains_current_batch_and_stops_claiming():
    repo, sender, stop = Repository(), Sender(), asyncio.Event()
    task = asyncio.create_task(worker(repo, sender).run(stop))
    await asyncio.wait_for(sender.started.wait(), 1)
    stop.set()
    assert not task.done()
    sender.release.set()
    await asyncio.wait_for(task, 1)
    assert repo.calls == 1
    assert repo.finalizations[0][1] == 5


async def test_final_attempt_dead_letters_and_crashed_budget_does_not_send():
    for attempt in (5, 6, 26):
        repo, sender = Repository(), Sender()
        repo.item = replace(repo.item, attempt_count=attempt)
        sender.release.set()
        await worker(repo, sender).run_once()
        assert repo.finalizations[0][1] is None
        assert sender.calls == (1 if attempt == 5 else 0)


async def test_stopped_worker_does_not_claim():
    repo, sender, stop = Repository(), Sender(), asyncio.Event()
    stop.set()
    await worker(repo, sender).run(stop)
    assert repo.calls == 0
