"""Settings env shares the existing nonsecret launch authority."""

from copy import deepcopy

import pytest

from moonmind.provider_profiles import claude_configuration as config
from tests.unit.provider_profiles.test_claude_deny_configuration import configuration


def settings(value):
    result = configuration({"deny": []})
    result["fileTemplates"][0]["content_template"] = {"env": value}
    return result


@pytest.mark.parametrize(
    "value",
    [
        {"ANTHROPIC_API_KEY": "fake-key"},
        {"HOME": "/tmp/other"},
        {"NODE_OPTIONS": "--require attacker"},
        {"MOONMIND_FLAG": "1"},
        {"PROJECT_TOKEN": "fake-token"},
        {"PROJECT_URL": "https://user:pass@example.com"},
        {"PROJECT_LABEL": "{{workspace_path}}"},
        {"PROJECT_LABEL": "a\nb"},
        {"PROJECT_LABEL": 1},
        {"PROJECT_LABEL": ""},
        [],
        "invalid",
    ],
)
def test_unsupported_settings_environment_stays_native(value):
    with pytest.raises(ValueError):
        config.validate_configuration(settings(value))


@pytest.mark.parametrize("strategy", ["deep_merge", "append"])
def test_settings_env_requires_explicit_seed(strategy):
    value = settings({"PROJECT_LABEL": "selected"})
    value["fileTemplates"][0]["merge_strategy"] = strategy
    with pytest.raises(ValueError, match="replacement seed"):
        config.validate_configuration(value)


@pytest.mark.parametrize("strategy", ["deep_merge", "replace"])
@pytest.mark.parametrize(
    "last", [{"env": {"PROJECT_LABEL": "final"}}, {"env": {}}, {"language": "English"}]
)
def test_settings_env_matches_native_order_without_mutating_profile(
    tmp_path, strategy, last
):
    import json

    from moonmind.provider_profiles.template_renderer import ProfileTemplateRenderer
    from moonmind.schemas.agent_runtime_models import RuntimeFileTemplate

    value = settings({"PROJECT_LABEL": "initial", "PROJECT_ONLY": "retained"})
    value["fileTemplates"].append(
        {
            **value["fileTemplates"][0],
            "content_template": last,
            "merge_strategy": strategy,
        }
    )
    before = deepcopy(value)
    renderer = ProfileTemplateRenderer(toml_serializer=lambda _: "")
    for item in value["fileTemplates"]:
        renderer._materialize_file_template(
            RuntimeFileTemplate.model_validate(item),
            {"runtime_support_dir": str(tmp_path)},
        )
    native = json.loads((tmp_path / "claude-home/settings.json").read_text())
    admitted = config.validate_configuration(value)
    assert config.settings_environment(admitted) == native.get("env", {})
    assert config.validate_configuration(admitted) == admitted
    projected = config.settings_environment(admitted)
    projected["PROJECT_LABEL"] = "changed"
    assert value == before
    assert config.settings_environment(admitted) == native.get("env", {})


@pytest.mark.parametrize("remove_settings_env", [False, True])
def test_key_snapshot_projects_only_final_settings_env(remove_settings_env):
    from moonmind.omnigent.harness_platform.materializers import (
        claude_provider_config_endpoint,
    )
    from tests.unit.omnigent.test_claude_api_key_provider_config import _api_key_profile

    selected = settings({"PROJECT_LABEL": "settings-value"})
    if remove_settings_env:
        selected["fileTemplates"].append(
            {
                **selected["fileTemplates"][0],
                "content_template": {"language": "English"},
                "merge_strategy": "replace",
            }
        )
    profile = _api_key_profile(
        home_path_overrides=selected["homePathOverrides"],
        file_templates=selected["fileTemplates"],
        env_template={
            "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
            "PROJECT_LABEL": "process-value",
            "PROJECT_SIBLING": "sibling",
        },
    )
    before = deepcopy(vars(profile))
    endpoint, reason = claude_provider_config_endpoint(profile)
    assert endpoint is not None, reason
    assert endpoint.launchEnvironment == {
        "PROJECT_LABEL": "process-value" if remove_settings_env else "settings-value",
        "PROJECT_SIBLING": "sibling",
    }
    assert vars(profile) == before
