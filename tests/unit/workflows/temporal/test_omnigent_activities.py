import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    OmnigentExecutionPlanBinding,
)
from moonmind.workflows.temporal.activities import (
    omnigent_activities as omnigent_activities_module,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _checkpoint_branch_from_request,
    _checkpoint_recovery_decision,
    _checkpoint_recovery_from_request,
    _resolve_live_recovery_authority,
    _try_generic_realizer_dispatch,
    omnigent_execute_activity,
)


@pytest_asyncio.fixture()
async def isolated_control_plane(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Bind the canonical control plane to a per-test SQLite database.

    ``integration.omnigent.execute`` now claims a canonical turn command on every
    execution path, so a test that drives the activity must not write to (or read
    stale commands from) the ambient application database.
    """

    import api_service.db.base as db_base
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/control_plane.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", session_factory)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_generic_dispatch_loads_persisted_plan_and_invokes_selected_realizer() -> None:
    from tests.unit.omnigent.test_generic_platform_production_services import _plan

    plan = _plan("opencode-go/model")

    class PlanStore:
        async def load(self, plan_ref):
            assert plan_ref == plan.planRef
            return plan

        async def persist(self, _plan):
            raise AssertionError("unchanged admitted authority must not be re-persisted")

    class Realizer:
        async def execute(self, request, admitted):
            assert admitted == plan
            assert request.parameters["executionPlanRef"] == plan.planRef
            return AgentRunResult(summary="generic done")

    class Registry:
        def require(self, ref):
            assert ref == "generic-omnigent-host@1"
            return Realizer()

    result = await _try_generic_realizer_dispatch(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="workflow-generic",
            idempotencyKey="step-generic",
            resolvedSkillsetRef="artifact:skills",
            parameters={"executionPlanRef": plan.planRef},
        ),
        plan_store=PlanStore(),
        realizer_registry=Registry(),
    )

    assert result == AgentRunResult(summary="generic done")


@pytest.mark.asyncio
async def test_generic_dispatch_projects_typed_turn_not_started_code() -> None:
    """A typed realizer cause must reach the operator, not a generic code."""

    from moonmind.omnigent.execute import OmnigentTurnNotStartedError
    from tests.unit.omnigent.test_generic_platform_production_services import _plan

    plan = _plan("opencode-go/model")

    class PlanStore:
        async def load(self, plan_ref):
            assert plan_ref == plan.planRef
            return plan

    class Realizer:
        async def execute(self, request, admitted):
            raise OmnigentTurnNotStartedError(
                "Omnigent accepted the marked turn but the provider never started it"
            )

    class Registry:
        def require(self, ref):
            return Realizer()

    result = await _try_generic_realizer_dispatch(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="workflow-not-started",
            idempotencyKey="step-not-started",
            resolvedSkillsetRef="artifact:skills",
            parameters={"executionPlanRef": plan.planRef},
        ),
        plan_store=PlanStore(),
        realizer_registry=Registry(),
    )

    assert result is not None
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "OMNIGENT_CURRENT_TURN_NOT_STARTED"
    assert result.retry_recommendation == "retry_step_execution"
    # Provider-boundary exception text stays out of workflow history; the
    # typed code is the operator-facing cause.
    assert "OMNIGENT_CURRENT_TURN_NOT_STARTED" in result.summary
    assert "never started" not in result.summary


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_unprofiled_activity_projects_turn_not_started_as_typed_result(
    mock_run, monkeypatch: pytest.MonkeyPatch, isolated_control_plane
) -> None:
    """The direct (non-plan) path must return the same typed classification."""

    from moonmind.omnigent.execute import OmnigentTurnNotStartedError

    async def never_started(*_args, **_kwargs):
        raise OmnigentTurnNotStartedError(
            "Omnigent accepted the marked turn but the provider never started it"
        )

    mock_run.side_effect = never_started
    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-unprofiled-never-started",
        idempotencyKey="idem-unprofiled-never-started",
    )

    result = await ActivityEnvironment().run(omnigent_execute_activity, req)

    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "OMNIGENT_CURRENT_TURN_NOT_STARTED"
    assert result.retry_recommendation == "retry_step_execution"
    assert "OMNIGENT_CURRENT_TURN_NOT_STARTED" in result.summary
    assert "never started" not in result.summary
    mock_run.assert_called_once()


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_unprofiled_activity_keeps_raising_ambiguous_terminal(
    mock_run, monkeypatch: pytest.MonkeyPatch, isolated_control_plane
) -> None:
    """Ambiguous still-running turns keep failing the Activity so a retry reattaches."""

    from moonmind.omnigent.execute import OmnigentSessionStillRunningError

    async def still_running(*_args, **_kwargs):
        raise OmnigentSessionStillRunningError("current marked turn is ambiguous")

    mock_run.side_effect = still_running
    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-unprofiled-ambiguous",
        idempotencyKey="idem-unprofiled-ambiguous",
    )

    with pytest.raises(OmnigentSessionStillRunningError):
        await ActivityEnvironment().run(omnigent_execute_activity, req)


@pytest.mark.asyncio
async def test_generic_dispatch_keeps_generic_code_for_untyped_cause() -> None:
    from tests.unit.omnigent.test_generic_platform_production_services import _plan

    plan = _plan("opencode-go/model")

    class PlanStore:
        async def load(self, plan_ref):
            return plan

    class Realizer:
        async def execute(self, request, admitted):
            raise RuntimeError("boom")

    class Registry:
        def require(self, ref):
            return Realizer()

    result = await _try_generic_realizer_dispatch(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="workflow-untyped",
            idempotencyKey="step-untyped",
            resolvedSkillsetRef="artifact:skills",
            parameters={"executionPlanRef": plan.planRef},
        ),
        plan_store=PlanStore(),
        realizer_registry=Registry(),
    )

    assert result is not None
    assert result.provider_error_code == "OMNIGENT_GENERIC_DISPATCH_FAILED"
    assert result.retry_recommendation == "contact_administrator"


@pytest.mark.asyncio
async def test_generic_profile_selection_fails_typed_when_host_plane_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED", "false")
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow-disabled",
        idempotencyKey="step-disabled",
        parameters={
            "omnigent": {
                "agentProfileRef": {
                    "profileId": "omnigent-opencode-default",
                    "version": 1,
                    "digest": "sha256:" + "1" * 64,
                }
            }
        },
    )

    result = await _try_generic_realizer_dispatch(request)

    assert result is not None
    assert result.failure_class == "configuration_error"
    assert result.provider_error_code == "OMNIGENT_GENERIC_REALIZER_NOT_READY"


@pytest.mark.parametrize(
    ("dimension", "changed"),
    [
        ("instructionDigest", "sha256:changed-instructions"),
        ("runtimeId", "codex"),
        ("model", "gpt-5.6"),
        ("effort", "high"),
        ("providerProfileId", "profile-2"),
        ("launchPolicyRef", "artifact://policy/2"),
        ("repositoryBranch", "feature/changed"),
        ("publishMode", "pull_request"),
    ],
)
def test_checkpoint_recovery_decision_requires_branch_for_immutable_change(
    dimension, changed
) -> None:
    immutable_source = {
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "artifact://policy/1",
        "repositoryBranch": "main",
        "publishMode": "none",
    }
    requested = {**immutable_source, dimension: changed}

    decision = _checkpoint_recovery_decision(
        {
            "immutableSource": immutable_source,
            "immutableRequested": requested,
            "liveReattachAvailable": True,
            "coldRestoreAvailable": True,
        }
    )

    assert decision == {
        "recoveryAction": "branch_required",
        "reasonCodes": [f"immutable_{dimension}_changed"],
    }


def test_checkpoint_recovery_decision_requires_new_session_without_branch_evidence() -> None:
    """Changed authority with no branch-capable evidence escalates (#3707 §5)."""

    immutable = {
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "artifact://policy/1",
        "repositoryBranch": "main",
        "publishMode": "none",
    }

    assert _checkpoint_recovery_decision(
        {
            "immutableSource": immutable,
            "immutableRequested": {**immutable, "model": "changed"},
            "omnigentCheckpoint": {
                "validation": {"branchCreationAvailable": False},
            },
        }
    ) == {
        "recoveryAction": "new_session_required",
        "reasonCodes": ["immutable_model_changed"],
    }
    assert _checkpoint_recovery_decision(
        {
            "immutableSource": immutable,
            "immutableRequested": {**immutable, "model": "changed"},
            "omnigentCheckpoint": {
                "validation": {"branchCreationAvailable": True},
            },
        }
    ) == {
        "recoveryAction": "branch_required",
        "reasonCodes": ["immutable_model_changed"],
    }


def test_checkpoint_recovery_decision_uses_the_closed_resume_vocabulary() -> None:
    """Every emitted action is a member of the one typed decision boundary."""

    from moonmind.omnigent.resume_decision import SESSION_RESUME_DECISIONS

    immutable = {
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "artifact://policy/1",
        "repositoryBranch": "main",
        "publishMode": "none",
    }
    emitted = {
        _checkpoint_recovery_decision({})["recoveryAction"],
        _checkpoint_recovery_decision(
            {
                "immutableSource": immutable,
                "immutableRequested": {**immutable, "model": "changed"},
            }
        )["recoveryAction"],
        _checkpoint_recovery_decision(
            {"immutableSource": immutable, "immutableRequested": immutable},
            cold_restore_authorized=True,
        )["recoveryAction"],
    }
    assert emitted <= SESSION_RESUME_DECISIONS
    assert "branch_required" in emitted
    assert "cold_restore" in emitted


def test_checkpoint_recovery_decision_fails_closed_without_authoritative_snapshot() -> None:
    decision = _checkpoint_recovery_decision(
        {"liveReattachAvailable": True, "coldRestoreAvailable": True}
    )

    assert decision == {
        "recoveryAction": "resume_unavailable",
        "reasonCodes": ["immutable_authority_missing"],
    }


def test_checkpoint_recovery_decision_selects_live_or_cold_with_bounded_rationale() -> None:
    immutable = {
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "artifact://policy/1",
        "repositoryBranch": "main",
        "publishMode": "none",
    }

    assert _checkpoint_recovery_decision(
        {
            "immutableSource": immutable,
            "immutableRequested": immutable,
            "liveReattachAvailable": False,
            "coldRestoreAvailable": False,
        },
        live_authority={
            "provider_lease": {"active": True},
            "host_registered": True,
            "session_valid": True,
            "first_message_consistent": True,
            "current_credential_generation": 4,
            "checkpoint_credential_generation": 4,
        },
        cold_restore_authorized=True,
        live_reattach_authorized=True,
    ) == {"recoveryAction": "live_reattach", "reasonCodes": ["all_authority_valid"]}
    assert _checkpoint_recovery_decision(
        {
            "immutableSource": immutable,
            "immutableRequested": immutable,
            "liveReattachAvailable": True,
            "coldRestoreAvailable": False,
        },
        live_authority={
            "provider_lease": None,
            "host_registered": False,
            "session_valid": False,
            "first_message_consistent": False,
            "current_credential_generation": 4,
            "checkpoint_credential_generation": 4,
        },
        cold_restore_authorized=True,
    ) == {
        "recoveryAction": "cold_restore",
        "reasonCodes": ["live_authority_unavailable"],
    }


def test_checkpoint_recovery_decision_ignores_caller_availability_assertions() -> None:
    immutable = {
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "artifact://policy/1",
        "repositoryBranch": "main",
        "publishMode": "none",
    }

    assert _checkpoint_recovery_decision(
        {
            "immutableSource": immutable,
            "immutableRequested": immutable,
            "liveReattachAvailable": True,
            "coldRestoreAvailable": True,
        }
    ) == {
        "recoveryAction": "resume_unavailable",
        "reasonCodes": ["checkpoint_authority_unavailable"],
    }


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_omnigent_execute_activity_delegates(
    mock_run, monkeypatch: pytest.MonkeyPatch, isolated_control_plane
):
    expected_result = AgentRunResult(summary="done", output_refs=[])
    heartbeats: list[tuple[object, ...]] = []

    async def delayed_run(*_args, **_kwargs):
        omnigent_execute_module._safe_heartbeat(  # type: ignore[attr-defined]
            {"omnigentSessionId": "session-1", "eventsCaptured": 1}
        )
        await asyncio.sleep(0.035)
        return expected_result

    mock_run.side_effect = delayed_run
    monkeypatch.setattr(
        omnigent_execute_module,
        "_ACTIVITY_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="123",
        idempotencyKey="key",
    )

    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: heartbeats.append(details)
    result = await env.run(omnigent_execute_activity, req)

    assert result == expected_result
    mock_run.assert_called_once()
    called_req = mock_run.call_args.args[0]
    assert called_req == req
    assert isinstance(mock_run.call_args.kwargs["artifact_gateway"], LocalOmnigentArtifactGateway)
    assert isinstance(mock_run.call_args.kwargs["run_store"], OmnigentBridgeSessionStore)
    assert len(heartbeats) >= 2
    heartbeat_payloads = [
        detail
        for callback_args in heartbeats
        for detail in callback_args
        if isinstance(detail, dict)
    ]
    assert any(payload.get("activityAlive") is True for payload in heartbeat_payloads)
    assert all(
        payload.get("omnigentSessionId") == "session-1"
        for payload in heartbeat_payloads
        if payload.get("activityAlive") is True
        and payload.get("eventsCaptured") == 1
    )


@pytest.mark.asyncio
async def test_plan_bound_execute_dispatches_only_recorded_codex_realizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    plan = SimpleNamespace(
        planRef=plan_ref,
        payload=SimpleNamespace(executionRealizerRef="codex-profile-bound@1"),
    )
    calls: list[str] = []

    class PlanStore:
        def __init__(self, _session_factory):
            pass

        async def load(self, loaded_ref: str):
            assert loaded_ref == plan_ref
            return plan

    class RecordedCodexRealizer:
        async def execute(self, request, loaded_plan):
            calls.append("codex-profile-bound@1")
            assert loaded_plan is plan
            return AgentRunResult(summary="recorded Codex realizer completed")

    class Registry:
        def require(self, realizer_ref: str):
            calls.append(f"require:{realizer_ref}")
            assert realizer_ref == "codex-profile-bound@1"
            return RecordedCodexRealizer()

    from moonmind.omnigent.harness_platform import stores
    from moonmind.omnigent.realizers import registry

    monkeypatch.setattr(stores, "DbExecutionPlanStore", PlanStore)
    monkeypatch.setattr(registry, "get_default_registry", lambda: Registry())
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-codex",
        correlationId="workflow-codex",
        idempotencyKey="step-codex",
        omnigentExecutionPlan=OmnigentExecutionPlanBinding(
            planRef=plan_ref,
            planDigest="sha256:" + "a" * 64,
            planArtifactRef="art-plan-codex",
            taskInputSnapshotRef="art-task-codex",
            taskInputSnapshotDigest="sha256:" + "b" * 64,
        ),
    )

    result = await omnigent_activities_module._try_generic_realizer_dispatch(
        request,
        artifact_gateway=object(),
        run_store=object(),
    )

    assert result is not None
    assert result.summary == "recorded Codex realizer completed"
    assert calls == [
        "require:codex-profile-bound@1",
        "codex-profile-bound@1",
    ]


def test_omnigent_execution_path_does_not_use_managed_github_broker() -> None:
    """Omnigent is an external-agent adapter, not a managed runtime launcher."""

    source = "\n".join(
        [
            inspect.getsource(omnigent_activities_module),
            inspect.getsource(omnigent_execute_module),
        ]
    )

    for disallowed in (
        "github_auth_broker",
        "GitHubAuthBroker",
        "build_github_socket_path",
        "render_gh_wrapper_script",
        "render_git_credential_helper_script",
        "GITHUB_TOKEN",
    ):
        assert disallowed not in source


def test_checkpoint_recovery_request_builds_validated_candidate_workspace() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="recovery-workflow",
        idempotencyKey="recovery-step",
        checkpointRecovery={
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        },
    )

    parsed = _checkpoint_recovery_from_request(request)

    assert parsed is not None
    parsed_checkpoint, candidate = parsed
    assert parsed_checkpoint == checkpoint
    assert candidate.loop_id == (
        f"{checkpoint.workflow_id}:{checkpoint.logical_step_id}"
    )
    assert candidate.head_ref == checkpoint.head_ref
    assert candidate.checkpoint_ref == checkpoint.workspace_checkpoint_ref


def test_checkpoint_recovery_requires_the_admitted_execution_plan() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    checkpoint = _checkpoint().model_copy(
        update={
            "execution_plan_ref": plan_ref,
            "runtime_binding_ref": (
                "omnigent-runtime-binding:sha256:" + "b" * 64
            ),
            "runtime_binding_revision": 2,
            "runtime_binding_fencing_generation": 3,
        }
    )
    checkpoint_payload = checkpoint.model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    binding = OmnigentExecutionPlanBinding(
        planRef=plan_ref,
        planDigest="sha256:" + "a" * 64,
        planArtifactRef="art-plan",
        taskInputSnapshotRef="art-input",
        taskInputSnapshotDigest="sha256:" + "c" * 64,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="recovery-workflow",
        idempotencyKey="recovery-step",
        omnigentExecutionPlan=binding,
        checkpointRecovery={"omnigentCheckpoint": checkpoint_payload},
    )

    parsed = _checkpoint_recovery_from_request(request)
    assert parsed is not None
    assert parsed[0].execution_plan_ref == plan_ref

    mismatched = request.model_copy(
        update={
            "omnigent_execution_plan": binding.model_copy(
                update={
                    "plan_ref": (
                        "omnigent-execution-plan:sha256:" + "d" * 64
                    ),
                    "plan_digest": "sha256:" + "d" * 64,
                }
            )
        }
    )
    with pytest.raises(
        ValueError,
        match="checkpoint execution plan does not match the admitted request",
    ):
        _checkpoint_recovery_from_request(mismatched)


def test_checkpoint_branch_request_requires_explicit_action_and_new_boundary() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="branch-workflow",
        idempotencyKey="branch-turn-1",
        checkpointRecovery={
            "recoveryAction": "branch_required",
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        },
    )

    parsed = _checkpoint_branch_from_request(request)

    assert parsed is not None
    parsed_checkpoint, candidate = parsed
    assert parsed_checkpoint == checkpoint
    assert candidate.checkpoint_ref == checkpoint.workspace_checkpoint_ref


def test_checkpoint_branch_request_is_derived_from_immutable_input_change() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    source = {
        "instructionDigest": "sha256:old",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": checkpoint.provider_profile_id,
        "launchPolicyRef": checkpoint.launch_policy_ref,
        "repositoryBranch": "main",
        "publishMode": "none",
    }
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="branch-workflow",
        idempotencyKey="branch-turn-derived",
        checkpointRecovery={
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            "immutableSource": source,
            "immutableRequested": {
                **source,
                "instructionDigest": "sha256:new",
            },
            "liveReattachAvailable": True,
            "coldRestoreAvailable": True,
        },
    )

    assert _checkpoint_branch_from_request(request) is not None
    assert request.checkpoint_recovery["recoveryDecision"] == {
        "recoveryAction": "branch_required",
        "reasonCodes": ["immutable_instructionDigest_changed"],
    }


def test_checkpoint_branch_request_rejects_source_idempotency_boundary() -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=checkpoint.provider_profile_id,
        correlationId="branch-workflow",
        idempotencyKey=checkpoint.idempotency_key,
        checkpointRecovery={
            "recoveryAction": "branch_required",
            "omnigentCheckpoint": checkpoint.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        },
    )

    with pytest.raises(ValueError, match="new idempotency key"):
        _checkpoint_branch_from_request(request)


@pytest.mark.asyncio
async def test_live_recovery_authority_requires_matching_current_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint().model_copy(
        update={
            "provider_lease_ref": "provider-lease",
            "host_lease_ref": "host-lease",
            "omnigent_host_id": "host-1",
            "omnigent_session_id": "session-1",
            "last_bridge_event_cursor": "4",
            "first_message_id": "message-1",
            "first_message_digest": "sha256:" + "a" * 64,
            "execution_plan_ref": (
                "omnigent-execution-plan:sha256:" + "b" * 64
            ),
            "runtime_binding_ref": (
                "omnigent-runtime-binding:sha256:" + "c" * 64
            ),
            "runtime_binding_revision": 4,
            "runtime_binding_fencing_generation": 5,
        }
    )
    provider = SimpleNamespace(credential_generation=checkpoint.credential_generation)
    provider_lease = SimpleNamespace(
        lease_id="provider-lease",
        owner_id="owner-1",
        idempotency_key=checkpoint.idempotency_key,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            value = self.value if isinstance(self.value, list) else [self.value]
            return SimpleNamespace(all=lambda: value)

        def scalar(self):
            return self.value

    class Session:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args):
            return provider

        async def execute(self, _query):
            self.calls += 1
            return Result(provider_lease if self.calls == 1 else 7)

    host = SimpleNamespace(
        omnigent_host_id="host-1",
        omnigent_session_id="session-1",
        bridge_session_id=checkpoint.bridge_session_id,
        status="assigned",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        model_dump=lambda **_kwargs: {
            "leaseId": "host-lease",
            "status": "assigned",
            "credentialGeneration": checkpoint.credential_generation,
        },
    )
    bridge = SimpleNamespace(
        omnigent_host_id="host-1",
        omnigent_session_id="session-1",
        status="active",
        first_message_digest=checkpoint.first_message_digest,
        first_message_item_id="message-1",
        first_message_pending_id=None,
        first_message_state="posted",
    )

    provider_authority = SimpleNamespace(
        providerProfileRef=checkpoint.provider_profile_id,
        providerLeaseRef=checkpoint.provider_lease_ref,
        credentialGeneration=checkpoint.credential_generation,
    )
    runtime_state = SimpleNamespace(
        binding=SimpleNamespace(
            executionPlanRef=checkpoint.execution_plan_ref,
            runtimeBindingRef=checkpoint.runtime_binding_ref,
            hostBindingRef=checkpoint.host_binding_ref,
            hostLeaseRef=checkpoint.host_lease_ref,
            omnigentHostId=checkpoint.omnigent_host_id,
            omnigentSessionId=checkpoint.omnigent_session_id,
            providerLeases={"primary-model": provider_authority},
        ),
        revision=checkpoint.runtime_binding_revision,
        fencing_generation=checkpoint.runtime_binding_fencing_generation,
    )

    class RuntimeBindingStore:
        def __init__(self, _session_factory):
            pass

        async def get_state(self, binding_ref):
            assert binding_ref == checkpoint.runtime_binding_ref
            return runtime_state

    from moonmind.omnigent.harness_platform import stores

    monkeypatch.setattr(stores, "DbRuntimeBindingStore", RuntimeBindingStore)
    authority = await _resolve_live_recovery_authority(
        checkpoint=checkpoint,
        session_factory=Session,
        host_repository=SimpleNamespace(
            get_host_lease=lambda _lease_id: _async_value(host)
        ),
        run_store=SimpleNamespace(
            get_bridge_session=lambda _bridge_id: _async_value(bridge)
        ),
    )

    assert authority["provider_lease"]["active"] is True
    assert authority["host_registered"] is True
    assert authority["session_valid"] is True
    assert authority["first_message_consistent"] is True
    assert authority["runtime_binding_current"] is True
    assert (
        authority["current_credential_generation"]
        == checkpoint.credential_generation
    )

    runtime_state.revision += 1
    stale_authority = await _resolve_live_recovery_authority(
        checkpoint=checkpoint,
        session_factory=Session,
        host_repository=SimpleNamespace(
            get_host_lease=lambda _lease_id: _async_value(host)
        ),
        run_store=SimpleNamespace(
            get_bridge_session=lambda _bridge_id: _async_value(bridge)
        ),
    )

    assert stale_authority["runtime_binding_current"] is False
    assert stale_authority["host_registered"] is False
    assert stale_authority["session_valid"] is False
    assert stale_authority["first_message_consistent"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lease_rows",
    [
        [],
        [
            SimpleNamespace(
                lease_id="provider-lease",
                owner_id="owner-1",
                idempotency_key="wrong-boundary",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        ],
        [
            SimpleNamespace(
                lease_id="provider-lease",
                owner_id="owner-1",
                idempotency_key="checkpoint-key",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
            SimpleNamespace(
                lease_id="provider-lease",
                owner_id="owner-2",
                idempotency_key="checkpoint-key",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
        ],
    ],
)
async def test_live_recovery_authority_fails_closed_for_ambiguous_or_mismatched_lease(
    lease_rows,
) -> None:
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint

    checkpoint = _checkpoint().model_copy(
        update={
            "idempotency_key": "checkpoint-key",
            "provider_lease_ref": "provider-lease",
            "host_lease_ref": "host-lease",
            "omnigent_host_id": "host-1",
            "omnigent_session_id": "session-1",
            "last_bridge_event_cursor": "4",
            "first_message_id": "message-1",
            "first_message_digest": "sha256:" + "a" * 64,
        }
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalars(self):
            return SimpleNamespace(all=lambda: self.value)

        def scalar(self):
            return self.value

    class Session:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args):
            return SimpleNamespace(
                credential_generation=checkpoint.credential_generation
            )

        async def execute(self, _query):
            self.calls += 1
            return Result(lease_rows if self.calls == 1 else 7)

    authority = await _resolve_live_recovery_authority(
        checkpoint=checkpoint,
        session_factory=Session,
        host_repository=SimpleNamespace(get_host_lease=lambda _ref: _async_value(None)),
        run_store=SimpleNamespace(get_bridge_session=lambda _ref: _async_value(None)),
    )

    assert authority["provider_lease"] is None or not authority["provider_lease"][
        "active"
    ]


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_profile_bound_activity_coordinator_claims_canonical_continuations(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The legacy coordinator path owns the canonical turn boundary (#3707 §1).

    A profile-bound Codex request that carries no execution plan and no Agent
    Profile ref is not realizer-dispatched: ``_try_generic_realizer_dispatch``
    returns ``None`` and this activity builds the coordinator directly. That
    construction must inject the canonical turn service, or every repository
    continuation on this path submits outside the boundary and fences no
    cleanup.

    This drives the real activity, captures the coordinator it actually built,
    and then exercises that coordinator's real continuation-claim method against
    a real control-plane store.
    """

    import api_service.db.base as db_base
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base
    from moonmind.omnigent import profile_bound_execution as pbe_module
    from moonmind.omnigent.control_plane import (
        OmnigentControlPlaneStore,
        TurnSource,
    )
    from moonmind.omnigent.control_plane.identities import (
        canonical_omnigent_session_id,
    )
    from moonmind.omnigent.control_plane.turn_commands import (
        CanonicalTurnCommandService,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/legacy_path.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", session_factory)

    built: list[object] = []
    expected_result = AgentRunResult(summary="legacy path", output_refs=[])

    class CapturingCoordinator(pbe_module.OmnigentProfileBoundExecutionCoordinator):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            built.append(self)

        async def execute(self, request):
            return expected_result

    monkeypatch.setattr(
        pbe_module,
        "OmnigentProfileBoundExecutionCoordinator",
        CapturingCoordinator,
    )

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="legacy-run",
        idempotencyKey="legacy-key",
        executionProfileRef="codex-profile-1",
        parameters={"publishMode": "none"},
    )

    result = await ActivityEnvironment().run(omnigent_execute_activity, request)

    assert result == expected_result
    assert len(built) == 1
    coordinator = built[0]
    # The activity-built coordinator owns the canonical turn service.
    assert isinstance(coordinator._turn_commands, CanonicalTurnCommandService)

    # Drive the coordinator's real continuation admission.
    continuation = request.model_copy(
        deep=True,
        update={"idempotency_key": f"{request.idempotency_key}:repository-continuation:1"},
    )
    claim = await coordinator._claim_continuation_turn(
        request=continuation,
        source_request=request,
        workflow_id="legacy-run",
        step_execution_id="legacy-step",
        recorded_plan=None,
        provider_profile_id="codex-profile-1",
        credential_generation=1,
        runtime_binding_ref=None,
    )
    assert claim is not None

    expected_session_id = canonical_omnigent_session_id(
        workflow_id="legacy-run",
        step_execution_id="legacy-step",
        agent_run_id="legacy-run",
    )
    assert claim.session_id == expected_session_id

    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(expected_session_id)
        cleanup = await repos.cleanup.get(expected_session_id)
    lineages = [turn.lineage_kind for turn in turns]
    assert TurnSource.REPOSITORY_CONTINUATION.value in lineages
    # The admitted continuation fenced incompatible cleanup before mutation.
    assert cleanup is not None and cleanup.generation >= 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_unprofiled_execute_claims_the_canonical_turn_boundary(
    monkeypatch: pytest.MonkeyPatch, isolated_control_plane
) -> None:
    """The unprofiled path mutates the provider, so it claims like every other.

    A request with no ``executionProfileRef`` is not realizer-dispatched and
    never reaches the coordinator, yet ``run_omnigent_execution`` creates a
    provider session and posts its first message. Before this boundary it did so
    with no command claim, no immutable admission, no owner check, and no
    cleanup fence.
    """

    from moonmind.omnigent.control_plane import (
        OmnigentControlPlaneStore,
        TurnSource,
    )
    from moonmind.omnigent.control_plane.identities import (
        canonical_omnigent_session_id,
    )

    session_factory = isolated_control_plane

    expected_result = AgentRunResult(
        summary="unprofiled path",
        output_refs=[],
        metadata={"omnigentSessionId": "provider-session-unprofiled"},
    )

    async def fake_run(*_args, **_kwargs):
        return expected_result

    monkeypatch.setattr(omnigent_execute_module, "run_omnigent_execution", fake_run)

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="unprofiled-run",
        idempotencyKey="unprofiled-key",
    )

    result = await ActivityEnvironment().run(omnigent_execute_activity, request)

    assert result == expected_result
    session_id = canonical_omnigent_session_id(
        workflow_id="unprofiled-run",
        step_execution_id="unprofiled-run",
        agent_run_id="unprofiled-run",
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(session_id)
        turns = await repos.turn_attempts.list_for_session(session_id)
        commands = await repos.commands.list_for_session(session_id)
        cleanup = await repos.cleanup.get(session_id)

    assert [turn.lineage_kind for turn in turns] == [TurnSource.INITIAL.value]
    assert len(commands) == 1
    # The admitted turn fenced incompatible cleanup before the provider mutation
    # and the delivered provider session is attached to canonical authority.
    assert cleanup is not None and cleanup.generation >= 1
    assert session.provider_session_ref == "provider-session-unprofiled"
