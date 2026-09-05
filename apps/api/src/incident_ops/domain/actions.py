"""Strict v1 action registry."""

from dataclasses import dataclass
from types import MappingProxyType

from incident_ops.domain.enums import ActionType
from incident_ops.domain.errors import UnsupportedActionError


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_type: ActionType
    auto_allowed: bool
    maximum_job_retries: int | None


_ACTION_REGISTRY = MappingProxyType(
    {
        ActionType.RETRY_FAILED_JOB: ActionDefinition(
            action_type=ActionType.RETRY_FAILED_JOB,
            auto_allowed=True,
            maximum_job_retries=2,
        ),
        ActionType.REQUEST_STATUS_UPDATE: ActionDefinition(
            action_type=ActionType.REQUEST_STATUS_UPDATE,
            auto_allowed=False,
            maximum_job_retries=None,
        ),
    }
)


def get_action_definition(action_type: ActionType | str) -> ActionDefinition:
    """Resolve a known action without converting unknown input into a proposal."""

    try:
        normalized = ActionType(action_type)
        return _ACTION_REGISTRY[normalized]
    except (KeyError, ValueError) as error:
        raise UnsupportedActionError() from error
