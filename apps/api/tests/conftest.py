"""Shared test configuration."""

import hashlib
import hmac
import json
import os
import time

import pytest

from incident_ops.config import Settings
from incident_ops.infrastructure.slack_config import SlackIntakeSettings


@pytest.fixture
def slack_config() -> SlackIntakeSettings:
    return SlackIntakeSettings(
        enabled=True,
        signing_secret="synthetic" * 4,
        app_id="A123ABC",
        team_id="T123ABC",
        channels=[{"channel_id": "C123ABC", "service": "checkout"}],
    )


@pytest.fixture
def slack_event() -> dict:
    return {
        "type": "event_callback",
        "team_id": "T123ABC",
        "api_app_id": "A123ABC",
        "event_id": "Ev123ABC",
        "is_ext_shared_channel": False,
        "token": "deprecated-provider-token-must-never-be-stored",
        "event": {
            "type": "message",
            "user": "U123ABC",
            "channel": "C123ABC",
            "text": "Checkout errors\nSynthetic issue from the issues channel.",
            "ts": "1789668000.000001",
            "channel_type": "channel",
        },
    }


@pytest.fixture
def sign_slack(slack_config):
    def sign(payload, *, timestamp=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        stamp = str(int(time.time()) if timestamp is None else timestamp)
        digest = hmac.new(
            slack_config.signing_secret.get_secret_value().encode(),
            b"v0:" + stamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
        return body, {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": stamp,
            "X-Slack-Signature": "v0=" + digest,
        }

    return sign


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["testserver"],
    )


@pytest.fixture(scope="session")
def postgres_test_url() -> str:
    """Return an explicitly disposable PostgreSQL URL for integration tests."""

    configured_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if configured_url is None:
        configured_url = Settings().database_url.get_secret_value()  # type: ignore[call-arg]

    url_prefix, database_part = configured_url.rsplit("/", maxsplit=1)
    database_name, separator, query = database_part.partition("?")
    if not database_name.endswith("_test"):
        configured_url = f"{url_prefix}/{database_name}_test{separator}{query}"

    database_name = configured_url.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
    if not database_name.endswith("_test"):
        pytest.fail("integration tests refuse a database whose name does not end in '_test'")
    return configured_url
