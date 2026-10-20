"""Action allowlist, retry cap, and policy snapshot tests."""

import pytest

from incident_ops.domain.actions import get_action_definition
from incident_ops.domain.enums import ActionType, ActorType, IncidentSeverity, PolicyDecision
from incident_ops.domain.errors import UnsupportedActionError
from incident_ops.domain.policies import ActionPolicyInput, evaluate_action_policy


@pytest.mark.parametrize(
    ("severity", "retry_count", "expected"),
    [
        (IncidentSeverity.LOW, 0, PolicyDecision.AUTO_APPLY),
        (IncidentSeverity.MEDIUM, 0, PolicyDecision.REQUIRE_APPROVAL),
        (IncidentSeverity.HIGH, 0, PolicyDecision.REQUIRE_APPROVAL),
        (IncidentSeverity.CRITICAL, 0, PolicyDecision.REQUIRE_APPROVAL),
        (IncidentSeverity.LOW, 1, PolicyDecision.REQUIRE_APPROVAL),
        (IncidentSeverity.LOW, 2, PolicyDecision.REJECT),
    ],
)
def test_retry_policy_separates_automatic_human_and_rejected_attempts(
    severity: IncidentSeverity,
    retry_count: int,
    expected: PolicyDecision,
) -> None:
    result = evaluate_action_policy(
        ActionPolicyInput(
            action_type=ActionType.RETRY_FAILED_JOB,
            incident_severity=severity,
            requested_by=ActorType.AGENT,
            parameters_valid=True,
            retry_count=retry_count,
        )
    )

    assert result.decision is expected
    assert result.snapshot["policy_version"] == result.policy_version
    assert result.snapshot["facts"]["retry_count"] == retry_count


def test_invalid_parameters_are_rejected_before_approval() -> None:
    result = evaluate_action_policy(
        ActionPolicyInput(
            action_type=ActionType.RETRY_FAILED_JOB,
            incident_severity=IncidentSeverity.LOW,
            requested_by=ActorType.HUMAN,
            parameters_valid=False,
            retry_count=0,
        )
    )

    assert result.decision is PolicyDecision.REJECT
    assert "invalid_parameters" in result.reasons


def test_status_update_is_never_automatically_applied() -> None:
    result = evaluate_action_policy(
        ActionPolicyInput(
            action_type=ActionType.REQUEST_STATUS_UPDATE,
            incident_severity=IncidentSeverity.LOW,
            requested_by=ActorType.HUMAN,
            parameters_valid=True,
            retry_count=0,
        )
    )

    assert result.decision is PolicyDecision.REQUIRE_APPROVAL


def test_action_registry_rejects_unknown_commands() -> None:
    with pytest.raises(UnsupportedActionError):
        get_action_definition("run_shell_command")


@pytest.mark.parametrize("severity", list(IncidentSeverity))
def test_higher_firing_risk_requires_review_with_an_explicit_reason(severity):
    result = evaluate_action_policy(
        ActionPolicyInput(
            action_type=ActionType.RETRY_FAILED_JOB,
            incident_severity=severity,
            requested_by=ActorType.HUMAN,
            parameters_valid=True,
            retry_count=0,
            higher_monitoring_risk=True,
        )
    )
    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert "higher_monitoring_risk" in result.reasons
    assert result.snapshot["facts"]["higher_monitoring_risk"] is True


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_retry_does_not_execute_against_a_terminal_incident(status):
    from incident_ops.domain.enums import IncidentStatus

    result = evaluate_action_policy(
        ActionPolicyInput(
            action_type=ActionType.RETRY_FAILED_JOB,
            incident_severity=IncidentSeverity.LOW,
            incident_status=IncidentStatus(status),
            requested_by=ActorType.HUMAN,
            parameters_valid=True,
            retry_count=0,
        )
    )
    assert result.decision is PolicyDecision.REJECT
    assert result.reasons == ("incident_not_active",)
