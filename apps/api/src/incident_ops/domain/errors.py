"""Errors shared by the domain and application layers."""

from collections.abc import Mapping


class IncidentOpsError(Exception):
    """Base error with a stable public code and generic message."""

    code = "incident_ops_error"
    public_message = "The request could not be completed."
    status_code = 400

    def __init__(self, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(self.public_message)
        self.details = dict(details) if details is not None else None


class InvalidStateTransitionError(IncidentOpsError):
    code = "invalid_state_transition"
    public_message = "The requested state transition is not allowed."
    status_code = 409


class UnsupportedActionError(IncidentOpsError):
    code = "unsupported_action_type"
    public_message = "The requested action is not supported."
    status_code = 422


class AuthorizationDeniedError(IncidentOpsError):
    code = "forbidden"
    public_message = "The authenticated principal is not authorized for this operation."
    status_code = 403


class AuthenticationRequiredError(IncidentOpsError):
    code = "authentication_required"
    public_message = "Authentication is required."
    status_code = 401


class VersionConflictError(IncidentOpsError):
    code = "version_conflict"
    public_message = "The resource changed. Refresh it before retrying."
    status_code = 409


class AlertVersionConflictError(IncidentOpsError):
    code = "alert_version_conflict"
    public_message = "The alert version conflicts with an existing observation."
    status_code = 409


class ActionNotPendingError(IncidentOpsError):
    code = "action_not_pending"
    public_message = "The action is no longer pending."
    status_code = 409


class IdempotencyKeyMismatchError(IncidentOpsError):
    code = "idempotency_key_reused_with_different_payload"
    public_message = "The idempotency key was already used for a different request."
    status_code = 422


class InvalidCursorError(IncidentOpsError):
    code = "invalid_cursor"
    public_message = "The pagination cursor is invalid."
    status_code = 422


class RateLimitExceededError(IncidentOpsError):
    code = "rate_limit_exceeded"
    public_message = "Too many requests. Try again later."
    status_code = 429


class ResourceNotFoundError(IncidentOpsError):
    code = "not_found"
    public_message = "The requested resource was not found."
    status_code = 404


class InvalidActionParametersError(IncidentOpsError):
    code = "invalid_action_parameters"
    public_message = "The action parameters are not valid for the requested action."
    status_code = 422


class NoChangesRequestedError(IncidentOpsError):
    code = "no_changes_requested"
    public_message = "At least one change must be requested."
    status_code = 422


class WebhookAuthenticationError(IncidentOpsError):
    code = "invalid_webhook_authentication"
    public_message = "Webhook authentication failed."
    status_code = 401


class InvalidWebhookError(IncidentOpsError):
    code = "invalid_webhook"
    public_message = "The webhook request is invalid."
    status_code = 400


class InvalidAlertFieldsError(InvalidWebhookError):
    code = "invalid_alert_fields"
    public_message = "The normalized alert fields are invalid."
    status_code = 422


class UnsupportedWebhookMediaError(InvalidWebhookError):
    code = "unsupported_webhook_media"
    public_message = "The webhook media type is unsupported."
    status_code = 415


class WebhookTooLargeError(InvalidWebhookError):
    status_code = 413


class IngestionUnavailableError(IncidentOpsError):
    code = "ingestion_unavailable"
    public_message = "Intake is temporarily unavailable. Retry later."
    status_code = 503
