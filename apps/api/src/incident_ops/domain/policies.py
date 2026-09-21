"""Pure, versioned action-policy evaluation."""

from dataclasses import dataclass

from incident_ops.domain.actions import get_action_definition
from incident_ops.domain.enums import (
    ActionType,
    ActorType,
    IncidentSeverity,
    PolicyDecision,
)

POLICY_VERSION = "2026-09-25.v2"


@dataclass(frozen=True, slots=True)
class ActionPolicyInput:
    action_type: ActionType
    incident_severity: IncidentSeverity
    requested_by: ActorType
    parameters_valid: bool
    retry_count: int
    higher_monitoring_risk: bool = False

    def __post_init__(self) -> None:
        if self.retry_count < 0:
            raise ValueError("retry_count must be nonnegative")


@dataclass(frozen=True, slots=True)
class EvaluatedPolicy:
    decision: PolicyDecision
    reasons: tuple[str, ...]
    facts: ActionPolicyInput
    policy_version: str = POLICY_VERSION

    @property
    def snapshot(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "facts": {
                "action_type": self.facts.action_type.value,
                "incident_severity": self.facts.incident_severity.value,
                "requested_by": self.facts.requested_by.value,
                "parameters_valid": self.facts.parameters_valid,
                "retry_count": self.facts.retry_count,
                "higher_monitoring_risk": self.facts.higher_monitoring_risk,
            },
        }


def evaluate_action_policy(policy_input: ActionPolicyInput) -> EvaluatedPolicy:
    """Evaluate authoritative persisted facts against the strict v1 registry."""

    definition = get_action_definition(policy_input.action_type)

    if not policy_input.parameters_valid:
        return EvaluatedPolicy(
            decision=PolicyDecision.REJECT,
            reasons=("invalid_parameters",),
            facts=policy_input,
        )

    maximum_retries = definition.maximum_job_retries
    if maximum_retries is not None and policy_input.retry_count >= maximum_retries:
        return EvaluatedPolicy(
            decision=PolicyDecision.REJECT,
            reasons=("retry_limit_reached",),
            facts=policy_input,
        )

    first_eligible_retry = (
        policy_input.action_type is ActionType.RETRY_FAILED_JOB
        and policy_input.retry_count == 0
        and policy_input.incident_severity is IncidentSeverity.LOW
    )
    if definition.auto_allowed and first_eligible_retry and not policy_input.higher_monitoring_risk:
        return EvaluatedPolicy(
            decision=PolicyDecision.AUTO_APPLY,
            reasons=("low_severity_first_retry",),
            facts=policy_input,
        )

    return EvaluatedPolicy(
        decision=PolicyDecision.REQUIRE_APPROVAL,
        reasons=("human_approval_required",),
        facts=policy_input,
    )
