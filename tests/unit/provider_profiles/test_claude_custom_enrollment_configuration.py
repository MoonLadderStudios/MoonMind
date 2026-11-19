"""Custom enrollment composes existing preferences without enlarging authority."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.materializers import snapshot_provider_configuration
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.provider_profiles.claude_configuration import oauth_configuration_for_profile

TARGET = "/home/app/selected-auth"


def profile():
    return SimpleNamespace(
        runtime_id="claude_code", provider_id="anthropic", credential_source="oauth_volume",
        volume_mount_path=TARGET, volume_ref="selected", credential_generation=7,
        env_template={}, home_path_overrides={"CLAUDE_CONFIG_DIR": TARGET,
            "HOME": "{{runtime_support_dir}}/process-home"},
        file_templates=[{"path": TARGET + "/settings.json", "format": "json",
            "content_template": {"language": "English", "permissions": {"deny": ["Read"]}}},
            {"path": TARGET + "/CLAUDE.md", "format": "text",
             "content_template": "Keep literal " + TARGET + " and {{workspace_path}}."},
            {"path": "{{runtime_support_dir}}/process-home/NOTES.md", "format": "text",
             "content_template": "Private process notes."}],
    )


def test_normalization_preserves_content_home_and_enrollment_binding():
    selected = profile()
    original = deepcopy(vars(selected))
    frozen = snapshot_provider_configuration(selected)
    assert vars(selected) == original
    assert frozen.claudeEnrollmentHome.model_dump() == {
        "volumeRef": "selected", "targetPath": TARGET, "credentialGeneration": 7,
    }
    configuration = frozen.claudeConfiguration
    assert configuration["homePathOverrides"] == {
        "CLAUDE_CONFIG_DIR": "/home/app/.claude", "HOME": "{{runtime_support_dir}}/process-home",
    }
    assert configuration["fileTemplates"][1]["content_template"] == original["file_templates"][1]["content_template"]
    assert configuration["fileTemplates"][2]["path"] == original["file_templates"][2]["path"]


@pytest.mark.parametrize("change", [
    {"home_path_overrides": {"CLAUDE_CONFIG_DIR": "/home/app/other-auth"}},
    {"home_path_overrides": {"HOME": TARGET}},
    {"volume_mount_path": "/home/app/.omnigent"},
    {"file_templates": [{"path": "/home/app/.claude/settings.json", "format": "json", "content_template": {}}]},
    {"file_templates": [{"path": TARGET + "/../other/settings.json", "format": "json", "content_template": {}}]},
    {"file_templates": [{"path": TARGET + "/.credentials.json", "format": "json", "content_template": {}}]},
    {"file_templates": [{"path": TARGET + "/settings.json", "format": "json", "content_template": {"hooks": {}}}]},
])
def test_unqualified_custom_combinations_stay_native(change):
    selected = profile()
    for key, value in change.items():
        setattr(selected, key, value)
    configuration, reason = oauth_configuration_for_profile(selected)
    assert configuration is None and reason
    with pytest.raises(HarnessPlatformError):
        snapshot_provider_configuration(selected)
