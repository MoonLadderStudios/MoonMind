"""Obsolete Omnigent configuration is never silently ignored at startup.

Source issues: MoonLadderStudios/MoonMind#3835 (required work section 10),
MoonLadderStudios/MoonMind#5342.

Before an image or environment alias is removed, a deployment that still supplies
it must get an actionable failure naming the replacement; after removal the value
is rejected outright. These tests drive the real API startup helper rather than
the pure guard so the wiring at the startup boundary is proven, not assumed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging

import pytest

from moonmind.omnigent import legacy_retirement
from moonmind.omnigent.legacy_retirement import (
    ObsoleteConfiguration,
    ObsoleteConfigurationError,
)

VARIABLE = "OMNIGENT_HOST_IMAGE_REF"
ROW = "omnigent.legacy.host_image_variable_alias"


@pytest.fixture(autouse=True)
def _startup_modules_loaded() -> None:
    """Import the startup modules before the test's log capture starts.

    Importing ``api_service.main`` configures logging, which detaches the
    capture handler of whichever test triggers the import.
    """

    importlib.import_module("api_service.main")
    importlib.import_module("moonmind.workflows.temporal.worker_runtime")


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


# ---------------------------------------- the deprecated generic host image

# What .env-template shipped for these variables before their deprecation: a
# deployment whose .env was copied from it still carries exactly these values.
TEMPLATE_DEFAULTS = {
    "OMNIGENT_HOST_IMAGE_REF": "",
    "OMNIGENT_HOST_IMAGE": "ghcr.io/omnigent-ai/omnigent-host",
    "OMNIGENT_HOST_IMAGE_TAG": "latest",
}


def _host_image_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records if ROW in record.getMessage()
    ]


def _startup_warnings(caplog: pytest.LogCaptureFixture) -> dict[str, list[str]]:
    """Run the API and the worker startup checks; collect each one's warnings."""

    from moonmind.workflows.temporal import worker_runtime

    found: dict[str, list[str]] = {}
    for process, check in (
        ("api", _startup_guard()),
        (
            "worker",
            lambda: legacy_retirement.enforce_obsolete_configuration_at_startup(
                worker_runtime.logger
            ),
        ),
    ):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            check()
        found[process] = _host_image_warnings(caplog)
    return found


def _with_template_defaults(monkeypatch) -> None:
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", raising=False)
    for variable, value in TEMPLATE_DEFAULTS.items():
        monkeypatch.setenv(variable, value)


@pytest.mark.parametrize("stage", ["deprecated", "removed"])
def test_untouched_template_defaults_upgrade_without_an_edit(
    monkeypatch, caplog: pytest.LogCaptureFixture, stage: str
) -> None:
    """An ``.env`` copied from the old template neither warns nor fails.

    ``api`` and the workers load ``.env`` directly, so removing the variables
    from the template and the Compose defaults does not remove them from an
    existing deployment's process environment.
    """

    if stage == "removed":
        monkeypatch.setattr(
            legacy_retirement,
            "OBSOLETE_CONFIGURATION",
            tuple(
                (
                    entry.model_copy(update={"removed": True})
                    if entry.retirement_path_id == ROW
                    else entry
                )
                for entry in legacy_retirement.OBSOLETE_CONFIGURATION
            ),
        )
    _with_template_defaults(monkeypatch)

    assert _startup_warnings(caplog) == {"api": [], "worker": []}


@pytest.mark.parametrize(
    ("variable", "pinned"),
    [
        ("OMNIGENT_HOST_IMAGE_REF", "ghcr.io/example/host@sha256:" + "a" * 64),
        ("OMNIGENT_HOST_IMAGE", "registry.example/omnigent-host"),
        ("OMNIGENT_HOST_IMAGE_TAG", "2026.11.1"),
    ],
)
def test_pinned_legacy_host_image_names_the_shared_ref_at_startup(
    monkeypatch, caplog: pytest.LogCaptureFixture, variable: str, pinned: str
) -> None:
    _with_template_defaults(monkeypatch)
    monkeypatch.setenv(variable, pinned)

    for process, warnings in _startup_warnings(caplog).items():
        assert len(warnings) == 1, (process, warnings)
        assert f"{variable} is obsolete" in warnings[0], process
        assert "OMNIGENT_SHARED_HOST_IMAGE_REF" in warnings[0], process


def test_release_rendered_generic_host_ref_is_not_deployment_configuration(
    monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """The release controller still renders its ``codex`` ref under the alias."""

    rendered = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "e" * 64
    desired_state = tmp_path / "desired-state.json"
    desired_state.write_text(
        json.dumps(
            {
                "omnigentRelease": {
                    "serverImageRef": "ghcr.io/omnigent-ai/omnigent-server@sha256:"
                    + "c" * 64,
                    "hostImageRefs": {"codex": rendered},
                }
            }
        )
    )
    _with_template_defaults(monkeypatch)
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(desired_state)
    )
    monkeypatch.setenv(VARIABLE, rendered)

    assert _startup_warnings(caplog) == {"api": [], "worker": []}


# ---------------------------------------------- the deprecated OpenCode pin

OPENCODE_VARIABLE = "OMNIGENT_OPENCODE_HOST_IMAGE_REF"
OPENCODE_ROW = "omnigent.legacy.opencode_host_image_alias"


def _opencode_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if OPENCODE_VARIABLE in record.getMessage()
    ]


def test_supplied_opencode_image_pin_warns_without_failing_startup(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", raising=False)
    monkeypatch.setenv(OPENCODE_VARIABLE, "ghcr.io/example/opencode@sha256:" + "a" * 64)
    with caplog.at_level(logging.WARNING):
        _startup_guard()()
    warnings = _opencode_warnings(caplog)
    assert len(warnings) == 1
    assert "OMNIGENT_SHARED_HOST_IMAGE_REF" in warnings[0]
    assert OPENCODE_ROW in warnings[0]


def test_release_rendered_opencode_ref_is_not_deployment_configuration(
    monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """The release controller writes its recorded refs into ``.env.deploy``.

    A default deployment never chose the deprecated variable, so the value the
    installed release rendered must not warn; a different value is the
    deployment's own pin and still does.
    """

    rendered = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "b" * 64
    desired_state = tmp_path / "desired-state.json"
    desired_state.write_text(
        json.dumps(
            {
                "omnigentRelease": {
                    "serverImageRef": "ghcr.io/omnigent-ai/omnigent-server@sha256:"
                    + "c" * 64,
                    "hostImageRefs": {"opencode": rendered, "shared": rendered},
                }
            }
        )
    )
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(desired_state)
    )
    monkeypatch.setenv(OPENCODE_VARIABLE, rendered)
    with caplog.at_level(logging.WARNING):
        _startup_guard()()
    assert _opencode_warnings(caplog) == []

    monkeypatch.setenv(OPENCODE_VARIABLE, "ghcr.io/example/opencode@sha256:" + "d" * 64)
    with caplog.at_level(logging.WARNING):
        _startup_guard()()
    assert len(_opencode_warnings(caplog)) == 1


# ---------------------------------------------- every consuming process


def test_agent_runtime_worker_startup_enforces_the_same_check(monkeypatch) -> None:
    """The worker is separately restartable and consumes the same variables.

    Installing the check only in the API lifespan means restarting or deploying
    ``temporal-worker-agent-runtime`` without the API produces no deprecation
    warning during the window, and after removal the worker would silently
    accept a variable the API rejects.
    """

    from moonmind.workflows.temporal import worker_runtime

    removed = (_deprecated()[0].model_copy(update={"removed": True}),)
    monkeypatch.setattr(legacy_retirement, "OBSOLETE_CONFIGURATION", removed)
    monkeypatch.setenv(VARIABLE, "ghcr.io/example/host@sha256:" + "a" * 64)

    called: list[str] = []
    monkeypatch.setattr(
        worker_runtime,
        "describe_configured_workers",
        lambda **kwargs: called.append("topology") or _unreachable_topology(),
    )
    with pytest.raises(ObsoleteConfigurationError, match="no longer honored"):
        asyncio.run(worker_runtime.main_async())
    assert called == []


def test_agent_runtime_worker_startup_warns_during_deprecation(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    from moonmind.workflows.temporal import worker_runtime

    monkeypatch.setattr(legacy_retirement, "OBSOLETE_CONFIGURATION", _deprecated())
    monkeypatch.setenv(VARIABLE, "ghcr.io/example/host@sha256:" + "a" * 64)
    with caplog.at_level(logging.WARNING):
        legacy_retirement.enforce_obsolete_configuration_at_startup(
            worker_runtime.logger
        )
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        VARIABLE in message and "OMNIGENT_SHARED_HOST_IMAGE_REF" in message
        for message in messages
    ), messages


def _unreachable_topology():
    raise AssertionError(
        "worker startup must reject obsolete configuration before topology use"
    )
