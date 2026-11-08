"""Fresh attempts of an admitted workflow bind the newly installed runtime.

A schedule's stored plan, or the plan an interrupted Step's earlier attempt
used, was compiled before a release. The schedule occurrence or the Step
successor compiles the same frozen inputs through the real plan compiler at
its own admission. Neither the schedule definition, its policy/Profile
versions, nor the bulk schedule refresh are involved.
"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from api_service.db.models import ManagedAgentProviderProfile, TemporalArtifact
from api_service.services import omnigent_execution_plan_service as plan_service
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.omnigent.harness_platform import stores
from moonmind.workflows.temporal.artifacts import (
    ArtifactTransactionOwnership,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ReadableRepositoryPlanArtifacts as _ArtifactService,
    _compiler_dependency_owner,  # noqa: F401 - pytest fixture import
    _configure_ready_host_image_pair,
    _PlanStore,
    _policy_snapshot,
    _snapshot,
)

_REPO = "ghcr.io/example/omnigent-host@sha256:"
_SERVER = "ghcr.io/example/omnigent-server@sha256:"
_TASK_REF = "art_original_task"
_TASK_DIGEST = "sha256:" + "1" * 64


class _OccurrenceSession:
    """Admission lookup rows and staged compiler artifact metadata."""

    def __init__(self, provider, *, artifacts) -> None:
        self.artifacts = artifacts
        self.provider = provider
        self.commits = 0
        self.rollbacks = 0
        self.artifact_rows = []

    def add(self, row):
        assert isinstance(row, TemporalArtifact)
        self.artifact_rows.append(row)

    async def get(self, model, key):
        if model is ManagedAgentProviderProfile and key == self.provider.profile_id:
            return self.provider
        if model is TemporalArtifact and key == _TASK_REF:
            return SimpleNamespace(
                created_by_principal="original-task-principal",
                sha256=_TASK_DIGEST.removeprefix("sha256:"),
            )
        if model is TemporalArtifact and key in self.artifacts.payloads:
            from hashlib import sha256

            # Exact serialized root bytes from the same fake compiler owner,
            # not a permissive metadata row for arbitrary requested IDs.
            return SimpleNamespace(
                artifact_id=key,
                sha256=sha256(self.artifacts.payloads[key]).hexdigest(),
                dependency_closure_version=None,
                dependency_closure_sha256=None,
                dependency_request_sha256=None,
            )
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _bind_caller_artifacts(monkeypatch, artifacts):
    class BoundArtifactService(TemporalArtifactService):
        def __new__(cls, repository, *, transaction_ownership):
            assert transaction_ownership is ArtifactTransactionOwnership.CALLER
            # Preserve the compiler's shared payload transport, but stage its
            # metadata in the Activity's admission transaction.
            artifacts._db_session = repository._session
            return artifacts

    monkeypatch.setattr(
        "moonmind.workflows.temporal.artifacts.TemporalArtifactService",
        BoundArtifactService,
    )


class _RetiringArtifacts(_ArtifactService):
    """Artifacts that record which ones a refused admission retires."""

    def __init__(self) -> None:
        super().__init__()
        self.retired: list[str] = []

    async def read(self, *, artifact_id: str, **_kwargs):
        from moonmind.workflows.temporal.artifacts import TemporalArtifactNotFoundError

        if artifact_id not in self.payloads:
            raise TemporalArtifactNotFoundError(artifact_id)
        return SimpleNamespace(artifact_id=artifact_id), self.payloads[artifact_id]

    async def soft_delete(self, *, artifact_id: str, principal: str, **_kwargs):
        assert principal == "original-task-principal"
        self.retired.append(artifact_id)


def _install(monkeypatch, *, host: str, server: str) -> None:
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", host)
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", server)


@pytest.fixture
def plan_input_delegation(monkeypatch):
    from unittest.mock import AsyncMock

    from moonmind.omnigent import bridge_artifacts

    monkeypatch.setattr(stores, "DbExecutionPlanStore", _PlanStore)
    # Compiler fixtures have no artifact metadata database. Real delegation
    # and workflow reads are covered by test_scheduled_planning_input_authority.
    delegate = AsyncMock()
    monkeypatch.setattr(
        bridge_artifacts, "link_verified_execution_plan_inputs", delegate
    )
    return delegate


@pytest.fixture
def compiler(monkeypatch, plan_input_delegation):
    # Resolved state follows the installed env refs and records provenance.
    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "true")
    monkeypatch.setattr(
        plan_service,
        "resolve_execution_evidence",
        lambda *_a, **_kw: (None, "uncertified"),
    )
    policy_versions: list[str] = []

    async def resolve_policy(**kwargs):
        # The schedule keeps its policy version across the release.
        policy_versions.append(kwargs["policy_ref"])
        return _policy_snapshot(harness="codex-native", policy="codex-on-demand@1")

    monkeypatch.setattr(
        plan_service, "_resolve_runtime_policy_snapshot", resolve_policy
    )

    async def no_catalog(**_kwargs):
        # Hermetic: the bounded fixture harness configuration.
        return None

    monkeypatch.setattr(plan_service, "_try_load_real_harness_config", no_catalog)
    monkeypatch.setattr(stores, "SessionExecutionPlanStore", _PlanStore)

    async def refresh_forbidden(*_args, **_kwargs):
        raise AssertionError("an occurrence must not need the bulk schedule refresh")

    monkeypatch.setattr(
        RecurringWorkflowsService,
        "refresh_managed_bootstrap_schedules",
        refresh_forbidden,
    )
    return SimpleNamespace(policy_versions=policy_versions)


async def _schedule_parameters(
    artifacts, *, provider=None, steps=None, session=None
) -> dict:
    """Plan a schedule the way the API did before the release."""

    snapshot = _snapshot(
        harness="codex-native", policy="codex-on-demand@1", provider_id="codex"
    )
    parameters = {
        "targetRuntime": "omnigent",
        "publishMode": "none",
        "workflow": {"instructions": "Summarize the nightly report."},
        "agentProfileSnapshot": snapshot,
        "system": {"recurrence": {"definitionId": "schedule-1"}},
    }
    if steps is not None:
        parameters["workflow"]["steps"] = steps
    stored = await plan_service.compile_and_persist_execution_plan(
        session_factory=None,
        artifact_service=artifacts,
        principal="original-task-principal",
        workflow_id="mm-schedule:schedule-1",
        agent_profile_snapshot=snapshot,
        provider_profile=provider or _provider(),
        initial_parameters=parameters,
        authored_request_ref=_TASK_REF,
        authored_request_digest=_TASK_DIGEST,
        task_input_snapshot_ref=_TASK_REF,
        task_input_snapshot_digest=_TASK_DIGEST,
        execution_plan_store=_PlanStore(object()),
        db_session=session,
        access=access_for_test("original-task-principal"),
    )
    parameters["omnigentExecutionPlan"] = stored.binding.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    parameters["resolvedSkillsetRef"] = stored.resolved_skillset_ref
    if stored.step_parameters:
        parameters["omnigentStepExecutionPlans"] = stored.step_parameters
    return parameters, stored.envelope.payload


def _provider():
    return SimpleNamespace(
        profile_id="codex", runtime_id="codex_cli", provider_id="openai"
    )


@pytest.mark.asyncio
async def test_occurrence_binds_the_newly_installed_host_without_schedule_rewrite(
    monkeypatch, compiler
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters, stored_plan = await _schedule_parameters(artifacts)
    assert stored_plan.hostImageRef == _REPO + "7" * 64
    action_input = deepcopy(parameters)

    # A routine release installs a rebuilt host and server of the same family.
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    session = _OccurrenceSession(_provider(), artifacts=artifacts)
    admitted = await RecurringWorkflowsService(
        session, artifact_service=artifacts
    ).admit_occurrence_execution_plan(
        workflow_id="mm:schedule-1:occurrence-2",
        initial_parameters=parameters,
    )

    assert admitted["disposition"] == "admitted"
    plan = _PlanStore.persisted.payload
    assert plan.hostImageRef == _REPO + "8" * 64
    assert plan.launchPolicyRef == "codex-on-demand@1"
    launch = json.loads(
        artifacts.payloads[plan.effectiveLaunchSnapshotRef.removeprefix("artifact:")]
    )
    assert launch["hostImageRef"] == _REPO + "8" * 64
    assert launch["serverImageRef"] == _SERVER + "9" * 64
    assert admitted["omnigentExecutionPlan"]["planRef"] == _PlanStore.persisted.planRef
    assert admitted["omnigentExecutionPlan"]["planRef"] != (
        action_input["omnigentExecutionPlan"]["planRef"]
    )
    # The frozen task input and its admitted principal carry over unchanged.
    assert admitted["omnigentExecutionPlan"]["taskInputSnapshotRef"] == _TASK_REF
    assert admitted["omnigentExecutionPlan"]["taskInputSnapshotDigest"] == _TASK_DIGEST
    assert plan.authority.taskInputSnapshotRef == _TASK_REF
    # No new policy/Profile version: both compiles used the same pinned refs.
    assert set(compiler.policy_versions) == {"codex-on-demand@1"}
    # The schedule's own inputs and stored plan are untouched, and the plan an
    # earlier attempt recorded keeps its images.
    assert parameters == action_input
    assert stored_plan.hostImageRef == _REPO + "7" * 64
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("successor", [False, True], ids=["occurrence", "successor"])
async def test_profile_edit_refreshes_occurrences_but_preserves_successor_settings(
    monkeypatch, compiler, successor
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    provider = _provider()
    provider.credential_source = "oauth_volume"
    provider.env_template = {"PROJECT_RUNTIME_STYLE": "admitted"}
    parameters, recorded_plan = await _schedule_parameters(artifacts, provider=provider)
    original_parameters = deepcopy(parameters)
    provider.env_template = {"PROJECT_RUNTIME_STYLE": "edited"}
    session = _OccurrenceSession(provider, artifacts=artifacts)

    if successor:
        admitted = await _successor_admission(monkeypatch, artifacts, parameters, session)
    else:
        admitted = await RecurringWorkflowsService(
            session, artifact_service=artifacts
        ).admit_occurrence_execution_plan(
            workflow_id="mm:schedule-1:occurrence-2",
            initial_parameters=parameters,
        )

    assert admitted["disposition"] == "admitted"
    plan = _PlanStore.persisted.payload
    configuration = plan.providerConfigurationSnapshot
    saved = json.loads(
        artifacts.payloads[configuration.artifactRef.removeprefix("artifact:")]
    )
    assert saved["primary-model"]["launchEnvironment"] == {
        "PROJECT_RUNTIME_STYLE": "admitted" if successor else "edited"
    }
    assert (configuration == recorded_plan.providerConfigurationSnapshot) is successor
    assert plan.authority == recorded_plan.authority
    assert plan.credentialBindings == recorded_plan.credentialBindings
    assert plan.modelConfig == recorded_plan.modelConfig
    assert parameters == original_parameters
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("successor", [False, True], ids=["occurrence", "successor"])
@pytest.mark.parametrize("distinct_profile", [False, True], ids=["same-account", "step-account"])
@pytest.mark.parametrize("legacy_configuration", [False, True], ids=["snapshot", "legacy"])
async def test_specialized_step_configuration_survives_successor_profile_edits(
    monkeypatch, compiler, successor, distinct_profile, legacy_configuration
) -> None:
    from unittest.mock import AsyncMock

    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    provider = _provider()
    provider.credential_source = "oauth_volume"
    provider.env_template = {"PROJECT_RUNTIME_STYLE": "root-admitted"}
    step_provider = deepcopy(provider) if distinct_profile else provider
    if distinct_profile:
        step_provider.profile_id = "step-codex"
        step_provider.env_template = {"PROJECT_RUNTIME_STYLE": "step-admitted"}

    class StepSession(_OccurrenceSession):
        async def get(self, model, key):
            if model is ManagedAgentProviderProfile and key == step_provider.profile_id:
                return step_provider
            return await super().get(model, key)

    session = StepSession(provider, artifacts=artifacts)
    monkeypatch.setattr(
        "api_service.services.omnigent_agent_profile_selection.resolve_default_agent_profile_snapshot",
        AsyncMock(return_value=_snapshot(
            harness="codex-native", policy="codex-on-demand@1",
            provider_id=step_provider.profile_id,
        )),
    )
    with monkeypatch.context() as old_admission:
        if legacy_configuration:
            # Plans admitted before configuration snapshots omitted this
            # optional field on both root and specialized plans.
            old_admission.setattr(plan_service, "PROVIDER_CONFIGURATION_MATERIALIZERS", frozenset())
        parameters, recorded_root = await _schedule_parameters(
            artifacts, provider=provider, session=session,
            steps=[{
                "id": "specialized",
                "runtime": {
                    "mode": "omnigent", "model": "example/specialized-model",
                    "providerProfileRef": step_provider.profile_id,
                },
            }],
        )
    recorded_step = _PlanStore.plans[
        parameters["omnigentStepExecutionPlans"]["specialized"]["omnigentExecutionPlan"]["planRef"]
    ].payload
    original_parameters = deepcopy(parameters)
    original_artifacts = deepcopy(artifacts.payloads)
    provider.env_template = {"PROJECT_RUNTIME_STYLE": "root-edited"}
    if distinct_profile:
        step_provider.env_template = {"PROJECT_RUNTIME_STYLE": "step-edited"}
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)

    if successor:
        admitted = await _successor_admission(monkeypatch, artifacts, parameters, session)
    else:
        admitted = await RecurringWorkflowsService(
            session, artifact_service=artifacts
        ).admit_occurrence_execution_plan(
            workflow_id="mm:schedule-1:occurrence-2", initial_parameters=parameters,
        )

    assert admitted["disposition"] == "admitted"
    root = _PlanStore.plans[admitted["omnigentExecutionPlan"]["planRef"]].payload
    step = _PlanStore.plans[
        admitted["omnigentStepExecutionPlans"]["specialized"]["omnigentExecutionPlan"]["planRef"]
    ].payload
    for plan, recorded, account, label in (
        (root, recorded_root, provider, "root"),
        (step, recorded_step, step_provider, "step" if distinct_profile else "root"),
    ):
        configuration = plan.providerConfigurationSnapshot
        if legacy_configuration and successor:
            assert configuration is None
            assert "providerConfigurationSnapshot" not in plan.model_dump(mode="json")
        else:
            saved = json.loads(artifacts.payloads[configuration.artifactRef.removeprefix("artifact:")])
            assert saved["primary-model"]["providerProfileRef"] == account.profile_id
            assert saved["primary-model"]["launchEnvironment"] == {
                "PROJECT_RUNTIME_STYLE": f"{label}-{'admitted' if successor else 'edited'}"
            }
        assert (configuration == recorded.providerConfigurationSnapshot) is successor
        assert plan.hostImageRef == _REPO + "8" * 64
        assert plan.authority == recorded.authority
        assert plan.credentialBindings == recorded.credentialBindings
        assert plan.modelConfig == recorded.modelConfig
    assert parameters == original_parameters
    assert all(artifacts.payloads[ref] == body for ref, body in original_artifacts.items())
    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_occurrence_keeps_its_recorded_plan_for_a_foreign_host_family(
    monkeypatch, compiler
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters, _stored_plan = await _schedule_parameters(artifacts)

    _install(
        monkeypatch,
        host="ghcr.io/example/other-host@sha256:" + "8" * 64,
        server=_SERVER + "9" * 64,
    )
    session = _OccurrenceSession(_provider(), artifacts=artifacts)
    admitted = await RecurringWorkflowsService(
        session, artifact_service=artifacts
    ).admit_occurrence_execution_plan(
        workflow_id="mm:schedule-1:occurrence-2",
        initial_parameters=parameters,
    )

    # Another image family is never substituted; the admitted plan stays.
    assert admitted["disposition"] == "recorded"
    assert "host image" in admitted["reason"]
    assert "omnigentExecutionPlan" not in admitted
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_worker_activity_admits_the_occurrence_through_the_schedule_owner(
    monkeypatch, compiler
) -> None:
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from api_service.db import base as db_base
    from moonmind.omnigent import bridge_artifacts
    from moonmind.workflows.temporal.activities import omnigent_activities

    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters, _stored_plan = await _schedule_parameters(artifacts)
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    session = _OccurrenceSession(_provider(), artifacts=artifacts)

    @asynccontextmanager
    async def session_maker():
        yield session

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    monkeypatch.setattr(stores, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="mm:schedule-1:occurrence-3",
            workflow_run_id="occurrence-run",
            namespace="default",
        ),
    )
    # This compiler-focused fixture has no blob/metadata database. The real
    # producer-to-planner delegation is exercised by the artifact-service test.
    monkeypatch.setattr(
        bridge_artifacts, "link_verified_execution_plan_inputs", AsyncMock()
    )
    _bind_caller_artifacts(monkeypatch, artifacts)

    with pytest.raises(ValueError, match="workflowId"):
        await omnigent_activities.omnigent_admit_scheduled_occurrence_plan_activity(
            {"initialParameters": parameters}
        )
    # Temporal delivers the workflow's parameters as JSON.
    admitted = (
        await (
            omnigent_activities.omnigent_admit_scheduled_occurrence_plan_activity(
                {
                    "workflowId": "mm:schedule-1:occurrence-3",
                    "initialParameters": json.loads(json.dumps(parameters)),
                }
            )
        )
    )

    assert admitted["disposition"] == "admitted"
    assert _PlanStore.persisted.payload.hostImageRef == _REPO + "8" * 64
    assert admitted["omnigentExecutionPlan"]["planRef"] == _PlanStore.persisted.planRef
    assert session.commits == 2  # Plan persistence plus caller-owned input admission.


async def _successor_admission(monkeypatch, artifacts, parameters, session):
    from contextlib import asynccontextmanager

    from api_service.db import base as db_base
    from moonmind.workflows.temporal.activities import omnigent_activities

    @asynccontextmanager
    async def session_maker():
        yield session

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    _bind_caller_artifacts(monkeypatch, artifacts)
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="mm:interrupted-run",
            workflow_run_id="successor-run",
            namespace="default",
        ),
    )
    # Temporal delivers the workflow's parameters as JSON.
    return await omnigent_activities.omnigent_admit_step_successor_plan_activity(
        {
            "workflowId": "mm:interrupted-run",
            "initialParameters": json.loads(json.dumps(parameters)),
        }
    )


@pytest.mark.asyncio
async def test_step_successor_binds_the_installed_runtime_with_the_same_intent(
    monkeypatch, compiler
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters, recorded_plan = await _schedule_parameters(artifacts)

    # An update replaced the host and server while the Step was running.
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    session = _OccurrenceSession(_provider(), artifacts=artifacts)
    admitted = await _successor_admission(monkeypatch, artifacts, parameters, session)

    assert admitted["disposition"] == "admitted"
    plan = _PlanStore.persisted.payload
    assert admitted["omnigentExecutionPlan"]["planRef"] == _PlanStore.persisted.planRef
    assert admitted["resolvedSkillsetRef"]
    launch = json.loads(
        artifacts.payloads[plan.effectiveLaunchSnapshotRef.removeprefix("artifact:")]
    )
    assert plan.hostImageRef == _REPO + "8" * 64
    assert launch["serverImageRef"] == _SERVER + "9" * 64
    # Harness, Provider Profile account, model and effort, launch and capture
    # policy, source and publication intent are the recorded ones.
    assert plan_service.changed_admitted_intent(recorded_plan, plan) == []
    assert plan.authority == recorded_plan.authority
    assert plan.credentialBindings == recorded_plan.credentialBindings
    assert plan.modelConfig == recorded_plan.modelConfig
    # The interrupted attempt's plan keeps the images it actually used.
    assert recorded_plan.hostImageRef == _REPO + "7" * 64
    assert session.commits == 1


@pytest.mark.asyncio
async def test_step_successor_keeps_the_recorded_plan_when_intent_would_change(
    monkeypatch, compiler
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _RetiringArtifacts()
    parameters, _recorded_plan = await _schedule_parameters(artifacts)
    recorded_artifacts = set(artifacts.payloads)

    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    # The Provider Profile was edited to another route since admission.
    edited = SimpleNamespace(
        profile_id="codex", runtime_id="codex_cli", provider_id="azure-openai"
    )
    session = _OccurrenceSession(edited, artifacts=artifacts)
    admitted = await _successor_admission(monkeypatch, artifacts, parameters, session)

    assert admitted["disposition"] == "recorded"
    assert "would change the recorded request intent: model" in admitted["reason"]
    assert "omnigentExecutionPlan" not in admitted
    assert session.commits == 0
    assert session.rollbacks == 1
    # The refused candidate's artifacts are retired; the recorded ones stay.
    assert set(artifacts.retired) == set(artifacts.payloads) - recorded_artifacts
    assert artifacts.retired


@pytest.mark.asyncio
async def test_occurrence_without_a_snapshot_keeps_its_plan_when_its_task_is_unreadable(
    monkeypatch, compiler
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _RetiringArtifacts()
    parameters, _stored_plan = await _schedule_parameters(artifacts)
    # A legacy schedule action carries no Agent Profile snapshot, and its
    # frozen task input is no longer readable.
    parameters.pop("agentProfileSnapshot")

    session = _OccurrenceSession(_provider(), artifacts=artifacts)
    admitted = await RecurringWorkflowsService(
        session, artifact_service=artifacts
    ).admit_occurrence_execution_plan(
        workflow_id="mm:schedule-1:occurrence-2",
        initial_parameters=parameters,
    )

    assert admitted["disposition"] == "recorded"
    assert "TemporalArtifactNotFoundError" in admitted["reason"]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_step_successor_keeps_the_recorded_plan_it_cannot_compare(
    monkeypatch, compiler
) -> None:
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters, _recorded_plan = await _schedule_parameters(artifacts)
    _PlanStore.plans.pop(parameters["omnigentExecutionPlan"]["planRef"])

    session = _OccurrenceSession(_provider(), artifacts=artifacts)
    admitted = await _successor_admission(monkeypatch, artifacts, parameters, session)

    assert admitted == {
        "disposition": "recorded",
        "reason": "the recorded execution plan is unavailable",
    }
    assert session.commits == 0


class _ChildSuccessorSession:
    """The real repository-connection database plus the rows admission reads."""

    def __init__(
        self, session, provider, *, task_ref: str, task_digest: str, artifacts
    ):
        self._session = session
        self.artifacts = artifacts
        self.provider = provider
        self.task_ref = task_ref
        self.task_digest = task_digest
        self.commits = 0
        self.rollbacks = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def get(self, model, key, **kwargs):
        if model is ManagedAgentProviderProfile:
            return self.provider if key == self.provider.profile_id else None
        if model is TemporalArtifact and key == self.task_ref:
            return SimpleNamespace(
                created_by_principal="user-1",
                sha256=self.task_digest.removeprefix("sha256:"),
            )
        row = await self._session.get(model, key, **kwargs)
        if row is None and model is TemporalArtifact and key in self.artifacts.payloads:
            from hashlib import sha256

            return SimpleNamespace(
                artifact_id=key,
                sha256=sha256(self.artifacts.payloads[key]).hexdigest(),
                dependency_closure_version=None,
                dependency_closure_sha256=None,
                dependency_request_sha256=None,
            )
        return row

    async def commit(self):
        self.commits += 1
        await self._session.commit()

    async def rollback(self):
        self.rollbacks += 1
        await self._session.rollback()


@pytest.mark.asyncio
async def test_resolver_child_successor_binds_the_installed_runtime_with_its_authority(
    monkeypatch, tmp_path, plan_input_delegation
) -> None:
    """A merge-automation resolver child is admitted like any other Run.

    Its plan was compiled from the parent's plan, so the child workflow carries
    no Agent Profile snapshot of its own. Its successor still binds the
    installed images and keeps the child's admitted repository authority.
    """

    from contextlib import asynccontextmanager

    from api_service.db import base as db_base
    from moonmind.workflows.temporal.activities import (
        omnigent_activities,
        omnigent_session_activities,
    )
    from tests.unit.services.test_omnigent_execution_plan_service import (
        _compile_opencode_plan,
        _configure_github_repository_plan_test,
        _ReadableRepositoryPlanArtifacts,
    )

    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.setattr(stores, "SessionExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(plan_service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(plan_service, "SessionExecutionPlanStore", _PlanStore)
    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch,
        tmp_path,
        operations=("read", "write", "branch_write", "review_request", "merge_request"),
    )
    artifacts = _ReadableRepositoryPlanArtifacts()
    provider = SimpleNamespace(
        profile_id="provider-opencode-native",
        runtime_id="opencode",
        provider_id="opencode-go",
    )
    parent_task = {"target": {"initialParameters": {"workflow": {"instructions": "Resolve the existing pull request."}}}}
    artifacts.payloads["art_request_1"] = json.dumps(parent_task, sort_keys=True, separators=(",", ":")).encode()
    try:
        parent = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            task_input_snapshot_digest=plan_service.json_artifact_digest(parent_task),
            profile_tools=("gh",),
            extra_parameters={
                "repository": repository,
                "publishMode": "none",
                "workflow": {
                    "instructions": "Resolve the existing pull request.",
                    "publish": {
                        "mode": "none",
                        "mergeAutomation": {"enabled": True, "finishMode": "merge"},
                    },
                },
            },
            workflow_id="mm:merge-parent",
        )
        current: dict = {}

        @asynccontextmanager
        async def session_maker():
            async with sessions() as session:
                current["session"] = _ChildSuccessorSession(
                    session,
                    provider,
                    task_ref=current.get("taskRef", ""),
                    task_digest=current.get("taskDigest", ""),
                    artifacts=artifacts,
                )
                yield current["session"]

        async def load_parent(binding, *, access):
            assert binding == parent.binding
            return parent.envelope

        async def read_json(ref, *, access):
            return json.loads(artifacts.payloads[ref])

        monkeypatch.setattr(db_base, "async_session_maker", session_maker)
        _bind_caller_artifacts(monkeypatch, artifacts)
        monkeypatch.setattr(
            omnigent_session_activities, "_load_verified_execution_plan", load_parent
        )
        monkeypatch.setattr(
            omnigent_session_activities, "_read_json_artifact", read_json
        )

        # Merge automation prepares the resolver child through its owner.
        child_request = (
            await omnigent_activities.omnigent_prepare_child_execution_plan_activity(
                {
                    "principal": "user-1",
                    "parentExecutionPlan": parent.binding.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                    "childWorkflowId": "resolver:mm:merge-parent:1",
                    "childWorkflowRequest": {
                        "workflow_type": "MoonMind.UserWorkflow",
                        "initial_parameters": {
                            "repository": repository,
                            "publishMode": "auto",
                            "targetRuntime": "omnigent",
                            "githubOperations": [
                                "write",
                                "branch_write",
                                "review_request",
                            ],
                            "task": {"instructions": "Resolve PR #350."},
                        },
                    },
                },
                access=access_for_test("user-1"),
            )
        )
        parameters = child_request["initial_parameters"]
        assert "agentProfileSnapshot" not in parameters
        recorded_plan = _PlanStore.plans[
            parameters["omnigentExecutionPlan"]["planRef"]
        ].payload
        current["taskRef"] = parameters["omnigentExecutionPlan"]["taskInputSnapshotRef"]
        current["taskDigest"] = parameters["omnigentExecutionPlan"][
            "taskInputSnapshotDigest"
        ]
        recorded_launch = json.loads(
            artifacts.payloads[
                recorded_plan.effectiveLaunchSnapshotRef.removeprefix("artifact:")
            ]
        )

        # An update replaced the host and server while the resolver Step ran.
        monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", _REPO + "8" * 64)
        _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
        monkeypatch.setattr(
            omnigent_activities.activity,
            "info",
            lambda: SimpleNamespace(
                workflow_id="resolver:mm:merge-parent:1",
                workflow_run_id="successor-run",
                namespace="default",
            ),
        )
        admitted = (
            await omnigent_activities.omnigent_admit_step_successor_plan_activity(
                {
                    "workflowId": "resolver:mm:merge-parent:1",
                    "initialParameters": json.loads(json.dumps(parameters)),
                }
            )
        )
    finally:
        await engine.dispose()

    assert admitted["disposition"] == "admitted", admitted
    plan = _PlanStore.persisted.payload
    assert admitted["omnigentExecutionPlan"]["planRef"] == _PlanStore.persisted.planRef
    assert admitted["omnigentExecutionPlan"]["planRef"] != (
        parameters["omnigentExecutionPlan"]["planRef"]
    )
    launch = json.loads(
        artifacts.payloads[plan.effectiveLaunchSnapshotRef.removeprefix("artifact:")]
    )
    assert plan.hostImageRef == _REPO + "8" * 64
    assert launch["serverImageRef"] == _SERVER + "9" * 64
    # Harness, Provider Profile, model, source, publication and the child's
    # admitted repository connection and roles are the recorded ones.
    assert plan_service.changed_admitted_intent(recorded_plan, plan) == []
    assert plan.modelConfig == recorded_plan.modelConfig
    assert plan.authority == recorded_plan.authority
    assert set(plan.resolvedTools["repositoryAccess"]) == {"source", "collaboration"}
    assert set(recorded_plan.resolvedTools["repositoryAccess"]) == {
        "source",
        "collaboration",
    }
    for slot in ("source", "collaboration"):
        selection = json.loads(
            artifacts.payloads[
                plan.resolvedTools["repositoryAccess"][slot][
                    "artifactRef"
                ].removeprefix("artifact:")
            ]
        )["selection"]
        recorded_selection = json.loads(
            artifacts.payloads[
                recorded_plan.resolvedTools["repositoryAccess"][slot][
                    "artifactRef"
                ].removeprefix("artifact:")
            ]
        )["selection"]
        for key in ("connectionId", "role", "operations"):
            assert selection[key] == recorded_selection[key]
    # The interrupted attempt keeps the images it actually used.
    assert recorded_plan.hostImageRef == _REPO + "7" * 64
    assert recorded_launch["serverImageRef"] != _SERVER + "9" * 64
    assert current["session"].commits == 1


@pytest.mark.asyncio
async def test_step_successor_keeps_the_recorded_plan_when_its_repository_route_moves(
    monkeypatch, tmp_path, plan_input_delegation
) -> None:
    """A successor never moves to another repository route or endpoint.

    The binding's connection and role can stay equal while the connection's
    assignment now resolves elsewhere; only the access snapshot records the
    selected route.
    """

    from contextlib import asynccontextmanager

    from api_service.db import base as db_base
    from moonmind.workflows.temporal.activities import omnigent_activities
    from tests.unit.services.test_omnigent_execution_plan_service import (
        _compile_opencode_plan,
        _configure_github_repository_plan_test,
        _ReadableRepositoryPlanArtifacts,
    )

    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.setattr(stores, "SessionExecutionPlanStore", _PlanStore)
    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch, tmp_path
    )
    artifacts = _ReadableRepositoryPlanArtifacts()
    provider = SimpleNamespace(
        profile_id="provider-opencode-native",
        runtime_id="opencode",
        provider_id="opencode-go",
    )
    compiled: dict = {}
    real_compile = plan_service.compile_and_persist_execution_plan

    async def recording_compile(**kwargs):
        compiled.setdefault("kwargs", kwargs)
        return await real_compile(**kwargs)

    monkeypatch.setattr(
        plan_service, "compile_and_persist_execution_plan", recording_compile
    )
    current: dict = {}

    @asynccontextmanager
    async def session_maker():
        async with sessions() as session:
            current["session"] = _ChildSuccessorSession(
                session,
                provider,
                task_ref="art_request_1",
                task_digest="sha256:" + "1" * 64,
                artifacts=artifacts,
            )
            yield current["session"]

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    _bind_caller_artifacts(monkeypatch, artifacts)
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="mm:test-deployment-evidence",
            workflow_run_id="successor-run",
            namespace="default",
        ),
    )

    async def admit(parameters):
        return await omnigent_activities.omnigent_admit_step_successor_plan_activity(
            {
                "workflowId": "mm:test-deployment-evidence",
                "initialParameters": json.loads(json.dumps(parameters)),
            }
        )

    try:
        recorded = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            profile_tools=("gh",),
            extra_parameters={"repository": repository},
        )
        parameters = {
            **compiled["kwargs"]["initial_parameters"],
            "agentProfileSnapshot": compiled["kwargs"]["agent_profile_snapshot"],
            "omnigentExecutionPlan": recorded.binding.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
        assert recorded.envelope.payload.resolvedTools["repositoryAccess"]

        # The same route admits a successor that refreshes its snapshots.
        unchanged = await admit(parameters)
        assert unchanged["disposition"] == "admitted", unchanged

        # The interrupted attempt selected another route than the one its
        # connection now resolves to.
        payload = recorded.envelope.payload
        access = dict(payload.resolvedTools["repositoryAccess"])
        artifact_id = access["source"]["artifactRef"].removeprefix("artifact:")
        snapshot = json.loads(artifacts.payloads[artifact_id])
        snapshot["selection"]["routeId"] = "github:previous-route"
        body = plan_service._json_bytes(snapshot)
        artifacts.payloads[artifact_id] = body
        access["source"] = {
            **access["source"],
            "snapshotRef": "repository-access-snapshot:" + plan_service._sha256(body),
        }
        from moonmind.omnigent.harness_platform.execution_plan import (
            create_execution_plan_envelope,
        )

        historical = create_execution_plan_envelope(
            payload.model_copy(
                update={
                    "resolvedTools": {
                        **payload.resolvedTools,
                        "repositoryAccess": access,
                    },
                }
            )
        )
        # A recorded prior selection must remain an internally consistent
        # immutable envelope, not mutate payload underneath the old plan hash.
        root_id = "art_historical_route_plan"
        artifacts.payloads[root_id] = plan_service._json_bytes(
            historical.model_dump(mode="json", by_alias=True)
        )
        _PlanStore.plans[historical.planRef] = historical
        parameters["omnigentExecutionPlan"] = recorded.binding.model_copy(
            update={
                "plan_ref": historical.planRef,
                "plan_digest": "sha256:" + historical.planRef.rsplit(":", 1)[-1],
                "plan_artifact_ref": root_id,
            }
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        moved = await admit(parameters)
    finally:
        await engine.dispose()

    assert moved["disposition"] == "recorded"
    assert "would change the recorded request intent" in moved["reason"]
    assert "repositoryAccess.source" in moved["reason"]
    assert "repositoryAccess.collaboration" not in moved["reason"]
    assert current["session"].commits == 0


def _family_provider(*catalog: str, tiers=None):
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )

    template, default_tier = first_party_family_tier_template("codex_cli", "openai")
    return SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        provider_id="openai",
        default_model=None,
        default_effort=None,
        model_tiers=tiers if tiers is not None else template,
        default_model_tier=default_tier,
        model_catalog_evidence_json={"models": [{"id": item} for item in catalog]},
    )


class _SnapshotSession(_OccurrenceSession):
    """Occurrence lookups whose original task input is a real schedule snapshot."""

    def __init__(self, provider, *, artifacts, task_ref, task_digest) -> None:
        super().__init__(provider, artifacts=artifacts)
        self.task_ref, self.task_digest = task_ref, task_digest

    async def get(self, model, key):
        if model is TemporalArtifact and key == self.task_ref:
            return SimpleNamespace(
                created_by_principal="original-task-principal",
                sha256=self.task_digest.removeprefix("sha256:"),
            )
        return await super().get(model, key)


def _saved_schedule_selection(runtime, model, effort, model_source, provider):
    """Mirror the recurring target the API saves for one authored selection."""

    from moonmind.config.settings import settings
    from moonmind.workflows.executions.model_resolver import resolve_model_effort

    resolved = resolve_model_effort(
        runtime_id="codex_cli",
        profile=provider,
        authored_runtime=runtime if runtime else None,
        require_launch_ready=False,
        workflow_settings=settings.workflow,
    )
    assert (resolved.model, resolved.effort, resolved.model_source) == (
        model,
        effort,
        model_source,
    )
    resolution = {**resolved.as_metadata(), "providerProfileId": "codex"}
    return {
        "model": model,
        "effort": effort,
        "modelSource": model_source,
        "modelTierResolution": resolution,
        "requestedModel": runtime.get("model"),
        **({"modelTier": runtime["modelTier"]} if "modelTier" in runtime else {}),
        "workflowRuntime": {
            **runtime,
            "resolvedModel": model,
            "resolvedEffort": effort,
            "modelTierResolution": resolution,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection,change,expected,expected_effort",
    [
        ("omitted", "release", "gpt-7-sol", "medium"),
        ("tier", "release", "gpt-7-sol", "medium"),
        ("pinned", "release", "gpt-6.1-sol", "medium"),
        ("custom", "release", "gpt-6-luna", "low"),
        # The operator pins Tier 2 to an older Sol after the schedule was saved.
        ("omitted", "pin", "gpt-6-sol", "medium"),
        ("tier", "pin", "gpt-6-sol", "medium"),
        ("pinned", "pin", "gpt-6-sol", "medium"),
        ("tier_effort", "pin", "gpt-6-sol", "low"),
        ("legacy_flat", "pin", "gpt-6-sol", "low"),
        ("custom", "pin", "gpt-6-luna", "low"),
        # The operator raises Tier 2 effort after the schedule was saved.
        ("omitted", "effort", "gpt-7-sol", "high"),
        ("tier", "effort", "gpt-7-sol", "high"),
        ("pinned", "effort", "gpt-6.1-sol", "high"),
        ("tier_effort", "effort", "gpt-7-sol", "low"),
        ("legacy_flat", "effort", "gpt-7-sol", "low"),
        ("custom", "effort", "gpt-6-luna", "low"),
        # Sol is withdrawn: the tier is unavailable, never replaced.
        ("omitted", "withdrawn", None, None),
        ("tier", "withdrawn", None, None),
        ("pinned", "withdrawn", "gpt-6.1-sol", "medium"),
        ("custom", "withdrawn", "gpt-6-luna", "low"),
        # The operator sets Tier 2 to Runtime default after the save.
        ("omitted", "runtime_default", "runtime", "runtime"),
        ("tier", "runtime_default", "runtime", "runtime"),
        ("custom", "runtime_default", "gpt-6-luna", "low"),
        # Saved while Tier 2 was Runtime default; the profile then adopts the
        # automatic family template.
        ("omitted", "adopted", "gpt-7-sol", "medium"),
        ("tier", "adopted", "gpt-7-sol", "medium"),
        # The profile's default tier moves to Terra.
        ("omitted", "default_tier", "gpt-6.1-terra", "medium"),
        ("tier", "default_tier", "gpt-7-sol", "medium"),
        # Only Astra remains, so Tier 2 falls back to it.
        ("omitted", "tiers_shrink", "gpt-6-astra", "medium"),
        ("tier", "tiers_shrink", "gpt-6-astra", "medium"),
        # Tier 2 is pinned to the release it already resolved to.
        ("omitted", "pin_same", "gpt-6.1-sol", "medium"),
    ],
)
async def test_fresh_occurrence_launches_the_model_its_admission_planned(
    monkeypatch, compiler, selection, change, expected, expected_effort
) -> None:
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from api_service.db import base as db_base
    from moonmind.config.settings import settings
    from moonmind.omnigent import bridge_artifacts
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
    from moonmind.workflows.executions.model_resolver import resolve_model_effort
    from moonmind.workflows.temporal.activities import omnigent_activities
    from moonmind.workflows.temporal.activities import (
        omnigent_session_activities as session_activities,
    )

    template, _ = first_party_family_tier_template("codex_cli", "openai")
    runtime_default_tier = {
        "label": "Sol",
        "model": None,
        "effort": None,
        "parameters": {},
        "annotations": {},
    }
    tiers = None
    if selection == "pinned":
        tiers = [dict(tier) for tier in template]
        tiers[1].update(model_mode="pinned", model="gpt-6.1-sol")
    if change == "adopted":
        tiers = [dict(tier) for tier in template]
        tiers[1] = dict(runtime_default_tier)
    runtime, model, effort, source = {
        "omitted": ({}, "gpt-6.1-sol", "medium", "profile_default_tier"),
        "tier": ({"modelTier": 2}, "gpt-6.1-sol", "medium", "requested_tier"),
        "pinned": ({"modelTier": 2}, "gpt-6.1-sol", "medium", "requested_tier"),
        # A tier with an authored effort keeps that effort.
        "tier_effort": (
            {"modelTier": 2, "effort": "low"},
            "gpt-6.1-sol",
            "low",
            "requested_tier",
        ),
        "legacy_flat": (
            {"modelTier": 2, "effort": "low"},
            "gpt-6.1-sol",
            "low",
            "requested_tier",
        ),
        "custom": (
            {"model": "gpt-6-luna", "effort": "low"},
            "gpt-6-luna",
            "low",
            "task_override",
        ),
    }[selection]
    provider = _family_provider("gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna", tiers=tiers)
    if change == "adopted" and selection != "custom":
        saved_pair = resolve_model_effort(
            runtime_id="codex_cli",
            profile=provider,
            authored_runtime=runtime or None,
            require_launch_ready=False,
            workflow_settings=settings.workflow,
        )
        model, effort, source = (
            saved_pair.model,
            saved_pair.effort,
            saved_pair.model_source,
        )
        assert source == "runtime_default"
    saved = _saved_schedule_selection(runtime, model, effort, source, provider)
    workflow_runtime = saved.pop("workflowRuntime")
    if selection == "legacy_flat":
        # Saved as a flat tier and effort pair, before tier resolutions were
        # recorded: that effort stays authored.
        saved.pop("modelTierResolution")
        workflow_runtime = {}
    snapshot = _snapshot(
        harness="codex-native", policy="codex-on-demand@1", provider_id="codex"
    )
    authored = {
        "targetRuntime": "omnigent",
        "publishMode": "none",
        "workflow": {
            "instructions": "Summarize the nightly report.",
            "runtime": workflow_runtime,
        },
        "system": {"recurrence": {"definitionId": "schedule-1"}},
        # Saving compiles the Agent Profile snapshot into the task input.
        "agentProfileSnapshot": snapshot,
        **saved,
    }
    # The schedule's original task input, persisted when the definition was saved.
    task_snapshot = {
        "snapshotVersion": "original-task-input/v1",
        "source": {"kind": "schedule", "definitionId": "schedule-1"},
        "target": {"initialParameters": deepcopy(authored)},
    }
    task_ref = "art_schedule_task"
    task_digest = session_activities._digest_bytes(
        session_activities._json_bytes(task_snapshot)
    )
    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters = deepcopy(authored)
    stored = await plan_service.compile_and_persist_execution_plan(
        session_factory=None,
        artifact_service=artifacts,
        principal="original-task-principal",
        workflow_id="mm-schedule:schedule-1",
        agent_profile_snapshot=snapshot,
        provider_profile=provider,
        initial_parameters=parameters,
        authored_request_ref=task_ref,
        authored_request_digest=task_digest,
        task_input_snapshot_ref=task_ref,
        task_input_snapshot_digest=task_digest,
        execution_plan_store=_PlanStore(object()),
        access=access_for_test("original-task-principal"),
    )
    assert stored.envelope.payload.modelConfig.qualifiedId == model
    parameters.update(stored.execution_parameters)
    action_input = deepcopy(parameters)

    # Sol 7 is released after the definition was saved.
    provider.model_catalog_evidence_json["models"].append({"id": "gpt-7-sol"})
    # The operator then edits Tier 2; the schedule is not revised.
    provider.model_tiers = [dict(tier) for tier in provider.model_tiers]
    if change == "pin":
        provider.model_tiers[1].update(model_mode="pinned", model="gpt-6-sol")
    elif change == "pin_same":
        provider.model_tiers[1].update(model_mode="pinned", model="gpt-6.1-sol")
    elif change == "effort":
        provider.model_tiers[1]["effort"] = "high"
    elif change == "withdrawn":
        provider.model_catalog_evidence_json = {"models": [{"id": "gpt-6-luna"}]}
    elif change == "runtime_default":
        provider.model_tiers[1] = dict(runtime_default_tier)
    elif change == "adopted":
        provider.model_tiers = [dict(tier) for tier in template]
    elif change == "default_tier":
        provider.model_catalog_evidence_json["models"].append({"id": "gpt-6.1-terra"})
        provider.default_model_tier = 3
    elif change == "tiers_shrink":
        provider.model_catalog_evidence_json["models"].append({"id": "gpt-6-astra"})
        provider.model_tiers = provider.model_tiers[:1]
    # A new admission of the same authored selection, against the edited
    # profile, is what this occurrence must plan and launch.
    current = None
    if expected is not None:
        current = resolve_model_effort(
            runtime_id="codex_cli",
            profile=provider,
            authored_runtime=runtime or None,
            require_launch_ready=False,
            workflow_settings=settings.workflow,
        )
        if expected == "runtime":
            expected, expected_effort = current.model, current.effort
            assert current.model_source == "runtime_default"
        if selection != "custom":
            assert (current.model, current.effort) == (expected, expected_effort)
    session = _SnapshotSession(
        provider, artifacts=artifacts, task_ref=task_ref, task_digest=task_digest
    )

    @asynccontextmanager
    async def session_maker():
        yield session

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    monkeypatch.setattr(stores, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="mm:schedule-1:occurrence-2",
            workflow_run_id="occurrence-run",
            namespace="default",
        ),
    )
    monkeypatch.setattr(
        bridge_artifacts, "link_verified_execution_plan_inputs", AsyncMock()
    )
    _bind_caller_artifacts(monkeypatch, artifacts)
    admit = omnigent_activities.omnigent_admit_scheduled_occurrence_plan_activity
    admitted = await admit(
        {
            "workflowId": "mm:schedule-1:occurrence-2",
            "initialParameters": json.loads(json.dumps(parameters)),
        }
    )
    if expected is None:
        # The occurrence keeps the schedule's recorded plan and says why.
        assert admitted["disposition"] == "recorded", admitted
        assert "ModelTierFamilyUnavailableError" in admitted["reason"]
        assert session.commits == 1  # Input admission commits without replacing the plan.
        return
    assert admitted["disposition"] == "admitted", admitted
    binding = OmnigentExecutionPlanBinding.model_validate(
        admitted["omnigentExecutionPlan"]
    )
    plan = _PlanStore.plans[binding.plan_ref]
    assert plan.payload.modelConfig.qualifiedId == expected
    assert plan.payload.modelConfig.effort == expected_effort
    assert binding.task_input_snapshot_ref == task_ref

    # The occurrence launches from the schedule's original task input.
    monkeypatch.setattr(
        session_activities, "_read_json_artifact", AsyncMock(return_value=task_snapshot)
    )
    request = await session_activities._reconstruct_plan_bound_request(
        binding=binding,
        plan=plan,
        workflow_id="mm:schedule-1:occurrence-2",
        step_execution_id="mm:schedule-1:occurrence-2:run:execution:1",
        agent_run_id="agent-run-1",
    )

    assert request.parameters["model"] == expected
    assert request.parameters["omnigent"]["session"]["modelOverride"] == expected
    assert request.parameters["effort"] == expected_effort
    assert request.parameters["omnigent"]["session"]["reasoningEffort"] == (
        expected_effort
    )
    resolution = request.parameters["modelTierResolution"]
    assert (resolution["resolvedModel"], resolution["resolvedEffort"]) == (
        expected,
        expected_effort,
    )
    if selection == "custom":
        assert resolution["modelSource"] == source
    else:
        # The launch records the occurrence admission's whole resolution:
        # tier, label, mode, family status, fallback and sources.
        admitted_resolution = {**current.as_metadata(), "providerProfileId": "codex"}
        assert resolution == admitted_resolution
        assert request.parameters["modelSource"] == current.model_source
    if selection != "legacy_flat":
        runtime_resolution = request.parameters["workflow"]["runtime"]
        assert runtime_resolution["resolvedModel"] == expected
        assert runtime_resolution["resolvedEffort"] == expected_effort
        assert runtime_resolution["modelTierResolution"] == resolution
    if change == "pin_same":
        assert admitted_resolution["modelMode"] == "pinned"

    # An update installs a new runtime while the occurrence's Step runs, and
    # Sol 8 is released before its successor is admitted.
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    provider.model_catalog_evidence_json["models"].append({"id": "gpt-8-sol"})
    if change == "release" and selection in {"omitted", "tier"}:
        # A new admission would now resolve the newer release.
        assert (
            resolve_model_effort(
                runtime_id="codex_cli",
                profile=provider,
                authored_runtime=runtime or None,
                require_launch_ready=False,
                workflow_settings=settings.workflow,
            ).model
            == "gpt-8-sol"
        )
    # The occurrence workflow carries its admitted plan into its input; the
    # rest is the schedule's saved selection.
    occurrence_input = {**parameters, **admitted}
    occurrence_input.pop("disposition")
    successor = await omnigent_activities.omnigent_admit_step_successor_plan_activity(
        {
            "workflowId": "mm:schedule-1:occurrence-2",
            "initialParameters": json.loads(json.dumps(occurrence_input)),
        }
    )
    # The successor binds the installed runtime with the occurrence's pair.
    assert successor["disposition"] == "admitted", successor
    successor_binding = OmnigentExecutionPlanBinding.model_validate(
        successor["omnigentExecutionPlan"]
    )
    successor_plan = _PlanStore.plans[successor_binding.plan_ref]
    assert successor_binding.plan_ref != binding.plan_ref
    assert successor_plan.payload.hostImageRef == _REPO + "8" * 64
    assert (
        plan_service.changed_admitted_intent(plan.payload, successor_plan.payload) == []
    )
    relaunched = await session_activities._reconstruct_plan_bound_request(
        binding=successor_binding,
        plan=successor_plan,
        workflow_id="mm:schedule-1:occurrence-2",
        step_execution_id="mm:schedule-1:occurrence-2:run:execution:2",
        agent_run_id="agent-run-2",
    )
    assert relaunched.parameters["model"] == expected
    assert relaunched.parameters["effort"] == expected_effort
    assert relaunched.parameters["modelTierResolution"] == resolution
    # The definition, its stored plan and its original task input are unchanged.
    assert parameters == action_input
    assert task_snapshot["target"]["initialParameters"]["model"] == model
    assert task_snapshot["target"]["initialParameters"]["effort"] == effort
    saved_plan = _PlanStore.plans[stored.binding.plan_ref]
    assert saved_plan.payload.modelConfig.qualifiedId == model
    assert saved_plan.payload.modelConfig.effort == effort
    if selection == "custom":
        # An authored Custom model stays authored: a plan for another model
        # is a conflict, never a silent replacement.
        with pytest.raises(HarnessPlatformError) as conflict:
            await session_activities._reconstruct_plan_bound_request(
                binding=binding,
                plan=SimpleNamespace(
                    payload=plan.payload.model_copy(
                        update={
                            "modelConfig": plan.payload.modelConfig.model_copy(
                                update={"qualifiedId": "gpt-7-sol"}
                            )
                        }
                    )
                ),
                workflow_id="mm:schedule-1:occurrence-2",
                step_execution_id="mm:schedule-1:occurrence-2:run:execution:1",
                agent_run_id="agent-run-1",
            )
        assert conflict.value.code == "OMNIGENT_EXECUTION_PLAN_CONFLICT"
