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
    refresh_managed_bootstrap_snapshot,
    resolve_agent_profile_snapshot,
    resolve_default_agent_profile_snapshot,
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
        self.usage = None
        self.added = []

    async def get(self, model, key):
        return self.profile if model is OmnigentAgentProfile else None

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is OmnigentAgentProfile:
            return self.profile
        if entity is OmnigentAgentProfileVersion:
            return self.version
        if entity is ManagedAgentProviderProfile:
            return self.provider
        if entity is OmnigentAgentProfileUsage:
            return self.usage
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
            "omnigent": {
                "agentProfileRef": "team-codex@1",
                "executionProfileRef": "omnigent-codex@1",
                "launchPolicyRef": "stale-policy",
                "agent": {
                    "agentId": "stale-agent-id",
                    "agentName": "portable-agent-name",
                },
            },
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
        "agent": {"agentName": "portable-agent-name"},
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
async def test_default_resolver_freezes_default_and_explicit_provider_at_admission():
    session = _Session()

    snapshot = await resolve_default_agent_profile_snapshot(
        session,
        provider_profile_ref="oauth-team",
        launch_policy_ref="on-demand@1",
        consumer_type="workflow",
        consumer_id="workflow-default-1",
        user=SimpleNamespace(id=uuid4()),
    )

    assert snapshot["profileId"] == "team-codex"
    assert snapshot["providerProfileRef"] == "oauth-team"
    assert snapshot["launchPolicyRef"] == "on-demand@1"


@pytest.mark.asyncio
async def test_resolver_replaces_managed_schedule_usage_in_place():
    session = _Session()
    session.usage = SimpleNamespace(
        profile_id="team-codex",
        version=1,
        digest="sha256:" + "1" * 64,
        effective_snapshot={"version": 1},
    )

    snapshot = await resolve_agent_profile_snapshot(
        session,
        selection={
            "profileId": "team-codex",
            "providerProfileRef": "oauth-team",
        },
        consumer_type="schedule",
        consumer_id="schedule-1",
        user=None,
        replace_existing_usage=True,
    )

    assert session.added == []
    assert session.usage.version == 2
    assert session.usage.digest == "sha256:" + "a" * 64
    assert session.usage.effective_snapshot == snapshot


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


@pytest.mark.asyncio
async def test_exact_rerun_refreshes_managed_bootstrap_authority(monkeypatch):
    old_digest = "sha256:" + "1" * 64
    previous_version = SimpleNamespace(
        digest=old_digest,
        document={
            "model": {"model": "gpt-5.6-sol"},
            "capture": {},
            "rag": {},
            "publish": {},
        },
    )

    class _RerunSession:
        async def scalar(self, statement):
            return previous_version

    captured = {}

    async def _resolve(
        session,
        *,
        selection,
        consumer_type,
        consumer_id,
        user,
        replace_existing_usage=False,
    ):
        captured.update(
            selection=selection,
            consumer_type=consumer_type,
            consumer_id=consumer_id,
        )
        return {
            "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
            "profileId": "omnigent-bootstrap-default",
            "version": 2,
            "digest": "sha256:" + "2" * 64,
            "providerProfileRef": "codex_openai_oauth",
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@2",
            "agentId": "codex-native-ui",
            "document": {
                "model": {"model": "gpt-5.6-sol", "effort": "xhigh"},
                "capture": {},
                "rag": {},
                "publish": {},
                "workspace": {},
            },
        }

    monkeypatch.setattr(
        "api_service.services.omnigent_agent_profile_selection."
        "resolve_agent_profile_snapshot",
        _resolve,
    )
    refreshed = await refresh_managed_bootstrap_snapshot(
        _RerunSession(),
        parameters={
            "instructions": "keep the same task",
            "agentProfileSnapshot": {
                "profileId": "omnigent-bootstrap-default",
                "version": 1,
                "digest": old_digest,
                "providerProfileRef": "codex_openai_oauth",
                "document": {
                    "model": {"model": "gpt-5.6-sol", "effort": "xhigh"},
                    "capture": {},
                    "rag": {},
                    "publish": {},
                },
            },
            "omnigent": {
                "agentProfileRef": "omnigent-bootstrap-default@1",
                "executionProfileRef": "omnigent-codex@1",
                "launchPolicyRef": "codex-on-demand@1",
                "agent": {"agentId": "stale-agent-id"},
            },
        },
        consumer_type="workflow",
        consumer_id="mm:rerun",
        user=SimpleNamespace(id=uuid4()),
    )

    assert refreshed["instructions"] == "keep the same task"
    assert refreshed["agentProfile"]["version"] == 2
    assert refreshed["omnigent"] == {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@2",
    }
    assert captured == {
        "selection": {
            "profileId": "omnigent-bootstrap-default",
            "providerProfileRef": "codex_openai_oauth",
            "overrides": {"model": {"effort": "xhigh"}},
        },
        "consumer_type": "workflow",
        "consumer_id": "mm:rerun",
    }


@pytest.mark.asyncio
async def test_managed_schedule_refresh_requires_and_replaces_exact_usage(
    monkeypatch,
):
    old_digest = "sha256:" + "1" * 64
    previous = {
        "profileId": "omnigent-bootstrap-default",
        "version": 1,
        "digest": old_digest,
        "providerProfileRef": "codex_openai_oauth",
        "document": {"model": {}, "capture": {}, "rag": {}, "publish": {}},
    }
    previous_version = SimpleNamespace(
        digest=old_digest,
        document=previous["document"],
    )
    usage = SimpleNamespace(
        profile_id="omnigent-bootstrap-default",
        version=1,
        digest=old_digest,
        effective_snapshot=previous,
    )

    class _ScheduleSession:
        async def scalar(self, statement):
            entity = statement.column_descriptions[0].get("entity")
            if entity is OmnigentAgentProfileVersion:
                return previous_version
            if entity is OmnigentAgentProfileUsage:
                return usage
            return None

    async def _resolve(
        session,
        *,
        selection,
        consumer_type,
        consumer_id,
        user,
        replace_existing_usage,
    ):
        assert consumer_type == "schedule"
        assert consumer_id == "schedule-1"
        assert replace_existing_usage is True
        return {
            **previous,
            "version": 2,
            "digest": "sha256:" + "2" * 64,
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@2",
            "agentId": "codex-native-ui",
            "document": {
                **previous["document"],
                "workspace": {},
            },
        }

    monkeypatch.setattr(
        "api_service.services.omnigent_agent_profile_selection."
        "resolve_agent_profile_snapshot",
        _resolve,
    )

    refreshed = await refresh_managed_bootstrap_snapshot(
        _ScheduleSession(),
        parameters={"agentProfileSnapshot": previous},
        consumer_type="schedule",
        consumer_id="schedule-1",
        user=None,
        replace_existing_usage=True,
    )

    assert refreshed["agentProfile"]["version"] == 2
    assert refreshed["omnigent"]["launchPolicyRef"] == "codex-on-demand@2"


@pytest.mark.asyncio
async def test_exact_rerun_reconstructs_explicit_null_overrides(monkeypatch):
    old_digest = "sha256:" + "3" * 64
    previous_version = SimpleNamespace(
        digest=old_digest,
        document={
            "model": {"model": "gpt-5.6-sol", "effort": "xhigh"},
            "capture": {"retentionDays": 30},
            "rag": {},
            "publish": {},
        },
    )

    class _RerunSession:
        async def scalar(self, statement):
            return previous_version

    captured = {}

    async def _resolve(
        session,
        *,
        selection,
        consumer_type,
        consumer_id,
        user,
        replace_existing_usage=False,
    ):
        captured["selection"] = selection
        return {
            "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
            "profileId": "omnigent-bootstrap-default",
            "version": 2,
            "digest": "sha256:" + "4" * 64,
            "providerProfileRef": "codex_openai_oauth",
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@2",
            "agentId": "codex-native-ui",
            "document": {
                "model": {},
                "capture": {},
                "rag": {},
                "publish": {},
                "workspace": {},
            },
        }

    monkeypatch.setattr(
        "api_service.services.omnigent_agent_profile_selection."
        "resolve_agent_profile_snapshot",
        _resolve,
    )

    await refresh_managed_bootstrap_snapshot(
        _RerunSession(),
        parameters={
            "agentProfileSnapshot": {
                "profileId": "omnigent-bootstrap-default",
                "version": 1,
                "digest": old_digest,
                "providerProfileRef": "codex_openai_oauth",
                # The effective snapshot was serialized with exclude_none=True,
                # so these omitted baseline keys are authored null overrides.
                "document": {
                    "model": {},
                    "capture": {},
                    "rag": {},
                    "publish": {},
                },
            }
        },
        consumer_type="workflow",
        consumer_id="mm:rerun-null-overrides",
        user=SimpleNamespace(id=uuid4()),
    )

    assert captured["selection"]["overrides"] == {
        "model": {"effort": None, "model": None},
        "capture": {"retentionDays": None},
    }


@pytest.mark.asyncio
async def test_exact_rerun_preserves_operator_owned_profile_snapshot():
    parameters = {
        "agentProfileSnapshot": {"profileId": "operator-profile"},
        "omnigent": {"launchPolicyRef": "operator-policy@7"},
    }
    assert await refresh_managed_bootstrap_snapshot(
        SimpleNamespace(),
        parameters=parameters,
        consumer_type="workflow",
        consumer_id="mm:rerun",
        user=SimpleNamespace(id=uuid4()),
    ) == parameters
