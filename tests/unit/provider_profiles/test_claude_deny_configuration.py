import pytest

from moonmind.provider_profiles import claude_configuration as config


def configuration(permissions):
    return {
        "homePathOverrides": {
            "CLAUDE_CONFIG_DIR": "{{runtime_support_dir}}/claude-home"
        },
        "fileTemplates": [
            {
                "path": "{{runtime_support_dir}}/claude-home/settings.json",
                "format": "json",
                "content_template": {"permissions": permissions},
            }
        ],
    }


def test_literal_deny_configuration_is_admitted_and_extracted():
    rules = ["Read(**/.env)", "Bash(git push *)", "WebFetch"]
    value = config.validate_configuration(configuration({"deny": rules}))
    assert config.denied_tools(value) == rules


@pytest.mark.parametrize(
    "permissions",
    [
        {"allow": ["Bash"]},
        {"deny": ["Read"], "defaultMode": "bypassPermissions"},
        {"deny": ["mcp__remote__tool"]},
        {"deny": ["Read(a,b)"]},
        {"deny": ["Read({{workspace_path}}/secret)"]},
        {"deny": ["Bash(x\ny)"]},
        {"deny": "Read"},
        {"deny": ["UnknownTool"]},
        {"deny": ["Write(**/private/**)"]},
        {"deny": ["Glob(**/private/**)"]},
        {"deny": ["Grep(**/private/**)"]},
    ],
)
def test_unsupported_permission_authority_stays_native(permissions):
    with pytest.raises(ValueError):
        config.validate_configuration(configuration(permissions))


def test_multiple_or_merged_settings_do_not_guess_effective_deny():
    value = configuration({"deny": ["Read"]})
    value["fileTemplates"].append(dict(value["fileTemplates"][0]))
    with pytest.raises(ValueError, match="one replace"):
        config.validate_configuration(value)
    value["fileTemplates"].pop()
    value["fileTemplates"][0]["merge_strategy"] = "deep_merge"
    with pytest.raises(ValueError, match="one replace"):
        config.validate_configuration(value)


def test_oauth_and_key_extract_same_rules_without_changing_credential_home():
    key = configuration({"deny": ["Read(**/.env)"]})
    oauth = configuration({"deny": ["Read(**/.env)"]})
    oauth["homePathOverrides"]["CLAUDE_CONFIG_DIR"] = "/home/app/.claude"
    oauth["fileTemplates"][0]["path"] = "/home/app/.claude/settings.json"
    assert config.denied_tools(key) == config.denied_tools(oauth) == ["Read(**/.env)"]
    assert oauth["homePathOverrides"]["CLAUDE_CONFIG_DIR"] == "/home/app/.claude"


def test_frozen_host_rules_merge_with_workflow_and_authored_denies(monkeypatch):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from tests.unit.omnigent.test_headless_claude_permission_mode import (
        _bind_generic,
        _launch_args,
        _request,
    )

    original = GenericOmnigentHostRealizer._bind_exact_host

    def bind(self, request, plan, host, binding):
        return original(
            self,
            request,
            plan,
            {**host, "claudeDeniedTools": ["Read(**/.env)"]},
            binding,
        )

    monkeypatch.setattr(GenericOmnigentHostRealizer, "_bind_exact_host", bind)
    bound = _bind_generic(
        _request({"terminalLaunchArgs": ["--disallowedTools", "Bash(git push *)"]}),
        "claude-native",
    )
    args = _launch_args(bound)
    assert (
        args[args.index("--disallowedTools") + 1]
        == "Bash(git push *),Read(**/.env),ScheduleWakeup"
    )
    assert "--setting-sources" not in args and "--settings" not in args


@pytest.mark.asyncio
async def test_actual_key_materializer_preserves_frozen_rules(tmp_path):
    from tests.unit.omnigent.test_claude_api_key_provider_config import _provision

    value = configuration({"deny": ["Read(**/.env)"]})
    handles, _, _, _, resolver = await _provision(
        tmp_path,
        profile_values={
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "api_key_env",
            "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
            "env_template": {
                "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}
            },
            "home_path_overrides": value["homePathOverrides"],
            "file_templates": value["fileTemplates"],
        },
        materializer_ref="omnigent-provider-config@1",
    )
    assert resolver.resolved == ["ANTHROPIC_API_KEY"]
    assert handles[0].claudeDeniedTools == ["Read(**/.env)"]
    from moonmind.omnigent.host_services.runtime_scripts import (
        claude_denied_tools_from_handles,
    )

    assert claude_denied_tools_from_handles([handles[0].model_dump()]) == [
        "Read(**/.env)"
    ]


@pytest.mark.parametrize(
    "authored",
    [
        ["--disallowedTools=Read"],
        ["--disallowed-tools=Read"],
        ["--disallowedTools", "Read"],
        ["--disallowed-tools", "Read"],
    ],
)
def test_existing_cli_spellings_normalize_for_upstream_merge(authored):
    from moonmind.provider_profiles.claude_configuration import (
        merge_disallowed_tools,
    )

    args = list(authored)
    merge_disallowed_tools(args, ["Bash(git push *)"])
    assert args == ["--disallowedTools", "Read,Bash(git push *)"]


def test_sensitive_looking_rule_is_not_projected_or_echoed():
    from types import SimpleNamespace

    value = configuration({"deny": ["Bash(ghp_" + "a" * 32 + ")"]})
    admitted, reason = config.configuration_for_profile(
        SimpleNamespace(
            home_path_overrides=value["homePathOverrides"],
            file_templates=value["fileTemplates"],
        )
    )
    assert admitted is None
    assert "ghp_" not in reason


@pytest.mark.parametrize(
    "arguments",
    [
        ["--disallowedTools", "Read", "--disallowed-tools=Write"],
        ["--disallowedTools"],
        ["--disallowed-tools", "--verbose"],
        "--verbose",
    ],
)
def test_frozen_rules_reject_ambiguous_authored_cli(monkeypatch, arguments):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
    from tests.unit.omnigent.test_headless_claude_permission_mode import (
        _bind_generic,
        _request,
    )

    original = GenericOmnigentHostRealizer._bind_exact_host

    def bind(self, request, plan, host, binding):
        return original(
            self,
            request,
            plan,
            {**host, "claudeDeniedTools": ["Read(**/.env)"]},
            binding,
        )

    monkeypatch.setattr(GenericOmnigentHostRealizer, "_bind_exact_host", bind)
    with pytest.raises(ValueError):
        _bind_generic(_request({"terminalLaunchArgs": arguments}), "claude-native")


def test_projection_retains_provider_guard_outside_generic_lifecycle():
    with pytest.raises(ValueError, match="Claude harness"):
        config.project_denied_tools(harness_id="codex-native", arguments=[], rules=["Read"])
