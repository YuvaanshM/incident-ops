"""Fixture reset safety is independent from command-line parsing."""

import pytest

from incident_ops.config import Settings
from incident_ops.seed import ensure_reset_allowed


def test_additive_seed_is_allowed_without_reset_flag(test_settings: Settings) -> None:
    ensure_reset_allowed(test_settings, reset=False)


def test_reset_is_refused_outside_explicit_development_mode(
    test_settings: Settings,
) -> None:
    with pytest.raises(SystemExit, match="reset refused"):
        ensure_reset_allowed(test_settings, reset=True)


def test_reset_requires_second_explicit_guard(test_settings: Settings) -> None:
    development_settings = test_settings.model_copy(
        update={"app_env": "development", "allow_development_reset": False}
    )

    with pytest.raises(SystemExit, match="ALLOW_DEVELOPMENT_RESET=true"):
        ensure_reset_allowed(development_settings, reset=True)


def test_reset_is_allowed_only_when_both_development_guards_are_set(
    test_settings: Settings,
) -> None:
    development_settings = test_settings.model_copy(
        update={"app_env": "development", "allow_development_reset": True}
    )

    ensure_reset_allowed(development_settings, reset=True)
