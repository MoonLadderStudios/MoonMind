"""Schedule deployment refresh preserves execution authority across image updates."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
)
from api_service.services import omnigent_agent_profile_selection as selection
from api_service.services.omnigent_policies import OmnigentPolicyService
from moonmind.omnigent.policies import document_digest
from tests.unit.services.test_omnigent_agent_profile_selection import _GenericV2Session
from tests.unit.services.test_omnigent_execution_plan_service import _policy_snapshot


class DeploymentSession(_GenericV2Session):
    def __init__(self, runtime="opencode", harness="opencode-native", provider="opencode-go"):
        super().__init__(provider_runtime_id=runtime, harness_id=harness)
        self.profile.owner_id = None
        self.provider.provider_id = provider
        self.version.document["credentialSlots"][0]["acceptedProviderIds"] = [provider]
        self.version.document["model"] = {"qualifiedId": "example/default", "effort": "high"}
        self.version.document["allowedLaunchPolicyRefs"] = ["omnigent-on-demand@2"]
        self.old = deepcopy(self.version)
        self.old.version = 1
        self.old.digest = "sha256:" + "1" * 64
        self.old.document["harness"]["catalogRef"] = "old-catalog"
        self.old.document["allowedLaunchPolicyRefs"] = ["omnigent-on-demand@1"]
        self.previous = {
            "profileId": self.profile.profile_id,
            "version": 1,
            "digest": self.old.digest,
            "providerProfileRef": self.provider.profile_id,
            "executionProfileRef": f"omnigent-{harness.removesuffix('-native')}@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "agentId": "imported-agent",
            "document": deepcopy(self.old.document),
        }
        # Historical exclude_none serialization removed authored nulls.
        self.previous["document"]["model"] = {}
        self.usage = SimpleNamespace(
            profile_id=self.profile.profile_id, version=1,
            digest=self.old.digest, effective_snapshot=deepcopy(self.previous),
        )
        self.policies = {
            f"omnigent-on-demand@{v}": _policy_snapshot(
                harness=harness, policy=f"omnigent-on-demand@{v}"
            ) for v in (1, 2)
        }
        for v in (1, 2):
            host = self.policies[f"omnigent-on-demand@{v}"]["boundaries"]["host"]
            host["serverImageRef"] = "ghcr.io/example/server@sha256:" + str(v) * 64
            host["hostImageRef"] = "ghcr.io/example/host@sha256:" + str(v) * 64
        self.policy_states = {ref: "active" for ref in self.policies}

    async def get(self, model, key):
        if model is ManagedAgentProviderProfile:
            return self.provider
        return await super().get(model, key)

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is OmnigentAgentProfileVersion:
            number = statement.compile().params.get("version_1")
            return self.old if number == 1 else self.version
        return await super().scalar(statement)

    def parameters(self):
        return {
            "agentProfileSnapshot": deepcopy(self.previous),
            "model": "example/selected", "effort": "xhigh",
            "profileId": self.provider.profile_id,
            "workflow": {"instructions": "Preserve this task"},
        }


@pytest.fixture
def deployment_session(monkeypatch, request):
    session = DeploymentSession(**getattr(request, "param", {}))

    async def policy_version(_self, policy_id, version):
        ref = f"{policy_id}@{version}"
        document = deepcopy(session.policies[ref]["boundaries"])
        return SimpleNamespace(
            state=session.policy_states[ref], document_json=document,
            validation_json={"valid": True}, digest=document_digest(document),
        )

    monkeypatch.setattr(OmnigentPolicyService, "get_version", policy_version)
    monkeypatch.setattr(selection, "_managed_secret_statuses_for_profiles", AsyncMock(return_value={}))
    monkeypatch.setattr(selection, "provider_profile_launch_ready", lambda *_a, **_kw: True)
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("deployment_session", [
    {},
    {"runtime": "codex_cli", "harness": "codex-native", "provider": "openai"},
    {"runtime": "claude_code", "harness": "claude-native", "provider": "anthropic"},
    {"runtime": "omnigent", "harness": "pi-native", "provider": "anthropic"},
], indirect=True, ids=["opencode", "codex", "claude", "pi"])
async def test_refresh_resolves_real_profile_and_preserves_authored_nulls(deployment_session):
    session = deployment_session
    parameters = session.parameters()
    refreshed = await selection.refresh_schedule_deployment_snapshot(
        session, parameters=parameters, consumer_id="schedule", user=None,
    )
    assert refreshed["agentProfile"]["version"] == 2
    assert refreshed["omnigent"]["launchPolicyRef"] == "omnigent-on-demand@2"
    assert refreshed["model"] == parameters["model"]
    assert refreshed["effort"] == parameters["effort"]
    assert refreshed["workflow"] == parameters["workflow"]
    assert refreshed["profileId"] == parameters["profileId"]
    assert session.usage.version == 1  # Published only with the schedule revision.
    assert parameters["agentProfileSnapshot"]["version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("selection_values", [
    {"model": "example/selected", "effort": "xhigh"},
    {"model": None, "effort": None},
    {},
], ids=["authored", "explicit-null", "omitted"])
async def test_refresh_preserves_resolved_model_over_non_null_snapshot_defaults(
    deployment_session, selection_values,
):
    session = deployment_session
    session.previous["document"]["model"] = deepcopy(session.old.document["model"])
    session.usage.effective_snapshot = deepcopy(session.previous)
    parameters = session.parameters()
    parameters.pop("model")
    parameters.pop("effort")
    parameters.update(selection_values)
    refreshed = await selection.refresh_schedule_deployment_snapshot(
        session, parameters=parameters, consumer_id="schedule", user=None,
    )
    expected = selection_values or {"model": "example/default", "effort": "high"}
    assert {field: refreshed[field] for field in ("model", "effort")} == expected
    assert refreshed["agentProfileSnapshot"]["document"]["model"] == session.old.document["model"]
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["serverImageRef", "hostImageRef"])
@pytest.mark.parametrize("replacement", [
    "other.example/same-image@sha256:" + "2" * 64,
    "ghcr.io/example/different-image@sha256:" + "2" * 64,
])
async def test_refresh_rejects_changed_image_source(deployment_session, field, replacement):
    session = deployment_session
    session.policies["omnigent-on-demand@2"]["boundaries"]["host"][field] = replacement
    with pytest.raises(ValueError, match="launch policy boundaries changed"):
        await selection.refresh_schedule_deployment_snapshot(
            session, parameters=session.parameters(), consumer_id="schedule", user=None,
        )
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["serverImageRef", "hostImageRef"])
async def test_refresh_rejects_removing_image_digest(deployment_session, field):
    session = deployment_session
    host = session.policies["omnigent-on-demand@2"]["boundaries"]["host"]
    host[field] = host[field].partition("@")[0]
    with pytest.raises(ValueError, match="launch policy boundaries changed"):
        await selection.refresh_schedule_deployment_snapshot(
            session, parameters=session.parameters(), consumer_id="schedule", user=None,
        )
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("predecessor_state", ["deprecated", "superseded"])
async def test_refresh_uses_inactive_predecessor_as_comparison_evidence(
    deployment_session, predecessor_state,
):
    session = deployment_session
    session.policy_states["omnigent-on-demand@1"] = predecessor_state
    refreshed = await selection.refresh_schedule_deployment_snapshot(
        session, parameters=session.parameters(), consumer_id="schedule", user=None,
    )
    assert refreshed["omnigent"]["launchPolicyRef"] == "omnigent-on-demand@2"
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_state", ["deprecated", "superseded", "draft"])
async def test_refresh_requires_active_replacement_policy(deployment_session, replacement_state):
    session = deployment_session
    session.policy_states["omnigent-on-demand@2"] = replacement_state
    with pytest.raises(ValueError, match="runtime policy is not active"):
        await selection.refresh_schedule_deployment_snapshot(
            session, parameters=session.parameters(), consumer_id="schedule", user=None,
        )
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("projection_changed", [False, True], ids=["checked", "drifted"])
async def test_refresh_checks_live_projection_against_immutable_upstream_snapshot(
    deployment_session, monkeypatch, projection_changed,
):
    session = deployment_session
    source = {
        "kind": "upstream", "upstreamId": "agent", "upstreamVersion": "1",
        "upstreamSnapshotDigest": "sha256:" + "3" * 64,
    }
    metadata = {"id": "agent", "version": "1", "harness": "opencode-native", "capabilities": []}
    for version in (session.old, session.version):
        version.document["source"] = deepcopy(source)
        version.upstream_snapshot = deepcopy(metadata)
    session.previous["document"]["source"] = deepcopy(source)
    session.usage.effective_snapshot = deepcopy(session.previous)
    projection = SimpleNamespace(
        metadata_snapshot=deepcopy(metadata),
        last_successful_sync_at=datetime.now(timezone.utc), last_attempt_at=None,
        available=True, compatible=True, error=None,
    )
    if projection_changed:
        projection.metadata_snapshot["capabilities"] = ["new-authority"]
    original_get = session.get

    async def get(model, key):
        if model is OmnigentUpstreamAgentProjection:
            return projection
        return await original_get(model, key)

    monkeypatch.setattr(session, "get", get)
    if projection_changed:
        with pytest.raises(ValueError, match="resolved upstream agent metadata differs"):
            await selection.refresh_schedule_deployment_snapshot(
                session, parameters=session.parameters(), consumer_id="schedule", user=None,
            )
    else:
        refreshed = await selection.refresh_schedule_deployment_snapshot(
            session, parameters=session.parameters(), consumer_id="schedule", user=None,
        )
        assert refreshed["agentProfileSnapshot"]["upstreamSnapshot"] == metadata
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["model", "harness", "source", "upstream", "network", "resources", "usage", "policy_identity"])
async def test_refresh_rejects_authority_changes_before_usage_mutation(deployment_session, change):
    session = deployment_session
    if change == "model":
        session.version.document["model"]["qualifiedId"] = "example/more-expensive"
    elif change == "harness":
        session.version.document["harness"]["id"] = "pi-native"
    elif change == "source":
        session.version.document["source"]["importedAgentId"] = "another-agent"
    elif change == "upstream":
        session.version.upstream_snapshot["capabilities"] = ["new-authority"]
    elif change == "usage":
        session.usage.digest = "sha256:" + "0" * 64
    elif change == "policy_identity":
        session.version.document["allowedLaunchPolicyRefs"] = ["another-policy@2"]
    else:
        session.policies["omnigent-on-demand@2"]["boundaries"][change] = {}
    with pytest.raises(ValueError):
        await selection.refresh_schedule_deployment_snapshot(
            session, parameters=session.parameters(), consumer_id="schedule", user=None,
        )
    assert session.usage.version == 1


@pytest.mark.asyncio
async def test_non_generic_historical_snapshot_is_unchanged():
    parameters = {"agentProfileSnapshot": {"profileId": "historical-profile"}}
    assert await selection.refresh_schedule_deployment_snapshot(
        SimpleNamespace(), parameters=parameters, consumer_id="schedule", user=None,
    ) == parameters


@pytest.mark.asyncio
async def test_failed_plan_compilation_cannot_commit_new_schedule_usage(
    deployment_session, monkeypatch,
):
    from uuid import uuid4
    from api_service.services import omnigent_execution_plan_service
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService, RecurringWorkflowValidationError,
    )
    from tests.unit.services.test_recurring_workflows_service import _schedule_plan_binding

    session = deployment_session
    parameters = session.parameters()
    parameters["omnigentExecutionPlan"] = _schedule_plan_binding("1")
    target = {
        "initialParameters": parameters,
        "agentProfileSnapshot": deepcopy(parameters["agentProfileSnapshot"]),
    }
    definition = SimpleNamespace(id=uuid4(), version=1, target=target, owner_user_id=None)

    async def failing_compile(**_kwargs):
        # Artifact repositories may commit independently before the compiler
        # fails. There must be no new usage in that transaction to publish.
        assert session.usage.version == 1
        raise RuntimeError("qualification unavailable")

    monkeypatch.setattr(omnigent_execution_plan_service, "compile_and_persist_execution_plan", failing_compile)
    with pytest.raises(RecurringWorkflowValidationError, match="qualification unavailable"):
        await RecurringWorkflowsService(session, artifact_service=object())._refresh_managed_bootstrap_target(definition)
    assert session.usage.version == definition.version == 1
    assert definition.target == target
