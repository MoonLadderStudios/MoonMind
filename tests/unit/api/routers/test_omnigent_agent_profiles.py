"""Contract tests for MoonLadderStudios/MoonMind#3517 agent profiles."""
import pytest
from pydantic import ValidationError

from api_service.api.routers.omnigent_agent_profiles import AgentProfileDocument, _digest, _normalized

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
    value["model"]["nested"] = {field: "unsafe"}
    with pytest.raises(ValidationError, match="runtime authority"):
        AgentProfileDocument.model_validate(value)

def test_profile_contracts_are_typed_and_reject_unknown_execution_authority():
    value = document(upstreamId="agent-123").model_dump(by_alias=True)
    value["execution"]["hostId"] = "caller-host"
    with pytest.raises(ValidationError):
        AgentProfileDocument.model_validate(value)
