"""Provider-neutral authenticated principal and authorization helpers."""

from dataclasses import dataclass
from uuid import UUID

from incident_ops.domain.enums import ActorType
from incident_ops.domain.errors import AuthorizationDeniedError


@dataclass(frozen=True, slots=True)
class Principal:
    actor_type: ActorType
    subject: str
    scopes: frozenset[str]
    delegated_by: str | None = None
    credential_id: UUID | None = None

    def __post_init__(self) -> None:
        normalized_subject = self.subject.strip()
        if not normalized_subject or len(normalized_subject) > 255:
            raise ValueError("subject must contain between 1 and 255 characters")
        if any(not scope.strip() or len(scope) > 100 for scope in self.scopes):
            raise ValueError("scopes must contain between 1 and 100 characters")
        if self.delegated_by is not None and (
            self.actor_type is not ActorType.AGENT
            or not self.delegated_by.strip()
            or len(self.delegated_by) > 255
            or self.credential_id is None
        ):
            raise ValueError("invalid delegation metadata")
        object.__setattr__(self, "subject", normalized_subject)
        object.__setattr__(self, "scopes", frozenset(scope.strip() for scope in self.scopes))

    def has_scope(self, required_scope: str) -> bool:
        return required_scope in self.scopes


def require_scope(principal: Principal, required_scope: str) -> None:
    if not principal.has_scope(required_scope):
        raise AuthorizationDeniedError()


def require_actor_type(principal: Principal, required_actor_type: ActorType) -> None:
    if principal.actor_type is not required_actor_type:
        raise AuthorizationDeniedError()
