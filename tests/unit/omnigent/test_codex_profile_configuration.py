"""Custom Codex model settings reach the shared lifecycle without losing tuning."""

from types import SimpleNamespace
import copy
import json

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    codex_provider_config_endpoint,
)
from tests.helpers.provider_egress import minimax_egress  # noqa: F401

pytestmark = pytest.mark.usefixtures("minimax_egress")


def profile(**changes):
    content = {
        "model": "example-model",
        "model_provider": "custom",
        "model_reasoning_effort": "high",
        "model_providers": {
            "custom": {
                "name": "Custom Responses",
                "base_url": "https://api.minimax.io/v1",
                "env_key": "CUSTOM_MODEL_KEY",
                "wire_api": "responses",
                "requires_openai_auth": False,
                "request_max_retries": 4,
                "stream_max_retries": 10,
                "stream_idle_timeout_ms": 300000,
            }
        },
    }
    values = {
        "profile_id": "operator-codex",
        "runtime_id": "codex_cli",
        "provider_id": "minimax",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "composite",
        "secret_refs": {"provider_api_key": "env://CUSTOM_MODEL_KEY"},
        "env_template": {"CUSTOM_MODEL_KEY": {"from_secret_ref": "provider_api_key"}},
        "file_templates": [
            {
                "path": "{{runtime_support_dir}}/codex-home/config.toml",
                "format": "toml",
                "merge_strategy": "deep_merge",
                "content_template": content,
                "permissions": "0600",
            }
        ],
        "home_path_overrides": {"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
        "default_model": "example-model",
        "model_overrides": {},
        "command_behavior": {"suppress_default_model_flag": True},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_custom_codex_configuration_has_an_exact_shared_translation():
    endpoint, reason = codex_provider_config_endpoint(profile())
    assert endpoint is not None, reason
    assert endpoint.secretRole == "provider_api_key"
    assert endpoint.baseUrl == "https://api.minimax.io/v1"
    assert endpoint.defaultModel == "example-model"
    configured = endpoint.codexConfiguration
    assert configured["provider"]["request_max_retries"] == 4
    assert configured["provider"]["stream_max_retries"] == 10
    assert configured["provider"]["stream_idle_timeout_ms"] == 300000
    assert configured["document"]["model_reasoning_effort"] == "high"


def test_custom_provider_keeps_safe_project_values_without_another_key():
    selected = profile()
    selected.env_template["PROJECT_RUNTIME_STYLE"] = "  exact $(not-executed) literal  "
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is not None, reason
    assert endpoint.secretRole == "provider_api_key"
    assert endpoint.baseUrl == "https://api.minimax.io/v1"
    assert endpoint.launchEnvironment == {"PROJECT_RUNTIME_STYLE": "  exact $(not-executed) literal  "}
    assert endpoint.codexConfiguration["provider"]["env_key"] == "CUSTOM_MODEL_KEY"


@pytest.mark.parametrize("extra", [
    {"HOME": "/another-home"},
    {"PROJECT_RUNTIME_STYLE": {"from_secret_ref": "another_key"}},
    {"PROJECT_RUNTIME_STYLE": "{{workspace_path}}"},
])
def test_custom_provider_cannot_smuggle_other_authority_as_project_values(extra):
    selected = profile()
    selected.env_template.update(extra)
    selected.secret_refs["another_key"] = "env://ANOTHER_KEY"
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is None
    assert reason


@pytest.mark.parametrize("suppress_model_default", [False, True])
def test_plain_openai_key_preferences_do_not_require_another_provider_declaration(suppress_model_default):
    selected = profile(
        provider_id="openai", runtime_materialization_mode="api_key_env", command_behavior={"suppress_default_model_flag": suppress_model_default},
        secret_refs={"provider_api_key": "env://OPENAI_API_KEY"},
        env_template={"OPENAI_API_KEY": {"from_secret_ref": "provider_api_key"}},
        file_templates=[{
            "path": "{{runtime_support_dir}}/codex-home/config.toml", "format": "toml",
            "merge_strategy": "deep_merge", "permissions": "0600",
            "content_template": {"developer_instructions": "Preserve native authority."},
        }],
    )
    endpoint, reason = codex_provider_config_endpoint(selected)
    if suppress_model_default:
        assert endpoint is None
        return
    assert endpoint is not None, reason
    assert endpoint.family == "openai" and endpoint.secretRole == "provider_api_key"
    assert endpoint.codexConfiguration == {
        "document": {"developer_instructions": "Preserve native authority."}, "privateHomeOnly": True,
    }


def test_unchanged_managed_minimax_recipe_is_repaired_without_rewriting_the_profile():
    import copy
    from moonmind.provider_profiles.codex_configuration import (
        MINIMAX_MODEL,
        minimax_file_templates,
    )

    selected = profile(
        profile_id="codex_minimax_m27",
        default_model=MINIMAX_MODEL,
        model_overrides={"codex_profile_name": "m27"},
        secret_refs={"provider_api_key": "env://MINIMAX_API_KEY"},
        env_template={"MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}},
        file_templates=minimax_file_templates(legacy=True),
    )
    recorded = copy.deepcopy(selected.file_templates)
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is not None, reason
    assert endpoint.codexConfiguration["managedRecipeRepaired"] is True
    assert endpoint.codexConfiguration["provider"]["wire_api"] == "responses"
    assert "profile" not in endpoint.codexConfiguration["document"]
    assert selected.file_templates == recorded
    selected.file_templates[0]["content_template"]["model_providers"]["minimax"][
        "request_max_retries"
    ] = 5
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is None
    assert "Responses wire" in reason
    assert (
        selected.file_templates[0]["content_template"]["model_providers"]["minimax"][
            "wire_api"
        ]
        == "chat"
    )


def test_native_private_provider_keeps_explicit_tuning_and_generated_auth(tmp_path):
    import copy
    import tomllib
    from moonmind.provider_profiles.codex_configuration import (
        preserve_provider_settings,
        render_toml,
    )

    endpoint, _reason = codex_provider_config_endpoint(profile())
    generated = {
        "model": "example-model",
        "model_provider": "custom",
        "model_providers": {
            "generated": {
                "name": "generated",
                "base_url": endpoint.baseUrl,
                "wire_api": "responses",
                "experimental_bearer_token": "synthetic-runtime-key",
                "request_max_retries": 3,
                "stream_max_retries": 3,
                "stream_idle_timeout_ms": 60000,
            }
        },
        "mcp_servers": {"owned": {"command": "owned-transport"}},
    }
    before = copy.deepcopy(generated)
    (tmp_path / "config.toml").write_text(render_toml(generated))
    preserve_provider_settings(
        endpoint.codexConfiguration,
        tmp_path,
        ["app-server", "-c", 'model_provider="generated"'],
    )
    after = tomllib.loads((tmp_path / "config.toml").read_text())
    assert (
        after["model_providers"]["generated"]["experimental_bearer_token"]
        == "synthetic-runtime-key"
    )
    assert after["model_providers"]["generated"]["request_max_retries"] == 4
    assert after["model_providers"]["generated"]["stream_max_retries"] == 10
    assert after["model_providers"]["generated"]["stream_idle_timeout_ms"] == 300000
    for key in ("request_max_retries", "stream_max_retries", "stream_idle_timeout_ms"):
        before["model_providers"]["generated"][key] = after["model_providers"][
            "generated"
        ][key]
    assert after == before


@pytest.mark.parametrize(
    "extra",
    [
        {"requires_openai_auth": True},
        {"http_headers": {"X-Private-Access": "authored-secret"}},
        {"env_http_headers": {"X-Private-Access": "UNDECLARED_KEY"}},
        {"query_params": {"access": "authored-secret"}},
    ],
)
def test_authentication_that_the_selected_materializer_cannot_carry_stays_explicit(
    extra,
):
    selected = profile()
    selected.file_templates[0]["content_template"]["model_providers"]["custom"].update(
        extra
    )
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is None
    assert "authentication" in reason


def test_unselected_config_and_mcp_environment_are_not_recorded_as_launch_metadata():
    selected = profile()
    selected.file_templates[0]["content_template"]["mcp_servers"] = {
        "private": {"command": "tool", "env": {"CUSTOM_ACCESS": "authored-secret"}}
    }
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is None
    assert "configuration" in reason


def test_selected_profile_is_flattened_without_leaving_unselected_configuration():
    selected = profile(model_overrides={"codex_profile_name": "selected"})
    selected.file_templates[0]["content_template"]["profiles"] = {
        "selected": {"model": "selected-model", "model_reasoning_effort": "medium"},
        "other": {"model": "unselected-model"},
    }
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is not None, reason
    assert endpoint.defaultModel == "selected-model"
    assert endpoint.codexConfiguration["document"]["model_reasoning_effort"] == "medium"
    assert "profiles" not in endpoint.codexConfiguration["document"]


@pytest.mark.parametrize("settings", [
    {"developer_instructions": ["not a string"]},
    {"hide_agent_reasoning": "false"},
    {"project_doc_max_bytes": True},
    {"project_doc_fallback_filenames": "PROJECT.md"},
    {"history": {"persistence": "other"}},
    {"history": {"max_bytes": -1}},
    {"history": {"path": "/outside/the/private/home"}},
])
def test_invalid_session_preferences_never_reach_shared_materialization(settings):
    selected = profile()
    selected.file_templates[0]["content_template"].update(settings)
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is None
    assert "configuration" in reason


@pytest.mark.asyncio
async def test_custom_configuration_uses_the_existing_private_credential_volume(
    tmp_path,
):
    import json
    from tests.unit.omnigent.test_codex_api_key_provider_config import _KEY, _provision

    selected = vars(profile())
    for key in ("profile_id", "runtime_id", "provider_id"):
        selected.pop(key)
    selected["secret_refs"] = {"provider_api_key": "env://OPENAI_API_KEY"}
    handles, backend, artifacts, resolver = await _provision(
        tmp_path, profile_values=selected
    )
    assert resolver.resolved == ["OPENAI_API_KEY"]
    (handle,) = handles
    assert handle.runtimeEnvironment == {
        "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config",
        "MOONMIND_CODEX_PROFILE_CONFIG_FILE": "/home/app/.moonmind-provider-config/codex-profile.json",
    }
    (payload,) = [json.loads(data) for _argv, data in backend.calls if data]
    native = payload.pop("_moonmind_codex_configuration")
    assert native["provider"]["stream_max_retries"] == 10
    assert payload["providers"]["moonmind"]["openai"]["api_key"] == _KEY
    (writer,) = [argv for argv, data in backend.calls if data]
    assert writer[writer.index("--entrypoint") + 1] == "python3"
    compile(writer[writer.index("-c") + 1], "credential-writer", "exec")
    (verifier,) = [
        argv
        for argv, data in backend.calls
        if "codex-profile.json" in " ".join(argv) and not data
    ]
    assert "600" in " ".join(verifier)
    assert _KEY not in json.dumps(
        {
            "handle": handle.model_dump(mode="json"),
            "evidence": artifacts.payloads,
            "argv": [argv for argv, _data in backend.calls],
        }
    )


@pytest.mark.parametrize("session_settings", [
    {},
    {
        "developer_instructions": "Preserve the requested behavior and verified progress.",
        "hide_agent_reasoning": True,
        "show_raw_agent_reasoning": False,
        "project_doc_max_bytes": 65536,
        "project_doc_fallback_filenames": ["PROJECT.md", "GUIDANCE.md"],
        "history": {"persistence": "none", "max_bytes": 1048576},
    },
])
def test_installed_launch_shim_preserves_native_generated_configuration(tmp_path, session_settings):
    import json
    import os
    import subprocess
    import sys
    import tomllib
    from pathlib import Path
    from moonmind.provider_profiles import codex_configuration

    selected = profile()
    selected.file_templates[0]["content_template"].update(session_settings)
    endpoint, reason = codex_provider_config_endpoint(selected)
    assert endpoint is not None, reason
    configuration = endpoint.codexConfiguration
    config = tmp_path / "profile.json"
    config.write_text(json.dumps(configuration))
    home = tmp_path / "codex-home"
    helper = Path(codex_configuration.__file__)
    wrapper = tmp_path / "codex"
    context = tmp_path / "context"
    context.write_text('#!/bin/sh\nexec "$@"\n')
    context.chmod(0o700)
    binary = tmp_path / "native-binary"
    binary.write_text(
        '#!/bin/sh\ncp "$CODEX_HOME/config.toml" "$CAPTURED_CONFIG"\nprintf "%s\\n" "$@" > "$CAPTURED_ARGS"\n'
    )
    binary.chmod(0o700)
    environment = {
        **os.environ,
        "CODEX_HOME": str(home),
        "CAPTURED_CONFIG": str(tmp_path / "captured.toml"),
        "CAPTURED_ARGS": str(tmp_path / "args"),
    }
    subprocess.run(
        [
            sys.executable,
            str(helper),
            "prepare",
            str(config),
            str(binary),
            str(wrapper),
            str(context),
        ],
        env=environment,
        check=True,
        capture_output=True,
    )
    assert (
        tomllib.loads((home / "config.toml").read_text()) == configuration["document"]
    )
    assert (home / "config.toml").stat().st_mode & 0o777 == 0o600
    private_home = tmp_path / "private-session-home"
    private_home.mkdir()
    generated = {
        **configuration["document"],
        "model_providers": {
            "generated": {
                "wire_api": "responses",
                "experimental_bearer_token": "synthetic-native-auth",
                "request_max_retries": 3,
            }
        },
    }
    (private_home / "config.toml").write_text(
        codex_configuration.render_toml(generated)
    )
    # Omnigent selects a private per-session HOME after copying base settings
    # and adding its generated provider/auth and native transport configuration.
    subprocess.run(
        [str(wrapper), "app-server", "-c", 'model_provider="generated"'],
        env={**environment, "CODEX_HOME": str(private_home)},
        check=True,
        capture_output=True,
    )
    captured = tomllib.loads((tmp_path / "captured.toml").read_text())
    for key, value in session_settings.items():
        assert captured[key] == value
    assert captured["model_providers"]["generated"]["request_max_retries"] == 4
    assert captured["model_providers"]["generated"]["stream_max_retries"] == 10
    assert (
        captured["model_providers"]["generated"]["experimental_bearer_token"]
        == "synthetic-native-auth"
    )
    assert (tmp_path / "args").read_text().splitlines() == [
        "app-server",
        "-c",
        'model_provider="generated"',
    ]
    assert "synthetic-native-auth" not in wrapper.read_text()


@pytest.mark.asyncio
async def test_queued_direct_materialization_repairs_only_the_unchanged_managed_recipe(
    tmp_path,
):
    import tomllib

    from moonmind.provider_profiles.codex_configuration import (
        MINIMAX_MODEL,
        minimax_file_templates,
    )
    from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
    from moonmind.provider_profiles.isolation_policy import derive_isolation_policy
    from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer
    from tests.unit.workflows.adapters.test_materializer import StaticSecretResolver

    selected = profile(
        profile_id="codex_minimax_m27",
        default_model=MINIMAX_MODEL,
        model_overrides={"codex_profile_name": "m27"},
        secret_refs={"provider_api_key": "env://MINIMAX_API_KEY"},
        env_template={"MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}},
        file_templates=minimax_file_templates(legacy=True),
    )
    policy = derive_isolation_policy(
        runtime_id="codex_cli",
        provider_id="minimax",
        authentication_method="api_key",
        credential_source="secret_ref",
        runtime_materialization_mode="composite",
    )
    recorded = ManagedRuntimeProfile(**vars(selected), clear_env_keys=list(policy.keys))
    before = recorded.model_dump(mode="json")
    materializer = ProviderProfileMaterializer(
        base_env={},
        secret_resolver=StaticSecretResolver(
            {"env://MINIMAX_API_KEY": "synthetic-managed-key"}
        ),
    )
    environment, _command = await materializer.materialize(
        recorded, runtime_support_dir=str(tmp_path)
    )
    actual = tomllib.loads((tmp_path / "codex-home" / "config.toml").read_text())
    assert actual["model"] == MINIMAX_MODEL
    assert actual["model_providers"]["minimax"]["wire_api"] == "responses"
    assert "profile" not in actual
    assert environment["MINIMAX_API_KEY"] == "synthetic-managed-key"
    assert recorded.model_dump(mode="json") == before


@pytest.mark.parametrize("format_,content,filename,expected", [
    ("text", "Keep project instructions from the selected Profile.\n", "AGENTS.md", "Keep project instructions"),
    ("json", {"project": "{{workspace_path}}", "style": "concise"}, "preferences/settings.json", '"style": "concise"'),
    ("text", "Bound to the current workspace.", "workspace-{{workspace_path}}.txt", "current workspace"),
])
def test_additional_profile_files_reach_the_private_native_home(tmp_path, format_, content, filename, expected):
    from moonmind.provider_profiles.codex_configuration import (
        configuration_for_profile,
    )
    selected = profile()
    selected.file_templates.append({
        "path": "{{runtime_support_dir}}/codex-home/" + filename,
        "format": format_, "merge_strategy": "replace",
        "content_template": content, "permissions": "0600",
    })
    before = copy.deepcopy(vars(selected))
    configuration, reason = configuration_for_profile(selected)
    assert configuration is not None, reason
    private_home = tmp_path / "private-native-home"
    private_home.mkdir()
    auth = private_home / "auth.json"
    auth.write_text("keep-owned-auth")
    source_home = tmp_path / "enrolled-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text("keep-enrollment")
    workspace = tmp_path / "repository"
    workspace.mkdir()
    from moonmind.provider_profiles.codex_configuration import configure_profile_files

    configure_profile_files(configuration, private_home, workspace)
    actual = private_home / filename.replace("{{workspace_path}}", str(workspace))
    assert expected in actual.read_text()
    assert actual.stat().st_mode & 0o777 == 0o600
    if format_ == "json":
        assert json.loads(actual.read_text())["project"] == str(workspace)
    assert auth.read_text() == "keep-owned-auth"
    assert list(source_home.iterdir()) == [source_home / "auth.json"]
    assert vars(selected) == before
    configure_profile_files(configuration, private_home, workspace)
    assert expected in actual.read_text()


@pytest.mark.parametrize("relative_path", [
    "{{provider_api_key}}.txt", "{{ provider_api_key }}.txt", "nested/{{unavailable}}.json",
])
def test_additional_file_paths_requiring_unavailable_context_keep_native_delivery(relative_path):
    from moonmind.provider_profiles.codex_configuration import configuration_for_profile

    selected = profile()
    selected.file_templates.append({
        "path": "{{runtime_support_dir}}/codex-home/" + relative_path,
        "format": "text", "merge_strategy": "replace", "permissions": "0600",
        "content_template": "Nonsecret file content.",
    })
    before = copy.deepcopy(vars(selected))
    configuration, reason = configuration_for_profile(selected)
    assert configuration is None
    assert reason
    assert vars(selected) == before


@pytest.mark.parametrize("path", ["auth.json", "config.toml", "sessions/history.jsonl", "archived_sessions/history.jsonl", "../outside", ".moonmind-profile-file-digest"])
def test_recorded_additional_files_cannot_replace_native_authority(tmp_path, path):
    from moonmind.provider_profiles.codex_configuration import configure_profile_files
    home = tmp_path / "native-home"
    home.mkdir()
    original = home / "auth.json"
    original.write_text("preserve-native-auth")
    configuration = {"additionalFiles": [{
        "path": "{{runtime_support_dir}}/" + path, "format": "text",
        "merge_strategy": "replace", "content_template": "unselected-data",
        "permissions": "0600",
    }]}
    with pytest.raises(ValueError):
        configure_profile_files(configuration, home, tmp_path)
    assert original.read_text() == "preserve-native-auth"
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("reference", ["{{runtime_support_dir}}/elsewhere.json", "{{runtime_support_dir}}/codex-home-other/settings.json"])
def test_additional_file_references_outside_declared_home_keep_native_delivery(reference):
    from moonmind.provider_profiles.codex_configuration import configuration_for_profile
    selected = profile()
    selected.file_templates.append({
        "path": "{{runtime_support_dir}}/codex-home/settings.json",
        "format": "json", "merge_strategy": "replace", "permissions": "0600",
        "content_template": {"otherFile": reference},
    })
    configuration, reason = configuration_for_profile(selected)
    assert configuration is None
    assert reason


def test_interrupted_profile_file_projection_preserves_confirmed_append(tmp_path, monkeypatch):
    from moonmind.provider_profiles.codex_configuration import configure_profile_files
    from moonmind.provider_profiles.template_renderer import ProfileTemplateRenderer
    home = tmp_path / "native-home"
    home.mkdir()
    configuration = {"additionalFiles": [{
        "path": "{{runtime_support_dir}}/" + filename, "format": "text",
        "merge_strategy": "append", "content_template": "once\n", "permissions": "0600",
    } for filename in ("AGENTS.md", "preferences.txt")]}
    render = ProfileTemplateRenderer._materialize_file_template
    count = 0
    def interrupted(self, template, context):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("simulated interruption before launch")
        return render(self, template, context)
    monkeypatch.setattr(ProfileTemplateRenderer, "_materialize_file_template", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        configure_profile_files(configuration, home, tmp_path)
    assert (home / "AGENTS.md").read_text() == "once\n"
    monkeypatch.setattr(ProfileTemplateRenderer, "_materialize_file_template", render)
    with pytest.raises(ValueError, match="interrupted"):
        configure_profile_files(configuration, home, tmp_path)
    assert (home / "AGENTS.md").read_text() == "once\n"
    fresh = tmp_path / "recovered-private-home"
    fresh.mkdir()
    configure_profile_files(configuration, fresh, tmp_path)
    configure_profile_files(configuration, fresh, tmp_path)
    assert (fresh / "AGENTS.md").read_text() == "once\n"
    assert (fresh / "preferences.txt").read_text() == "once\n"
    assert (fresh / ".moonmind-profile-file-digest").stat().st_mode & 0o777 == 0o600


def test_profile_file_projection_cannot_follow_a_sibling_home_symlink(tmp_path):
    from moonmind.provider_profiles.codex_configuration import configure_profile_files
    home = tmp_path / "native-home"
    home.mkdir()
    sibling = tmp_path / "sibling-home"
    sibling.mkdir()
    original = sibling / "AGENTS.md"
    original.write_text("Keep sibling instructions.")
    (home / "AGENTS.md").symlink_to(original)
    configuration = {"additionalFiles": [{
        "path": "{{runtime_support_dir}}/AGENTS.md", "format": "text",
        "merge_strategy": "replace", "content_template": "Do not write through.", "permissions": "0600",
    }]}
    with pytest.raises(ValueError, match="within runtime_support_dir"):
        configure_profile_files(configuration, home, tmp_path)
    assert original.read_text() == "Keep sibling instructions."
