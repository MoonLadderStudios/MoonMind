from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.schemas.managed_session_models import SendCodexManagedSessionTurnRequest
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentTerminalContract,
)
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.provider_profiles.lease_client import (
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
)
from moonmind.workflows.adapters.codex_session_adapter import _pr_resolver_terminal_contract
from moonmind.workflows.provider_failures import classify_provider_failure
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexManagedSessionRuntime,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence
from tests.integration.reliability.helpers import (
    FinalizationFaultInjector,
    NestedYieldProcess,
    load_replay,
)
from tests.helpers.codex_session_runtime import launch_request, write_fake_app_server
from tests.unit.workflows.adapters.test_codex_session_adapter import (
    _binding,
    _pr_resolver_request,
    _terminal_contract_test_adapter,
    _turn_response,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _finalize_and_capture_summary,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]


async def test_oauth_maintenance_lease_replays_through_activity_update_boundary() -> (
    None
):
    manifest = load_replay("oauth-maintenance-external-update", "manifest.json")
    expected = load_replay(
        "oauth-maintenance-external-update", "expected-outcome.json"
    )

    class Adapter:
        def __init__(self) -> None:
            self.update_name = ""
            self.payload: dict[str, object] = {}

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            return None

        async def update_workflow(self, _workflow_id, update_name, payload):
            self.update_name = update_name
            self.payload = payload
            return {
                "profile_id": manifest["profileId"],
                "lease_id": payload["owner_id"],
            }

    route = build_default_activity_catalog().resolve_activity(
        manifest["expectedActivityType"]
    )
    adapter = Adapter()
    lease = await ProviderProfileLeaseClient(adapter).acquire_maintenance_lease(
        runtime_id=manifest["runtimeId"],
        profile_id=manifest["profileId"],
        owner_id=manifest["leaseRequest"]["ownerId"],
        purpose=CredentialLeasePurpose(manifest["leaseRequest"]["purpose"]),
        metadata={"oauthSessionId": "oas-replay"},
        owner_is_workflow=True,
    )

    assert route.task_queue == manifest["expectedTaskQueue"]
    assert lease.lease_id == manifest["leaseRequest"]["ownerId"]
    assert adapter.update_name == expected["acknowledgedBy"]
    assert adapter.payload["metadata"]["ownerIsWorkflow"] is expected[
        "ownerIsWorkflow"
    ]


async def test_completed_batch_turn_without_fanout_evidence_fails() -> None:
    replay_id = "batch-workflows-missing-fanout-evidence"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    evaluation = evaluate_terminal_evidence(
        manifest["terminalContract"], workspace_path=manifest["workspacePath"]
    )
    assert manifest["agentTurn"]["status"] == "completed"
    assert manifest["postRecords"] == []
    assert evaluation.satisfied is False
    assert evaluation.failure_code == expected["failureCode"]
    assert expected["parentState"] == "failed"


async def test_completed_batch_turn_is_rejected_at_agent_run_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the escaped MM-1201 journey through AgentRun's authority handoff."""
    replay_id = "batch-workflows-missing-fanout-evidence"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    agent_run = MoonMindAgentRun()

    async def execute_activity(name: str, payload: dict, **kwargs: object) -> dict:
        assert name == "agent_runtime.evaluate_terminal_evidence"
        assert kwargs["task_queue"] == "mm.activity.agent_runtime"
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    # Patch only the Temporal SDK handoff. Keep AgentRun's production catalog
    # lookup and route construction in the replay so catalog drift cannot be
    # hidden by a test double.
    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="mm-1201",
        idempotencyKey="mm-1201:replay",
        workspaceSpec={"workspacePath": manifest["workspacePath"]},
        terminalContract=manifest["terminalContract"],
    )
    provider_result = AgentRunResult(
        summary=manifest["agentTurn"]["assistantText"],
        metadata={"workspacePath": manifest["workspacePath"]},
    )

    result = await agent_run._evaluate_terminal_contract(
        request=request, result=provider_result
    )

    assert result.failure_class == expected["failureClass"]
    assert result.metadata["failureCode"] == expected["failureCode"]
    assert result.metadata["terminalContractMissingEvidence"] == expected["missingEvidence"]
    assert result.metadata["terminalContractAuthority"] == "MoonMind.AgentRun"

    parent = MoonMindRunWorkflow()
    parent._owner_type = "user"
    parent._owner_id = "mm-1201-replay"
    diagnostic = parent._record_result_failure_diagnostic(
        stage="execute",
        category=result.failure_class,
        source="child_workflow",
        step_id="batch-workflows",
        step_title="batch-workflows",
        message=result.summary,
        child_workflow_id="agent-run-mm-1201",
        terminal_evidence=result.metadata,
    )
    from moonmind.workflows.temporal.workflows import run as run_workflow_module

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime(2026, 7, 11, tzinfo=timezone.utc),
    )
    summary = await _finalize_and_capture_summary(
        monkeypatch,
        parent,
        parameters={"publishMode": "none"},
        status="failed",
        error=diagnostic["message"],
    )

    assert summary["finishOutcome"]["code"] == "FAILED"
    assert summary["failure"]["failureCode"] == expected["failureCode"]
    assert summary["failure"]["terminalContractMissingEvidence"] == expected[
        "missingEvidence"
    ]


async def test_completed_batch_no_op_replays_through_production_activity_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay the endless WorkflowTaskFailed incident at its routing boundary."""
    manifest = load_replay("agent-run-terminal-evidence-routing", "manifest.json")
    workspace = tmp_path / "repo"
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True)
    targets_bytes = json.dumps(manifest["resolvedTargets"]).encode("utf-8")
    (artifacts / "batch-workflows-targets.json").write_bytes(targets_bytes)
    terminal_evidence = dict(manifest["terminalEvidence"])
    terminal_evidence["targetsSha256"] = hashlib.sha256(targets_bytes).hexdigest()
    (artifacts / "batch-workflows-result.json").write_text(
        json.dumps(terminal_evidence), encoding="utf-8"
    )

    async def execute_activity(name: str, payload: dict, **kwargs: object) -> dict:
        assert name == manifest["activityName"]
        assert kwargs["task_queue"] == manifest["expectedTaskQueue"]
        activities = TemporalAgentRuntimeActivities(client_adapter=object())
        evaluated = await activities.agent_runtime_evaluate_terminal_evidence(payload)
        return evaluated.model_dump(mode="json", by_alias=True)

    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=f"{manifest['incidentWorkflowId']}:replay",
        workspaceSpec={"workspacePath": str(workspace)},
        terminalContract=manifest["terminalContract"],
    )

    result = await MoonMindAgentRun()._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="No child workflows were queued.",
            metadata={"workspacePath": str(workspace)},
        ),
    )

    assert result.failure_class is None
    assert result.metadata["terminalContractId"] == "batch_workflows_fanout.v1"
    assert result.metadata["queuedChildCount"] == 0


def _materialize_workspace_fixture(replay_id: str, workspace: Path) -> None:
    manifest = load_replay(replay_id, "workspace-manifest.json")
    for item in manifest["artifacts"]:
        target = workspace / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(item["content"]), encoding="utf-8")


async def test_nested_yield_attempts_remain_non_terminal(tmp_path: Path) -> None:
    replay_id = "incomplete-terminal-contract"
    expected = load_replay(replay_id, "expected-outcome.json")
    process = NestedYieldProcess("inner-shell-3145")
    workspace = tmp_path / "repo"
    _materialize_workspace_fixture(replay_id, workspace)

    first_yield = process.first_tool_yield()
    wrapper_result = process.wrapper_completes()
    satisfied, missing, metadata = _pr_resolver_terminal_contract(str(workspace))

    assert first_yield == {"session_id": "inner-shell-3145", "status": "running"}
    assert wrapper_result["status"] == "completed"
    assert process.inner_active is True, "wrapper completion terminated inner process"
    assert satisfied is False, "attempt artifact incorrectly became terminal evidence"
    assert missing == expected["missingEvidence"]
    assert metadata["prResolverLatestAttempt"]["attemptCount"] == 2
    requests: list[SendCodexManagedSessionTurnRequest] = []

    async def send_turn(
        request: SendCodexManagedSessionTurnRequest,
    ) -> object:
        requests.append(request)
        return _turn_response(
            session_id=request.session_id,
            session_epoch=request.session_epoch,
            container_id=request.container_id,
            thread_id=request.thread_id,
            turn_id=f"turn-{len(requests)}",
        )

    binding = _binding()
    adapter = _terminal_contract_test_adapter(tmp_path, send_turn=send_turn)
    handle = await adapter.start(_pr_resolver_request(binding, workspace))

    # Provider adapters translate one runtime turn. AgentRun owns terminal
    # evidence evaluation and any capability-aware bounded continuation.
    assert handle.status == "completed"
    assert len(requests) == 1
    assert {
        (item.session_id, item.session_epoch, item.thread_id) for item in requests
    } == {(binding.session_id, binding.session_epoch, "thread-terminal-contract")}


@pytest.mark.parametrize("recover", [True, False], ids=["recovered", "exhausted"])
async def test_nested_yield_continuation_replays_through_production_agent_run_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    """MoonLadderStudios/MoonMind#3145 continuation journey across the agent route.

    AgentRun owns capability-aware bounded continuation. Replay the incident at
    the production activity-routing and terminal-evidence boundaries: every
    continuation activity must resolve through the real catalog to the managed
    agent-runtime queue, evaluate real workspace evidence, and preserve a stable
    session/thread/epoch identity across bounded continuation turns.
    """
    replay_id = "incomplete-terminal-contract"
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / "repo"
    _materialize_workspace_fixture(replay_id, workspace)

    # AgentRun evaluates terminal evidence and drives continuation outside a live
    # Temporal event loop here; enable the continuation patch gates and provide a
    # workflow info stub so the production helper runs in-process.
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="reliability-3145:agent:node-1",
        run_id="reliability-3145-run",
        search_attributes={},
        parent=None,
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch: True)

    binding = _binding()
    execution_ref = "mm:reliability-3145:terminal-contract"
    container_id = "ctr-reliability-3145"
    thread_id = "thread-terminal-contract"
    request = _pr_resolver_request(binding, workspace).model_copy(
        update={
            "terminal_contract": AgentTerminalContract(
                contractId="pr_resolver_terminal.v1",
                relativePath="var/pr_resolver/result.json",
                expectedSchemaVersion="moonmind.pr-resolver-result.v1",
                executionRef=execution_ref,
            )
        }
    )

    turns: list[SendCodexManagedSessionTurnRequest] = []
    routed_queues: set[str] = set()

    async def execute_activity(name: str, payload: object, **kwargs: object) -> object:
        # Keep AgentRun's production catalog lookup and route construction in the
        # replay; only the Temporal SDK handoff is doubled, so catalog drift that
        # sent continuation activities to the wrong worker cannot be hidden.
        routed_queues.add(kwargs["task_queue"])
        if name == "agent_runtime.evaluate_terminal_evidence":
            activities = TemporalAgentRuntimeActivities(client_adapter=object())
            evaluated = await activities.agent_runtime_evaluate_terminal_evidence(
                payload
            )
            return evaluated.model_dump(mode="json", by_alias=True)
        if name == "agent_runtime.load_session_snapshot":
            return {
                "sessionId": binding.session_id,
                "sessionEpoch": binding.session_epoch,
                "containerId": container_id,
                "threadId": thread_id,
            }
        if name == "agent_runtime.send_turn":
            turns.append(payload)
            if recover and len(turns) == 1:
                # A recovered continuation writes satisfied terminal evidence:
                # a merged disposition whose executionRef matches the contract,
                # plus the required auto-publish artifact.
                result_path = workspace / "var/pr_resolver/result.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(
                    json.dumps(
                        {
                            "executionRef": execution_ref,
                            "mergeAutomationDisposition": "merged",
                        }
                    ),
                    encoding="utf-8",
                )
                publish_path = workspace / "artifacts/publish_result.json"
                publish_path.parent.mkdir(parents=True, exist_ok=True)
                publish_path.write_text(
                    json.dumps({"status": "merged"}), encoding="utf-8"
                )
            return _turn_response(
                session_id=payload.session_id,
                session_epoch=payload.session_epoch,
                container_id=payload.container_id,
                thread_id=payload.thread_id,
                turn_id=f"continuation-{len(turns)}",
            ).model_dump(mode="json", by_alias=True)
        if name == "agent_runtime.fetch_result":
            return AgentRunResult(
                summary="continuation completed",
                metadata={"workspacePath": str(workspace)},
            ).model_dump(mode="json", by_alias=True)
        raise AssertionError(f"unexpected activity: {name}")

    monkeypatch.setattr(agent_run_module, "execute_typed_activity", execute_activity)

    result = await MoonMindAgentRun()._evaluate_terminal_contract(
        request=request,
        result=AgentRunResult(
            summary="wrapper completed while inner process remained active",
            metadata={"workspacePath": str(workspace)},
        ),
    )

    # Every terminal-contract continuation activity crosses the production
    # managed agent-runtime route, never a per-test task queue.
    assert routed_queues == {"mm.activity.agent_runtime"}
    expected_turns = 1 if recover else expected["continuationCount"]
    assert len(turns) == expected_turns
    assert {
        (turn.session_id, turn.session_epoch, turn.thread_id) for turn in turns
    } == {(binding.session_id, binding.session_epoch, thread_id)}
    assert result.metadata["terminalContractContinuationCount"] == expected_turns
    if recover:
        assert result.failure_class is None
        assert result.metadata["terminalContractRecoveryOutcome"] == "recovered"
    else:
        assert result.failure_class == expected["failureClass"]
        assert result.metadata["failureCode"] == expected["failureCode"]
        assert result.metadata["terminalContractRecoveryOutcome"] == "incomplete"


async def test_sandbox_checkpoint_rejects_managed_workspace_without_resolving_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    expected = load_replay(replay_id, "expected-outcome.json")
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "sandbox-root")

    sandbox_calls = 0

    def forbidden_sandbox_resolver(*_args: object, **_kwargs: object) -> Path:
        nonlocal sandbox_calls
        sandbox_calls += 1
        raise AssertionError("managed workspace reached sandbox resolver")

    monkeypatch.setattr(activities, "_resolve_workspace", forbidden_sandbox_resolver)
    payload = {
        "identity": {
            "workflowId": "wf-reliability-3145",
            "runId": "run-3145",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "workspaceLocator": {
            "kind": "managed_runtime",
            "runtimeId": "codex",
            "agentRunId": "run-3145",
        },
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "reliability-3145-after-execution",
    }
    with pytest.raises(WorkspaceLocatorResolutionError) as exc_info:
        await activities.workspace_capture_checkpoint(payload)

    assert exc_info.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert sandbox_calls == expected["sandboxResolverCalls"] == 0


async def test_codex_oauth_failure_preserves_primary_error_and_managed_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:28dae38e through the child-to-parent authority handoff."""
    replay_id = "codex-oauth-checkpoint-masking"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    classified = classify_provider_failure(manifest["providerLog"])

    assert classified is not None
    assert classified.failure_class == expected["failureClass"]
    assert classified.provider_error_code == expected["providerErrorCode"]
    assert classified.retry_recommendation == expected["retryRecommendation"]

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=f"{manifest['incidentWorkflowId']}:agent:node-1",
        run_id="replay-run",
        search_attributes={},
        parent=None,
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch: True)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="codex-default",
        correlationId=manifest["incidentWorkflowId"],
        idempotencyKey=f"{manifest['incidentWorkflowId']}:replay",
        managedSession={
            "workflowId": f"{manifest['incidentWorkflowId']}:session:codex_cli",
            "agentRunId": manifest["incidentWorkflowId"],
            "sessionId": f"sess:{manifest['incidentWorkflowId']}:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        },
    )
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=request,
        result=AgentRunResult(
            summary=manifest["providerLog"],
            failureClass=classified.failure_class,
            providerErrorCode=classified.provider_error_code,
            retryRecommendation=classified.retry_recommendation,
            metadata={"workspacePath": manifest["legacyWorkspacePath"]},
        ),
    )

    assert result is not None
    assert result.metadata["workspaceLocator"] == expected["workspaceLocator"]
    assert "workspacePath" not in result.metadata

    parent_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id="replay-parent-run",
        task_queue="mm.workflow",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: parent_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    async def unexpected_activity(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("managed workspace must not reach a sandbox activity")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        unexpected_activity,
    )
    parent = MoonMindRunWorkflow()
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Replay"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    parent._mark_step_running("node-1", updated_at=now, summary="Running")
    parent._record_step_workspace_capture_input("node-1", result.metadata)

    checkpoint_ref = await parent._record_canonical_step_checkpoint(
        "node-1", boundary="after_execution", updated_at=now
    )

    assert checkpoint_ref is None
    assert parent._step_checkpoint_capture_outcomes["node-1"] == (
        expected["checkpointOutcome"]
    )


async def test_codex_system_error_waits_for_delayed_oauth_failure_log(
    tmp_path: Path,
) -> None:
    """Replay mm:32a5549d through the real managed-session runtime boundary."""

    replay_id = "codex-oauth-log-settle-race"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = launch_request(tmp_path)
    transcript_path = Path(request.codex_home_path) / manifest["rolloutRelativePath"]
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        omit_turns_on_read=True,
        thread_status_type=manifest["threadStatusType"],
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[manifest["terminalRolloutEvent"]],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    log_path = Path(request.codex_home_path) / "logs_1.sqlite"
    with sqlite3.connect(log_path) as connection:
        connection.execute(
            "CREATE TABLE logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER, "
            "feedback_log_body TEXT"
            ")"
        )

    def commit_provider_failure_after_terminal_event() -> None:
        time.sleep(manifest["providerLogDelaySeconds"])
        with sqlite3.connect(log_path) as connection:
            connection.execute(
                "INSERT INTO logs (ts, feedback_log_body) VALUES (?, ?)",
                (int(time.time()), manifest["providerLog"]),
            )

    writer = threading.Thread(
        target=commit_provider_failure_after_terminal_event,
        daemon=True,
    )
    writer.start()
    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert response.status == "failed"
    assert response.metadata["failureClass"] == expected["runtimeFailureClass"]
    assert response.metadata["reason"] == expected["reason"]
    assert "retryRecommendedAction" not in response.metadata
    classified = classify_provider_failure(response.metadata["reason"])
    assert classified is not None
    assert classified.failure_class == expected["failureClass"]
    assert classified.provider_error_code == expected["providerErrorCode"]
    assert classified.retry_recommendation == expected["retryRecommendation"]


async def test_checkpoint_finalization_fault_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_id = "managed-workspace-checkpoint-routing"
    workspace_root = tmp_path / "workspaces"
    repo = workspace_root / "temporal_sandbox" / "run-3145" / "repo"
    repo.mkdir(parents=True)
    activities = TemporalSandboxActivities(workspace_root=workspace_root)

    agent_execution_calls = 0
    durable_execution_result: dict[str, object] | None = None

    async def execute_agent_once() -> dict[str, object]:
        """Primary agent execution is exactly-once and durable across retries."""
        nonlocal agent_execution_calls, durable_execution_result
        if durable_execution_result is None:
            agent_execution_calls += 1
            durable_execution_result = load_replay(replay_id, "execution-result.json")
        return durable_execution_result

    original_capture = activities._capture_workspace_evidence
    fault = FinalizationFaultInjector()

    async def fail_once(model: object, workspace: Path):
        # The retried finalization path must reuse the durable primary execution
        # result, never re-run the agent. If a regression re-executed the agent on
        # each finalization attempt, ``agent_execution_calls`` would exceed one.
        execution = await execute_agent_once()
        assert execution["status"] == "completed"
        return await fault.invoke(original_capture, model, workspace)

    monkeypatch.setattr(activities, "_capture_workspace_evidence", fail_once)
    payload = {
        "identity": {
            "workflowId": "wf-reliability-3145",
            "runId": "run-3145",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "workspacePath": str(repo),
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "reliability-3145-after-execution",
    }
    first = await activities.workspace_capture_checkpoint(payload)
    assert first["status"] == "invalid"
    assert durable_execution_result["status"] == "completed"
    second = await activities.workspace_capture_checkpoint(payload)
    assert second["status"] == "captured"
    assert durable_execution_result["status"] == "completed"
    assert fault.calls == 2
    assert agent_execution_calls == 1
