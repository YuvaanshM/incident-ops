"""Phase 3 helpers that must remain deterministic and provider independent."""

from datetime import UTC, datetime, timedelta

import pytest

from incident_ops.application.auth import Principal
from incident_ops.application.idempotency import canonical_request_fingerprint
from incident_ops.application.pagination import decode_cursor, encode_cursor
from incident_ops.domain.advisory import recommend_severity
from incident_ops.domain.enums import ActorType, IncidentSeverity
from incident_ops.domain.errors import InvalidCursorError, RateLimitExceededError
from incident_ops.infrastructure.rate_limit import FixedWindowRateLimiter


def test_request_fingerprint_is_stable_for_equivalent_json_and_changes_with_the_command() -> None:
    first = canonical_request_fingerprint(
        operation="incidents.create",
        path_parameters={},
        body={"severity": "low", "title": "Checkout latency"},
    )
    reordered = canonical_request_fingerprint(
        operation="incidents.create",
        path_parameters={},
        body={"title": "Checkout latency", "severity": "low"},
    )
    different_operation = canonical_request_fingerprint(
        operation="incidents.update",
        path_parameters={},
        body={"title": "Checkout latency", "severity": "low"},
    )

    assert first == reordered
    assert len(first) == 64
    assert first != different_operation


def test_cursor_round_trip_preserves_utc_timestamp_and_uuid_tie_breaker() -> None:
    occurred_at = datetime(2026, 9, 8, 12, 30, 45, 123456, tzinfo=UTC)
    resource_id = "12345678-1234-4234-8234-123456789abc"

    cursor = encode_cursor(timestamp=occurred_at, resource_id=resource_id)

    assert decode_cursor(cursor) == (occurred_at, resource_id)


@pytest.mark.parametrize("cursor", ["", "not-base64", "e30", "W10"])
def test_invalid_cursor_is_rejected_without_leaking_parser_details(cursor: str) -> None:
    with pytest.raises(InvalidCursorError) as error:
        decode_cursor(cursor)

    assert error.value.code == "invalid_cursor"
    assert error.value.details is None


def test_severity_recommendation_is_advisory_and_uses_explainable_rules() -> None:
    recommendation = recommend_severity(
        title="Checkout outage",
        description="All users cannot submit payments.",
    )

    assert recommendation.advisory is True
    assert recommendation.severity is IncidentSeverity.CRITICAL
    assert recommendation.reasons == ("outage_signal", "broad_impact_signal")


async def test_rate_limit_is_principal_aware_and_stricter_for_agents() -> None:
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    limiter = FixedWindowRateLimiter(
        window=timedelta(minutes=1),
        human_limit=2,
        agent_limit=1,
        clock=lambda: now,
    )
    human = Principal(ActorType.HUMAN, "operator-1", frozenset({"incidents:write"}))
    agent = Principal(ActorType.AGENT, "agent-1", frozenset({"actions:request"}))

    await limiter.check(human, bucket="commands")
    await limiter.check(human, bucket="commands")
    await limiter.check(agent, bucket="commands")

    with pytest.raises(RateLimitExceededError):
        await limiter.check(human, bucket="commands")
    with pytest.raises(RateLimitExceededError):
        await limiter.check(agent, bucket="commands")

    # Another principal gets an independent allowance.
    await limiter.check(
        Principal(ActorType.AGENT, "agent-2", frozenset({"actions:request"})),
        bucket="commands",
    )
