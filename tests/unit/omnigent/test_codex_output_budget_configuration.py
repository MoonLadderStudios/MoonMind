"""Codex's native output budget passes through without changing tool authority."""
from types import SimpleNamespace

import pytest

from moonmind.provider_profiles.codex_configuration import (
    configuration_for_profile,
    oauth_configuration_for_profile,
)
from tests.unit.omnigent.test_codex_profile_configuration import profile


def _configure(value, key_auth):
    if key_auth:
        authored = profile()
        authored.file_templates[0]["content_template"]["tool_output_token_limit"] = value
        return configuration_for_profile(authored)
    authored = SimpleNamespace(
        file_templates=[{
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml", "merge_strategy": "replace",
            "content_template": {"tool_output_token_limit": value}, "permissions": "0600",
        }],
        home_path_overrides={"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
        env_template={}, volume_mount_path="/home/app/.codex",
    )
    return oauth_configuration_for_profile(authored)


@pytest.mark.parametrize("key_auth", [False, True], ids=["oauth", "key"])
@pytest.mark.parametrize("value", [0, 1024, (1 << 63) - 1], ids=["zero", "ordinary", "toml-max"])
def test_codex_output_budget_retains_exact_value(value, key_auth):
    configured, reason = _configure(value, key_auth)
    assert configured is not None, reason
    assert configured["document"]["tool_output_token_limit"] == value


@pytest.mark.parametrize("key_auth", [False, True], ids=["oauth", "key"])
@pytest.mark.parametrize(
    "value", [-1, 1 << 63, True, 1.5, "1024", None, [1024], {}],
    ids=["negative", "overflow", "boolean", "fraction", "string", "null", "array", "object"],
)
def test_invalid_codex_output_budget_stays_native(value, key_auth):
    configured, reason = _configure(value, key_auth)
    assert configured is None
    assert reason
