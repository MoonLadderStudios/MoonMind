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
)
from api_service.services.omnigent_agent_profile_selection import (
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
        )
        self.provider = SimpleNamespace(profile_id="oauth-team")
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


@pytest.mark.asyncio
async def test_resolver_persists_exact_version_digest_and_effective_overrides():
    session = _Session()
    user = SimpleNamespace(id=uuid4())

    snapshot = await resolve_agent_profile_snapshot(
        session, selection={"profileId": "team-codex", "version": 2, "overrides": {"model": {"effort": "high"}}},
        consumer_type="checkpoint", consumer_id="branch-1", user=user,
    )

    assert snapshot["profileId"] == "team-codex"
    assert snapshot["version"] == 2
    assert snapshot["digest"] == "sha256:" + "a" * 64
    assert snapshot["document"]["model"] == {"model": "gpt-5.4", "effort": "high"}
    assert snapshot["providerProfileRef"] == "oauth-team"
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
            _Session(state=state, ready=ready), selection={"profileId": "team-codex"},
            consumer_type="workflow", consumer_id="workflow-1", user=SimpleNamespace(id=uuid4()),
        )
