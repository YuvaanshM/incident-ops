"""Opaque, deterministic timestamp/UUID cursor encoding."""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from incident_ops.domain.errors import InvalidCursorError


def encode_cursor(*, timestamp: datetime, resource_id: UUID | str) -> str:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("cursor timestamp must be timezone-aware")
    payload = json.dumps(
        {
            "timestamp": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "id": str(UUID(str(resource_id))),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        if not cursor:
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"timestamp", "id"}:
            raise ValueError
        timestamp = datetime.fromisoformat(str(payload["timestamp"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        resource_id = str(UUID(str(payload["id"])))
    except (
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise InvalidCursorError() from error
    return timestamp.astimezone(UTC), resource_id
