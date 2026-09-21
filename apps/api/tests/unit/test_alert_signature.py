"""Generic alert webhook authentication derives one narrow source principal."""

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import WebhookAuthenticationError
from incident_ops.infrastructure.auth.alert_webhook import AlertWebhookVerifier
from incident_ops.infrastructure.monitoring_config import AlertIntakeSettings

NOW = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)
PATH = "/api/v1/alerts/webhook"
KEY = UUID("10000000-0000-4000-8000-000000000001")
BODY = b'{"schema_version":1}'
PRIMARY_SECRET = "synthetic-primary-signing-secret-123"


def settings(*, enabled: bool = True, keys: list[dict[str, object]] | None = None):
    return AlertIntakeSettings(
        enabled=enabled,
        sources=[
            {
                "source": "monitoring-demo-prod",
                "services": ["checkout", "payments"],
                "keys": keys
                or [
                    {
                        "key_id": "demo-primary",
                        "signing_secret": "synthetic-primary-signing-secret-123",
                    }
                ],
            }
        ],
    )


def signature(
    *,
    secret: str = PRIMARY_SECRET,
    key_id: str = "demo-primary",
    timestamp: str = "1790006400",
    idempotency_key: str = str(KEY),
    body: bytes = BODY,
) -> str:
    base = f"v1\nPOST\n{PATH}\n{key_id}\n{timestamp}\n{idempotency_key}\n".encode() + body
    return "v1=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verifier(config: AlertIntakeSettings | None = None) -> AlertWebhookVerifier:
    return AlertWebhookVerifier(config or settings(), clock=lambda: NOW.timestamp())


def test_valid_signature_derives_only_source_bound_ingestion_authority() -> None:
    verified = verifier().verify(
        key_id="demo-primary",
        timestamp="1790006400",
        idempotency_key=str(KEY),
        signature=signature(),
        body=BODY,
    )

    assert verified.principal.actor_type is ActorType.SYSTEM
    assert verified.principal.subject == "monitoring:monitoring-demo-prod"
    assert verified.principal.scopes == frozenset({"alerts:ingest"})
    assert verified.source == "monitoring-demo-prod"
    assert verified.services == frozenset({"checkout", "payments"})
    assert verified.idempotency_key == KEY


def test_rotated_keys_keep_one_source_identity() -> None:
    config = settings(
        keys=[
            {
                "key_id": "demo-old",
                "signing_secret": "synthetic-old-signing-secret-12345",
            },
            {
                "key_id": "demo-new",
                "signing_secret": "synthetic-new-signing-secret-12345",
            },
        ]
    )

    identities = {
        verifier(config)
        .verify(
            key_id=key_id,
            timestamp="1790006400",
            idempotency_key=str(KEY),
            signature=signature(key_id=key_id, secret=secret),
            body=BODY,
        )
        .principal.subject
        for key_id, secret in (
            ("demo-old", "synthetic-old-signing-secret-12345"),
            ("demo-new", "synthetic-new-signing-secret-12345"),
        )
    }

    assert identities == {"monitoring:monitoring-demo-prod"}


@pytest.mark.parametrize("skew", [-301, -300, 0, 300, 301])
def test_delivery_clock_accepts_only_five_minute_window(skew: int) -> None:
    timestamp = str(int(NOW.timestamp()) + skew)
    call = {
        "key_id": "demo-primary",
        "timestamp": timestamp,
        "idempotency_key": str(KEY),
        "signature": signature(timestamp=timestamp),
        "body": BODY,
    }

    if abs(skew) > 300:
        with pytest.raises(WebhookAuthenticationError):
            verifier().verify(**call)
    else:
        verifier().verify(**call)


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("key_id", "unknown-key"),
        ("key_id", "x" * 101),
        ("timestamp", "not-a-time"),
        ("idempotency_key", "not-a-uuid"),
        ("signature", "v1=" + "A" * 64),
        ("signature", "v2=" + "0" * 64),
        ("body", BODY + b" "),
    ],
)
def test_ambiguous_or_modified_authentication_input_is_denied(
    override: str, value: str | bytes
) -> None:
    call: dict[str, str | bytes] = {
        "key_id": "demo-primary",
        "timestamp": "1790006400",
        "idempotency_key": str(KEY),
        "signature": signature(),
        "body": BODY,
    }
    call[override] = value

    with pytest.raises(WebhookAuthenticationError):
        verifier().verify(**call)  # type: ignore[arg-type]


def test_disabled_intake_denies_a_valid_signature() -> None:
    with pytest.raises(WebhookAuthenticationError):
        verifier(settings(enabled=False)).verify(
            key_id="demo-primary",
            timestamp="1790006400",
            idempotency_key=str(KEY),
            signature=signature(),
            body=BODY,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"sources": []},
        {
            "sources": [
                {
                    "source": "monitoring-demo-prod",
                    "services": ["*"],
                    "keys": [
                        {
                            "key_id": "demo-primary",
                            "signing_secret": "synthetic-primary-signing-secret-123",
                        }
                    ],
                }
            ]
        },
        {
            "sources": [
                {
                    "source": "monitoring-demo-prod",
                    "services": ["checkout"],
                    "keys": [
                        {"key_id": "duplicate", "signing_secret": "synthetic-secret-one-123456789"},
                        {"key_id": "duplicate", "signing_secret": "synthetic-secret-two-123456789"},
                    ],
                }
            ]
        },
    ],
)
def test_enabled_configuration_requires_explicit_unique_bindings(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AlertIntakeSettings.model_validate({"enabled": True, **override})


def test_signing_secrets_are_redacted_from_repr_and_validation_errors() -> None:
    secret = "synthetic-primary-signing-secret-123"
    config = settings()
    assert secret not in repr(config)

    with pytest.raises(ValidationError) as raised:
        settings(keys=[{"key_id": "demo-primary", "signing_secret": "sensitive-short"}])

    assert "sensitive-short" not in str(raised.value)
