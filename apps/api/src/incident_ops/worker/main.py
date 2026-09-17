"""Run with python -m incident_ops.worker.main; --check probes database readiness."""

import argparse
import asyncio
import json
import logging
import secrets
import signal

from httpx2 import AsyncClient, Limits

from incident_ops.application.services.outbox import OutboxWorker
from incident_ops.domain.delivery import RetryPolicy
from incident_ops.infrastructure.database.outbox import SqlAlchemyOutboxDeliveryRepository
from incident_ops.infrastructure.database.session import create_database_engine
from incident_ops.infrastructure.slack import SlackWebhookSender
from incident_ops.worker.config import WorkerSettings


class SafeWorkerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            name: getattr(record, name)
            for name in (
                "event_id",
                "source_event_id",
                "request_id",
                "attempt",
                "result",
                "finalized",
            )
            if hasattr(record, name)
        }
        return json.dumps({"level": record.levelname, "message": record.getMessage(), **fields})


def configure_logging() -> None:
    # Only our allowlisted messages are emitted. HTTP libraries log full secret webhook URLs.
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.NullHandler())
    root.setLevel(logging.CRITICAL)
    for name in ("httpx2", "httpcore2", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    logger = logging.getLogger("incident_ops.worker")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(SafeWorkerFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


async def run(*, check: bool = False) -> int:
    logger = logging.getLogger("incident_ops.worker")
    try:
        settings = WorkerSettings()  # type: ignore[call-arg]
    except Exception:
        logger.error("worker_configuration_invalid")
        return 1
    engine = create_database_engine(settings.database_url)
    repository = SqlAlchemyOutboxDeliveryRepository(engine)
    try:
        if not await repository.ready():
            logger.error("worker_database_unavailable")
            return 1
        if check:
            return 0
        assert settings.slack_webhook_url is not None
        async with AsyncClient(
            trust_env=False,
            follow_redirects=False,
            limits=Limits(max_connections=settings.worker_batch_size, max_keepalive_connections=5),
        ) as client:
            sender = SlackWebhookSender(
                client,
                webhook_url=settings.slack_webhook_url,
                web_url=str(settings.web_public_url),
                connect_timeout=settings.slack_connect_timeout_seconds,
                read_timeout=settings.slack_read_timeout_seconds,
                total_timeout=settings.slack_total_timeout_seconds,
                max_retry_after=settings.worker_retry_max_seconds,
            )
            worker = OutboxWorker(
                repository,
                sender,
                retry_policy=RetryPolicy(
                    settings.worker_max_attempts,
                    settings.worker_retry_base_seconds,
                    settings.worker_retry_max_seconds,
                ),
                random_value=secrets.SystemRandom().random,
                batch_size=settings.worker_batch_size,
                lease_seconds=settings.worker_lease_seconds,
                poll_seconds=settings.worker_poll_seconds,
            )
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            logger.info("worker_started")
            try:
                await worker.run(stop)
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
            logger.info("worker_stopped")
        return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver committed incident notifications")
    parser.add_argument(
        "--check", action="store_true", help="Check configuration and database access"
    )
    args = parser.parse_args()
    configure_logging()
    try:
        code = asyncio.run(run(check=args.check))
    except KeyboardInterrupt:
        code = 0
    except Exception:
        logging.getLogger("incident_ops.worker").error("worker_stopped_unexpectedly")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
