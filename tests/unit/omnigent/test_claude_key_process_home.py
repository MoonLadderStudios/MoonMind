"""API-key Claude HOME remains separate from private configuration and authentication."""

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    claude_provider_config_endpoint,
)
from moonmind.provider_profiles.claude_configuration import (
    materialize_configuration,
    materialize_process_home,
    process_home_configuration,
)
from tests.unit.omnigent.test_claude_api_key_provider_config import (
    _api_key_profile,
    _provision,
)
from tests.unit.omnigent.test_claude_profile_configuration import configuration


def key_configuration():
    config = configuration()
    config["homePathOverrides"]["HOME"] = "{{runtime_support_dir}}/process-home"
    config["fileTemplates"].append({"path": "{{runtime_support_dir}}/process-home/NOTES.md", "content_template": "Work in {{workspace_path}}.", "format": "text"})
    return config


def test_key_configuration_admits_and_materializes_distinct_homes(tmp_path):
    config = key_configuration()
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile(home_path_overrides=config["homePathOverrides"], file_templates=config["fileTemplates"]))
    assert endpoint is not None, reason
    for sibling in ("first", "second"):
        root = tmp_path / sibling
        for _ in range(2):
            home = materialize_process_home(endpoint.claudeConfiguration, runtime_support_dir=root, workspace_path="/work")
            config_home = materialize_configuration(endpoint.claudeConfiguration, runtime_support_dir=root, workspace_path="/work")
        assert home != config_home
        assert (root / "process-home/NOTES.md").read_text() == "Work in /work."
        assert not (root / "claude-home/NOTES.md").exists()
        assert process_home_configuration(endpoint.claudeConfiguration)["homePathOverrides"] == {"HOME": "{{runtime_support_dir}}/process-home"}


@pytest.mark.asyncio
async def test_key_process_home_materialization_keeps_selected_key_and_private_files(tmp_path):
    config = key_configuration()
    handles, _, _backend, _, resolver = await _provision(tmp_path, profile_values={
        "credential_source": "secret_ref", "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        "env_template": {"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
        "home_path_overrides": config["homePathOverrides"], "file_templates": config["fileTemplates"],
    }, materializer_ref="omnigent-provider-config@1")
    assert resolver.resolved == ["ANTHROPIC_API_KEY"]
    assert handles[0].claudeProcessHomeConfiguration == process_home_configuration(config)
    assert handles[0].materializerRef == "omnigent-provider-config@1"


@pytest.mark.parametrize("home", ["{{runtime_support_dir}}/claude-home", "{{runtime_support_dir}}/codex-home", "/tmp/private-home", "{{runtime_support_dir}}/../other-home", None])
def test_key_process_home_rejects_collisions_or_unowned_paths(home):
    config = key_configuration()
    config["homePathOverrides"]["HOME"] = home
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile(home_path_overrides=config["homePathOverrides"], file_templates=config["fileTemplates"]))
    assert endpoint is None
    assert reason


@pytest.mark.parametrize("name,content", [(".gitconfig", "unsafe"), ("nested/NOTES.md", "unsafe"), ("NOTES.md", "{{provider_api_key}}"), ("NOTES.sh", "echo unsafe")])
def test_key_process_home_preserves_narrow_file_authority(name, content):
    config = key_configuration()
    config["fileTemplates"][-1].update(path="{{runtime_support_dir}}/process-home/" + name, content_template=content)
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile(home_path_overrides=config["homePathOverrides"], file_templates=config["fileTemplates"]))
    assert endpoint is None
    assert reason


def test_key_process_home_normalizes_model_backed_templates():
    from moonmind.provider_profiles.claude_configuration import validate_configuration
    from moonmind.schemas.agent_runtime_models import RuntimeFileTemplate

    config = key_configuration()
    expected = validate_configuration(config)
    config["fileTemplates"] = [RuntimeFileTemplate.model_validate({**item, "permissions": "0600"}) for item in config["fileTemplates"]]
    assert validate_configuration(config) == expected


@pytest.mark.parametrize("malformed", [None, "NOTES.md", 5])
def test_key_process_home_rejects_malformed_templates(malformed):
    from moonmind.provider_profiles.claude_configuration import validate_configuration

    config = key_configuration()
    config["fileTemplates"].append(malformed)
    with pytest.raises(ValueError, match="must be objects"):
        validate_configuration(config)


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [False, True])
async def test_key_process_home_uses_existing_server_admission_gate(monkeypatch, supported):
    from unittest.mock import AsyncMock

    from api_service.services.omnigent_execution_plan_service import (
        _require_process_home_server,
    )
    from moonmind.omnigent.harness_platform.materializers import (
        ProviderConfigurationHandoffUnavailable,
    )
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    config = key_configuration()
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile(home_path_overrides=config["homePathOverrides"], file_templates=config["fileTemplates"]))
    assert endpoint is not None, reason
    gate = AsyncMock(return_value=supported)
    monkeypatch.setattr(OmnigentHttpClient, "supports_session_process_home", gate)
    if supported:
        await _require_process_home_server({"primary-model": endpoint})
    else:
        with pytest.raises(ProviderConfigurationHandoffUnavailable):
            await _require_process_home_server({"primary-model": endpoint})
    gate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["persistence", "cancellation", "anticipated"])
async def test_key_home_clears_resolved_secret_before_any_resource_on_early_failure(tmp_path, monkeypatch, failure):
    import asyncio
    from unittest.mock import AsyncMock

    from moonmind.omnigent import credential_materializers as materializers
    from moonmind.omnigent.secret_resolution import OmnigentSecretResolutionService
    from tests.unit.omnigent.test_claude_api_key_provider_config import _DockerBackend

    captured = []
    original = OmnigentSecretResolutionService.resolve_provider_config

    async def resolve(self, **kwargs):
        bundle, endpoint = await original(self, **kwargs)
        captured.append(bundle)
        return bundle, endpoint

    monkeypatch.setattr(OmnigentSecretResolutionService, "resolve_provider_config", resolve)
    expected = asyncio.CancelledError if failure == "cancellation" else ValueError
    if failure == "anticipated":
        def invalid(*args, **kwargs):
            raise ValueError("invalid anticipated handle")
        monkeypatch.setattr(materializers, "anticipated_credential_handle", invalid)
    else:
        monkeypatch.setattr(materializers.OmnigentCredentialProvisioningService, "_persist_handle", AsyncMock(side_effect=expected("unavailable")))
    backend = AsyncMock()
    monkeypatch.setattr(_DockerBackend, "run", backend)
    config = key_configuration()
    with pytest.raises(expected):
        await _provision(tmp_path, profile_values={
            "credential_source": "secret_ref", "runtime_materialization_mode": "api_key_env",
            "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
            "env_template": {"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
            "home_path_overrides": config["homePathOverrides"], "file_templates": config["fileTemplates"],
        }, materializer_ref="omnigent-provider-config@1")
    backend.assert_not_awaited()
    assert len(captured) == 1
    assert not captured[0].values, "resolved key must be cleared on every exit"
