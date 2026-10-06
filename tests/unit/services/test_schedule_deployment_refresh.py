"""Schedule deployment refresh preserves execution authority across image updates."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentOAuthHostBindingRecord,
    OmnigentUpstreamAgentProjection,
    TemporalArtifact,
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
        self.task_inputs = {
            artifact_id: SimpleNamespace(
                created_by_principal="original-task-principal", sha256="a" * 64,
            )
            for artifact_id in ("art_original_task", "task-input")
        }

    async def get(self, model, key):
        if model is ManagedAgentProviderProfile:
            return self.provider
        if model is TemporalArtifact:
            return self.task_inputs.get(key)
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
@pytest.mark.parametrize("accepted", [[], ["opencode-go", "other-provider"]])
async def test_refresh_keeps_pinned_provider_when_profile_acceptance_expands(
    deployment_session, accepted,
):
    session = deployment_session
    session.version.document["credentialSlots"][0]["acceptedProviderIds"] = accepted

    refreshed = await selection.refresh_schedule_deployment_snapshot(
        session, parameters=session.parameters(), consumer_id="schedule", user=None,
    )

    assert refreshed["agentProfileSnapshot"]["version"] == 2
    assert refreshed["agentProfileSnapshot"]["providerProfileRef"] == session.provider.profile_id
    assert refreshed["omnigent"]["launchPolicyRef"] == "omnigent-on-demand@2"
    assert session.usage.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["provider_removed", "provider_unpinned", "auth_model", "multiple_slots"]
)
async def test_refresh_rejects_credential_change_affecting_pinned_provider(
    deployment_session, change,
):
    session = deployment_session
    if change == "provider_removed":
        session.version.document["credentialSlots"][0]["acceptedProviderIds"] = ["other-provider"]
    elif change == "provider_unpinned":
        session.previous["providerProfileRef"] = None
        session.usage.effective_snapshot = deepcopy(session.previous)
        session.version.document["credentialSlots"][0]["acceptedProviderIds"] = []
    elif change == "auth_model":
        session.version.document["credentialSlots"][0]["acceptedAuthModels"] = ["none"]
    else:
        # The one pinned Provider Profile does not prove the binding for a
        # second slot; keep that schedule on its authored snapshot.
        second = {
            "id": "secondary",
            "optional": True,
            "acceptedAuthModels": ["none"],
            "acceptedProviderIds": ["other-provider"],
        }
        session.old.document["credentialSlots"].append(deepcopy(second))
        session.version.document["credentialSlots"].append(second)
        session.version.document["credentialSlots"][0]["acceptedProviderIds"] = []

    with pytest.raises(ValueError, match="scheduled Agent Profile semantics changed"):
        await selection.refresh_schedule_deployment_snapshot(
            session, parameters=session.parameters(), consumer_id="schedule", user=None,
        )
    assert session.usage.version == 1


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
        assert _kwargs["principal"] == "original-task-principal"
        assert session.usage.version == 1
        raise RuntimeError("qualification unavailable")

    monkeypatch.setattr(omnigent_execution_plan_service, "compile_and_persist_execution_plan", failing_compile)
    with pytest.raises(RecurringWorkflowValidationError, match="qualification unavailable"):
        await RecurringWorkflowsService(session, artifact_service=object())._refresh_managed_bootstrap_target(definition)
    assert session.usage.version == definition.version == 1
    assert definition.target == target


# A release that publishes a new shared host image cuts the built-in Codex
# policy (codex-on-demand@19 -> @20) and advances the managed bootstrap Agent
# Profile (v18 -> v19). A schedule that records an Omnigent execution plan must
# follow that cut: recompiling against its pinned v18 snapshot planned the
# retired host image, so every deployment update failed with "effective launch
# host image conflicts with the selected Host Class" (mm:220e9937, 2026-10-06).
_BOOTSTRAP = "omnigent-bootstrap-default"


class ManagedBootstrapScheduleSession:
    def __init__(self, *, binding_policy="codex-on-demand@20"):
        def document(policy):
            return {
                "schemaVersion": "moonmind.omnigent-agent-profile.v1",
                "harness": "codex-native",
                "model": {"settings": {}},
                "execution": {
                    "allowedLaunchPolicyRefs": [policy],
                    "defaultExecutionProfileRef": "omnigent-codex@1",
                },
                "policyRef": policy,
            }

        self.old = SimpleNamespace(
            version=18, digest="sha256:" + "8" * 64, document=document("codex-on-demand@19"),
        )
        self.active = SimpleNamespace(
            version=19, digest="sha256:" + "9" * 64, document=document("codex-on-demand@20"),
        )
        self.profile = SimpleNamespace(profile_id=_BOOTSTRAP, state="active", active_version=19)
        self.previous = self.snapshot(self.old)
        self.usage = SimpleNamespace(
            profile_id=_BOOTSTRAP, version=18, digest=self.old.digest,
            effective_snapshot=deepcopy(self.previous),
        )
        self.binding = SimpleNamespace(launch_policy_ref=binding_policy)
        self.provider = SimpleNamespace(profile_id="codex_openai_oauth")
        self.task_input = SimpleNamespace(
            created_by_principal="original-task-principal", sha256="a" * 64,
        )

    @staticmethod
    def snapshot(version):
        policy = version.document["policyRef"]
        return {
            "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
            "profileId": _BOOTSTRAP,
            "version": version.version,
            "digest": version.digest,
            "document": deepcopy(version.document),
            "providerProfileRef": "codex_openai_oauth",
            "executionProfileRef": "omnigent-codex@1",
            "allowedLaunchPolicyRefs": [policy],
            "launchPolicyRef": policy,
            "policyRef": policy,
            "agentId": "codex-native-ui",
        }

    async def get(self, model, key):
        if model is OmnigentAgentProfile:
            return self.profile if key == _BOOTSTRAP else None
        if model is ManagedAgentProviderProfile:
            return self.provider
        if model is TemporalArtifact:
            return self.task_input
        raise AssertionError(f"unexpected get {model.__name__}")

    async def scalar(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is OmnigentAgentProfileVersion:
            number = statement.compile().params.get("version_1")
            return {18: self.old, 19: self.active}.get(number)
        if entity is OmnigentAgentProfileUsage:
            return self.usage
        if entity is OmnigentOAuthHostBindingRecord:
            return self.binding
        raise AssertionError(f"unexpected scalar {entity}")

    async def refresh(self, *_args, **_kwargs):
        return None

    async def flush(self):
        return None


def _managed_plan_schedule(session):
    from uuid import uuid4
    from tests.unit.services.test_recurring_workflows_service import _schedule_plan_binding

    parameters = {
        "targetRuntime": "omnigent",
        "model": "gpt-6.1-sol",
        "effort": "max",
        "agentProfileSnapshot": deepcopy(session.previous),
        "omnigentExecutionPlan": _schedule_plan_binding("1"),
        "task": {"instructions": "Resolve one eligible issue"},
    }
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": parameters,
        "agentProfileSnapshot": deepcopy(session.previous),
        "runtimeProviderTarget": {"targetId": "codex.legacy-profile-bound-omnigent"},
    }
    return SimpleNamespace(id=uuid4(), version=972, target=target, owner_user_id=None)


@pytest.fixture
def managed_snapshot_resolver(monkeypatch):
    """Resolve the active managed snapshot without provider-readiness I/O."""

    calls = []

    async def resolve(session, *, selection, persist_usage=True, **_kwargs):
        calls.append({"selection": deepcopy(selection), "persistUsage": persist_usage})
        assert selection["profileId"] == _BOOTSTRAP
        assert "version" not in selection  # Resolves the active managed version.
        return session.snapshot(session.active)

    monkeypatch.setattr(selection, "resolve_agent_profile_snapshot", resolve)
    return calls


@pytest.mark.asyncio
async def test_plan_schedule_follows_managed_bootstrap_policy_cutover(
    monkeypatch, managed_snapshot_resolver,
):
    from api_service.services import omnigent_execution_plan_service
    from api_service.services.recurring_workflows_service import RecurringWorkflowsService
    from moonmind.omnigent import deployment_identity
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
    from tests.unit.services.test_recurring_workflows_service import _schedule_plan_binding

    session = ManagedBootstrapScheduleSession()
    definition = _managed_plan_schedule(session)
    compiled = []

    async def compile_plan(**kwargs):
        compiled.append(deepcopy(kwargs))
        # Usage is published with the schedule revision, never by compilation.
        assert session.usage.version == 18
        return SimpleNamespace(
            envelope=SimpleNamespace(payload=SimpleNamespace()),
            binding=OmnigentExecutionPlanBinding.model_validate(_schedule_plan_binding("2")),
            artifact_refs=("artifact:plan",),
            resolved_skillset_ref="artifact:skills",
            runtime_provider_rollout={"targetId": "codex.legacy-profile-bound-omnigent"},
        )

    monkeypatch.setattr(
        omnigent_execution_plan_service, "compile_and_persist_execution_plan", compile_plan
    )
    monkeypatch.setattr(deployment_identity, "assert_plan_matches_deployed_runtime", AsyncMock())

    service = RecurringWorkflowsService(session, artifact_service=object())
    assert await service._refresh_managed_bootstrap_target(definition) is True

    planned = compiled[0]["agent_profile_snapshot"]
    assert planned["version"] == 19
    assert planned["launchPolicyRef"] == "codex-on-demand@20"
    assert managed_snapshot_resolver[0]["persistUsage"] is False
    published = definition.target["initialParameters"]
    assert published["agentProfileSnapshot"] == definition.target["agentProfileSnapshot"] == planned
    assert published["omnigentExecutionPlan"] == _schedule_plan_binding("2")
    # Authored task selections survive the managed profile advance.
    assert (published["model"], published["effort"]) == ("gpt-6.1-sol", "max")
    assert published["task"] == {"instructions": "Resolve one eligible issue"}
    assert session.usage.version == 19
    assert session.usage.effective_snapshot == planned
    assert definition.version == 973


@pytest.mark.asyncio
async def test_plan_schedule_waits_for_its_host_binding_to_reach_the_new_policy(
    monkeypatch, managed_snapshot_resolver,
):
    from api_service.services import omnigent_execution_plan_service
    from api_service.services.recurring_workflows_service import RecurringWorkflowsService

    # The release defers cutting over a binding whose host is still serving.
    session = ManagedBootstrapScheduleSession(binding_policy="codex-on-demand@19")
    definition = _managed_plan_schedule(session)
    original = deepcopy(definition.target)
    compile_plan = AsyncMock()
    monkeypatch.setattr(
        omnigent_execution_plan_service, "compile_and_persist_execution_plan", compile_plan
    )

    service = RecurringWorkflowsService(session, artifact_service=object())
    assert await service._refresh_managed_bootstrap_target(definition) is False

    compile_plan.assert_not_awaited()
    assert managed_snapshot_resolver == []
    assert definition.target == original
    assert (definition.version, session.usage.version) == (972, 18)


@pytest.mark.asyncio
async def test_current_managed_snapshot_is_not_re_resolved(managed_snapshot_resolver):
    session = ManagedBootstrapScheduleSession()
    session.profile.active_version = 18
    parameters = {"agentProfileSnapshot": deepcopy(session.previous), "model": "gpt-6.1-sol"}

    assert await selection.refresh_schedule_deployment_snapshot(
        session, parameters=parameters, consumer_id="schedule", user=None,
    ) == parameters
    assert managed_snapshot_resolver == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "moved", [None, "policy", "server", "strict", "repository", "process"]
)
async def test_plan_schedule_refresh_converges_until_authority_moves(
    monkeypatch, managed_snapshot_resolver, moved,
):
    """The bootstrap reconcile pass re-plans a schedule only when it is stale.

    Compilation writes fresh artifacts, so two compiles of unchanged authority
    never produce equal bindings. Recompiling every 120-second pass rewrote the
    schedule ~720 times a day with ~17 new artifacts each time.
    """
    from api_service.services import omnigent_execution_plan_service
    from api_service.services import recurring_workflows_service
    from api_service.services.recurring_workflows_service import RecurringWorkflowsService
    from moonmind.omnigent import deployment_identity
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
    from tests.unit.services.test_recurring_workflows_service import _schedule_plan_binding

    monkeypatch.setattr(recurring_workflows_service, "_SCHEDULE_PLANS_COMPILED_IN_PROCESS", set())
    session = ManagedBootstrapScheduleSession()
    definition = _managed_plan_schedule(session)
    policy = {"policyRef": "codex-on-demand@20", "boundaries": {"host": {"mode": "on_demand_docker"}}}
    stored_plan = SimpleNamespace(
        launchPolicyRef="codex-on-demand@20",
        policySnapshotDigest=omnigent_execution_plan_service.json_artifact_digest(policy),
        admissionAuthority=SimpleNamespace(
            admissionMode="strict" if moved == "strict" else "ordinary"
        ),
        resolvedTools={"repositoryAccess": {"repo": {}}} if moved == "repository" else {},
    )
    compiled = []

    async def compile_plan(**kwargs):
        compiled.append(kwargs)
        # Every compile persists new artifacts, so its binding is always new.
        binding = _schedule_plan_binding(str(len(compiled) + 1))
        return SimpleNamespace(
            envelope=SimpleNamespace(payload=SimpleNamespace()),
            binding=OmnigentExecutionPlanBinding.model_validate(binding),
            artifact_refs=("artifact:plan",),
            resolved_skillset_ref="artifact:skills",
            runtime_provider_rollout={"targetId": "codex.legacy-profile-bound-omnigent"},
        )

    async def load_plan(_store, plan_ref):
        assert plan_ref == definition.target["initialParameters"]["omnigentExecutionPlan"]["planRef"]
        return SimpleNamespace(payload=stored_plan)

    async def resolve_policy(_service, policy_ref):
        assert policy_ref == "codex-on-demand@20"
        return {**policy, "rollout": {"cohort": "next"}} if moved == "policy" else policy

    async def deployed_runtime(payload):
        if moved == "server" and payload is stored_plan:
            raise deployment_identity.OmnigentDeploymentIdentityConflict("server moved")

    monkeypatch.setattr(
        omnigent_execution_plan_service, "compile_and_persist_execution_plan", compile_plan
    )
    monkeypatch.setattr(SessionExecutionPlanStore, "load", load_plan)
    monkeypatch.setattr(OmnigentPolicyService, "resolve_runtime_snapshot", resolve_policy)
    monkeypatch.setattr(deployment_identity, "assert_plan_matches_deployed_runtime", deployed_runtime)

    service = RecurringWorkflowsService(session, artifact_service=object())
    # The release cut moves the schedule onto the new policy.
    assert await service._refresh_managed_bootstrap_target(definition) is True
    assert definition.version == 973
    published = deepcopy(definition.target)
    if moved == "process":
        # A restarted API cannot know which image or environment compiled it.
        monkeypatch.setattr(recurring_workflows_service, "_SCHEDULE_PLANS_COMPILED_IN_PROCESS", set())

    # The next reconcile pass, ~120 seconds later.
    refreshed = await service._refresh_managed_bootstrap_target(definition)

    if moved is None:
        assert refreshed is False
        assert len(compiled) == 1
        assert definition.target == published
        assert definition.version == 973
    else:
        assert refreshed is True
        assert len(compiled) == 2
        assert definition.target["initialParameters"]["omnigentExecutionPlan"] == (
            _schedule_plan_binding("3")
        )
        assert definition.version == 974
