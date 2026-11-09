"""API-key path templates retain their admitted, nonsecret runtime context."""

import hashlib
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.materializers import (
    load_provider_configuration_snapshot,
    provider_config_endpoint,
    provider_configuration_snapshot_bytes,
    snapshot_provider_configuration,
)
from tests.unit.omnigent.test_codex_api_key_provider_config import (
    _api_key_profile,
    _provision,
)


def profile(runtime):
    selected = _api_key_profile()
    if runtime == "claude_code":
        selected.runtime_id = runtime
        selected.provider_id = "anthropic"
        selected.env_template = {
            "ANTHROPIC_API_KEY": {"from_secret_ref": "openai_api_key"}
        }
    return selected


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_available_key_context_selects_the_shared_lifecycle(runtime, schema):
    from api_service.services.profile_execution_selection import (
        configuration_accepts_profile,
        profile_has_native_inventory_route,
    )
    from tests.unit.api_service.test_oauth_profile_launch_options import (
        profile_and_configuration,
    )

    selected, configuration = profile_and_configuration(runtime, schema)
    selected.credential_source = "secret_ref"
    selected.runtime_materialization_mode = "api_key_env"
    selected.secret_refs = profile(runtime).secret_refs
    selected.env_template = {
        **profile(runtime).env_template,
        "REPORT_DIR": "{{workspace_path}}/reports",
    }
    document = configuration[1].document
    if schema == "v2":
        document["credentialSlots"][0]["acceptedAuthModels"] = ["omnigent-provider-config"]
    else:
        document["providerRequirements"].update(
            credentialSource="secret_ref", materializationMode="api_key_env"
        )
    assert configuration_accepts_profile(document, selected)
    assert not profile_has_native_inventory_route(selected, [configuration])


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
def test_templates_survive_admission_edits_and_snapshot_retries(runtime):
    selected = profile(runtime)
    values = {
        "REPORT_DIR": "{{workspace_path}}/reports",
        "CACHE_DIR": "{{ runtime_support_dir }}/cache",
    }
    selected.env_template.update(values)
    admitted = snapshot_provider_configuration(selected)
    assert admitted.environmentTemplates == values
    assert admitted.launchEnvironment == {}
    binding = SimpleNamespace(
        providerProfileRef=selected.profile_id,
        materializerRef="omnigent-provider-config@1",
    )
    bindings = {"primary-model": binding}
    content = provider_configuration_snapshot_bytes(
        bindings, {"primary-model": admitted}
    )
    selected.env_template["REPORT_DIR"] = "{{selected_secret}}"
    snapshot = SimpleNamespace(
        contentDigest="sha256:" + hashlib.sha256(content).hexdigest()
    )
    restored = load_provider_configuration_snapshot(content, snapshot, bindings)[
        "primary-model"
    ]
    assert restored.environmentTemplates == values
    assert (
        provider_configuration_snapshot_bytes(bindings, {"primary-model": restored})
        == content
    )


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize(
    "name,value",
    [
        ("REPORT_DIR", "{{selected_secret}}"),
        ("REPORT_DIR", "{{unknown}}"),
        ("REPORT_DIR", "{{workspace_path.secret}}"),
        ("HOME", "{{workspace_path}}"),
        ("GITHUB_TOKEN", "{{workspace_path}}"),
        ("OMNIGENT_SERVER_URL", "{{workspace_path}}"),
        ("OPENAI_BASE_URL", "{{workspace_path}}"),
        ("ANTHROPIC_MODEL", "{{workspace_path}}"),
    ],
)
def test_untranslated_template_authority_keeps_the_direct_route(runtime, name, value):
    selected = profile(runtime)
    selected.env_template[name] = value
    endpoint, reason = provider_config_endpoint(selected)
    assert endpoint is None
    assert reason


@pytest.mark.asyncio
async def test_provisioned_key_handle_delivers_templates_without_resolving_other_secrets(
    tmp_path,
):
    selected = profile("codex_cli")
    selected.env_template["REPORT_DIR"] = "{{workspace_path}}/reports"
    handles, _backend, _artifacts, resolver = await _provision(
        tmp_path,
        profile_values={
            key: getattr(selected, key)
            for key in (
                "credential_source",
                "runtime_materialization_mode",
                "secret_refs",
                "env_template",
            )
        },
    )
    assert handles[0].runtimeEnvironmentTemplates == {
        "REPORT_DIR": "{{workspace_path}}/reports"
    }
    assert "REPORT_DIR" not in handles[0].runtimeEnvironment
    assert resolver.resolved == ["OPENAI_API_KEY"]


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
def test_shared_paths_match_direct_renderer_and_are_attempt_local(runtime):
    from moonmind.omnigent.host_services.runtime_scripts import (
        OmnigentRuntimeScriptService,
    )
    from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer

    selected = profile(runtime)
    values = {
        "REPORT_DIR": "{{workspace_path}}/reports",
        "CACHE_DIR": "{{ runtime_support_dir }}/cache",
    }
    selected.env_template.update(values)
    admitted = snapshot_provider_configuration(selected)
    handle = {
        "materializerRef": "omnigent-provider-config@1",
        "attachments": [],
        "runtimeEnvironmentTemplates": admitted.environmentTemplates,
    }
    for workspace in ("/workspaces/first", "/workspaces/sibling", "/workspaces/retry"):
        _, delivered = OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[handle],
            skill_attachment={"targetPath": "/opt/skills"},
            step_execution_id="workflow:run:work:execution:1",
            enable_codex_runtime=runtime == "codex_cli",
            enable_claude_runtime=runtime == "claude_code",
            workspace_path=workspace,
        )
        direct = ProviderProfileMaterializer({}, None).render_environment(
            values,
            {
                "workspace_path": workspace,
                "runtime_support_dir": "/home/app/.omnigent/moonmind/runtime-context",
            },
        )
        assert {key: delivered[key] for key in values} == direct
        assert handle["runtimeEnvironmentTemplates"] == values


@pytest.mark.parametrize(
    "templates,enabled,workspace",
    [
        ({"REPORT_DIR": "{{selected_secret}}"}, True, "/workspaces/run"),
        ({"HOME": "{{workspace_path}}"}, True, "/workspaces/run"),
        ({"GITHUB_TOKEN": "{{workspace_path}}"}, True, "/workspaces/run"),
        ({"REPORT_DIR": "{{workspace_path}}"}, False, "/workspaces/run"),
        ({"REPORT_DIR": "{{workspace_path}}"}, True, None),
    ],
)
def test_key_host_rejects_unavailable_context_and_authority(
    templates, enabled, workspace
):
    from moonmind.omnigent.host_services.runtime_scripts import (
        OmnigentRuntimeScriptService,
    )

    with pytest.raises(ValueError):
        OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[
                {
                    "materializerRef": "omnigent-provider-config@1",
                    "attachments": [],
                    "runtimeEnvironmentTemplates": templates,
                }
            ],
            skill_attachment={"targetPath": "/opt/skills"},
            step_execution_id="workflow:run:work:execution:1",
            enable_codex_runtime=enabled,
            workspace_path=workspace,
        )


def test_retained_key_snapshot_keeps_literal_interpretation_and_bytes():
    import json

    binding = SimpleNamespace(
        providerProfileRef="retained", materializerRef="omnigent-provider-config@1"
    )
    content = json.dumps(
        {
            "model": {
                "providerProfileRef": "retained",
                "materializerRef": "omnigent-provider-config@1",
                "endpoint": {
                    "family": "openai",
                    "secretRole": "api_key",
                    "baseUrl": "https://api.openai.com/v1",
                    "defaultModel": None,
                    "modelPins": {},
                    "launchEnvironment": {"REPORT_DIR": "{{workspace_path}}/literal"},
                },
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    snapshot = SimpleNamespace(
        contentDigest="sha256:" + hashlib.sha256(content).hexdigest()
    )
    restored = load_provider_configuration_snapshot(
        content, snapshot, {"model": binding}
    )
    assert restored["model"].environmentTemplates is None
    assert restored["model"].launchEnvironment == {
        "REPORT_DIR": "{{workspace_path}}/literal"
    }
    assert (
        provider_configuration_snapshot_bytes({"model": binding}, restored) == content
    )


def test_template_handle_cannot_override_a_sibling_launch_value():
    from moonmind.omnigent.host_services.runtime_scripts import (
        OmnigentRuntimeScriptService,
    )

    with pytest.raises(ValueError, match="conflicting"):
        OmnigentRuntimeScriptService().build_entrypoint(
            credential_handles=[
                {
                    "materializerRef": "omnigent-provider-config@1",
                    "attachments": [],
                    "runtimeEnvironmentTemplates": {
                        "REPORT_DIR": "{{workspace_path}}/reports"
                    },
                },
                {
                    "materializerRef": "omnigent-provider-config@1",
                    "attachments": [],
                    "runtimeEnvironment": {"REPORT_DIR": "/another/account"},
                },
            ],
            skill_attachment={"targetPath": "/opt/skills"},
            workspace_path="/workspaces/run",
            step_execution_id="workflow:run:work:execution:1",
            enable_codex_runtime=True,
        )
