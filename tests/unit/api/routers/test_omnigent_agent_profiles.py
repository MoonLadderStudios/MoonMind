"""Contract tests for MoonLadderStudios/MoonMind#3517 agent profiles."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api_service.api.routers.omnigent_agent_profiles import (
    AgentProfileDocument,
    GuidedProfileCreate,
    _default_builtin_opencode_launch_policy_refs,
    _catalog_refresh_preserves_builtin_binding,
    _digest,
    _normalized,
    _preserve_builtin_opencode_model,
    _response,
    router,
)
from moonmind.omnigent.harness_platform import create_catalog_snapshot


def document(**source):
    return AgentProfileDocument.model_validate({
        "endpointRef": "default", "bridgeMode": "proxy", "source": source,
        "harness": "codex-native", "requiredCapabilities": ["session.start"],
        "execution": {"defaultExecutionProfileRef": "omnigent-codex@1", "allowedLaunchPolicyRefs": ["codex-on-demand@1"]},
        "providerRequirements": {"runtimeId": "codex_cli", "credentialSource": "oauth_volume", "materializationMode": "oauth_home"},
        "model": {"model": "gpt-5", "effort": "high"}, "workspace": {"mutation": "allowed"},
        "capture": {"stream": True}, "rag": {"initial": {}, "followUp": {}},
        "continuations": {"checkpoint": True, "branch": True, "remediation": True},
        "publish": {"mode": "draft"}, "policyRef": "codex-on-demand@1",
    })

def test_normalization_and_digest_are_stable():
    first = _normalized(document(upstreamId="agent-123", upstreamVersion="v2"))
    second = dict(reversed(list(first.items())))
    assert _digest(first) == _digest(second)
    assert _digest(first).startswith("sha256:")


def test_catalog_refresh_preserves_bootstrap_qualified_opencode_model():
    seed = {
        "source": {"upstreamId": "agent-1", "upstreamVersion": "6"},
        "model": {},
    }
    active = {
        "source": {"upstreamId": "agent-1", "upstreamVersion": "6"},
        "model": {
            "qualifiedId": "opencode-go/gpt-5.6-luna",
            "effort": "xhigh",
        },
    }

    reconciled = _preserve_builtin_opencode_model(seed, active)

    assert reconciled["model"] == active["model"]
    assert seed["model"] == {}


def test_catalog_refresh_preserves_binding_when_only_live_inventory_changes():
    implementation = {
        "sourceKind": "core",
        "package": "omnigent.harnesses.opencode",
        "version": "1.0.0",
        "digest": "sha256:" + "1" * 64,
    }
    harnesses = [
        {
            "id": "opencode-native",
            "label": "OpenCode",
            "implementation": implementation,
        }
    ]
    authority = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.2.3",
        omnigentBuildDigest="sha256:" + "2" * 64,
        sourceDigest="sha256:" + "3" * 64,
        harnesses=harnesses,
    )
    observation = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.2.3",
        omnigentBuildDigest="sha256:" + "2" * 64,
        sourceDigest="sha256:" + "4" * 64,
        harnesses=harnesses,
    )

    assert _catalog_refresh_preserves_builtin_binding(
        authority_payload=authority.model_dump(by_alias=True, mode="json"),
        observation=observation,
        harness_id="opencode-native",
        implementation_ref=authority.harnesses[0].implementation.implementation_ref(),
    )


def test_catalog_refresh_rejects_a_changed_omnigent_build():
    harnesses = [
        {
            "id": "opencode-native",
            "label": "OpenCode",
            "implementation": {
                "sourceKind": "core",
                "package": "omnigent.harnesses.opencode",
                "version": "1.0.0",
                "digest": "sha256:" + "1" * 64,
            },
        }
    ]
    authority = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.2.3",
        omnigentBuildDigest="sha256:" + "2" * 64,
        sourceDigest="sha256:" + "3" * 64,
        harnesses=harnesses,
    )
    observation = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.2.4",
        omnigentBuildDigest="sha256:" + "5" * 64,
        sourceDigest="sha256:" + "4" * 64,
        harnesses=harnesses,
    )

    assert not _catalog_refresh_preserves_builtin_binding(
        authority_payload=authority.model_dump(by_alias=True, mode="json"),
        observation=observation,
        harness_id="opencode-native",
        implementation_ref=authority.harnesses[0].implementation.implementation_ref(),
    )


@pytest.mark.asyncio
async def test_builtin_opencode_profile_binds_current_policy_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PolicyService:
        def __init__(self, session):
            assert session == "session"

        async def resolve_default_runtime_snapshot(self, policy_id: str):
            versions = {"omnigent-on-demand": 7, "opencode-on-demand": 9}
            return {"policyRef": f"{policy_id}@{versions[policy_id]}"}

    monkeypatch.setattr(
        "api_service.services.omnigent_policies.OmnigentPolicyService",
        _PolicyService,
    )

    assert await _default_builtin_opencode_launch_policy_refs("session") == [
        "omnigent-on-demand@7",
        "opencode-on-demand@9",
    ]


def test_guided_profile_rejects_unqualified_pi_preset() -> None:
    with pytest.raises(ValidationError):
        GuidedProfileCreate.model_validate(
            {
                "profileId": "omnigent-pi-default",
                "displayName": "Pi via Omnigent",
                "preset": "pi-experimental",
                "defaultModel": "anthropic/model",
            }
        )


def test_defaulted_rag_max_tokens_is_not_mistaken_for_runtime_authority():
    parsed = document(upstreamId="agent-123")

    assert parsed.rag.max_tokens is None


def test_fixed_post_routes_precede_lifecycle_catch_all():
    post_paths = [
        route.path
        for route in router.routes
        if "POST" in getattr(route, "methods", set())
    ]
    catch_all = post_paths.index("/api/omnigent/agent-profiles/{profile_id}/{action}")

    assert post_paths.index("/api/omnigent/agent-profiles/{profile_id}/default") < catch_all
    assert post_paths.index("/api/omnigent/agent-profiles/{profile_id}/snapshot") < catch_all
    assert post_paths.index("/api/omnigent/agent-profiles/{profile_id}/smoke") < catch_all
    assert post_paths.index("/api/omnigent/agent-profiles/{profile_id}/import-bundle") < catch_all


def test_list_response_contract_includes_ordered_versions_and_default_state():
    profile = SimpleNamespace(
        profile_id="codex-team",
        display_name="Team Codex",
        description=None,
        visibility="workspace",
        state="active",
        active_version=2,
        default_for_runtime=True,
    )
    versions = [
        SimpleNamespace(
            version=2,
            digest="sha256:" + "a" * 64,
            document={"schemaVersion": "moonmind.omnigent-agent-profile.v1"},
            parent_version=1,
            cloned_from_profile_id=None,
            cloned_from_version=None,
            upstream_snapshot={"upstreamId": "codex"},
            validation_result={"ready": True},
            rollout_metadata={"bundleImport": {"status": "succeeded"}},
            created_at=None,
        ),
        SimpleNamespace(
            version=1,
            digest="sha256:" + "b" * 64,
            document={"schemaVersion": "moonmind.omnigent-agent-profile.v1"},
            parent_version=None,
            cloned_from_profile_id=None,
            cloned_from_version=None,
            upstream_snapshot=None,
            validation_result=None,
            rollout_metadata=None,
            created_at=None,
        ),
    ]

    response = _response(profile, versions)

    assert response["defaultForRuntime"] is True
    assert "default" not in response
    assert [version["version"] for version in response["versions"]] == [2, 1]
    assert response["versions"][0]["validationResult"] == {"ready": True}
    assert response["versions"][0]["rolloutMetadata"]["bundleImport"]["status"] == "succeeded"

def test_source_requires_stable_identity_or_immutable_bundle():
    with pytest.raises(ValidationError, match="exactly one"):
        document()
    with pytest.raises(ValidationError, match="bundleDigest"):
        document(bundleArtifactRef="artifact:agent-bundle")
    bundle = document(bundleArtifactRef="artifact:agent-bundle", bundleDigest="sha256:" + "a" * 64)
    assert bundle.source.bundle_artifact_ref == "artifact:agent-bundle"

@pytest.mark.parametrize("field", ["credentials", "dockerfile", "hostPath", "volumeName", "privileged"])
def test_profile_rejects_runtime_authority(field):
    value = document(upstreamId="agent-123").model_dump(by_alias=True)
    value["workspace"][field] = "unsafe"
    with pytest.raises(ValidationError, match="runtime authority"):
        AgentProfileDocument.model_validate(value)

@pytest.mark.parametrize("field", ["apiToken", "access_token", "clientSecret", "passwordHash"])
def test_profile_rejects_nested_credential_like_authority(field):
    value = document(upstreamId="agent-123").model_dump(by_alias=True)
    value["model"]["settings"] = {field: "unsafe"}
    with pytest.raises(ValidationError, match="runtime authority"):
        AgentProfileDocument.model_validate(value)

def test_profile_contracts_are_typed_and_reject_unknown_execution_authority():
    value = document(upstreamId="agent-123").model_dump(by_alias=True)
    value["execution"]["hostId"] = "caller-host"
    with pytest.raises(ValidationError):
        AgentProfileDocument.model_validate(value)

@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("model", "effort", "unbounded"),
        ("capture", "retentionDays", 0),
        ("rag", "maxTokens", 1_000_001),
        ("rag", "maxLatencyMs", 600_001),
        ("publish", "mode", "force"),
    ],
)
def test_profile_rejects_unsupported_or_unbounded_defaults(section, field, value):
    payload = document(upstreamId="agent-123").model_dump(by_alias=True)
    payload[section][field] = value
    with pytest.raises(ValidationError):
        AgentProfileDocument.model_validate(payload)

def test_profile_rejects_unknown_model_capture_rag_and_publish_fields():
    for section in ("model", "capture", "rag", "publish"):
        payload = document(upstreamId="agent-123").model_dump(by_alias=True)
        payload[section]["unexpected"] = True
        with pytest.raises(ValidationError):
            AgentProfileDocument.model_validate(payload)
