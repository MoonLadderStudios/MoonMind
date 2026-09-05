"""Obsolete Omnigent configuration is never silently ignored at startup.

Source issue: MoonLadderStudios/MoonMind#3835 (required work section 10).

Before an image or environment alias is removed, a deployment that still supplies
it must get an actionable failure naming the replacement; after removal the value
is rejected outright. These tests drive the real API startup helper rather than
the pure guard so the wiring at the startup boundary is proven, not assumed.
"""

from __future__ import annotations

import logging

import pytest

from moonmind.omnigent import legacy_retirement
from moonmind.omnigent.legacy_retirement import (
    ObsoleteConfiguration,
    ObsoleteConfigurationError,
)

VARIABLE = "OMNIGENT_HOST_IMAGE_REF"
ROW = "omnigent.legacy.host_image_variable_alias"


def _startup_guard():
    from api_service.main import _assert_omnigent_configuration_is_current

    return _assert_omnigent_configuration_is_current


def _deprecated() -> tuple[ObsoleteConfiguration, ...]:
    return (
        ObsoleteConfiguration(
            variable=VARIABLE,
            retirementPathId=ROW,
            replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
            deprecated=True,
            guidance="Repin the shared image digest before the next release.",
        ),
    )


def test_startup_accepts_the_current_configuration(monkeypatch) -> None:
    monkeypatch.setenv(VARIABLE, "ghcr.io/example/host@sha256:" + "a" * 64)
    # Nothing is deprecated today: the alias is still the supported way to pin a
    # prior image while the rollback window is open.
    _startup_guard()()


def test_startup_warns_actionably_during_the_deprecation_window(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(legacy_retirement, "OBSOLETE_CONFIGURATION", _deprecated())
    monkeypatch.setenv(VARIABLE, "ghcr.io/example/host@sha256:" + "a" * 64)
    with caplog.at_level(logging.WARNING):
        _startup_guard()()
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        VARIABLE in message
        and "OMNIGENT_SHARED_HOST_IMAGE_REF" in message
        and ROW in message
        for message in messages
    ), messages


def test_startup_is_rejected_after_the_variable_is_removed(monkeypatch) -> None:
    removed = (_deprecated()[0].model_copy(update={"removed": True}),)
    monkeypatch.setattr(legacy_retirement, "OBSOLETE_CONFIGURATION", removed)
    monkeypatch.setenv(VARIABLE, "ghcr.io/example/host@sha256:" + "a" * 64)
    with pytest.raises(ObsoleteConfigurationError, match="no longer honored"):
        _startup_guard()()


def test_startup_ignores_an_unset_obsolete_variable(monkeypatch) -> None:
    removed = (_deprecated()[0].model_copy(update={"removed": True}),)
    monkeypatch.setattr(legacy_retirement, "OBSOLETE_CONFIGURATION", removed)
    monkeypatch.delenv(VARIABLE, raising=False)
    _startup_guard()()
