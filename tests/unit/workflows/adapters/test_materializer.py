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
        file_templates={},
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
async def test_materializer_file_templates_written_and_cleanup():
    """File templates should be written to disk with secrets interpolated."""
    import os
    base_env = {}
    resolver = MockSecretResolver()

    materializer = ProviderProfileMaterializer(base_env=base_env, secret_resolver=resolver)

    profile = ManagedRuntimeProfile(
        profile_id="test_file_templates",
        runtime_id="claude_code",
        provider_id="anthropic",
        secret_refs={"MY_SECRET": "ref_to_secret"},
        file_templates={"CREDENTIALS_FILE": "token={{MY_SECRET}}\nextra=static"},
        command_template=["claude", "start"]
    )

    env, cmd = await materializer.materialize(profile)

    assert "CREDENTIALS_FILE" in env
    tmp_path = env["CREDENTIALS_FILE"]
    assert os.path.exists(tmp_path), "Temp file should exist after materialize()"

    with open(tmp_path) as f:
        content = f.read()
    assert "decrypted_ref_to_secret" in content
    assert "token=decrypted_ref_to_secret" in content

    mode = oct(os.stat(tmp_path).st_mode & 0o777)
    assert mode == "0o600", f"Expected 0o600, got {mode}"

    assert tmp_path in materializer.generated_files

    materializer.cleanup()
    assert not os.path.exists(tmp_path), "Temp file should be removed after cleanup()"
    assert materializer.generated_files == []
