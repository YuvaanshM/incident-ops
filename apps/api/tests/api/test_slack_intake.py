"""An unauthenticated provider envelope must never reach application ingestion."""

import asyncio
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from incident_ops.application.services.slack_intake import IntakeResult, slack_principal
from incident_ops.infrastructure.rate_limit import FixedWindowRateLimiter
from incident_ops.main import create_app

PATH = "/api/v1/integrations/slack/events"


class RecordingIntake:
    def __init__(self):
        self.calls = []

    async def ingest(self, **kwargs):
        self.calls.append(kwargs)
        return IntakeResult(replayed=False)


@pytest.fixture
def slack_client(test_settings, slack_config):
    app = create_app(test_settings.model_copy(update={"slack_intake": slack_config}))
    app.state.slack_intake_service = RecordingIntake()
    return TestClient(app, base_url="https://testserver")


def post(client, sign, payload, **headers):
    body, signed = sign(payload)
    return client.post(PATH, content=body, headers={**signed, **headers})


def test_disabled_is_closed_even_with_valid_signature(test_settings, sign_slack, slack_event):
    client = TestClient(create_app(test_settings), base_url="https://testserver")
    assert post(client, sign_slack, slack_event).status_code == 404


def test_signed_human_message_derives_only_source_bound_principal(
    slack_client, sign_slack, slack_event
):
    slack_event["event"].update(actor_type="human", scopes=["actions:approve"], severity="critical")
    response = post(slack_client, sign_slack, slack_event)
    assert response.status_code == 200
    call = slack_client.app.state.slack_intake_service.calls[0]
    assert call["principal"].actor_type.value == "system"
    assert call["principal"].subject == "slack:T123ABC:C123ABC"
    assert call["principal"].scopes == frozenset({"slack:ingest"})
    assert call["issue"].text == slack_event["event"]["text"]
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("header", ["X-Slack-Signature", "X-Slack-Request-Timestamp"])
def test_missing_signature_headers_denied(slack_client, sign_slack, slack_event, header):
    body, headers = sign_slack(slack_event)
    del headers[header]
    assert slack_client.post(PATH, content=body, headers=headers).status_code == 401
    assert not slack_client.app.state.slack_intake_service.calls


@pytest.mark.parametrize(
    "value", ["", "v1=" + "a" * 64, "v0=" + "A" * 64, "v0=" + "0" * 64, "x" * 1000]
)
def test_invalid_signatures_denied(slack_client, sign_slack, slack_event, value):
    assert (
        post(slack_client, sign_slack, slack_event, **{"X-Slack-Signature": value}).status_code
        == 401
    )
    assert not slack_client.app.state.slack_intake_service.calls


def test_changed_raw_bytes_rejected_before_json_parser(slack_client, sign_slack, slack_event):
    body, headers = sign_slack(slack_event)
    assert slack_client.post(PATH, content=body + b" ", headers=headers).status_code == 401
    assert slack_client.post(PATH, content=b"not json", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "header", ["X-Slack-Signature", "X-Slack-Request-Timestamp", "Content-Type"]
)
def test_duplicate_security_headers_rejected(slack_client, sign_slack, slack_event, header):
    body, headers = sign_slack(slack_event)
    response = slack_client.post(
        PATH, content=body, headers=[*headers.items(), (header, headers[header])]
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer human-demo"},
        {"Authorization": "Bearer agent-demo"},
        {"Cookie": "session=synthetic"},
        {"X-Slack-Request-Timestamp": "NaN"},
    ],
)
def test_no_session_bearer_or_invalid_timestamp_bypass(
    slack_client, sign_slack, slack_event, headers
):
    assert post(slack_client, sign_slack, slack_event, **headers).status_code == 401
    assert not slack_client.app.state.slack_intake_service.calls


def test_signed_url_challenge_is_verified_before_echo(slack_client, sign_slack):
    payload = {"type": "url_verification", "challenge": "synthetic-challenge", "token": "untrusted"}
    assert post(slack_client, sign_slack, payload).json() == {"challenge": "synthetic-challenge"}
    assert slack_client.post(PATH, json=payload).status_code == 401
    assert not slack_client.app.state.slack_intake_service.calls


@pytest.mark.parametrize(
    "change",
    [
        {"team_id": "TOTHER"},
        {"api_app_id": "AOTHER"},
        {"is_ext_shared_channel": True},
        {"context_team_id": "TOTHER"},
    ],
)
def test_wrong_installation_or_shared_channel_rejected(
    slack_client, sign_slack, slack_event, change
):
    slack_event.update(change)
    assert post(slack_client, sign_slack, slack_event).status_code == 403
    assert not slack_client.app.state.slack_intake_service.calls


@pytest.mark.parametrize("change", [{"channel": "COTHER"}, {"team": "TOTHER"}])
def test_wrong_message_source_rejected(slack_client, sign_slack, slack_event, change):
    slack_event["event"].update(change)
    assert post(slack_client, sign_slack, slack_event).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"bot_id": "B123ABC"},
        {"app_id": "A123ABC"},
        {"subtype": "bot_message"},
        {"subtype": "message_changed"},
        {"subtype": "message_deleted"},
        {"subtype": "thread_broadcast"},
        {"thread_ts": "1789668000.000001"},
        {"hidden": True},
        {"type": "reaction_added"},
        {"channel_type": "im"},
    ],
)
def test_loops_replies_and_changes_are_acknowledged_without_ingestion(
    slack_client, sign_slack, slack_event, change
):
    slack_event["event"].update(change)
    assert post(slack_client, sign_slack, slack_event).status_code == 200
    assert not slack_client.app.state.slack_intake_service.calls


@pytest.mark.parametrize(
    "body",
    [
        b'{"type":"url_verification","type":"event_callback"}',
        b"[]",
        b"null",
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"\xff",
        b'{"x":"\\ud800"}',
        b'{"x":"\\u0000"}',
        b"[" * 30 + b"0" + b"]" * 30,
    ],
)
def test_invalid_json_is_generic_and_not_ingested(slack_client, sign_slack, body):
    response = post(slack_client, sign_slack, body)
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "invalid_webhook", "message": "The webhook request is invalid."}
    }
    assert not slack_client.app.state.slack_intake_service.calls


@pytest.mark.parametrize(
    "change", [{"text": " "}, {"text": "x" * 5001}, {"user": None}, {"ts": 123}]
)
def test_invalid_messages_rejected(slack_client, sign_slack, slack_event, change):
    slack_event["event"].update(change)
    assert post(slack_client, sign_slack, slack_event).status_code == 400


def test_body_limit_and_no_compression(slack_client, sign_slack, slack_event):
    assert post(slack_client, sign_slack, b"x" * 65537).status_code == 413
    body, headers = sign_slack(b"x" * 65537)
    # No Content-Length: enforce the limit while consuming streamed chunks too.
    assert (
        slack_client.post(PATH, content=iter([body[:100], body[100:]]), headers=headers).status_code
        == 413
    )
    assert (
        post(slack_client, sign_slack, slack_event, **{"Content-Encoding": "gzip"}).status_code
        == 400
    )


def test_untrusted_forwarded_headers_cannot_supply_https(slack_client, sign_slack, slack_event):
    body, headers = sign_slack(slack_event)
    response = slack_client.post(
        "http://testserver" + PATH,
        content=body,
        headers={**headers, "X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 401


def test_request_logs_never_contain_provider_secrets_or_text(
    slack_client, sign_slack, slack_event, caplog
):
    body, headers = sign_slack(slack_event)
    with caplog.at_level(logging.INFO, logger="incident_ops.http"):
        assert slack_client.post(PATH, content=body, headers=headers).status_code == 200
    records = repr(
        [record.__dict__ for record in caplog.records if record.name == "incident_ops.http"]
    )
    assert slack_event["token"] not in records
    assert slack_event["event"]["text"] not in records
    assert headers["X-Slack-Signature"] not in records


def test_source_rate_limit_and_global_unverified_budget(slack_client, sign_slack, slack_event):
    slack_client.app.state.slack_source_limiter = FixedWindowRateLimiter(
        human_limit=1, agent_limit=1
    )
    assert post(slack_client, sign_slack, slack_event).status_code == 200
    assert post(slack_client, sign_slack, slack_event).status_code == 429
    slack_client.app.state.slack_ingress_limiter = FixedWindowRateLimiter(
        human_limit=1, agent_limit=1
    )
    assert slack_client.post(PATH, content=b"bad").status_code == 401
    assert slack_client.post(PATH, content=b"bad").status_code == 429


def test_database_error_is_retryable_without_details(slack_client, sign_slack, slack_event):
    class Unavailable:
        async def ingest(self, **kwargs):
            raise OperationalError("private statement", {}, Exception("private credentials"))

    slack_client.app.state.slack_intake_service = Unavailable()
    response = post(slack_client, sign_slack, slack_event)
    assert response.status_code == 503
    assert "private" not in response.text


def test_slow_storage_is_not_acknowledged_before_commit(slack_client, sign_slack, slack_event):
    class Slow:
        async def ingest(self, **kwargs):
            await asyncio.sleep(10)
            raise AssertionError("deadline did not cancel intake")

    slack_client.app.state.slack_intake_service = Slow()
    assert post(slack_client, sign_slack, slack_event).status_code == 503


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/api/v1/incidents", None),
        (
            "POST",
            "/api/v1/incidents",
            {
                "title": "Synthetic",
                "description": "Synthetic",
                "service": "checkout",
                "severity": "low",
            },
        ),
        (
            "PATCH",
            "/api/v1/incidents/00000000-0000-4000-8000-000000000001",
            {"expected_version": 1, "status": "closed"},
        ),
        ("POST", "/api/v1/actions/00000000-0000-4000-8000-000000000001/approve", None),
        (
            "POST",
            "/api/v1/incidents/00000000-0000-4000-8000-000000000001/actions",
            {
                "expected_incident_version": 1,
                "action_type": "request_status_update",
                "parameters": {"target_status": "investigating"},
                "rationale": "Synthetic",
            },
        ),
    ],
)
def test_source_principal_has_no_human_or_action_route_authority(test_settings, method, path, body):
    class SourceVerifier:
        async def verify(self, token):
            return slack_principal("T123ABC", "C123ABC")

    client = TestClient(create_app(test_settings, token_verifier=SourceVerifier()))
    response = client.request(
        method,
        path,
        json=body,
        headers={"Authorization": "Bearer synthetic", "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 403
