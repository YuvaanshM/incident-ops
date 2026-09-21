"""Slack v0 HMAC authentication over the original bytes, before deserialization."""

import hashlib
import hmac
import re
import time
from collections.abc import Callable

from pydantic import SecretStr

from incident_ops.domain.errors import WebhookAuthenticationError


class SlackSignatureVerifier:
    def __init__(self, secret: SecretStr | None, *, clock: Callable[[], float] = time.time) -> None:
        self._secret = secret
        self._clock = clock

    def verify(self, *, timestamp: str, signature: str, body: bytes) -> None:
        if (
            self._secret is None
            or re.fullmatch(r"[0-9]{1,12}", timestamp) is None
            or re.fullmatch(r"v0=[0-9a-f]{64}", signature) is None
            or abs(self._clock() - int(timestamp)) > 300
        ):
            raise WebhookAuthenticationError()
        expected = (
            "v0="
            + hmac.new(
                self._secret.get_secret_value().encode("utf-8"),
                b"v0:" + timestamp.encode("ascii") + b":" + body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise WebhookAuthenticationError()
