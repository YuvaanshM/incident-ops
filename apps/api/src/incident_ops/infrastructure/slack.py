"""Allowlisted Slack webhook messages and bounded network delivery."""

import asyncio
import re
from typing import Literal
from uuid import UUID

from httpx2 import AsyncClient, HTTPError, Timeout
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from incident_ops.application.outbox import OutboxClaim
from incident_ops.domain.delivery import DeliveryResult


class IncidentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    incident_id: str = Field(max_length=36)
    event_id: str = Field(max_length=36)
    severity: Literal["low", "medium", "high", "critical"]
    # Accepted for compatibility with committed Phase 3 facts, never forwarded externally.
    title: str = Field(max_length=200, repr=False)


class ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    incident_id: str = Field(max_length=36)
    event_id: str = Field(max_length=36)
    action_id: str = Field(max_length=36)
    action_type: Literal["retry_failed_job", "request_status_update"]
    severity: Literal["low", "medium", "high", "critical"] | None = None


def sanitize_summary(value: str) -> str:
    """Single-line plain text, with Slack control syntax and mention triggers neutralized."""
    value = " ".join(value.split())[:300]
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return value.replace("@", "\uff20").translate(str.maketrans("_", " ", "*`~"))


def build_message(claim: OutboxClaim, web_url: str) -> dict[str, object]:
    """Only server-authored summaries leave the app; free text may contain arbitrary secrets."""
    action_id = None
    if claim.event_type == "slack.action.human_required":
        action = ActionPayload.model_validate(claim.payload)
        action_id = UUID(action.action_id)
        if action_id != claim.action_id:
            raise ValueError("invalid action reference")
        payload: IncidentPayload | ActionPayload = action
        heading = "Human approval required"
        summary = (
            "A retry request needs review."
            if action.action_type == "retry_failed_job"
            else "A status proposal needs review."
        )
    elif claim.event_type in {
        "slack.incident.created",
        "slack.incident.escalated",
        "slack.incident.critical",
    }:
        payload = IncidentPayload.model_validate(claim.payload)
        heading = (
            "Incident created"
            if claim.event_type == "slack.incident.created"
            else "Severity escalated"
        )
        summary = "Review the incident for the latest response context."
    else:
        raise ValueError("unsupported notification")
    if (
        UUID(payload.incident_id) != claim.incident_id
        or UUID(payload.event_id) != claim.source_event_id
    ):
        raise ValueError("invalid source reference")
    severity = payload.severity or "not recorded"
    body = sanitize_summary(f"{heading} · Severity: {severity}. {summary}")
    references = f"Outbox: {claim.id} • Source: {claim.source_event_id}"
    # Only server-configured origins and validated UUIDs become links.
    links = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View incident"},
            "url": f"{web_url.rstrip('/')}/incidents/{claim.incident_id}",
        }
    ]
    if action_id is not None:
        links.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Review request"},
                "url": f"{web_url.rstrip('/')}/approvals/{action_id}",
            }
        )
    return {
        "text": f"{body} {references}",
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {"type": "section", "text": {"type": "plain_text", "text": body, "emoji": False}},
            {"type": "actions", "elements": links},
            {"type": "context", "elements": [{"type": "plain_text", "text": references}]},
        ],
    }


def retry_after_seconds(value: str | None, maximum: float) -> float | None:
    # Slack documents delta seconds. Dates, non-finite numbers and huge strings are ignored.
    if value is None or re.fullmatch(r"[0-9]{1,8}", value.strip()) is None:
        return None
    return min(float(value), maximum)


class SlackWebhookSender:
    def __init__(
        self,
        client: AsyncClient,
        *,
        webhook_url: SecretStr,
        web_url: str,
        connect_timeout: float = 3,
        read_timeout: float = 10,
        total_timeout: float = 15,
        max_retry_after: float = 300,
    ) -> None:
        self._client = client
        self._webhook_url = webhook_url
        self._web_url = web_url
        self._timeout = Timeout(connect=connect_timeout, read=read_timeout, write=5, pool=3)
        self._total_timeout = total_timeout
        self._max_retry_after = max_retry_after

    async def send(self, claim: OutboxClaim) -> DeliveryResult:
        try:
            message = build_message(claim, self._web_url)
        except ValueError, ValidationError:
            return DeliveryResult("invalid_payload")
        try:
            async with asyncio.timeout(self._total_timeout):
                async with self._client.stream(
                    "POST",
                    self._webhook_url.get_secret_value(),
                    json=message,
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status == 429:
                        return DeliveryResult(
                            "rate_limited",
                            True,
                            retry_after_seconds(
                                response.headers.get("retry-after"),
                                self._max_retry_after,
                            ),
                        )
                    if status in {408, 425} or 500 <= status <= 599:
                        return DeliveryResult("provider_unavailable", True)
                    if status != 200:
                        return DeliveryResult("provider_rejected")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 64:
                            return DeliveryResult("invalid_response", True)
                        body.extend(chunk)
                    return (
                        DeliveryResult("delivered")
                        if body.strip() == b"ok"
                        else DeliveryResult("invalid_response", True)
                    )
        except HTTPError, TimeoutError:
            return DeliveryResult("network_error", True)
