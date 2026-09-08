"""Canonical command fingerprints for safe idempotent replay."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


def _json_default(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_request_fingerprint(
    *,
    operation: str,
    path_parameters: Mapping[str, object],
    body: Mapping[str, object],
) -> str:
    """Hash normalized command identity without including authenticated identity."""

    canonical = json.dumps(
        {
            "operation": operation,
            "path_parameters": dict(path_parameters),
            "body": dict(body),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def json_ready(value: Any) -> Any:
    """Return a JSON-compatible value suitable for an idempotency response record."""

    return json.loads(json.dumps(value, default=_json_default))
