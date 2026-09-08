"""Small explainable advisory rules; these functions never mutate incident state."""

from dataclasses import dataclass

from incident_ops.domain.enums import IncidentSeverity


@dataclass(frozen=True, slots=True)
class SeverityRecommendation:
    advisory: bool
    severity: IncidentSeverity
    reasons: tuple[str, ...]


def recommend_severity(*, title: str, description: str) -> SeverityRecommendation:
    text = f"{title} {description}".casefold()
    reasons: list[str] = []

    outage_signal = any(term in text for term in ("outage", "unavailable", "data loss"))
    broad_impact = any(term in text for term in ("all users", "everyone", "global"))
    security_signal = any(term in text for term in ("security breach", "credential leak"))
    if outage_signal:
        reasons.append("outage_signal")
    if broad_impact:
        reasons.append("broad_impact_signal")
    if security_signal:
        reasons.append("security_signal")

    if security_signal or (outage_signal and broad_impact):
        severity = IncidentSeverity.CRITICAL
    elif outage_signal or any(term in text for term in ("payment failure", "5xx spike")):
        severity = IncidentSeverity.HIGH
        if not reasons:
            reasons.append("major_failure_signal")
    elif any(term in text for term in ("latency", "degraded", "error rate")):
        severity = IncidentSeverity.MEDIUM
        reasons.append("degradation_signal")
    else:
        severity = IncidentSeverity.LOW
        reasons.append("no_elevated_signal")

    return SeverityRecommendation(advisory=True, severity=severity, reasons=tuple(reasons))
