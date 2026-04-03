import pytest

from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer

class MockSecretResolver:
    async def resolve_secrets(self, secret_refs):
        return {k: f"decrypted_{v}" for k, v in secret_refs.items()}

@pytest.mark.asyncio
async def test_materializer_generates_correct_env():
    base_env = {"SOME_PATH": "/usr/bin", "TO_BE_CLEARED": "bad_token"}
    resolver = MockSecretResolver()
    
    materializer = ProviderProfileMaterializer(base_env=base_env, secret_resolver=resolver)
    
    profile = ManagedRuntimeProfile(
        profile_id="test_profile",
        runtime_id="claude_code",
        provider_id="anthropic",
        clear_env_keys=["TO_BE_CLEARED"],
        secret_refs={"ANTHROPIC_API_KEY": "1234"},
        env_template={"API_URL": "https://api.anthropic.com/v1", "KEY_ECHO": "{{ANTHROPIC_API_KEY}}"},
        env_overrides={"OVERRIDE_VAR": "new_val"},
        file_templates=[],
        command_template=["claude", "start"]
    )
    
    env, cmd = await materializer.materialize(profile)
    
    assert "SOME_PATH" in env
    assert "TO_BE_CLEARED" not in env
    assert env["ANTHROPIC_API_KEY"] == "decrypted_1234"
    assert env["KEY_ECHO"] == "decrypted_1234"
    assert env["OVERRIDE_VAR"] == "new_val"
    assert cmd == ["claude", "start"]



@pytest.mark.asyncio
async def test_materializer_path_aware_file_templates_written_and_cleanup(tmp_path):
    """Path-aware file templates should be written to the requested runtime path."""
    import os
    base_env = {}
    resolver = MockSecretResolver()

    materializer = ProviderProfileMaterializer(base_env=base_env, secret_resolver=resolver)

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
                            "model": "qwen/qwen3.6-plus:free",
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
    assert 'base_url = "https://openrouter.ai/api/v1"' in content
    assert 'model = "qwen/qwen3.6-plus:free"' in content

    mode = oct(os.stat(config_path).st_mode & 0o777)
    assert mode == "0o600", f"Expected 0o600, got {mode}"

    assert str(config_path) in materializer.generated_files

    materializer.cleanup()
    assert not os.path.exists(config_path), "Config file should be removed after cleanup()"
    assert materializer.generated_files == []
