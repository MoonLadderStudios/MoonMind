"""Custom Codex model settings reach the shared lifecycle without losing tuning."""

from types import SimpleNamespace

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


def test_installed_launch_shim_preserves_native_generated_configuration(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import tomllib
    from pathlib import Path
    from moonmind.provider_profiles import codex_configuration

    endpoint, reason = codex_provider_config_endpoint(profile())
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
