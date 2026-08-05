"""Authoring-boundary tests for immutable Omnigent profile snapshots."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from api_service.services.omnigent_agent_profile_selection import (
    compile_agent_profile_snapshot_parameters,
    resolve_agent_profile_snapshot,
)


class _Session:
    def __init__(self, *, state="active", ready=True):
        self.profile = SimpleNamespace(
            profile_id="team-codex", state=state, visibility="workspace",
            owner_id=uuid4(), active_version=2,
        )
        self.version = SimpleNamespace(
            version=2, digest="sha256:" + "a" * 64,
            document={
                "endpointRef": "default", "bridgeMode": "proxy",
                "source": {"bundleArtifactRef": "artifact://bundle", "bundleDigest": "sha256:" + "b" * 64},
                "harness": "codex-native", "requiredCapabilities": ["session.start"],
                "execution": {"defaultExecutionProfileRef": "omnigent-codex@1", "allowedLaunchPolicyRefs": ["on-demand@1"]},
                "providerRequirements": {"runtimeId": "codex_cli", "credentialSource": "oauth", "materializationMode": "host", "providerIds": ["openai"]},
                "model": {"model": "gpt-5.4"}, "capture": {}, "rag": {}, "publish": {}, "policyRef": "default@1",
            },
            validation_result={"ready": ready}, upstream_snapshot={"id": "bundle"},
            rollout_metadata={
                "bundleImport": {
                    "status": "succeeded",
                    "upstreamAgent": {"id": "imported-agent"},
                }
            },
        )
        self.provider = SimpleNamespace(
            profile_id="oauth-team", enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED, disabled_reason=None,
            max_parallel_runs=1, cooldown_after_429_seconds=900,
            runtime_id="codex_cli", credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            provider_id="openai", volume_ref="codex-oauth",
            volume_mount_path="/root/.codex", secret_refs={},
            credential_bindings=[], command_behavior={"auth_readiness": {"launch_ready": True}},
        )
        self.added = []

    async def get(self, model, key):
        return self.profile if model is OmnigentAgentProfile else None

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is OmnigentAgentProfileVersion:
            return self.version
        if entity is ManagedAgentProviderProfile:
            return self.provider
        return None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def test_snapshot_parameter_compiler_keeps_authority_out_of_authored_omnigent() -> None:
    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "team-codex",
        "version": 2,
        "digest": "sha256:" + "a" * 64,
        "providerProfileRef": "oauth-team",
        "executionProfileRef": "omnigent-codex@2",
        "launchPolicyRef": "on-demand@1",
        "agentId": "upstream-agent-2",
        "document": {
            "model": {"model": "gpt-5.4", "effort": "high"},
            "rag": {"maxTokens": 2000},
            "capture": {"retentionDays": 14},
            "workspace": {"mutation": "allowed"},
        },
    }

    compiled = compile_agent_profile_snapshot_parameters(
        {
            "model": "stale-model",
            "omnigent": {"launchPolicyRef": "stale-policy"},
        },
        snapshot=snapshot,
    )

    assert compiled["agentProfileSnapshot"] == snapshot
    assert compiled["agentProfile"] == {
        "profileId": "team-codex",
        "version": 2,
        "digest": "sha256:" + "a" * 64,
    }
    assert compiled["profileId"] == "oauth-team"
    assert compiled["model"] == "gpt-5.4"
    assert compiled["effort"] == "high"
    assert compiled["omnigent"] == {
        "executionTargetRef": "omnigent-codex@2",
        "launchPolicyRef": "on-demand@1",
    }
    assert compiled["rag"] == {"maxTokens": 2000}
    assert compiled["capture"] == {"retentionDays": 14}
    assert compiled["workspace"] == {"mutation": "allowed"}


@pytest.mark.asyncio
async def test_resolver_persists_exact_version_digest_and_effective_overrides():
    session = _Session()
    user = SimpleNamespace(id=uuid4())

    snapshot = await resolve_agent_profile_snapshot(
        session, selection={"profileId": "team-codex", "version": 2, "providerProfileRef": "oauth-team", "overrides": {"model": {"effort": "high"}}},
        consumer_type="checkpoint", consumer_id="branch-1", user=user,
    )

    assert snapshot["profileId"] == "team-codex"
    assert snapshot["version"] == 2
    assert snapshot["digest"] == "sha256:" + "a" * 64
    assert snapshot["document"]["model"] == {
        "model": "gpt-5.4",
        "effort": "high",
        "settings": {},
    }
    assert snapshot["providerProfileRef"] == "oauth-team"
    assert snapshot["agentId"] == "imported-agent"
    assert snapshot["launchPolicyRef"] == "on-demand@1"
    usage = session.added[0]
    assert isinstance(usage, OmnigentAgentProfileUsage)
    assert usage.consumer_type == "checkpoint"
    assert usage.effective_snapshot == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "ready", "message"), [
    ("disabled", True, "not active"),
    ("active", False, "not launch ready"),
])
async def test_resolver_rejects_disabled_or_unready_versions(state, ready, message):
    with pytest.raises(HTTPException, match=message):
        await resolve_agent_profile_snapshot(
            _Session(state=state, ready=ready), selection={"profileId": "team-codex", "providerProfileRef": "oauth-team"},
            consumer_type="workflow", consumer_id="workflow-1", user=SimpleNamespace(id=uuid4()),
        )


@pytest.mark.asyncio
async def test_resolver_requires_authored_provider_profile_identity():
    with pytest.raises(HTTPException, match="providerProfileRef is required"):
        await resolve_agent_profile_snapshot(
            _Session(), selection={"profileId": "team-codex"},
            consumer_type="workflow", consumer_id="workflow-1", user=SimpleNamespace(id=uuid4()),
        )


@pytest.mark.asyncio
async def test_resolver_rejects_malformed_version_as_validation_error():
    with pytest.raises(HTTPException) as caught:
        await resolve_agent_profile_snapshot(
            _Session(),
            selection={
                "profileId": "team-codex",
                "version": "latest",
                "providerProfileRef": "oauth-team",
            },
            consumer_type="workflow",
            consumer_id="workflow-1",
            user=SimpleNamespace(id=uuid4()),
        )
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_resolver_revalidates_effective_override_document():
    with pytest.raises(HTTPException) as caught:
        await resolve_agent_profile_snapshot(
            _Session(),
            selection={
                "profileId": "team-codex",
                "providerProfileRef": "oauth-team",
                "overrides": {"model": {"apiToken": "forbidden"}},
            },
            consumer_type="workflow",
            consumer_id="workflow-1",
            user=SimpleNamespace(id=uuid4()),
        )
    assert caught.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"rag": {"maxTokens": 2001}},
    {"capture": {"retentionDays": 31}},
    {"publish": {"mode": "auto"}},
])
async def test_resolver_rejects_overrides_above_versioned_ceilings(overrides):
    session = _Session()
    session.version.document["rag"] = {"maxTokens": 2000}
    session.version.document["capture"] = {"retentionDays": 30}
    session.version.document["publish"] = {"mode": "draft"}
    with pytest.raises(HTTPException, match="policy ceiling"):
        await resolve_agent_profile_snapshot(
            session,
            selection={"profileId": "team-codex", "providerProfileRef": "oauth-team", "overrides": overrides},
            consumer_type="workflow", consumer_id="workflow-1", user=SimpleNamespace(id=uuid4()),
        )
