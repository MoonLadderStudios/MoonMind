"""OAuth home delivery must not discard a Profile's per-run configuration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from api_service.services.omnigent_agent_profile_selection import (
    _provider_materializer_error,
)
from api_service.services.profile_execution_selection import (
    configuration_accepts_profile,
    profile_has_native_inventory_route,
)
from moonmind.omnigent.harness_platform.materializers import (
    materializer_ref_for_profile,
)


def profile_and_configuration(runtime, schema, **options):
    provider = "openai" if runtime == "codex_cli" else "anthropic"
    harness = "codex-native" if runtime == "codex_cli" else "claude-native"
    profile = SimpleNamespace(
        profile_id="selected-oauth",
        runtime_id=runtime,
        provider_id=provider,
        credential_source="oauth_volume",
        runtime_materialization_mode="oauth_home",
        execution_configuration=None,
        env_template={},
        file_templates=[],
        home_path_overrides={},
        **options,
    )
    document = {"harness": {"id": harness}}
    if schema == "v2":
        document.update(
            schemaVersion="moonmind.omnigent-agent-profile.v2",
            credentialSlots=[
                {
                    "acceptedProviderIds": [provider],
                    "acceptedAuthModels": ["oauth_volume"],
                }
            ],
        )
    else:
        document["providerRequirements"] = {
            "runtimeId": runtime,
            "providerIds": [provider],
            "credentialSource": "oauth_volume",
            "materializationMode": "oauth_home",
        }
    return profile, (
        SimpleNamespace(profile_id="shared", default_for_runtime=True),
        SimpleNamespace(document=document),
    )


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("schema", ["v1", "v2"])
@pytest.mark.parametrize(
    "customization",
    [
        {"env_template": {"OMNIGENT_SERVER_URL": "http://unselected-control-owner"}},
        {
            "file_templates": [
                {
                    "path": "{{runtime_support_dir}}/settings.json",
                    "content_template": "accepted settings",
                }
            ]
        },
        {"home_path_overrides": {"HOME": "{{runtime_support_dir}}/private-home"}},
    ],
)
def test_custom_oauth_launch_options_keep_their_native_owner(
    runtime, schema, customization
):
    profile, configuration = profile_and_configuration(runtime, schema)
    for key, value in customization.items():
        setattr(profile, key, value)
    before = deepcopy(vars(profile))
    assert not configuration_accepts_profile(configuration[1].document, profile)
    assert profile_has_native_inventory_route(profile, [configuration])
    if schema == "v2":
        assert (
            _provider_materializer_error(configuration[1].document, profile) is not None
        )
    assert vars(profile) == before


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_plain_oauth_profiles_keep_the_equivalent_shared_route(runtime, schema):
    profile, configuration = profile_and_configuration(runtime, schema)
    assert configuration_accepts_profile(configuration[1].document, profile)
    assert not profile_has_native_inventory_route(profile, [configuration])


def private_codex_profile(schema="v2"):
    selected, configuration = profile_and_configuration("codex_cli", schema)
    selected.volume_mount_path = "/home/app/.codex"
    selected.home_path_overrides = {"CODEX_HOME": "{{runtime_support_dir}}/codex-home"}
    selected.file_templates = [
        {"path": "{{runtime_support_dir}}/codex-home/config.toml", "format": "toml",
         "merge_strategy": "deep_merge", "permissions": "0600",
         "content_template": {"developer_instructions": "Preserve accepted work.", "hide_agent_reasoning": True}},
        {"path": "{{runtime_support_dir}}/codex-home/AGENTS.md", "format": "text",
         "merge_strategy": "append", "permissions": "0600",
         "content_template": "Selected global instructions.\n"},
    ]
    return selected, configuration


@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_private_codex_oauth_preferences_have_shared_delivery(schema):
    from moonmind.omnigent.harness_platform.materializers import snapshot_provider_configuration

    selected, configuration = private_codex_profile(schema)
    before = deepcopy(vars(selected))
    assert configuration_accepts_profile(configuration[1].document, selected)
    assert not profile_has_native_inventory_route(selected, [configuration])
    admitted = snapshot_provider_configuration(selected)
    assert admitted.codexConfiguration["privateHomeOnly"] is True
    assert admitted.codexConfiguration["document"] == {"developer_instructions": "Preserve accepted work.", "hide_agent_reasoning": True}
    assert admitted.codexConfiguration["additionalFiles"][0]["path"] == "{{runtime_support_dir}}/AGENTS.md"
    assert vars(selected) == before


def test_private_codex_oauth_configuration_is_frozen_with_its_account():
    import hashlib
    from moonmind.omnigent.harness_platform.materializers import (
        ProviderConfigurationSnapshotRef, load_provider_configuration_snapshot,
        provider_configuration_snapshot_bytes, snapshot_provider_configuration,
    )

    selected, _ = private_codex_profile()
    bindings = {"model": SimpleNamespace(providerProfileRef=selected.profile_id, materializerRef="codex-oauth-home@1")}
    content = provider_configuration_snapshot_bytes(bindings, {"model": snapshot_provider_configuration(selected)})
    snapshot = ProviderConfigurationSnapshotRef(artifactRef="artifact:admitted-private-config", contentDigest="sha256:" + hashlib.sha256(content).hexdigest())
    selected.file_templates[0]["content_template"]["developer_instructions"] = "Later edit"
    restored = load_provider_configuration_snapshot(content, snapshot, bindings)
    assert restored["model"].codexConfiguration["document"]["developer_instructions"] == "Preserve accepted work."


def test_absent_private_configuration_preserves_recorded_snapshot_bytes():
    import json
    from moonmind.omnigent.harness_platform.materializers import (
        ProviderLaunchEnvironment, provider_configuration_snapshot_bytes,
    )

    bindings = {"model": SimpleNamespace(providerProfileRef="accepted-account", materializerRef="codex-oauth-home@1")}
    recorded = json.dumps({"model": {
        "providerProfileRef": "accepted-account", "materializerRef": "codex-oauth-home@1", "endpoint": None,
    }}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert provider_configuration_snapshot_bytes(bindings, {"model": ProviderLaunchEnvironment()}) == recorded


@pytest.mark.parametrize("extra", [
    {"model": "unselected-model"}, {"model_reasoning_effort": "unselected-effort"},
    {"model_provider": "unselected"}, {"mcp_servers": {"unselected": {"command": "other"}}},
    {"api_key": "unselected"}, {"history": {"persistence": "unsupported"}},
])
def test_private_oauth_preferences_cannot_replace_provider_or_tool_authority(extra):
    selected, configuration = private_codex_profile()
    selected.file_templates[0]["content_template"].update(extra)
    assert not configuration_accepts_profile(configuration[1].document, selected)
    assert profile_has_native_inventory_route(selected, [configuration])


@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_supported_claude_oauth_tuning_uses_its_shared_credential_owner(schema):
    profile, configuration = profile_and_configuration("claude_code", schema)
    profile.env_template = {
        "API_TIMEOUT_MS": "300000",
        "ANTHROPIC_SMALL_FAST_MODEL": "selected-small-model",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    before = deepcopy(vars(profile))
    assert configuration_accepts_profile(configuration[1].document, profile)
    assert not profile_has_native_inventory_route(profile, [configuration])
    assert vars(profile) == before


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_nonsecret_profile_shell_values_have_equivalent_shared_delivery(runtime, schema):
    profile, configuration = profile_and_configuration(runtime, schema)
    profile.env_template = {"PROJECT_RUNTIME_STYLE": "preserve-this-value"}
    assert configuration_accepts_profile(configuration[1].document, profile)
    assert not profile_has_native_inventory_route(profile, [configuration])


def test_claude_oauth_tuning_is_frozen_with_its_bound_account():
    import hashlib
    import json

    from moonmind.omnigent.harness_platform.materializers import (
        ProviderConfigurationSnapshotRef,
        load_provider_configuration_snapshot,
        provider_configuration_snapshot_bytes,
        snapshot_provider_configuration,
    )

    profile, _ = profile_and_configuration("claude_code", "v2")
    profile.env_template = {"API_TIMEOUT_MS": "300000"}
    bindings = {"model": SimpleNamespace(
        providerProfileRef=profile.profile_id,
        materializerRef="claude-oauth-home@1",
    )}
    content = provider_configuration_snapshot_bytes(
        bindings, {"model": snapshot_provider_configuration(profile)}
    )
    snapshot = ProviderConfigurationSnapshotRef(
        artifactRef="artifact:admitted-settings",
        contentDigest="sha256:" + hashlib.sha256(content).hexdigest(),
    )
    profile.env_template["API_TIMEOUT_MS"] = "later-value"
    restored = load_provider_configuration_snapshot(content, snapshot, bindings)
    assert restored["model"].launchEnvironment == {"API_TIMEOUT_MS": "300000"}
    assert "300000" in json.loads(content)["model"]["launchEnvironment"].values()
    bindings["model"].providerProfileRef = "a-different-account"
    with pytest.raises(ValueError, match="account"):
        load_provider_configuration_snapshot(content, snapshot, bindings)


@pytest.mark.parametrize("environment", [
    {"API_TIMEOUT_MS": "300000\nexport SECRET=value"},
    {"API_TIMEOUT_MS": {"from_secret_ref": "extra-secret"}},
    {"HTTPS_PROXY": "https://unselected-proxy.example"},
    {"ANTHROPIC_API_KEY": "unselected-key"},
    {"CODEX_HOME": "/unselected/home"},
    {"HOME": "/unselected/home"},
    {"PATH": "/unselected/bin"},
    {"MOONMIND_URL": "http://unselected/control"},
    {"LD_PRELOAD": "/unselected/library"},
    {"GH_CONFIG_DIR": "/unselected/repository-identity"},
    {"PROJECT_API_KEY": "unselected-key"},
    {"PROJECT_ENDPOINT": "https://synthetic-user:synthetic-value@example.test"},
    {"PROJECT_ENDPOINT": "https://example.test?signature=synthetic"},
    {"PROJECT_RUNTIME_STYLE": "value\x00extra"},
])
@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
def test_oauth_tuning_never_admits_credentials_or_another_launch_owner(environment, runtime):
    profile, configuration = profile_and_configuration(runtime, "v2")
    profile.env_template = environment
    assert not configuration_accepts_profile(configuration[1].document, profile)
    assert profile_has_native_inventory_route(profile, [configuration])


def test_old_provider_snapshot_does_not_invent_oauth_launch_settings():
    import hashlib

    from moonmind.omnigent.harness_platform.materializers import (
        ProviderConfigEndpoint,
        ProviderConfigurationSnapshotRef,
        load_provider_configuration_snapshot,
        provider_configuration_snapshot_bytes,
    )

    bindings = {
        "key": SimpleNamespace(
            providerProfileRef="key-profile", materializerRef="omnigent-provider-config@1",
        ),
        "oauth": SimpleNamespace(
            providerProfileRef="oauth-profile", materializerRef="claude-oauth-home@1",
        ),
    }
    endpoint = ProviderConfigEndpoint(
        family="anthropic", secretRole="api_key", baseUrl="https://api.anthropic.com",
    )
    content = provider_configuration_snapshot_bytes(bindings, {"key": endpoint})
    snapshot = ProviderConfigurationSnapshotRef(
        artifactRef="artifact:before-oauth-snapshots",
        contentDigest="sha256:" + hashlib.sha256(content).hexdigest(),
    )
    restored = load_provider_configuration_snapshot(content, snapshot, bindings)
    assert restored == {"key": endpoint}


@pytest.mark.parametrize(
    "runtime,expected",
    [("codex_cli", "codex-oauth-home@1"), ("claude_code", "claude-oauth-home@1")],
)
def test_recorded_oauth_materializer_identity_keeps_historical_delivery(
    runtime, expected
):
    profile, _ = profile_and_configuration(runtime, "v2")
    profile.env_template = {"RECORDED_NATIVE_OPTION": "retained"}
    assert materializer_ref_for_profile(profile) == expected


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_connected_oauth_home_metadata_uses_the_shared_credential_home(runtime, schema):
    from api_service.services.provider_profile_service import (
        get_first_party_oauth_profile,
        oauth_home_path_overrides,
    )
    from moonmind.omnigent.harness_platform.materializers import get_materializer

    selected, configuration = profile_and_configuration(runtime, schema)
    mapping = get_first_party_oauth_profile(runtime, selected.provider_id)
    target = get_materializer(materializer_ref_for_profile(selected)).target["path"]
    selected.volume_mount_path = target
    selected.home_path_overrides = oauth_home_path_overrides(mapping, target)
    before = deepcopy(vars(selected))

    assert configuration_accepts_profile(configuration[1].document, selected)
    assert not profile_has_native_inventory_route(selected, [configuration])
    if schema == "v2":
        assert _provider_materializer_error(configuration[1].document, selected) is None
    assert vars(selected) == before


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code"])
@pytest.mark.parametrize("schema", ["v1", "v2"])
@pytest.mark.parametrize("customization", ["other-mount", "other-selector", "other-value", "unknown-provider"])
def test_only_equivalent_connected_home_metadata_can_use_shared_delivery(runtime, schema, customization):
    from moonmind.omnigent.harness_platform.materializers import get_materializer
    from moonmind.provider_profiles.oauth_policy import oauth_home_path_keys

    selected, configuration = profile_and_configuration(runtime, schema)
    target = get_materializer(materializer_ref_for_profile(selected)).target["path"]
    selected.volume_mount_path = target
    key = oauth_home_path_keys(runtime)[0]
    selected.home_path_overrides = {key: target}
    if customization == "other-mount":
        selected.volume_mount_path = "/custom/credential-home"
    elif customization == "other-selector":
        selected.home_path_overrides = {"HOME": target}
    elif customization == "other-value":
        selected.home_path_overrides[key] = "/custom/credential-home"
    else:
        selected.provider_id = "unregistered-provider"
    assert not configuration_accepts_profile(configuration[1].document, selected)
    assert profile_has_native_inventory_route(selected, [configuration])
