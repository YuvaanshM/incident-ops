"""The real webhook adapter against deterministic local transports."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx2 import AsyncClient, MockTransport, ReadTimeout, Response
from pydantic import SecretStr, ValidationError

from incident_ops.application.outbox import OutboxClaim
from incident_ops.domain.delivery import RetryPolicy
from incident_ops.infrastructure.slack import (
    SlackWebhookSender,
    build_message,
    retry_after_seconds,
    sanitize_summary,
)
from incident_ops.worker.config import WorkerSettings
from incident_ops.worker.main import SafeWorkerFormatter


def claim() -> OutboxClaim:
    incident_id, source_id = uuid4(), uuid4()
    return OutboxClaim(
        id=uuid4(),
        source_event_id=source_id,
        incident_id=incident_id,
        action_id=None,
        request_id=uuid4(),
        event_type="slack.incident.created",
        payload={
            "incident_id": str(incident_id),
            "event_id": str(source_id),
            "severity": "high",
            "title": "<!channel> private credential and `instructions`",
        },
        attempt_count=1,
        lease_token=str(uuid4()),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )


@pytest.mark.parametrize(
    "status,body,code,retryable",
    [
        (200, "ok", "delivered", False),
        (200, " ok\n", "delivered", False),
        (200, "invalid body", "invalid_response", True),
        (200, "x" * 1000, "invalid_response", True),
        (204, "", "provider_rejected", False),
        (429, "ignored", "rate_limited", True),
        (500, "private", "provider_unavailable", True),
        (503, "private", "provider_unavailable", True),
        (408, "private", "provider_unavailable", True),
        (400, "private", "provider_rejected", False),
        (403, "private", "provider_rejected", False),
        (404, "private", "provider_rejected", False),
        (302, "private", "provider_rejected", False),
    ],
)
async def test_http_response_classification(status, body, code, retryable):
    requests = []

    def respond(request):
        requests.append(request)
        return Response(
            status,
            text=body,
            headers={"Retry-After": "999999", "Location": "http://invalid.example"},
        )

    async with AsyncClient(transport=MockTransport(respond)) as client:
        sender = SlackWebhookSender(
            client,
            webhook_url=SecretStr("http://localhost/mock"),
            web_url="https://console.example",
        )
        result = await sender.send(claim())
    assert (result.code, result.retryable) == (code, retryable)
    assert len(requests) == 1
    assert requests[0].extensions["timeout"]["connect"] == 3
    assert requests[0].extensions["timeout"]["read"] == 10
    if status == 429:
        assert result.retry_after == 300
    assert "private" not in repr(result)


async def test_network_timeout_and_total_deadline():
    def fail(request):
        raise ReadTimeout("private-url-and-credentials", request=request)

    async def hang(request):
        await asyncio.Event().wait()

    for handler in (fail, hang):
        async with AsyncClient(transport=MockTransport(handler)) as client:
            result = await SlackWebhookSender(
                client,
                webhook_url=SecretStr("http://localhost/mock"),
                web_url="https://console.example",
                total_timeout=0.02,
            ).send(claim())
        assert result.code == "network_error"
        assert "private" not in repr(result)


def test_messages_use_stable_references_and_never_forward_free_text():
    item = claim()
    message = build_message(item, "https://console.example/")
    serialized = json.dumps(message)
    assert str(item.id) in serialized and str(item.source_event_id) in serialized
    assert f"https://console.example/incidents/{item.incident_id}" in serialized
    assert "private" not in serialized and "<!channel>" not in serialized
    assert message["mrkdwn"] is False
    action_id = uuid4()
    action = replace(
        item,
        event_type="slack.action.human_required",
        action_id=action_id,
        payload={
            "incident_id": str(item.incident_id),
            "event_id": str(item.source_event_id),
            "action_id": str(action_id),
            "action_type": "retry_failed_job",
            "severity": "critical",
        },
    )
    assert f"/approvals/{action_id}" in json.dumps(build_message(action, "https://console.example"))
    assert "critical" in json.dumps(build_message(action, "https://console.example"))
    # Pre-worker pending facts did not carry a severity snapshot.
    legacy = dict(action.payload)
    legacy.pop("severity")
    assert "not recorded" in json.dumps(
        build_message(replace(action, payload=legacy), "https://console.example")
    )


def test_monitor_notifications_show_safe_context_without_leaking_secrets():
    item = claim()
    monitor = replace(
        item,
        payload={
            **item.payload,
            "title": "Checkout health check failing",
            "service": "checkout",
            "monitoring_source": "monitoring-demo-prod",
        },
    )
    message = build_message(monitor, "https://console.example")
    rendered = json.dumps(message)
    assert "Checkout health check failing" in rendered
    assert "checkout" in rendered and "monitoring-demo-prod" in rendered
    assert "high" in rendered
    assert f"/incidents/{item.incident_id}" in rendered
    assert message["mrkdwn"] is False

    unsafe = replace(
        monitor,
        payload={**monitor.payload, "title": "Bearer sk-verysecretvalue @channel"},
    )
    rendered = json.dumps(build_message(unsafe, "https://console.example"))
    assert "Monitoring alert" in rendered
    assert "verysecretvalue" not in rendered and "@channel" not in rendered

    review = replace(
        item,
        event_type="slack.alert.severity_review_required",
        payload={
            "incident_id": str(item.incident_id),
            "event_id": str(item.source_event_id),
            "alert_id": str(uuid4()),
            "stored_severity": "low",
            "reported_severity": "high",
            "monitoring_source": "monitoring-demo-prod",
            "service": "checkout",
        },
    )
    rendered = json.dumps(build_message(review, "https://console.example"))
    assert "Monitoring severity review required" in rendered
    assert "incident severity remains low" in rendered


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "execute_anything"},
        {"incident_id": uuid4()},
        {"source_event_id": uuid4()},
        {"payload": {"incident_id": "invalid"}},
    ],
)
async def test_invalid_events_never_call_webhook(changes):
    def unexpected(request):
        pytest.fail("invalid payload reached network")

    async with AsyncClient(transport=MockTransport(unexpected)) as client:
        sender = SlackWebhookSender(
            client,
            webhook_url=SecretStr("http://localhost/mock"),
            web_url="https://console.example",
        )
        assert (await sender.send(replace(claim(), **changes))).code == "invalid_payload"


def test_payload_rejects_unknown_fields_and_type_coercion():
    item = claim()
    for field, value in (("description", "private"), ("severity", 3), ("title", "x" * 201)):
        with pytest.raises(ValidationError):
            build_message(
                replace(item, payload={**item.payload, field: value}), "https://console.example"
            )


def test_sanitizer_neutralizes_slack_markup_and_mentions():
    result = sanitize_summary("<!channel> <@U123> @here & *bold* `code` _underline_ \n next")
    assert "<" not in result and "@" not in result and "`" not in result and "*" not in result
    assert "&lt;" in result and "\n" not in result
    assert len(sanitize_summary("x" * 1000)) == 300


@pytest.mark.parametrize(
    "value,expected",
    [
        ("17", 17),
        ("99999999", 300),
        ("-1", None),
        ("nan", None),
        ("1e3", None),
        ("x" * 1000, None),
        (None, None),
    ],
)
def test_bounded_retry_after(value, expected):
    assert retry_after_seconds(value, 300) == expected


def test_retry_policy_is_deterministic_and_bounded():
    policy = RetryPolicy()
    assert [policy.delay(i, 1) for i in range(1, 6)] == [5, 10, 20, 40, 80]
    assert policy.delay(1, 0) == 2.5
    assert policy.delay(2, 0.5, 19) == 19
    assert policy.delay(25, 1, 10000) == 300
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=26)


def test_worker_config_requires_valid_secret_and_bounds():
    base = {
        "_env_file": None,
        "database_url": "postgresql://test:test@localhost/test",
        "app_env": "test",
        "slack_webhook_url": "http://localhost/mock",
    }
    assert WorkerSettings(**base).worker_max_attempts == 5
    for values in (
        {"slack_webhook_url": None},
        {"slack_webhook_url": "https://evil.example/private"},
        {"slack_webhook_url": "http://localhost/mock?secret=private"},
        {"worker_max_attempts": 26},
        {"worker_poll_seconds": 0},
        {"worker_lease_seconds": 30, "slack_total_timeout_seconds": 45},
        {"worker_retry_base_seconds": 50, "worker_retry_max_seconds": 10},
        {"worker_poll_seconds": float("nan")},
    ):
        with pytest.raises(ValidationError) as error:
            WorkerSettings(**{**base, **values})
        assert "private" not in str(error.value)
    production = {
        **base,
        "app_env": "production",
        "api_public_url": "https://api.example",
        "web_public_url": "https://console.example",
        "oidc_issuer_url": "https://auth.example",
        "oidc_jwks_url": "https://auth.example/jwks",
        "oidc_audience": "demo",
    }
    with pytest.raises(ValidationError):
        WorkerSettings(**production)


def test_log_formatter_ignores_unapproved_extra_and_exception_fields():
    import logging

    record = logging.LogRecord(
        "incident_ops.worker", logging.INFO, "", 1, "outbox_attempt", (), None
    )
    record.event_id = "event-id"
    record.secret = "private"
    record.exc_info = (ValueError, ValueError("private"), None)
    output = SafeWorkerFormatter().format(record)
    assert "event-id" in output and "private" not in output


async def test_real_local_webhook_500_429_200_sequence():
    """Exercise HTTP serialization and status parsing over a loopback socket."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    statuses = iter((500, 429, 200))
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            status = next(statuses)
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            if status == 429:
                self.send_header("Retry-After", "7")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with AsyncClient(trust_env=False) as client:
            sender = SlackWebhookSender(
                client,
                webhook_url=SecretStr(f"http://127.0.0.1:{server.server_port}/mock"),
                web_url="https://console.example",
            )
            item = claim()
            results = [await sender.send(item) for _ in range(3)]
        assert [result.code for result in results] == [
            "provider_unavailable",
            "rate_limited",
            "delivered",
        ]
        assert results[1].retry_after == 7
        assert received[0] == received[1] == received[2]
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=1)
