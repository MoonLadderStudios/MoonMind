"""An explicit search restriction preserves native Codex Profile intent."""
from types import SimpleNamespace

import pytest

from moonmind.provider_profiles.codex_configuration import (
    configuration_for_profile,
    oauth_configuration_for_profile,
)
from tests.unit.omnigent.test_codex_profile_configuration import profile


def oauth_profile(value):
    return SimpleNamespace(
        file_templates=[{
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml", "merge_strategy": "replace",
            "content_template": {"web_search": value}, "permissions": "0600",
        }],
        home_path_overrides={"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
        env_template={}, volume_mount_path="/home/app/.codex",
    )


@pytest.mark.parametrize("key_auth", [False, True])
def test_explicit_disabled_search_retains_exact_shared_configuration(key_auth):
    authored = profile() if key_auth else oauth_profile("disabled")
    if key_auth:
        authored.file_templates[0]["content_template"]["web_search"] = "disabled"
    configured, reason = (configuration_for_profile if key_auth else oauth_configuration_for_profile)(authored)
    assert configured is not None, reason
    assert configured["document"]["web_search"] == "disabled"


@pytest.mark.parametrize("value", ["live", "cached", "enabled", "", None, False, {}, ["disabled"]],
                         ids=["live", "cached", "enabled", "empty", "null", "boolean", "mapping", "array"])
@pytest.mark.parametrize("key_auth", [False, True])
def test_search_enabling_or_ambiguous_values_keep_native_route(value, key_auth):
    authored = profile() if key_auth else oauth_profile(value)
    if key_auth:
        authored.file_templates[0]["content_template"]["web_search"] = value
    configured, reason = (configuration_for_profile if key_auth else oauth_configuration_for_profile)(authored)
    assert configured is None
    assert reason
