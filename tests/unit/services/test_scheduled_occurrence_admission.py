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
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
)
from moonmind.omnigent.harness_platform import stores
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
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
    """Rows the occurrence admission reads; it writes nothing else."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, key):
        if model is ManagedAgentProviderProfile and key == self.provider.profile_id:
            return self.provider
        if model is TemporalArtifact and key == _TASK_REF:
            return SimpleNamespace(
                created_by_principal="original-task-principal",
                sha256=_TASK_DIGEST.removeprefix("sha256:"),
            )
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _RetiringArtifacts(_ArtifactService):
    """Artifacts that record which ones a refused admission retires."""

    def __init__(self) -> None:
        super().__init__()
        self.retired: list[str] = []

    async def read(self, *, artifact_id: str, **_kwargs):
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactNotFoundError,
        )

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
def compiler(monkeypatch):
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


async def _schedule_parameters(artifacts) -> dict:
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
    stored = await plan_service.compile_and_persist_execution_plan(
        session_factory=None,
        artifact_service=artifacts,
        principal="original-task-principal",
        workflow_id="mm-schedule:schedule-1",
        agent_profile_snapshot=snapshot,
        provider_profile=_provider(),
        initial_parameters=parameters,
        authored_request_ref=_TASK_REF,
        authored_request_digest=_TASK_DIGEST,
        task_input_snapshot_ref=_TASK_REF,
        task_input_snapshot_digest=_TASK_DIGEST,
        execution_plan_store=_PlanStore(object()),
    )
    parameters["omnigentExecutionPlan"] = stored.binding.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    parameters["resolvedSkillsetRef"] = stored.resolved_skillset_ref
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
    session = _OccurrenceSession(_provider())
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
    session = _OccurrenceSession(_provider())
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

    from api_service.db import base as db_base
    from moonmind.workflows.temporal.activities import omnigent_activities

    _install(monkeypatch, host=_REPO + "7" * 64, server=_SERVER + "6" * 64)
    artifacts = _ArtifactService()
    parameters, _stored_plan = await _schedule_parameters(artifacts)
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    session = _OccurrenceSession(_provider())

    @asynccontextmanager
    async def session_maker():
        yield session

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    monkeypatch.setattr(
        omnigent_activities, "_OnDemandTemporalArtifactService", lambda _f: artifacts
    )

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
    assert session.commits == 1


async def _successor_admission(monkeypatch, artifacts, parameters, session):
    from contextlib import asynccontextmanager

    from api_service.db import base as db_base
    from moonmind.workflows.temporal.activities import omnigent_activities

    @asynccontextmanager
    async def session_maker():
        yield session

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    monkeypatch.setattr(
        omnigent_activities, "_OnDemandTemporalArtifactService", lambda _f: artifacts
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
    artifacts = _RetiringArtifacts()
    parameters, recorded_plan = await _schedule_parameters(artifacts)

    # An update replaced the host and server while the Step was running.
    _install(monkeypatch, host=_REPO + "8" * 64, server=_SERVER + "9" * 64)
    session = _OccurrenceSession(_provider())
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
    # The admission names the runtime it moved the Step to.
    assert admitted["runtimeChanges"] == {
        "hostImageRef": {"from": _REPO + "7" * 64, "to": _REPO + "8" * 64},
        "serverImageRef": {"from": _SERVER + "6" * 64, "to": _SERVER + "9" * 64},
    }
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
    session = _OccurrenceSession(edited)
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

    session = _OccurrenceSession(_provider())
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

    session = _OccurrenceSession(_provider())
    admitted = await _successor_admission(monkeypatch, artifacts, parameters, session)

    assert admitted == {
        "disposition": "recorded",
        "reason": "the recorded execution plan is unavailable",
    }
    assert session.commits == 0


class _ChildSuccessorSession:
    """The real repository-connection database plus the rows admission reads."""

    def __init__(self, session, provider, *, task_ref: str, task_digest: str):
        self._session = session
        self.provider = provider
        self.task_ref = task_ref
        self.task_digest = task_digest
        self.commits = 0
        self.rollbacks = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def get(self, model, key):
        if model is ManagedAgentProviderProfile:
            return self.provider if key == self.provider.profile_id else None
        if model is TemporalArtifact and key == self.task_ref:
            return SimpleNamespace(
                created_by_principal="user-1",
                sha256=self.task_digest.removeprefix("sha256:"),
            )
        return await self._session.get(model, key)

    async def commit(self):
        self.commits += 1
        await self._session.commit()

    async def rollback(self):
        self.rollbacks += 1
        await self._session.rollback()


@pytest.mark.asyncio
async def test_resolver_child_successor_binds_the_installed_runtime_with_its_authority(
    monkeypatch, tmp_path
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
    try:
        parent = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
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
                )
                yield current["session"]

        async def load_parent(binding, *, admitted_principal=None):
            assert binding == parent.binding
            return parent.envelope

        async def read_json(ref, *, admitted_principal=None):
            return json.loads(artifacts.payloads[ref])

        monkeypatch.setattr(db_base, "async_session_maker", session_maker)
        monkeypatch.setattr(
            omnigent_activities,
            "_OnDemandTemporalArtifactService",
            lambda _f: artifacts,
        )
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
                }
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
        monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER + "9" * 64)
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
    monkeypatch, tmp_path
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
            )
            yield current["session"]

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    monkeypatch.setattr(
        omnigent_activities, "_OnDemandTemporalArtifactService", lambda _f: artifacts
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
        # The installed runtime is the recorded one, so nothing moved.
        assert "runtimeChanges" not in unchanged

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
        _PlanStore.plans[recorded.envelope.planRef] = recorded.envelope.model_copy(
            update={
                "payload": payload.model_copy(
                    update={
                        "resolvedTools": {
                            **payload.resolvedTools,
                            "repositoryAccess": access,
                        }
                    }
                )
            }
        )
        moved = await admit(parameters)
    finally:
        await engine.dispose()

    assert moved["disposition"] == "recorded"
    assert "would change the recorded request intent" in moved["reason"]
    assert "repositoryAccess.source" in moved["reason"]
    assert "repositoryAccess.collaboration" not in moved["reason"]
    assert current["session"].commits == 0
