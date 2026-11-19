"""Frozen settings environment reaches real host launch and recovery owners."""

import pytest

from tests.integration.omnigent import test_claude_deny_projection as denies

session_factory = denies.session_factory
native_journey = denies.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
ENV = {"PROJECT_LABEL": "settings-value", "PROJECT_ONLY": "settings-only"}
EXPECTED = {**ENV, "PROJECT_SIBLING": "process-sibling"}
SELECTIONS = [
    {
        "harness": harness,
        "claudePreferences": True,
        "sessionSettings": {**denies.SETTINGS, "env": {"PROJECT_LABEL": "obsolete"}},
        "additionalFiles": [
            {
                "path": (
                    "/home/app/.claude/settings.json"
                    if harness == "claude-native"
                    else "{{runtime_support_dir}}/claude-home/settings.json"
                ),
                "format": "json",
                "merge_strategy": "deep_merge",
                "content_template": {"env": ENV},
            }
        ],
        ("oauthTuning" if harness == "claude-native" else "apiKeyTuning"): {
            "PROJECT_LABEL": "process-value",
            "PROJECT_SIBLING": "process-sibling",
            "PROJECT_ONLY": "{{workspace_path}}/overridden-template",
        },
    }
    for harness in ("claude-native", "claude-native-api-key")
]


def assert_environment(journey):
    for key, value in EXPECTED.items():
        assert journey.docker.launch_env[key] == value
    assert journey.docker.launch_env["HOME"] == "/home/app"


@pytest.mark.parametrize("native_journey", SELECTIONS, indirect=True)
async def test_settings_environment_survives_profile_edit_and_cold_successor(
    native_journey,
):
    await denies.assert_frozen_configuration_survives_cold_successor(
        native_journey, {**denies.SETTINGS, "env": ENV}, EXPECTED
    )
    assert_environment(native_journey)


@pytest.mark.parametrize("native_journey", SELECTIONS, indirect=True)
async def test_settings_environment_survives_ready_host_retry(
    native_journey, monkeypatch
):
    await denies.assert_ready_host_retry_keeps_configuration(
        native_journey, monkeypatch, EXPECTED
    )
    assert_environment(native_journey)
