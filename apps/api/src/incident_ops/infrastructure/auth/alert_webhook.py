"""HMAC authentication for the provider-neutral alert webhook."""

import hashlib
import hmac
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from incident_ops.application.auth import Principal
from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import WebhookAuthenticationError
from incident_ops.infrastructure.monitoring_config import AlertIntakeSettings

_PATH = "/api/v1/alerts/webhook"
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_SIGNATURE = re.compile(r"v1=[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VerifiedAlertSource:
    principal: Principal
    source: str
    services: frozenset[str]
    idempotency_key: UUID


class AlertWebhookVerifier:
    def __init__(
        self,
        settings: AlertIntakeSettings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._clock = clock

    def verify(
        self,
        *,
        key_id: str,
        timestamp: str,
        idempotency_key: str,
        signature: str,
        body: bytes,
    ) -> VerifiedAlertSource:
        try:
            parsed_key = UUID(idempotency_key)
        except ValueError, AttributeError:
            raise WebhookAuthenticationError() from None
        if (
            _KEY_ID.fullmatch(key_id) is None
            or _TIMESTAMP.fullmatch(timestamp) is None
            or _SIGNATURE.fullmatch(signature) is None
            or str(parsed_key) != idempotency_key
            or abs(self._clock() - int(timestamp)) > 300
        ):
            raise WebhookAuthenticationError()

        resolved = self._settings.resolve_key(key_id)
        if resolved is None:
            raise WebhookAuthenticationError()
        source, key = resolved
        signing_base = (
            f"v1\nPOST\n{_PATH}\n{key_id}\n{timestamp}\n{idempotency_key}\n".encode() + body
        )
        expected = (
            "v1="
            + hmac.new(
                key.signing_secret.get_secret_value().encode("utf-8"),
                signing_base,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise WebhookAuthenticationError()

        return VerifiedAlertSource(
            principal=Principal(
                actor_type=ActorType.SYSTEM,
                subject=f"monitoring:{source.source}",
                scopes=frozenset({"alerts:ingest"}),
            ),
            source=source.source,
            services=frozenset(source.services),
            idempotency_key=parsed_key,
        )
