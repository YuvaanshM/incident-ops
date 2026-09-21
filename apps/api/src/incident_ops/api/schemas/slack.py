"""Bounded native provider envelopes. Unknown provider metadata is discarded."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from incident_ops.domain.errors import InvalidWebhookError

SlackTimestamp = Annotated[str, Field(pattern=r"^[0-9]{1,12}\.[0-9]{6}$")]


class ProviderEnvelope(BaseModel):
    # Slack adds metadata over time. Only the typed projection is ever consumed;
    # this is a provider envelope, not a command schema allowing arbitrary fields.
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class SlackChallenge(ProviderEnvelope):
    type: Literal["url_verification"]
    challenge: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")]


class SlackMessage(ProviderEnvelope):
    type: Annotated[str, Field(min_length=1, max_length=100)]
    channel: Annotated[str, Field(pattern=r"^[CG][A-Z0-9]{2,31}$")] | None = None
    user: Annotated[str, Field(pattern=r"^[UW][A-Z0-9]{2,31}$")] | None = None
    text: Annotated[str, Field(max_length=5000)] = ""
    ts: SlackTimestamp | None = None
    thread_ts: SlackTimestamp | None = None
    subtype: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    bot_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    app_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    hidden: bool = False
    channel_type: Literal["channel", "group", "im", "mpim"] | None = None
    team: Annotated[str, Field(pattern=r"^T[A-Z0-9]{2,31}$")] | None = None


class SlackCallback(ProviderEnvelope):
    type: Literal["event_callback"]
    team_id: Annotated[str, Field(pattern=r"^T[A-Z0-9]{2,31}$")]
    api_app_id: Annotated[str, Field(pattern=r"^A[A-Z0-9]{2,31}$")]
    event_id: Annotated[str, Field(pattern=r"^Ev[A-Za-z0-9]{2,100}$")]
    event: SlackMessage
    is_ext_shared_channel: bool = False
    context_team_id: Annotated[str, Field(pattern=r"^T[A-Z0-9]{2,31}$")] | None = None


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    if len(pairs) > 100:
        raise ValueError("object limit")
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite number")


def _bound_tree(value: object, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("nesting limit")
    if isinstance(value, dict):
        for child in value.values():
            _bound_tree(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("array limit")
        for child in value:
            _bound_tree(child, depth + 1)
    elif isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("nonfinite number")
    elif isinstance(value, str) and any(
        ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        raise ValueError("invalid text")


def parse_slack_envelope(body: bytes) -> SlackChallenge | SlackCallback:
    try:
        value = json.loads(
            body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_reject_constant
        )
        _bound_tree(value)
        if not isinstance(value, dict):
            raise ValueError("object required")
        if value.get("type") == "url_verification":
            return SlackChallenge.model_validate(value)
        return SlackCallback.model_validate(value)
    except ValueError, RecursionError, ValidationError:
        raise InvalidWebhookError() from None
