"""An immutable source observation, never an executable command."""

import re
from dataclasses import dataclass

from incident_ops.domain.errors import InvalidWebhookError


@dataclass(frozen=True, slots=True)
class ChannelIssue:
    team_id: str
    app_id: str
    channel_id: str
    event_id: str
    message_ts: str
    user_id: str
    text: str

    def __post_init__(self) -> None:
        patterns = (
            (self.team_id, r"T[A-Z0-9]{2,31}"),
            (self.app_id, r"A[A-Z0-9]{2,31}"),
            (self.channel_id, r"[CG][A-Z0-9]{2,31}"),
            (self.event_id, r"Ev[A-Za-z0-9]{2,100}"),
            (self.message_ts, r"[0-9]{1,12}\.[0-9]{6}"),
            (self.user_id, r"[UW][A-Z0-9]{2,31}"),
        )
        if (
            any(re.fullmatch(pattern, value) is None for value, pattern in patterns)
            or not self.text.strip()
            or len(self.text) > 5000
            or any(ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF for char in self.text)
        ):
            raise InvalidWebhookError()

    def source_fields(self) -> dict[str, str]:
        return {
            "source": "slack",
            "team_id": self.team_id,
            "app_id": self.app_id,
            "channel_id": self.channel_id,
            "message_ts": self.message_ts,
            "user_id": self.user_id,
        }
