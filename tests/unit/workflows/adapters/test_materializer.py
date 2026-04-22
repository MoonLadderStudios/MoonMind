from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer


class MockSecretResolver:
    async def resolve_secrets(self, secret_refs):
        return {k: f"decrypted_{v}" for k, v in secret_refs.items()}


class StaticSecretResolver:
    def __init__(self, resolved):
        self.resolved = resolved

    async def resolve_secrets(self, secret_refs):
        return {
            k: self.resolved[v]
            for k, v in secret_refs.items()
            if v in self.resolved
        }


@pytest.mark.asyncio
async def test_materializer_generates_correct_env():
    base_env = {"SOME_PATH": "/usr/bin", "TO_BE_CLEARED": "bad_token"}
    resolver = MockSecretResolver()

    materializer = ProviderProfileMaterializer(
        base_env=base_env, secret_resolver=resolver
    )

    profile = ManagedRuntimeProfile(
        profile_id="test_profile",
        runtime_id="claude_code",
        provider_id="anthropic",
        clear_env_keys=["TO_BE_CLEARED"],
        secret_refs={"ANTHROPIC_API_KEY": "1234"},
        env_template={
            "API_URL": "https://api.anthropic.com/v1",
            "KEY_ECHO": "{{ANTHROPIC_API_KEY}}",
        },
        env_overrides={"OVERRIDE_VAR": "new_val"},
        file_templates=[],
        command_template=["claude", "start"],
    )

    env, cmd = await materializer.materialize(profile)

    assert "SOME_PATH" in env
    assert "TO_BE_CLEARED" not in env
    assert env["ANTHROPIC_API_KEY"] == "decrypted_1234"
    assert env["KEY_ECHO"] == "decrypted_1234"
    assert env["OVERRIDE_VAR"] == "new_val"
    assert cmd == ["claude", "start"]


@pytest.mark.asyncio
async def test_materializer_launches_claude_anthropic_from_secret_ref_alias():
    base_env = {
        "PATH": "/usr/bin",
        "ANTHROPIC_API_KEY": "ambient-key",
        "ANTHROPIC_AUTH_TOKEN": "ambient-token",
        "ANTHROPIC_BASE_URL": "https://ambient.example",
        "OPENAI_API_KEY": "ambient-openai",
    }
    materializer = ProviderProfileMaterializer(
        base_env=base_env,
        secret_resolver=StaticSecretResolver(
            {"db://claude_anthropic_token": "resolved-claude-key"}
        ),
    )

    profile = ManagedRuntimeProfile(
        profile_id="claude_anthropic",
        runtime_id="claude_code",
        provider_id="anthropic",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
        clear_env_keys=[
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "OPENAI_API_KEY",
        ],
        secret_refs={"anthropic_api_key": "db://claude_anthropic_token"},
        env_template={"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
        command_template=["claude", "-p", "hello"],
    )

    env, cmd = await materializer.materialize(profile)

    assert env["PATH"] == "/usr/bin"
    assert env["ANTHROPIC_API_KEY"] == "resolved-claude-key"
    assert "anthropic_api_key" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert cmd == ["claude", "-p", "hello"]


@pytest.mark.asyncio
async def test_materializer_missing_claude_secret_ref_alias_fails_secret_free():
    materializer = ProviderProfileMaterializer(
        base_env={},
        secret_resolver=StaticSecretResolver({}),
    )
    profile = ManagedRuntimeProfile(
        profile_id="claude_anthropic",
        runtime_id="claude_code",
        provider_id="anthropic",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
        secret_refs={"anthropic_api_key": "db://missing-claude-token"},
        env_template={"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
        command_template=["claude", "-p", "hello"],
    )

    with pytest.raises(ValueError, match="anthropic_api_key") as exc_info:
        await materializer.materialize(profile)

    message = str(exc_info.value)
    assert "db://missing-claude-token" not in message
    assert "resolved-claude-key" not in message


@pytest.mark.asyncio
async def test_materializer_path_aware_file_templates_written_and_cleanup(tmp_path):
    """Path-aware file templates should be written to the requested runtime path."""
    import os
    base_env = {}
    resolver = MockSecretResolver()

    materializer = ProviderProfileMaterializer(
        base_env=base_env, secret_resolver=resolver
    )

    profile = ManagedRuntimeProfile(
        profile_id="test_file_templates",
        runtime_id="codex_cli",
        provider_id="openrouter",
        secret_refs={"provider_api_key": "ref_to_secret"},
        env_template={
            "OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"},
        },
        file_templates=[
            {
                "path": "{{runtime_support_dir}}/codex-home/config.toml",
                "format": "toml",
                "mergeStrategy": "replace",
                "contentTemplate": {
                    "model_provider": "openrouter",
                    "model_reasoning_effort": "high",
                    "model": "qwen/qwen3.6-plus",
                    "profile": "openrouter_qwen36_plus",
                    "model_providers": {
                        "openrouter": {
                            "name": "OpenRouter",
                            "base_url": "https://openrouter.ai/api/v1",
                            "env_key": "OPENROUTER_API_KEY",
                            "wire_api": "responses",
                        }
                    },
                    "profiles": {
                        "openrouter_qwen36_plus": {
                            "model_provider": "openrouter",
                            "model": "qwen/qwen3.6-plus",
                        }
                    },
                },
            }
        ],
        home_path_overrides={
            "CODEX_HOME": "{{runtime_support_dir}}/codex-home",
        },
        command_template=["codex", "exec"]
    )

    runtime_support_dir = tmp_path / ".moonmind"
    env, cmd = await materializer.materialize(
        profile,
        workspace_path=str(tmp_path / "repo"),
        runtime_support_dir=str(runtime_support_dir),
    )

    config_path = runtime_support_dir / "codex-home" / "config.toml"
    assert env["OPENROUTER_API_KEY"] == "decrypted_ref_to_secret"
    assert env["CODEX_HOME"] == str(runtime_support_dir / "codex-home")
    assert os.path.exists(config_path), "Config file should exist after materialize()"

    with open(config_path) as f:
        content = f.read()
    assert 'model_provider = "openrouter"' in content
    assert 'model_reasoning_effort = "high"' in content
    assert 'base_url = "https://openrouter.ai/api/v1"' in content
    assert 'model = "qwen/qwen3.6-plus"' in content

    mode = oct(os.stat(config_path).st_mode & 0o777)
    assert mode == "0o600", f"Expected 0o600, got {mode}"

    assert str(config_path) in materializer.generated_files

    materializer.cleanup()
    assert not os.path.exists(config_path), "Config file should be removed after cleanup()"
    assert materializer.generated_files == []


@pytest.mark.asyncio
async def test_materializer_cleanup_removes_generated_support_dir_tree():
    materializer = ProviderProfileMaterializer(
        base_env={},
        secret_resolver=MockSecretResolver(),
    )
    profile = ManagedRuntimeProfile(
        profile_id="test_temp_support_dir",
        runtime_id="codex_cli",
        provider_id="openrouter",
        file_templates=[
            {
                "path": "{{runtime_support_dir}}/codex-home/config.toml",
                "format": "toml",
                "mergeStrategy": "replace",
                "contentTemplate": {
                    "model_provider": "openrouter",
                },
            }
        ],
        home_path_overrides={
            "CODEX_HOME": "{{runtime_support_dir}}/codex-home",
        },
        command_template=["codex", "exec"],
    )

    env, _cmd = await materializer.materialize(profile)

    support_dir = materializer.generated_dirs[0]
    config_path = Path(env["CODEX_HOME"]) / "config.toml"

    assert config_path.exists()
    assert Path(support_dir).exists()

    materializer.cleanup()

    assert not Path(support_dir).exists()
    assert materializer.generated_dirs == []


@pytest.mark.asyncio
async def test_materializer_rejects_unknown_template_variables(tmp_path):
    materializer = ProviderProfileMaterializer(
        base_env={},
        secret_resolver=MockSecretResolver(),
    )
    profile = ManagedRuntimeProfile(
        profile_id="test_unknown_template_variable",
        runtime_id="codex_cli",
        file_templates=[
            {
                "path": "{{missing_var}}/config.toml",
                "format": "toml",
                "contentTemplate": {"model_provider": "openrouter"},
            }
        ],
        command_template=["codex", "exec"],
    )

    with pytest.raises(ValueError, match="Unknown template variable: 'missing_var'"):
        await materializer.materialize(
            profile,
            runtime_support_dir=str(tmp_path / ".moonmind"),
        )


@pytest.mark.asyncio
async def test_materializer_rejects_paths_outside_runtime_support_dir(tmp_path):
    materializer = ProviderProfileMaterializer(
        base_env={},
        secret_resolver=MockSecretResolver(),
    )
    profile = ManagedRuntimeProfile(
        profile_id="test_path_escape",
        runtime_id="codex_cli",
        file_templates=[
            {
                "path": "../../escape.toml",
                "format": "toml",
                "contentTemplate": {"model_provider": "openrouter"},
            }
        ],
        command_template=["codex", "exec"],
    )

    with pytest.raises(
        ValueError,
        match="fileTemplates\\[\\]\\.path must stay within runtime_support_dir",
    ):
        await materializer.materialize(
            profile,
            runtime_support_dir=str(tmp_path / ".moonmind"),
        )
