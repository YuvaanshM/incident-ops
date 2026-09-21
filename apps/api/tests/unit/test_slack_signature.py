"""Raw bytes, delivery clocks, and configuration fail closed."""

import pytest
from pydantic import ValidationError

from incident_ops.domain.errors import WebhookAuthenticationError
from incident_ops.infrastructure.auth.slack import SlackSignatureVerifier
from incident_ops.infrastructure.slack_config import SlackIntakeSettings


@pytest.mark.parametrize("skew", [-301, -300, 0, 300, 301])
def test_delivery_clock_rejects_old_and_future_replays(slack_config, sign_slack, skew):
    body, headers = sign_slack(b'{"message":"synthetic"}', timestamp=1800000000 + skew)
    verifier = SlackSignatureVerifier(slack_config.signing_secret, clock=lambda: 1800000000)
    kwargs = {
        "body": body,
        "timestamp": headers["X-Slack-Request-Timestamp"],
        "signature": headers["X-Slack-Signature"],
    }
    if abs(skew) > 300:
        with pytest.raises(WebhookAuthenticationError):
            verifier.verify(**kwargs)
    else:
        verifier.verify(**kwargs)


def test_signing_secret_never_appears_in_repr_or_configuration_errors(slack_config):
    secret = slack_config.signing_secret.get_secret_value()
    assert secret not in repr(slack_config)
    with pytest.raises(ValidationError) as error:
        SlackIntakeSettings(enabled=True, signing_secret=secret)
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "override",
    [
        {"signing_secret": "short"},
        {"app_id": None},
        {"team_id": None},
        {"channels": []},
        {"channels": [{"channel_id": "*", "service": "checkout"}]},
        {"channels": [{"channel_id": "C123ABC", "service": "checkout"}] * 2},
    ],
)
def test_enabled_configuration_requires_explicit_valid_bindings(slack_config, override):
    with pytest.raises(ValidationError):
        SlackIntakeSettings.model_validate({**slack_config.model_dump(), **override})
