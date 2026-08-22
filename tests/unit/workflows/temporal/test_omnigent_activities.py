import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
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
    mock_run, monkeypatch: pytest.MonkeyPatch
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
async def test_live_recovery_authority_requires_matching_current_records() -> None:
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
    assert (
        authority["current_credential_generation"]
        == checkpoint.credential_generation
    )


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
