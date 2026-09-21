"""Normalized monitoring alerts are strict immutable source observations."""

import json
from datetime import UTC, datetime

import pytest

from incident_ops.domain.alerts import AlertStatus
from incident_ops.domain.errors import InvalidWebhookError
from incident_ops.infrastructure.monitoring.generic import GenericAlertAdapter

NOW = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)


def payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "source": "monitoring-demo-prod",
        "external_alert_id": "checkout-health",
        "service": "checkout",
        "severity": "high",
        "status": "ACTIVE",
        "title": "Checkout health check failing",
        "description": "Synthetic monitor received three HTTP 503 responses.",
        "started_at": "2026-09-21T11:55:00-04:00",
        "resolved_at": None,
        "event_version": 1,
        "metadata": {"environment": "demo", "region": "us-east"},
    }
    value.update(overrides)
    return value


def normalize(value: dict[str, object]):
    return GenericAlertAdapter(clock=lambda: NOW).normalize(
        json.dumps(value, separators=(",", ":")).encode()
    )


def test_active_payload_normalizes_to_an_immutable_firing_alert() -> None:
    alert = normalize(payload())

    assert alert.status is AlertStatus.FIRING
    assert alert.started_at == datetime(2026, 9, 21, 15, 55, tzinfo=UTC)
    assert alert.logical_identity == ("monitoring-demo-prod", "checkout-health", "checkout")
    assert alert.metadata == {"environment": "demo", "region": "us-east"}
    with pytest.raises(TypeError):
        alert.metadata["environment"] = "production"  # type: ignore[index]


def test_resolution_requires_a_matching_ordered_timestamp() -> None:
    alert = normalize(
        payload(
            status="RESOLVED",
            resolved_at="2026-09-21T15:59:00Z",
            event_version=2,
        )
    )

    assert alert.status is AlertStatus.RESOLVED
    assert alert.resolved_at == datetime(2026, 9, 21, 15, 59, tzinfo=UTC)


@pytest.mark.parametrize(
    "change",
    [
        {"unknown": "field"},
        {"schema_version": 2},
        {"source": " "},
        {"external_alert_id": "x" * 201},
        {"service": "*"},
        {"severity": "urgent"},
        {"status": "UNKNOWN"},
        {"title": " "},
        {"description": "x" * 5001},
        {"started_at": "2026-09-21T15:55:00"},
        {"started_at": "2026-09-21T16:05:01Z"},
        {"resolved_at": "2026-09-21T15:59:00Z"},
        {"status": "RESOLVED", "resolved_at": None},
        {"status": "RESOLVED", "resolved_at": "2026-09-21T15:54:59Z"},
        {"event_version": 0},
        {"event_version": 9_223_372_036_854_775_808},
        {"metadata": {"value": "x" * 8192}},
        {"raw_payload": {"value": "x" * 32768}},
    ],
)
def test_invalid_or_ambiguous_normalized_fields_are_rejected(change: dict[str, object]) -> None:
    with pytest.raises(InvalidWebhookError):
        normalize(payload(**change))


@pytest.mark.parametrize(
    "body",
    [
        b'{"schema_version":1,"schema_version":1}',
        b"[]",
        b"null",
        b'{"schema_version":NaN}',
        b'{"schema_version":1,"title":"\\ud800"}',
        b'{"schema_version":1,"title":"\\u0000"}',
        b'{"schema_version":1,"metadata":{"a":{"b":{"c":{"d":{"e":1}}}}}}',
        b"\xff",
    ],
)
def test_ambiguous_or_unsafe_json_is_rejected_before_mapping(body: bytes) -> None:
    with pytest.raises(InvalidWebhookError):
        GenericAlertAdapter(clock=lambda: NOW).normalize(body)
