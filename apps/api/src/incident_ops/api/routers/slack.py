"""Slack's signed server-to-server transport, deliberately separate from browser auth."""

import asyncio
import ipaddress
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from incident_ops.api.schemas.slack import SlackChallenge, parse_slack_envelope
from incident_ops.application.auth import Principal
from incident_ops.application.services.slack_intake import SlackIntakeService, slack_principal
from incident_ops.config import Settings
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import (
    AuthorizationDeniedError,
    IngestionUnavailableError,
    InvalidWebhookError,
    ResourceNotFoundError,
    WebhookAuthenticationError,
    WebhookTooLargeError,
)
from incident_ops.domain.source_issues import ChannelIssue
from incident_ops.infrastructure.auth.slack import SlackSignatureVerifier
from incident_ops.infrastructure.rate_limit import FixedWindowRateLimiter

router = APIRouter()
MAX_BODY_BYTES = 64 * 1024


def _single_header(request: Request, name: str, *, limit: int, required: bool = True) -> str:
    values = request.headers.getlist(name)
    if len(values) != 1 or len(values[0]) > limit:
        if not values and not required:
            return ""
        raise WebhookAuthenticationError()
    return values[0]


def _secure_transport(request: Request, settings: Settings) -> bool:
    if request.url.scheme == "https":
        return True
    if settings.app_env == "production" or request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


async def _body(request: Request) -> bytes:
    length = _single_header(request, "content-length", limit=10, required=False)
    if length and (not length.isascii() or not length.isdigit()):
        raise InvalidWebhookError()
    if length and int(length) > MAX_BODY_BYTES:
        raise WebhookTooLargeError()
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise WebhookTooLargeError()
        body.extend(chunk)
    if length and len(body) != int(length):
        raise InvalidWebhookError()
    return bytes(body)


@router.post("/api/v1/integrations/slack/events", include_in_schema=False)
async def receive_slack_event(request: Request) -> JSONResponse:
    settings = cast(Settings, request.app.state.settings)
    config = settings.slack_intake
    if not config.enabled:
        raise ResourceNotFoundError()
    # One constant key bounds unauthenticated state and total verification work.
    # No Origin/Referer, IP allowlist, or caller-supplied forwarding header is trusted.
    limiter = cast(FixedWindowRateLimiter, request.app.state.slack_ingress_limiter)
    await limiter.check(
        Principal(ActorType.SYSTEM, "slack:unverified", frozenset()), bucket="slack:unverified"
    )
    if (
        not _secure_transport(request, settings)
        or "authorization" in request.headers
        or "cookie" in request.headers
    ):
        raise WebhookAuthenticationError()
    timestamp = _single_header(request, "x-slack-request-timestamp", limit=12)
    signature = _single_header(request, "x-slack-signature", limit=67)
    content_type = _single_header(request, "content-type", limit=100).lower().replace(" ", "")
    if (
        content_type not in {"application/json", "application/json;charset=utf-8"}
        or "content-encoding" in request.headers
    ):
        raise InvalidWebhookError()
    try:
        async with asyncio.timeout(2):
            body = await _body(request)
            verifier = cast(SlackSignatureVerifier, request.app.state.slack_signature_verifier)
            verifier.verify(timestamp=timestamp, signature=signature, body=body)
            envelope = parse_slack_envelope(body)
            if isinstance(envelope, SlackChallenge):
                return JSONResponse({"challenge": envelope.challenge})
            if (
                envelope.api_app_id != config.app_id
                or envelope.team_id != config.team_id
                or envelope.is_ext_shared_channel
                or envelope.context_team_id not in {None, config.team_id}
            ):
                raise AuthorizationDeniedError()
            event = envelope.event
            if event.type != "message":
                return JSONResponse({"ok": True})
            if config.channel(event.channel or "") is None or event.team not in {
                None,
                config.team_id,
            }:
                raise AuthorizationDeniedError()
            if (
                event.bot_id is not None
                or event.app_id is not None
                or event.subtype is not None
                or event.thread_ts is not None
                or event.hidden
                or event.channel_type in {"im", "mpim"}
            ):
                return JSONResponse({"ok": True})
            if (
                event.user is None
                or event.ts is None
                or event.channel is None
                or not event.text.strip()
            ):
                raise InvalidWebhookError()
            principal = slack_principal(envelope.team_id, event.channel)
            source_limiter = cast(FixedWindowRateLimiter, request.app.state.slack_source_limiter)
            await source_limiter.check(principal, bucket="slack:ingest")
            service = cast(SlackIntakeService, request.app.state.slack_intake_service)
            result = await service.ingest(
                principal=principal,
                issue=ChannelIssue(
                    team_id=envelope.team_id,
                    app_id=envelope.api_app_id,
                    channel_id=event.channel,
                    event_id=envelope.event_id,
                    message_ts=event.ts,
                    user_id=event.user,
                    text=event.text,
                ),
                request_id=UUID(request.state.request_id),
            )
            return JSONResponse(
                {"ok": True}, headers={"Idempotent-Replayed": "true"} if result.replayed else None
            )
    except TimeoutError, SQLAlchemyError:
        raise IngestionUnavailableError() from None
