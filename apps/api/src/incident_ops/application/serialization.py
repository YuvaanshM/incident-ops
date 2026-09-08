"""Stable JSON representations shared by commands and HTTP adapters."""

from dataclasses import asdict
from typing import Any

from incident_ops.application.dto import DemoJobView, IncidentEventView, IncidentView
from incident_ops.application.idempotency import json_ready
from incident_ops.domain.errors import IncidentOpsError


def job_body(job: DemoJobView) -> dict[str, Any]:
    return json_ready(asdict(job))


def incident_body(incident: IncidentView) -> dict[str, Any]:
    result = json_ready(asdict(incident))
    result["jobs"] = [job_body(job) for job in incident.jobs]
    return result


def event_body(event: IncidentEventView) -> dict[str, Any]:
    return json_ready(asdict(event))


def error_body(error: IncidentOpsError) -> dict[str, Any]:
    content: dict[str, Any] = {
        "code": error.code,
        "message": error.public_message,
    }
    if error.details is not None:
        content["details"] = json_ready(error.details)
    return {"error": content}
