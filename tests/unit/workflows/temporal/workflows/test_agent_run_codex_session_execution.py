from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
    AgentTerminalContract,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    _MAX_SUMMARY_CHARS,
)
from moonmind.schemas.temporal_activity_models import AgentRuntimeFetchResultInput
from moonmind.workflows.provider_failures import ProviderFailureEvent
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.merge_gate import build_resolver_run_request

pytestmark = [pytest.mark.asyncio]

def _patch_all_enabled(_patch_id: str) -> bool:
    return True

def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-agent-run-1",
            "run_id": "run-1",
            "search_attributes": {},
            "parent": None,
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", _patch_all_enabled)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )

def _managed_session_request(
    *,
    parameters: dict[str, Any] | None = None,
    workspace_spec: dict[str, Any] | None = None,
    instruction_ref: str = "artifact:instructions",
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-managed-1",
        idempotencyKey="idem-managed-1",
        instructionRef=instruction_ref,
        managedSession={
            "workflowId": "wf-task-1:session:codex_cli",
            "agentRunId": "wf-task-1",
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        },
        parameters=parameters if parameters is not None else {"publishMode": "none"},
        workspaceSpec=workspace_spec if workspace_spec is not None else {},
    )

async def test_managed_session_request_preserves_explicit_empty_inputs() -> None:
    request = _managed_session_request(parameters={}, workspace_spec={})

    assert request.parameters == {}
    assert request.workspace_spec == {}


def _request_with_terminal_contract() -> AgentExecutionRequest:
    return _managed_session_request().model_copy(
        update={
            "terminal_contract": AgentTerminalContract(
                contractId="batch-fanout-v1",
                relativePath="reports/result.json",
                expectedSchemaVersion="1",
                executionRef="exec-1",
            )
        }
    )


async def test_terminal_contract_continuation_is_agent_run_owned_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = AgentExecutionRequest.model_validate(
        _request_with_terminal_contract().model_dump(by_alias=True)
    )
    request.parameters["_moonmindActiveSkillsDir"] = (
        "/work/runtime/skills_active/snapshot-retry"
    )
    request.step_execution = AgentRuntimeStepExecutionLaunch(
        workflowId="wf-task-1",
        runId="run-1",
        logicalStepId="batch-workflows",
        executionOrdinal=2,
        stepExecutionId="wf-task-1:run-1:batch-workflows:execution:2",
        runtimeContextPolicy="reuse_session_same_epoch",
    )
    calls: list[tuple[str, Any]] = []
    evaluations = 0

    async def fake_activity(name: str, payload: Any, **_kwargs: Any) -> Any:
        nonlocal evaluations
        calls.append((name, payload))
        if name == "agent_runtime.evaluate_terminal_evidence":
            evaluations += 1
            if evaluations == 1:
                return {
                    "summary": "missing",
                    "failureClass": "execution_error",
                    "metadata": {
                        "terminalContractMissingEvidence": ["reports/result.json"]
                    },
                }
            return {"summary": "recovered", "metadata": {}}
        if name == "agent_runtime.load_session_snapshot":
            return {"sessionEpoch": 0, "containerId": "ctr-1", "threadId": "thr-1"}
        if name == "agent_runtime.send_turn":
            return {"status": "completed"}
        if name == "agent_runtime.fetch_result":
            return {"summary": "continued", "metadata": {}}
        raise AssertionError(name)

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    result = await run._evaluate_terminal_contract(
        request=request, result=AgentRunResult(summary="initial")
    )

    assert result.failure_class is None
    assert result.metadata["terminalContractRecoveryOutcome"] == "recovered"
    assert result.metadata["terminalContractContinuationCount"] == 1
    assert calls[0][1]["runId"] == "wf-task-1"
    assert [name for name, _ in calls] == [
        "agent_runtime.evaluate_terminal_evidence",
        "agent_runtime.load_session_snapshot",
        "agent_runtime.send_turn",
        "agent_runtime.fetch_result",
        "agent_runtime.evaluate_terminal_evidence",
    ]
    turn = calls[2][1]
    assert turn.request_id == "idem-managed-1:terminal-contract:1"
    assert turn.session_epoch == 0
    assert turn.environment == {
        "MOONMIND_ACTIVE_SKILLS_DIR": "/work/runtime/skills_active/snapshot-retry",
        "MOONMIND_STEP_EXECUTION_ID": (
            "wf-task-1:run-1:batch-workflows:execution:2"
        ),
    }


async def test_terminal_contract_fails_immediately_when_runtime_cannot_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = AgentExecutionRequest.model_validate(
        {
            **_request_with_terminal_contract().model_dump(by_alias=True),
            "managedSession": None,
            "agentId": "claude_code",
        }
    )
    calls: list[str] = []

    async def fake_activity(name: str, _payload: Any, **_kwargs: Any) -> Any:
        calls.append(name)
        return {
            "summary": "missing",
            "failureClass": "execution_error",
            "metadata": {"terminalContractMissingEvidence": ["reports/result.json"]},
        }

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    result = await run._evaluate_terminal_contract(
        request=request, result=AgentRunResult(summary="initial")
    )

    assert result.failure_class == "execution_error"
    assert result.metadata["terminalContractRecoveryOutcome"] == "continuation_unsupported"
    assert result.metadata["terminalContractContinuationCount"] == 0
    assert calls == ["agent_runtime.evaluate_terminal_evidence"]


async def test_gate_owned_pr_resolver_continuation_bypasses_runtime_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = AgentExecutionRequest.model_validate(
        {
            **_request_with_terminal_contract().model_dump(by_alias=True),
            "managedSession": None,
            "agentId": "claude_code",
            "terminalContinuationAuthority": {
                "schemaVersion": "terminal-continuation-authority/v1",
                "gateType": "merge_automation",
                "ownerWorkflowId": "merge-automation:1",
                "ownerRunId": "owner-run-1",
                "ownerWorkflowType": "MoonMind.MergeAutomation",
                "allowedActions": ["reenter_gate"],
                "source": "validated_temporal_parent",
            },
        }
    )

    async def fake_activity(_name: str, _payload: Any, **_kwargs: Any) -> Any:
        return {
            "summary": "durable handoff",
            "failureClass": "execution_error",
            "providerErrorCode": "PR_RESOLVER_REENTER_GATE",
            "metadata": {
                "terminalContractOutcome": "continuation_requested",
                "terminalContractExecutionRef": "exec-1",
                "mergeAutomationDisposition": "reenter_gate",
                "gatedContinuation": {
                    "reason": "codex_review_grace_wait",
                    "notBefore": "2026-07-12T05:05:49Z",
                },
            },
        }

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    result = await run._evaluate_terminal_contract(
        request=request, result=AgentRunResult(summary="initial")
    )

    assert result.failure_class is None
    assert result.provider_error_code is None
    assert (
        result.metadata["terminalContractRecoveryOutcome"]
        == "durable_parent_handoff"
    )
    assert result.metadata["terminalContractContinuationCount"] == 0
    assert result.metrics["continuation_requested"] == 1
    assert result.metrics["continuation_accepted"] == 1
    assert "continuation_rejected_schema" not in result.metrics
    assert "continuation_rejected_ownership" not in result.metrics
    assert result.metadata["continuationReason"] == "codex_review_grace_wait"
    assert result.metadata["continuationNotBefore"] == "2026-07-12T05:05:49Z"
    assert result.metadata["continuationTimingSource"] == "skill_not_before"


@pytest.mark.parametrize(
    ("failure_class", "provider_error_code"),
    [
        ("integration_error", "AUTHENTICATION_FAILED"),
        ("integration_error", "RATE_LIMITED"),
        ("system_error", "MANAGED_RUNTIME_UNAVAILABLE"),
        ("execution_error", "CLI_NONZERO_EXIT"),
    ],
)
async def test_continuation_metadata_does_not_suppress_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_class: str,
    provider_error_code: str,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = AgentExecutionRequest.model_validate(
        {
            **_request_with_terminal_contract().model_dump(by_alias=True),
            "managedSession": None,
            "agentId": "claude_code",
            "terminalContinuationAuthority": {
                "schemaVersion": "terminal-continuation-authority/v1",
                "gateType": "merge_automation",
                "ownerWorkflowId": "merge-automation:1",
                "ownerRunId": "owner-run-1",
                "ownerWorkflowType": "MoonMind.MergeAutomation",
                "allowedActions": ["reenter_gate"],
                "source": "validated_temporal_parent",
            },
        }
    )

    async def fake_activity(_name: str, _payload: Any, **_kwargs: Any) -> Any:
        return {
            "summary": "runtime failed",
            "failureClass": failure_class,
            "providerErrorCode": provider_error_code,
            "metadata": {"terminalContractOutcome": "continuation_requested"},
        }

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    result = await run._evaluate_terminal_contract(
        request=request, result=AgentRunResult(summary="initial")
    )

    assert result.failure_class == failure_class
    assert result.provider_error_code == provider_error_code
    assert (
        result.metadata["terminalContractRecoveryOutcome"]
        == "continuation_rejected_failure_provenance"
    )
    assert result.metrics["continuation_requested"] == 1
    assert result.metrics["continuation_rejected_schema"] == 1
    assert "continuation_accepted" not in result.metrics


async def test_terminal_contract_continuation_exhaustion_is_agent_run_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    calls: list[tuple[str, Any]] = []

    async def fake_activity(name: str, payload: Any, **_kwargs: Any) -> Any:
        calls.append((name, payload))
        if name == "agent_runtime.evaluate_terminal_evidence":
            return {
                "summary": "missing",
                "failureClass": "execution_error",
                "metadata": {"terminalContractMissingEvidence": ["reports/result.json"]},
            }
        if name == "agent_runtime.load_session_snapshot":
            return {"sessionEpoch": 1, "containerId": "ctr-1", "threadId": "thr-1"}
        if name == "agent_runtime.send_turn":
            return {"status": "completed"}
        if name == "agent_runtime.fetch_result":
            return {"summary": "still missing", "metadata": {}}
        raise AssertionError(name)

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    result = await run._evaluate_terminal_contract(
        request=_request_with_terminal_contract(),
        result=AgentRunResult(summary="initial"),
    )

    assert result.failure_class == "execution_error"
    assert result.metadata["terminalContractContinuationCount"] == 2
    assert result.metadata["terminalContractRecoveryOutcome"] == "incomplete"
    turns = [payload for name, payload in calls if name == "agent_runtime.send_turn"]
    assert [turn.request_id for turn in turns] == [
        "idem-managed-1:terminal-contract:1",
        "idem-managed-1:terminal-contract:2",
    ]
    assert {(turn.session_id, turn.thread_id) for turn in turns} == {
        ("sess:wf-task-1:codex_cli", "thr-1")
    }
    assert {turn.session_epoch for turn in turns} == {1}


async def test_terminal_contract_provider_failure_retains_contract_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()

    async def fake_activity(name: str, _payload: Any, **_kwargs: Any) -> Any:
        if name == "agent_runtime.evaluate_terminal_evidence":
            return {
                "summary": "missing",
                "failureClass": "execution_error",
                "metadata": {"terminalContractMissingEvidence": ["reports/result.json"]},
            }
        if name == "agent_runtime.load_session_snapshot":
            return {"sessionEpoch": 1, "containerId": "ctr-1", "threadId": "thr-1"}
        if name == "agent_runtime.send_turn":
            raise RuntimeError("provider transport unavailable")
        raise AssertionError(name)

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    result = await run._evaluate_terminal_contract(
        request=_request_with_terminal_contract(),
        result=AgentRunResult(summary="initial"),
    )

    assert result.failure_class == "execution_error"
    assert result.metadata["terminalContractRecoveryOutcome"] == "provider_failure"
    assert result.metadata["terminalContractContinuationCount"] == 1
    assert result.metadata["terminalContractContinuationHistory"] == [
        {"continuation": 1, "reason": "missing_terminal_evidence", "outcome": "provider_failure"}
    ]


async def test_terminal_contract_continuation_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()

    async def fake_activity(name: str, _payload: Any, **_kwargs: Any) -> Any:
        if name == "agent_runtime.evaluate_terminal_evidence":
            return {
                "summary": "missing",
                "failureClass": "execution_error",
                "metadata": {"terminalContractMissingEvidence": ["reports/result.json"]},
            }
        if name == "agent_runtime.load_session_snapshot":
            return {"sessionEpoch": 1, "containerId": "ctr-1", "threadId": "thr-1"}
        if name == "agent_runtime.send_turn":
            raise asyncio.CancelledError
        raise AssertionError(name)

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await run._evaluate_terminal_contract(
            request=_request_with_terminal_contract(),
            result=AgentRunResult(summary="initial"),
        )


async def test_publish_terminal_result_compacts_replayed_moonspec_verify_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request()
    large_evidence = "verified evidence " * 2000

    async def fake_publish_activity(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "summary": "Completed.",
            "outputRefs": [],
            "diagnosticsRef": "art_agent_result",
            "metadata": {
                "gateResultRef": "art_gate_result",
                "moonSpecVerifyArtifactRef": "art_gate_result",
                "moonSpecVerify": {
                    "schemaVersion": 1,
                    "verdict": "FULLY_IMPLEMENTED",
                    "recommendedNextAction": "advance",
                    "recoverableInCurrentRuntime": True,
                    "remainingWork": [],
                    "blockingEvidenceRefs": ("ref-a", "ref-b"),
                    "requirementCoverage": [
                        {
                            "requirement": "large verification evidence",
                            "evidence": large_evidence,
                        }
                    ],
                    "gateResultRef": "art_gate_result",
                },
            },
        }

    run._execute_routed_activity = fake_publish_activity  # type: ignore[method-assign]

    result = await run._publish_terminal_result(
        request=request,
        result=AgentRunResult(summary="Completed."),
    )

    assert result.metadata["moonSpecVerify"]["verdict"] == "FULLY_IMPLEMENTED"
    assert result.metadata["moonSpecVerify"]["gateResultRef"] == "art_gate_result"
    assert result.metadata["moonSpecVerify"]["blockingEvidenceRefs"] == [
        "ref-a",
        "ref-b",
    ]
    assert "requirementCoverage" not in result.metadata["moonSpecVerify"]
    assert run._terminal_result_payload_compacted_for_history is True
    AgentRunResult(**result.model_dump(mode="json", by_alias=True))


async def test_publish_terminal_result_releases_slot_after_compacted_replay_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request()
    large_evidence = "verified evidence " * 2000
    signals: list[tuple[str, dict[str, Any]]] = []

    class FakeManagerHandle:
        async def signal(self, name: str, payload: dict[str, Any]) -> None:
            signals.append((name, payload))

    async def fake_publish_activity(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "summary": "Completed.",
            "outputRefs": [],
            "diagnosticsRef": "art_agent_result",
            "metadata": {
                "moonSpecVerify": {
                    "verdict": "FULLY_IMPLEMENTED",
                    "recommendedNextAction": "advance",
                    "requirementCoverage": [
                        {
                            "requirement": "large verification evidence",
                            "evidence": large_evidence,
                        }
                    ],
                    "gateResultRef": "art_gate_result",
                },
            },
        }

    run._execute_routed_activity = fake_publish_activity  # type: ignore[method-assign]

    result = await run._publish_terminal_result_with_compacted_replay_cleanup(
        request=request,
        result=AgentRunResult(summary="Completed."),
        manager_handle=FakeManagerHandle(),
    )

    assert result.metadata["moonSpecVerify"]["verdict"] == "FULLY_IMPLEMENTED"
    assert run._terminal_result_payload_compacted_for_history is False
    assert signals == [
        (
            "release_slot",
            {
                "requester_workflow_id": "wf-agent-run-1",
                "profile_id": "codex-default",
            },
        )
    ]


async def test_profile_resolution_error_summarizes_setup_condition() -> None:
    run = MoonMindAgentRun()
    request = _managed_session_request().model_copy(
        update={"execution_profile_ref": "codex-openai"}
    )

    with pytest.raises(ApplicationError) as exc_info:
        run._validate_synced_profile_selection(
            profile_count=0,
            runtime_id="codex_cli",
            request=request,
        )

    message = str(exc_info.value)
    assert "No launch-ready provider profiles found" in message
    assert "runtime=codex_cli" in message
    assert "exact_profile=codex-openai" in message
    assert "missing_condition=setup_required_or_policy" in message

async def test_slot_waiting_reason_summarizes_capacity_or_cooldown() -> None:
    run = MoonMindAgentRun()
    request = _managed_session_request().model_copy(
        update={
            "execution_profile_ref": None,
            "profile_selector": {"providerId": "openai"},
        }
    )

    reason = run._build_provider_slot_waiting_reason(
        runtime_id="codex_cli",
        request=request,
    )

    assert "runtime=codex_cli" in reason
    assert "provider=openai" in reason
    assert "missing_condition=capacity_or_cooldown" in reason


async def test_manager_slot_waiting_reason_identifies_moonmind_capacity() -> None:
    run = MoonMindAgentRun()
    request = _managed_session_request().model_copy(
        update={"execution_profile_ref": "codex-openai"}
    )

    reason = run._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=request,
        manager_state={
            "requester_queue_position": 3,
            "requested_profile": {
                "profile_id": "codex-openai",
                "max_parallel_runs": 1,
                "current_leases_count": 1,
                "cooldown_until": None,
                "enabled": True,
                "launch_ready": True,
            },
        },
    )

    assert "missing_condition=moonmind_slot_capacity" in reason
    assert "slots_in_use=1" in reason
    assert "max_parallel_runs=1" in reason
    assert "queue_position=3" in reason
    assert "capacity_or_cooldown" not in reason


async def test_manager_slot_waiting_reason_identifies_provider_cooldown() -> None:
    run = MoonMindAgentRun()
    request = _managed_session_request().model_copy(
        update={"execution_profile_ref": "codex-openai"}
    )

    reason = run._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=request,
        manager_state={
            "requester_queue_position": 1,
            "requested_profile": {
                "profile_id": "codex-openai",
                "max_parallel_runs": 1,
                "current_leases_count": 0,
                "cooldown_until": "2026-07-16T22:00:00+00:00",
                "enabled": True,
                "launch_ready": True,
            },
        },
    )

    assert "missing_condition=provider_cooldown" in reason
    assert "cooldown_until=2026-07-16T22:00:00+00:00" in reason
    assert "moonmind_slot_capacity" not in reason


async def test_manager_slot_waiting_reason_prioritizes_profile_readiness() -> None:
    run = MoonMindAgentRun()
    request = _managed_session_request().model_copy(
        update={"execution_profile_ref": "codex-openai"}
    )

    reason = run._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=request,
        manager_state={
            "requested_profile": {
                "profile_id": "codex-openai",
                "max_parallel_runs": 1,
                "current_leases_count": 1,
                "cooldown_until": "2026-07-16T22:00:00+00:00",
                "enabled": False,
                "launch_ready": False,
            },
        },
    )

    assert "missing_condition=profile_not_launch_ready" in reason
    assert "provider_cooldown" not in reason
    assert "moonmind_slot_capacity" not in reason


async def test_manager_state_omits_optional_profile_ref_from_legacy_payload() -> None:
    run = MoonMindAgentRun()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_activity(
        activity_name: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls.append((activity_name, payload))
        return {"running": True}

    run._execute_routed_activity = fake_activity  # type: ignore[method-assign]

    await run._manager_state_for_slot_wait(
        runtime_id="codex_cli",
        requester_workflow_id="wf-agent-run-1",
    )

    assert calls == [
        (
            "provider_profile.manager_state",
            {
                "runtime_id": "codex_cli",
                "requester_workflow_id": "wf-agent-run-1",
            },
        )
    ]

async def test_provider_cooldown_backoff_doubles_and_caps_per_profile() -> None:
    run = MoonMindAgentRun()

    cooldowns = [
        run._next_provider_cooldown_seconds(
            runtime_id="claude_code",
            profile_id="claude-anthropic",
            base_seconds=900,
        )
        for _ in range(5)
    ]

    assert cooldowns == [900, 1800, 3600, 3600, 3600]

async def test_provider_cooldown_backoff_tracks_profiles_independently() -> None:
    run = MoonMindAgentRun()

    first_profile = run._next_provider_cooldown_seconds(
        runtime_id="claude_code",
        profile_id="claude-anthropic",
        base_seconds=300,
    )
    second_profile = run._next_provider_cooldown_seconds(
        runtime_id="claude_code",
        profile_id="claude-anthropic-backup",
        base_seconds=300,
    )
    first_profile_second_failure = run._next_provider_cooldown_seconds(
        runtime_id="claude_code",
        profile_id="claude-anthropic",
        base_seconds=300,
    )

    assert (first_profile, second_profile, first_profile_second_failure) == (
        300,
        300,
        600,
    )

async def test_provider_cooldown_backoff_honors_structured_retry_timing() -> None:
    assert MoonMindAgentRun._provider_failure_supplies_retry_timing(
        ProviderFailureEvent(retry_after_seconds=120)
    )
    assert MoonMindAgentRun._provider_failure_supplies_retry_timing(
        ProviderFailureEvent(
            reset_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        )
    )
    assert not MoonMindAgentRun._provider_failure_supplies_retry_timing(
        ProviderFailureEvent(provider_error_class="rate_limit")
    )
    assert not MoonMindAgentRun._provider_failure_supplies_retry_timing(
        ProviderFailureEvent(reset_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    )
    assert not MoonMindAgentRun._provider_failure_supplies_retry_timing(
        ProviderFailureEvent(reset_at="not-a-timestamp")
    )

async def test_managed_fetch_result_input_ignores_legacy_workspace_branch_for_head_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={"publishMode": "pr"},
        workspace_spec={"branch": "main", "startingBranch": "main"},
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert isinstance(activity_input, AgentRuntimeFetchResultInput)
    assert activity_input.target_branch == "main"
    assert activity_input.head_branch is None


async def test_managed_fetch_result_versions_terminal_checkpoint_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.agent_run.workflow.patched",
        lambda patch_id: patch_id == "agent-run-terminal-checkpoint-publication-v1",
    )
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={"publishMode": "pr"},
        workspace_spec={"startingBranch": "main"},
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert activity_input.terminal_checkpoint_publication_enabled is True


async def test_managed_fetch_result_omits_terminal_checkpoint_fields_before_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.agent_run.workflow.patched",
        lambda patch_id: False,
    )
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={"publishMode": "pr"},
        workspace_spec={"startingBranch": "main"},
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert "terminal_checkpoint_publication_enabled" not in activity_input.model_fields_set
    assert "no_remote_writes" not in activity_input.model_fields_set
    assert "terminal_checkpoint_capability_supported" not in activity_input.model_fields_set


async def test_managed_fetch_result_wires_terminal_checkpoint_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.agent_run.workflow.patched",
        lambda patch_id: patch_id == "agent-run-terminal-checkpoint-publication-v1",
    )
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={
            "publishMode": "pr",
            "checkpointPolicy": {"publishOnGracefulFailure": False},
            "dryRun": True,
        },
        workspace_spec={
            "startingBranch": "main",
            "noRemoteWrites": True,
            "readOnly": True,
            "authorityLost": True,
            "terminalCheckpointPublicationUnsupported": True,
        },
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert activity_input.terminal_checkpoint_publication_enabled is False
    assert activity_input.no_remote_writes is True
    assert activity_input.read_only is True
    assert activity_input.dry_run is True
    assert activity_input.workspace_authoritative is False
    assert activity_input.terminal_checkpoint_capability_supported is False


async def test_managed_fetch_result_input_uses_publish_base_for_pr_target_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={"publishMode": "pr", "publishBaseBranch": "main"},
        workspace_spec={
            "branch": "use-this-preselected-single-story-reques-e5921fb9",
            "startingBranch": "use-this-preselected-single-story-reques-e5921fb9",
            "targetBranch": "complete-create-first-remediation-backen-dc3ddf43",
        },
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert isinstance(activity_input, AgentRuntimeFetchResultInput)
    assert activity_input.target_branch == "main"
    assert (
        activity_input.head_branch
        == "complete-create-first-remediation-backen-dc3ddf43"
    )


async def test_managed_fetch_result_marks_pr_resolver_from_task_tool_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    resolver_request = build_resolver_run_request(
        parent_workflow_id="mm:parent",
        pull_request={
            "repo": "MoonLadderStudios/Tactics",
            "number": 1671,
            "url": "https://github.com/MoonLadderStudios/Tactics/pull/1671",
            "headSha": "6b4d769fa0054ac6c2ac865787c0f13de6418bed",
            "baseBranch": "main",
            "headBranch": "feature/pr-1671",
        },
        jira_issue_key=None,
        merge_method="merge",
    )
    parameters = resolver_request["initial_parameters"]
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters=parameters,
        workspace_spec=parameters["workspaceSpec"],
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert isinstance(activity_input, AgentRuntimeFetchResultInput)
    assert activity_input.publish_mode == "auto"
    assert activity_input.pr_resolver_expected is True
    assert activity_input.pr_resolver_merge_gate_owned is True
    assert activity_input.head_branch == "feature/pr-1671"

async def test_managed_fetch_result_marks_standalone_pr_resolver_as_not_gate_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={
            "publishMode": "none",
            "metadata": {"moonmind": {"selectedSkill": "pr-resolver"}},
            "workflow": {
                "tool": {"type": "skill", "name": "pr-resolver"},
                "skill": {"name": "pr-resolver"},
            },
        },
        workspace_spec={"branch": "codex/example-pr"},
    )

    activity_input = run._build_managed_fetch_result_activity_input(request)

    assert activity_input.pr_resolver_expected is True
    assert activity_input.pr_resolver_merge_gate_owned is False

async def test_parent_child_state_signal_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    warnings: list[tuple[str, tuple[Any, ...]]] = []

    class _FailingParentHandle:
        async def signal(self, signal_name: str, args: list[str]) -> None:
            raise RuntimeError("parent unavailable")

    class _Logger:
        def warning(self, message: str, *args: Any, **_kwargs: Any) -> None:
            warnings.append((message, args))

    parent_info = type(
        "ParentInfo",
        (),
        {"workflow_id": "wf-parent-1", "run_id": "parent-run-1"},
    )()
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FailingParentHandle(),
    )
    monkeypatch.setattr(run, "_get_logger", lambda: _Logger())

    await run._signal_parent_child_state_changed(
        parent_info,
        "intervention_requested",
        "Agent requested human feedback.",
    )

    assert len(warnings) == 1
    assert warnings[0][0] == "Failed to signal parent workflow %s: %s"
    assert warnings[0][1][0] == "wf-parent-1"
    assert str(warnings[0][1][1]) == "parent unavailable"

async def test_timeout_result_is_published_through_artifact_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now_values = iter([start, start + timedelta(seconds=2)])

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: next(now_values),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _managed_session_request(
            parameters={"publishMode": "none"},
        ).model_copy(update={"timeout_policy": {"timeout_seconds": 1}})
    )

    assert result.failure_class == "execution_error"
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.publish_artifacts"
    ]

async def test_managed_session_result_enrichment_omits_large_inline_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    large_instruction = "Use this request as the canonical input:\n" + (
        "Implement the workflow cleanup. " * 400
    )
    request = _managed_session_request(
        instruction_ref=large_instruction,
        workspace_spec={
            "workspacePath": "/work/agent_jobs/wf-task-1/repo",
            "workspaceRoot": "/work/agent_jobs/wf-task-1/repo",
            "workspace_path": "/work/agent_jobs/wf-task-1/repo",
            "workspace_root": "/work/agent_jobs/wf-task-1/repo",
            "baseCommit": "abc123",
        },
    )

    result = run._enrich_result_metadata(
        request=request,
        result=AgentRunResult(
            summary="done",
            metadata={"workspacePath": "/work/agent_jobs/wf-task-1/repo"},
        ),
    )

    assert result is not None
    assert result.metadata["instructionRefOmitted"] is True
    assert result.metadata["instructionRefLengthChars"] == len(large_instruction.strip())
    assert len(result.metadata["instructionRefSha256"]) == 64
    assert "instructionRef" not in result.metadata
    assert result.metadata["managedSession"]["agentRunId"] == "wf-task-1"
    assert result.metadata["agentKind"] == "managed"
    assert result.metadata["agentId"] == "codex_cli"
    assert result.metadata["runtimeCapabilities"]["workspaceAuthority"] == (
        "managed_runtime"
    )
    assert result.metadata["runtimeCapabilities"]["checkpointCaptureKinds"] == [
        "worktree_archive"
    ]
    assert result.metadata["workspaceLocator"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "wf-task-1",
        "relativePath": "repo",
    }
    assert "workspacePath" not in result.metadata
    assert "workspaceRoot" not in result.metadata
    assert result.metadata["workspaceSpec"] == {"baseCommit": "abc123"}

async def test_managed_session_result_enrichment_carries_story_output_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={
            "story_output": {
                "story_breakdown_path": "artifacts/story-breakdowns/demo/stories.json",
                "story_breakdown_markdown_path": (
                    "artifacts/story-breakdowns/demo/stories.md"
                ),
            }
        },
    )

    result = run._enrich_result_metadata(
        request=request,
        result=AgentRunResult(summary="done", metadata={}),
    )

    assert result is not None
    assert result.metadata["storyBreakdownPath"] == (
        "artifacts/story-breakdowns/demo/stories.json"
    )
    assert result.metadata["storyBreakdownMarkdownPath"] == (
        "artifacts/story-breakdowns/demo/stories.md"
    )
    assert result.metadata["storyOutput"]["storyBreakdownPath"] == (
        "artifacts/story-breakdowns/demo/stories.json"
    )
    assert result.metadata["storyOutput"]["storyBreakdownMarkdownPath"] == (
        "artifacts/story-breakdowns/demo/stories.md"
    )


async def test_managed_session_result_enrichment_carries_moonspec_verify_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()
    request = _managed_session_request(
        parameters={
            "metadata": {"moonmind": {"selectedSkill": "moonspec-verify"}},
            "verify_artifact_path": "var/artifacts/moonspec-verify/final.json",
        },
    )

    result = run._enrich_result_metadata(
        request=request,
        result=AgentRunResult(summary="done", metadata={}),
    )

    assert result is not None
    assert result.metadata["verify_artifact_path"] == (
        "var/artifacts/moonspec-verify/final.json"
    )
    assert result.metadata["moonmind"]["selectedSkill"] == "moonspec-verify"


async def test_agent_run_uses_codex_session_adapter_for_managed_codex_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    session_adapter_requests: list[AgentExecutionRequest] = []
    loaded_snapshots: list[dict[str, Any]] = []
    requested_snapshot_workflow_ids: list[str] = []
    prepared_instruction_results: list[str] = []
    defer_instruction_flags: list[bool] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("ManagedAgentAdapter should not be used for managedSession requests")

    class _FakeCodexSessionAdapter:
        def __init__(self, **kwargs: Any) -> None:
            self._load_session_snapshot = kwargs["load_session_snapshot"]
            self._prepare_turn_instructions = kwargs["prepare_turn_instructions"]
            defer_instruction_flags.append(
                kwargs["defer_turn_instructions_until_session_launch"]
            )

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            session_adapter_requests.append(request)
            snapshot_workflow_id = "wf-task-1:session:override"
            requested_snapshot_workflow_ids.append(snapshot_workflow_id)
            loaded_snapshots.append(
                await self._load_session_snapshot(snapshot_workflow_id)
            )
            prepared_instruction_results.append(
                await self._prepare_turn_instructions(
                    {
                        "request": request.model_dump(by_alias=True, exclude_none=True),
                        "workspacePath": "/work/task/repo",
                    }
                )
            )
            return AgentRunHandle(
                runId="managed-session-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
                metadata={"sessionId": request.managed_session.session_id},
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="completed",
            )

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError(
                "Terminal managed-session runs should fetch results through "
                "agent_runtime.fetch_result"
            )

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.load_session_snapshot":
            return {
                "binding": {
                    "workflowId": "wf-task-1:session:codex_cli",
                    "agentRunId": "wf-task-1",
                    "sessionId": "sess:wf-task-1:codex_cli",
                    "sessionEpoch": 1,
                    "runtimeId": "codex_cli",
                    "executionProfileRef": "codex-default",
                },
                "status": "active",
                "containerId": None,
                "threadId": None,
                "activeTurnId": None,
                "terminationRequested": False,
            }
        if activity_name == "agent_runtime.fetch_result":
            return {
                "summary": "Session-backed Codex step completed.",
                "metadata": {"resultSource": "agent-runtime-fetch-result"},
            }
        if activity_name == "agent_runtime.prepare_turn_instructions":
            return "Prepared session instructions"
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert session_adapter_requests[0].managed_session is not None
    assert requested_snapshot_workflow_ids == ["wf-task-1:session:override"]
    assert loaded_snapshots == [
        {
            "binding": {
                "workflowId": "wf-task-1:session:codex_cli",
                "agentRunId": "wf-task-1",
                "sessionId": "sess:wf-task-1:codex_cli",
                "sessionEpoch": 1,
                "runtimeId": "codex_cli",
                "executionProfileRef": "codex-default",
            },
            "status": "active",
            "containerId": None,
            "threadId": None,
            "activeTurnId": None,
            "terminationRequested": False,
        }
    ]
    assert prepared_instruction_results == ["Prepared session instructions"]
    assert defer_instruction_flags == [True]
    assert run.run_id == "managed-session-run-1"
    assert result.summary == "Session-backed Codex step completed."
    assert result.metadata["resultSource"] == "agent-runtime-fetch-result"
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"
    assert result.metadata["agentRunId"] == "wf-task-1"
    assert result.metadata["managedSession"]["sessionId"] == "sess:wf-task-1:codex_cli"
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.load_session_snapshot",
        "agent_runtime.prepare_turn_instructions",
        "agent_runtime.fetch_result",
        "agent_runtime.publish_artifacts",
    ]
    assert routed_calls[0][1]["workflowId"] == "wf-task-1:session:override"
    assert routed_calls[0][1]["agentRunId"] == "wf-task-1"
    assert routed_calls[1][1]["workspacePath"] == "/work/task/repo"
    assert isinstance(routed_calls[2][1], AgentRuntimeFetchResultInput)
    assert routed_calls[2][1].run_id == "wf-task-1"
    assert routed_calls[2][1].agent_id == "codex"

async def test_agent_run_keeps_managed_adapter_for_non_session_managed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    managed_requests: list[AgentExecutionRequest] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            managed_requests.append(request)
            return AgentRunHandle(
                runId="managed-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="running",
                startedAt=agent_run_module.workflow.now(),
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("CodexSessionAdapter should not be used without managedSession")

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Managed adapter path", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            executionProfileRef="codex-default",
            correlationId="corr-managed-plain-1",
            idempotencyKey="idem-managed-plain-1",
            parameters={"publishMode": "none"},
        )
    )

    assert len(managed_requests) == 1
    assert managed_requests[0].managed_session is None
    assert result.summary == "Managed adapter path"
    assert result.metadata["agentRunId"] == "managed-run-1"
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.fetch_result",
        "agent_runtime.publish_artifacts",
    ]

async def test_agent_run_starts_deferred_codex_session_only_after_slot_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_info = type(
        "ParentInfo",
        (),
        {"workflow_id": "wf-task-1", "run_id": "parent-run-1"},
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-agent-run-1",
            "run_id": "run-1",
            "search_attributes": {},
            "parent": parent_info,
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", _patch_all_enabled)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )

    run = MoonMindAgentRun()
    order: list[str] = []
    parent_signals: list[tuple[str, Any]] = []
    session_starts: list[tuple[str, Any, dict[str, Any]]] = []
    session_adapter_requests: list[AgentExecutionRequest] = []

    class _FakeParentHandle:
        async def signal(
            self, signal_name: str, payload: Any = None, **kwargs: Any
        ) -> None:
            parent_signals.append(
                (signal_name, payload if payload is not None else kwargs.get("args"))
            )

    class _FakeSessionHandle:
        async def signal(self, _signal_name: str, _payload: Any = None) -> None:
            return None

    async def fake_start_child_workflow(
        workflow_name: str,
        payload: Any,
        **kwargs: Any,
    ) -> _FakeSessionHandle:
        order.append("start_session")
        session_starts.append((workflow_name, payload, kwargs))
        return _FakeSessionHandle()

    class _FakeManagerHandle:
        async def signal(self, _signal_name: str, _payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        if request_slot:
            order.append("request_slot")
            run._assigned_profile_id = execution_profile_ref or "codex-default"
            run.slot_assigned_event.set()
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not run for deferred session intent"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            order.append("adapter_start")
            session_adapter_requests.append(request)
            return AgentRunHandle(
                runId="managed-session-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Session-backed Codex step completed.", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeParentHandle(),
    )
    monkeypatch.setattr(
        agent_run_module.workflow,
        "start_child_workflow",
        fake_start_child_workflow,
    )
    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        agent_run_module,
        "CodexSessionAdapter",
        _FakeCodexSessionAdapter,
    )
    monkeypatch.setattr(
        run,
        "_ensure_manager_and_signal",
        fake_ensure_manager_and_signal,
    )
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId="corr-managed-deferred-1",
        idempotencyKey="idem-managed-deferred-1",
        instructionRef="artifact:instructions",
        parameters={
            "publishMode": "none",
            "metadata": {
                "moonmind": {
                    "deferManagedSessionUntilSlot": {
                        "runtimeId": "codex_cli",
                        "agentRunId": "wf-task-1",
                    }
                }
            },
        },
        workspaceSpec={},
    )

    result = await run.run(request)

    assert order[:3] == ["request_slot", "start_session", "adapter_start"]
    assert len(session_starts) == 1
    workflow_name, session_input, kwargs = session_starts[0]
    assert workflow_name == "MoonMind.AgentSession"
    assert kwargs["id"] == "wf-task-1:session:codex_cli"
    assert (
        kwargs["parent_close_policy"]
        == agent_run_module.workflow.ParentClosePolicy.ABANDON
    )
    assert session_input.agent_run_id == "wf-task-1"
    assert session_input.runtime_id == "codex_cli"
    assert session_adapter_requests[0].managed_session is not None
    assert session_adapter_requests[0].managed_session.session_id == (
        "sess:wf-task-1:codex_cli"
    )
    assert (
        "managed_session_bound",
        {
            "binding": session_adapter_requests[0].managed_session.model_dump(
                mode="json",
                by_alias=True,
            ),
            "child_workflow_id": "wf-agent-run-1",
            "runtime_id": "codex_cli",
        },
    ) in parent_signals
    assert result.summary == "Session-backed Codex step completed."

async def test_agent_run_managed_session_passes_publish_branch_context_to_fetch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-publish",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="completed",
            )

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError(
                "Terminal managed-session runs should fetch results through "
                "agent_runtime.fetch_result"
            )

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Managed publish success", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        if activity_name == "agent_runtime.load_session_snapshot":
            return {
                "binding": _managed_session_request().managed_session.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "status": "active",
                "containerId": None,
                "threadId": None,
                "activeTurnId": None,
                "terminationRequested": False,
            }
        if activity_name == "agent_runtime.prepare_turn_instructions":
            return "Prepared publish instructions"
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        agent_run_module,
        "CodexSessionAdapter",
        _FakeCodexSessionAdapter,
    )
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _managed_session_request(
            parameters={"publishMode": "pr"},
            workspace_spec={
                "startingBranch": "main",
                "targetBranch": "feature/recover-detached-head",
            },
        )
    )

    assert result.summary == "Managed publish success"
    fetch_payload = next(
        payload for name, payload in routed_calls if name == "agent_runtime.fetch_result"
    )
    assert isinstance(fetch_payload, AgentRuntimeFetchResultInput)
    assert fetch_payload.publish_mode == "pr"
    assert fetch_payload.target_branch == "main"
    assert fetch_payload.head_branch == "feature/recover-detached-head"

async def test_agent_run_managed_session_start_runtime_error_returns_failed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    manager_signals: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            raise RuntimeError(
                "codex app-server request thread/resume failed: "
                "{'code': -32600, 'message': 'no rollout found for thread id thread-1'}"
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            raise AssertionError("status should not be polled after start failure")

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError("fetch_result should not run after start failure")

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            manager_signals.append((signal_name, payload))

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        agent_run_module,
        "CodexSessionAdapter",
        _FakeCodexSessionAdapter,
    )
    monkeypatch.setattr(
        run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal
    )
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    result = await run.run(_managed_session_request())

    assert result.failure_class == "execution_error"
    assert "no rollout found for thread id" in result.summary
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"
    assert result.metadata["agentRunId"] == "wf-task-1"
    assert [name for name, _payload in routed_calls] == ["agent_runtime.publish_artifacts"]
    assert manager_signals[-1] == (
        "release_slot",
        {
            "requester_workflow_id": "wf-agent-run-1",
            "profile_id": "codex-default",
        },
    )

async def test_agent_run_managed_session_start_runtime_error_truncates_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            raise RuntimeError("managed start failed: " + ("x" * 5000))

        async def status(self, run_id: str) -> AgentRunStatus:
            raise AssertionError("status should not be polled after start failure")

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError("fetch_result should not run after start failure")

        async def cancel(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex",
                status="canceled",
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        agent_run_module,
        "CodexSessionAdapter",
        _FakeCodexSessionAdapter,
    )
    monkeypatch.setattr(
        run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal
    )
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    result = await run.run(_managed_session_request())

    assert result.failure_class == "execution_error"
    assert result.summary is not None
    assert len(result.summary) == _MAX_SUMMARY_CHARS
    assert result.summary.endswith("...")
    assert result.summary.startswith("managed start failed: ")

async def _run_default_profile_cooldown_retry_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sticky_patch_enabled: bool,
) -> tuple[
    AgentRunResult,
    list[str | None],
    list[dict[str, Any]],
    list[tuple[str, dict[str, Any]]],
]:
    run = MoonMindAgentRun()
    ensure_profile_refs: list[str | None] = []
    ensure_profile_selectors: list[dict[str, Any]] = []
    manager_signals: list[tuple[str, dict[str, Any]]] = []
    fetch_results = [
        AgentRunResult(
            failureClass="execution_error",
            providerErrorCode="provider_capacity",
            retryRecommendation="retry_after_cooldown",
            summary="provider capacity",
        ),
        AgentRunResult(summary="Recovered on pinned profile."),
    ]

    _configure_workflow_runtime(monkeypatch)
    if not sticky_patch_enabled:
        sticky_patch_id = (
            agent_run_module.STICKY_PINNED_PROFILE_COOLDOWN_RETRY_PATCH_ID
        )

        def patched(patch_id: str) -> bool:
            if patch_id == sticky_patch_id:
                return False
            return _patch_all_enabled(patch_id)

        monkeypatch.setattr(agent_run_module.workflow, "patched", patched)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId=f"managed-session-run-{len(ensure_profile_refs)}",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: dict[str, Any]) -> None:
            manager_signals.append((signal_name, payload))

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        if request_slot:
            ensure_profile_refs.append(execution_profile_ref)
            ensure_profile_selectors.append(dict(profile_selector))
            run.slot_assigned_event.set()
            run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        run._profile_snapshots = {
            "codex_openrouter_qwen36_plus": {
                "cooldown_after_429_seconds": 0,
                "enabled": True,
                "is_default": False,
            },
            "codex-default": {
                "cooldown_after_429_seconds": 0,
                "enabled": True,
                "is_default": True,
            },
        }
        return 2

    async def fake_fetch_managed_result(**_kwargs: Any) -> AgentRunResult:
        return fetch_results.pop(0)

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_fetch_managed_result", fake_fetch_managed_result)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    request = _managed_session_request().model_copy(
        update={"execution_profile_ref": None}
    )

    result = await run.run(request)

    return result, ensure_profile_refs, ensure_profile_selectors, manager_signals


async def test_agent_run_pins_default_profile_after_provider_cooldown_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        result,
        ensure_profile_refs,
        ensure_profile_selectors,
        manager_signals,
    ) = await _run_default_profile_cooldown_retry_case(
        monkeypatch,
        sticky_patch_enabled=True,
    )

    assert result.summary == "Recovered on pinned profile."
    assert ensure_profile_refs == ["codex-default", "codex-default"]
    assert ensure_profile_selectors == [
        {"tagsAny": [], "tagsAll": []},
        {"tagsAny": [], "tagsAll": []},
    ]
    assert manager_signals[:2] == [
        (
            "report_cooldown",
            {
                "profile_id": "codex-default",
                "cooldown_seconds": 0,
            },
        ),
        (
            "release_slot",
            {
                "requester_workflow_id": "wf-agent-run-1",
                "profile_id": "codex-default",
            },
        ),
    ]


async def test_agent_run_replays_legacy_default_fallback_retry_when_patch_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        result,
        ensure_profile_refs,
        ensure_profile_selectors,
        manager_signals,
    ) = await _run_default_profile_cooldown_retry_case(
        monkeypatch,
        sticky_patch_enabled=False,
    )

    assert result.summary == "Recovered on pinned profile."
    assert ensure_profile_refs == ["codex-default", None]
    assert ensure_profile_selectors == [
        {"tagsAny": [], "tagsAll": []},
        {"tagsAny": [], "tagsAll": [], "allowDefaultFallback": True},
    ]
    assert manager_signals[:2] == [
        (
            "report_cooldown",
            {
                "profile_id": "codex-default",
                "cooldown_seconds": 0,
            },
        ),
        (
            "release_slot",
            {
                "requester_workflow_id": "wf-agent-run-1",
                "profile_id": "codex-default",
            },
        ),
    ]


async def test_agent_run_preserves_operator_profile_selection_after_cooldown_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    ensure_profile_refs: list[str | None] = []
    fetch_results = [
        AgentRunResult(
            failureClass="execution_error",
            providerErrorCode="provider_capacity",
            retryRecommendation="retry_after_cooldown",
            summary="provider capacity",
        ),
        AgentRunResult(summary="Recovered on operator-selected profile."),
    ]

    _configure_workflow_runtime(monkeypatch)

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId=f"managed-session-run-{len(ensure_profile_refs)}",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: dict[str, Any]) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        if request_slot:
            ensure_profile_refs.append(execution_profile_ref)
            run.slot_assigned_event.set()
            run._assigned_profile_id = (
                execution_profile_ref or "codex_openrouter_qwen36_plus"
            )
            if len(ensure_profile_refs) == 1:
                run.update_runtime_selection(
                    {"executionProfileRef": "codex_openrouter_qwen36_plus"}
                )
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        run._profile_snapshots = {
            "codex_openrouter_qwen36_plus": {
                "cooldown_after_429_seconds": 0,
                "enabled": True,
                "is_default": False,
            },
            "codex-default": {
                "cooldown_after_429_seconds": 0,
                "enabled": True,
                "is_default": True,
            },
        }
        return 2

    async def fake_fetch_managed_result(**_kwargs: Any) -> AgentRunResult:
        return fetch_results.pop(0)

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(run, "_fetch_managed_result", fake_fetch_managed_result)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )

    result = await run.run(
        _managed_session_request().model_copy(update={"execution_profile_ref": None})
    )

    assert result.summary == "Recovered on operator-selected profile."
    assert ensure_profile_refs == [
        "codex-default",
        "codex_openrouter_qwen36_plus",
        "codex_openrouter_qwen36_plus",
    ]

async def test_agent_run_keeps_legacy_session_fetch_path_when_patch_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    def _patched(patch_id: str) -> bool:
        return (
            patch_id
            != agent_run_module.MANAGED_SESSION_FETCH_RESULT_ACTIVITY_PATCH_ID
        )

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-legacy",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def fetch_result(
            self,
            run_id: str,
            *,
            pr_resolver_expected: bool = False,
            pr_resolver_merge_gate_owned: bool = False,
        ) -> AgentRunResult:
            assert run_id == "managed-session-run-legacy"
            assert pr_resolver_expected is False
            assert pr_resolver_merge_gate_owned is False
            return AgentRunResult(summary="Legacy managed-session fetch path.")

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "patched", _patched)
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert result.summary == "Legacy managed-session fetch path."
    assert [name for name, _payload in routed_calls] == [
        "agent_runtime.publish_artifacts",
    ]

async def test_agent_run_keeps_legacy_instruction_preparation_for_pre_patch_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    _configure_workflow_runtime(monkeypatch)

    def _patched(patch_id: str) -> bool:
        return (
            patch_id
            != agent_run_module.MANAGED_SESSION_PREPARE_TURN_INSTRUCTIONS_ACTIVITY_PATCH_ID
        )

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["prepare_turn_instructions"] is None

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-legacy-prepare",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def fetch_result(
            self,
            run_id: str,
            *,
            pr_resolver_expected: bool = False,
            pr_resolver_merge_gate_owned: bool = False,
        ) -> AgentRunResult:
            assert run_id == "managed-session-run-legacy-prepare"
            assert pr_resolver_expected is False
            assert pr_resolver_merge_gate_owned is False
            return AgentRunResult(summary="Legacy instruction preparation path.")

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Legacy instruction preparation path.", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "patched", _patched)
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert result.summary == "Legacy instruction preparation path."

async def test_agent_run_preserves_pre_launch_instruction_order_for_pre_defer_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    _configure_workflow_runtime(monkeypatch)

    def _patched(patch_id: str) -> bool:
        return (
            patch_id
            != agent_run_module.MANAGED_SESSION_DEFER_TURN_INSTRUCTIONS_UNTIL_LAUNCH_PATCH_ID
        )

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError(
                "ManagedAgentAdapter should not be used for managedSession requests"
            )

    class _FakeCodexSessionAdapter:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["prepare_turn_instructions"] is not None
            assert kwargs["defer_turn_instructions_until_session_launch"] is False

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-pre-defer",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def fetch_result(
            self,
            run_id: str,
            *,
            pr_resolver_expected: bool = False,
            pr_resolver_merge_gate_owned: bool = False,
        ) -> AgentRunResult:
            assert run_id == "managed-session-run-pre-defer"
            assert pr_resolver_expected is False
            assert pr_resolver_merge_gate_owned is False
            return AgentRunResult(summary="Pre-defer instruction order path.")

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "codex-default"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Pre-defer instruction order path.", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "patched", _patched)
    monkeypatch.setattr(agent_run_module, "ManagedAgentAdapter", _FakeManagedAgentAdapter)
    monkeypatch.setattr(agent_run_module, "CodexSessionAdapter", _FakeCodexSessionAdapter)
    monkeypatch.setattr(run, "_ensure_manager_and_signal", fake_ensure_manager_and_signal)
    monkeypatch.setattr(run, "_sync_manager_profiles", fake_sync_manager_profiles)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_managed_session_request())

    assert result.summary == "Pre-defer instruction order path."
