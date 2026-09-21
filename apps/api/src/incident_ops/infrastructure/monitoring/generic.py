"""Strict adapter for the versioned provider-neutral alert envelope."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import ValidationError

from incident_ops.api.schemas.alerts import GenericAlertRequest
from incident_ops.domain.alerts import AlertStatus, FrozenJson, NormalizedAlert
from incident_ops.domain.errors import InvalidAlertFieldsError, InvalidWebhookError

MAX_BODY_BYTES = 64 * 1024
MAX_JSON_DEPTH = 4
MAX_OBJECT_KEYS = 64
MAX_ARRAY_ITEMS = 1000


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len(pairs) > MAX_OBJECT_KEYS:
        raise ValueError("object key limit")
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite number")


def _bound_tree(value: object, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("nesting limit")
    if isinstance(value, dict):
        for child in value.values():
            _bound_tree(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ValueError("array limit")
        for child in value:
            _bound_tree(child, depth + 1)
    elif isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("nonfinite number")
    elif isinstance(value, str) and any(
        ord(character) == 0 or 0xD800 <= ord(character) <= 0xDFFF for character in value
    ):
        raise ValueError("invalid text")


class GenericAlertAdapter:
    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    def normalize(self, body: bytes) -> NormalizedAlert:
        if len(body) > MAX_BODY_BYTES:
            raise InvalidWebhookError()
        try:
            value = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_object,
                parse_constant=_reject_constant,
            )
            _bound_tree(value)
            if not isinstance(value, dict):
                raise ValueError("object required")
            try:
                request = GenericAlertRequest.model_validate(value)
            except ValidationError:
                raise InvalidAlertFieldsError() from None
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise RuntimeError("alert adapter clock must be timezone-aware")
            future_limit = now + timedelta(seconds=300)
            if request.started_at > future_limit or (
                request.resolved_at is not None and request.resolved_at > future_limit
            ):
                raise InvalidAlertFieldsError() from None
            return NormalizedAlert(
                source=request.source,
                external_alert_id=request.external_alert_id,
                service=request.service,
                severity=request.severity,
                status=(
                    AlertStatus.RESOLVED if request.status == "RESOLVED" else AlertStatus.FIRING
                ),
                title=request.title,
                description=request.description,
                started_at=request.started_at,
                resolved_at=request.resolved_at,
                event_version=request.event_version,
                metadata=cast(dict[str, FrozenJson], request.metadata),
                raw_payload=(
                    None
                    if request.raw_payload is None
                    else cast(dict[str, FrozenJson], request.raw_payload)
                ),
            )
        except UnicodeDecodeError, ValueError, RecursionError:
            raise InvalidWebhookError() from None
