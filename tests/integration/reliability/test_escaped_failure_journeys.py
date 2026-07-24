from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import subprocess
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
    AgentRuntimeStepExecutionLaunch,
    AgentTerminalContract,
    ManagedRunRecord,
)
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.provider_profiles.lease_client import (
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
)
from moonmind.workflows.adapters.codex_session_adapter import (
    CodexSessionAdapter,
    _pr_resolver_terminal_contract,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.provider_failures import classify_provider_failure
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexManagedSessionRuntime,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
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
    pytest.mark.reliability_journey,
]


async def test_runtime_switch_rebinds_managed_session_authority_before_activity() -> (
    None
):
    """Replay mm:d3ca1354 at the AgentRun-to-Activity request boundary."""
    replay_id = "managed-session-runtime-switch"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    request = AgentExecutionRequest.model_validate(manifest["request"])
    agent_run = MoonMindAgentRun()

    agent_run._apply_runtime_selection_update(
        request,
        manifest["runtimeUpdate"],
    )
    agent_run._synchronize_runtime_selection_authority(request)
    activity_payload = request.model_dump(mode="json", by_alias=True)

    assert activity_payload["agentId"] == expected["agentId"]
    assert activity_payload["executionProfileRef"] == expected[
        "executionProfileRef"
    ]
    assert activity_payload["managedSession"] is expected["managedSession"]
    assert activity_payload["stepExecution"]["runtimeSessionReset"] is expected[
        "runtimeSessionReset"
    ]
    assert activity_payload["stepExecution"]["runtimeSelection"] == expected[
        "runtimeSelection"
    ]
    AgentExecutionRequest.model_validate(activity_payload)


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


async def test_verified_remediation_push_reaches_draft_publication_handoff() -> None:
    """Replay the two orphaned branches through the production authority seam."""
    replay_id = "draft-publication-authority-gap"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    for case in manifest["cases"]:
        workflow_run = MoonMindRunWorkflow()
        raw_result = {"outputs": case["pushResult"]}
        assert workflow_run._publication_feasibility(raw_result)["reason"] == (
            "publication_state_ambiguous"
        )

        accepted = TemporalAgentRuntimeActivities._accepted_repository_evidence(
            case["pushResult"]
        )
        assert accepted is not None
        assert accepted["schemaVersion"] == expected[
            "acceptedEvidenceSchemaVersion"
        ]
        assert accepted["authority"] == expected["acceptedEvidenceAuthority"]

        feasibility = workflow_run._publication_feasibility(
            {"outputs": {"acceptedRepositoryEvidence": accepted}}
        )
        assert feasibility["feasible"] is expected["publicationFeasible"]
        assert feasibility["reason"] == expected[
            "publicationFeasibilityReason"
        ]
        assert workflow_run._terminal_gate_handoff_kind(
            publish_mode=manifest["publishMode"],
            draft_publication_policy=manifest["draftPublicationPolicy"],
            publication_feasible=feasibility["feasible"],
        ) == expected["terminalHandoff"]


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


async def test_invalid_batch_range_records_terminal_failure_without_retry(
    tmp_path: Path,
) -> None:
    """Replay mm:3df0b867 through the terminal and parent retry boundaries."""
    manifest = load_replay("batch-workflows-invalid-range", "manifest.json")
    expected = load_replay(
        "batch-workflows-invalid-range", "expected-outcome.json"
    )
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(manifest["terminalEvidence"]), encoding="utf-8"
    )

    result = await TemporalAgentRuntimeActivities().agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(workspace),
            "artifactSpoolPath": str(spool),
            "terminalContract": manifest["terminalContract"],
            "result": {"summary": "Batch range validation failed."},
        }
    )

    assert result.failure_class == expected["failureClass"]
    assert result.provider_error_code == expected["failureCode"]
    assert result.metadata["terminalContractMissingEvidence"] == expected[
        "missingEvidence"
    ]

    parent = MoonMindRunWorkflow()
    retryable = parent._activity_result_retryable(
        {"outputs": result.model_dump(mode="json", by_alias=True)},
        failure_message="execution_error",
        tool_type="agent_runtime",
    )
    assert retryable is expected["retryable"]
    assert expected["parentState"] == "failed"


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


async def test_retry_batch_artifacts_in_spool_replay_as_terminal_success(
    tmp_path: Path,
) -> None:
    """Replay the false-negative fan-out through the production activity boundary."""

    replay_id = "batch-workflows-spool-retry-identity"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    targets_bytes = json.dumps(manifest["resolvedTargets"]).encode("utf-8")
    (spool / "batch-workflows-targets.json").write_bytes(targets_bytes)
    terminal_evidence = dict(manifest["terminalEvidence"])
    terminal_evidence["targetsSha256"] = hashlib.sha256(targets_bytes).hexdigest()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(terminal_evidence),
        encoding="utf-8",
    )

    result = (
        await TemporalAgentRuntimeActivities().agent_runtime_evaluate_terminal_evidence(
            {
                "workspacePath": str(workspace),
                "artifactSpoolPath": str(spool),
                "terminalContract": manifest["terminalContract"],
                "result": {"summary": "Queued both child workflows."},
            }
        )
    )

    assert not (workspace / "artifacts").exists()
    assert result.failure_class is expected["failureClass"]
    assert result.metadata["queuedChildCount"] == expected["queuedChildCount"]
    assert (
        result.metadata["terminalContractExecutionRef"] == expected["executionRef"]
    )
    assert expected["parentState"] == "completed"


async def test_successful_batch_without_publication_skips_prepublication_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the false-failure incident through the finalization boundary."""

    replay_id = "batch-fanout-no-publish-checkpoint"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 7, 14, 7, 14, tzinfo=timezone.utc)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": manifest["logicalStepId"],
                "inputs": {"title": "Queue GitHub issue workflows"},
            }
        ],
        dependency_map={manifest["logicalStepId"]: []},
        updated_at=now,
    )
    row = workflow._step_ledger_row_for(manifest["logicalStepId"])
    assert row is not None
    row["executionOutcome"] = manifest["executionOutcome"]
    workflow._publish_status = "not_required"
    workflow._publish_reason = (
        f"queued {manifest['queuedChildCount']} child workflows"
    )
    checkpoint_calls: list[str] = []

    async def checkpoint(
        _logical_step_id: str,
        *,
        boundary: str,
        updated_at: datetime,
    ) -> str:
        checkpoint_calls.append(boundary)
        raise AssertionError("publishMode none has no pre-publication boundary")

    monkeypatch.setattr(
        workflow,
        "_record_canonical_step_checkpoint",
        checkpoint,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _id: True)

    checkpoint_failed = await workflow._record_prepublication_checkpoint(
        manifest["logicalStepId"],
        publish_mode=manifest["publishMode"],
        updated_at=now,
    )
    completion = workflow._determine_publish_completion(
        parameters={"publishMode": manifest["publishMode"]}
    )

    assert checkpoint_failed is False
    assert len(checkpoint_calls) == expected["prepublicationCheckpointCalls"]
    assert completion[0] == expected["completionStatus"]
    assert completion[2] is False
    assert workflow._attention_required is expected["attentionRequired"]
    assert expected["parentState"] == "completed"


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


async def test_managed_checkpoint_waits_for_authoritative_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:8a09888d before the AgentRun workspace exists."""
    replay_id = "managed-checkpoint-missing-locator"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    parent_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["runId"],
        task_queue="mm.workflow.merge_automation",
        search_attributes={},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: parent_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)

    activity_calls: list[str] = []

    async def capture_activity(
        activity_type: str,
        _payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        activity_calls.append(activity_type)
        raise AssertionError("managed capture ran without an authoritative locator")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        capture_activity,
    )
    now = datetime(2026, 7, 14, 5, 25, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": "node-1", "inputs": {"title": "Investigate"}}],
        dependency_map={"node-1": []},
        updated_at=now,
    )
    parent._mark_step_running("node-1", updated_at=now, summary="Investigating")
    parent._record_step_workspace_capture_input("node-1", manifest["stepInputs"])

    checkpoint_ref = await parent._record_canonical_step_checkpoint(
        "node-1",
        boundary=manifest["boundary"],
        updated_at=now,
    )

    assert checkpoint_ref is None
    assert activity_calls == expected["activityCalls"]
    assert parent._step_checkpoint_capture_outcomes["node-1"] == expected[
        "captureOutcome"
    ]


async def test_codex_session_record_uses_step_workflow_checkpoint_authority(
    tmp_path: Path,
) -> None:
    """Replay mm:5fe90658 through adapter persistence and checkpoint capture."""
    replay_id = "codex-session-checkpoint-identity"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    step_execution = AgentRuntimeStepExecutionLaunch.model_validate(
        manifest["stepExecution"]
    )

    run_root = tmp_path / manifest["incidentWorkflowId"]
    workspace = run_root / "repo"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.name", "MoonMind Test"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=workspace,
        check=True,
    )
    (workspace / "result.txt").write_text("agent completed\n", encoding="utf-8")
    subprocess.run(["git", "add", "result.txt"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "agent result"], cwd=workspace, check=True
    )

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    adapter = CodexSessionAdapter.__new__(CodexSessionAdapter)
    adapter._run_store = run_store
    adapter._runtime_id = manifest["runtime"]
    adapter._workflow_id = manifest["agentRunWorkflowId"]
    adapter._task_workflow_id = manifest["incidentWorkflowId"]
    now = datetime.now(timezone.utc)
    adapter._persist_managed_run_record(
        run_id="session-turn-1",
        agent_id=manifest["runtime"],
        managed_run_id=manifest["incidentWorkflowId"],
        binding=_binding(),
        workspace_path=str(workspace),
        locator={
            "sessionId": f"sess:{manifest['incidentWorkflowId']}:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-replay",
            "threadId": "thread-replay",
        },
        active_turn_id=None,
        result={"summary": "completed"},
        status="completed",
        started_at=now,
        finished_at=now,
        step_execution=step_execution,
    )

    record = run_store.load(manifest["incidentWorkflowId"])
    assert record is not None
    assert record.workflow_id == expected["persistedWorkflowId"]
    assert record.session_id is not None

    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
        artifact_service=object(),
        client_adapter=object(),
    )

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        return f"artifact://{kind}/{hashlib.sha256(payload).hexdigest()}"

    activities._put_managed_checkpoint_artifact = put
    capabilities = resolve_runtime_execution_capabilities(manifest["runtime"])
    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": step_execution.workflow_id,
                "runId": step_execution.run_id,
                "logicalStepId": step_execution.logical_step_id,
                "executionOrdinal": step_execution.execution_ordinal,
            },
            "boundary": "after_execution",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": manifest["workspaceLocator"],
            "expectedRuntimeId": manifest["runtime"],
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "step-checkpoints/node-1",
            "idempotencyKey": f"{step_execution.step_execution_id}:checkpoint",
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )

    assert capture["status"] == expected["checkpointStatus"]
    assert capture["workspace"]["kind"] == "worktree_archive"


async def test_resolved_pr_resolver_contract_owns_durable_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay resolver PR 2189's missing terminal-contract launch payload."""

    replay_id = "pr-resolver-resolved-terminal-contract"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent_info = SimpleNamespace(
        workflow_id=manifest["parentWorkflowId"],
        run_id=manifest["parentRunId"],
    )
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id=manifest["incidentWorkflowId"],
        run_id=manifest["incidentRunId"],
        parent=parent_info,
        search_attributes={},
    )

    async def resolve_skillset(*_args: object, **_kwargs: object) -> object:
        return manifest["resolvedSkillSet"]

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        resolve_skillset,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch: True,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: workflow_info,
    )
    parent = MoonMindRunWorkflow()
    parent._owner_id = "owner-replay"
    resolved_ref = await parent._resolve_agent_node_skillset_ref(
        task_skills=None,
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        existing_skillset_ref=None,
    )
    request = parent._build_agent_execution_request(
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        tool_name=manifest["planNodeInputs"]["targetRuntime"],
        resolved_skillset_ref=resolved_ref,
        workflow_parameters={"mergeGate": manifest["mergeGate"]},
    )

    assert request.terminal_contract is not None
    assert request.terminal_contract.contract_id == expected["terminalContractId"]
    assert (
        request.terminal_contract.relative_path
        == expected["terminalContractPath"]
    )
    assert (
        request.terminal_contract.expected_schema_version
        == expected["terminalContractSchemaVersion"]
    )
    assert request.terminal_continuation_authority is not None
    assert (
        request.terminal_continuation_authority.owner_workflow_type
        == expected["continuationOwnerWorkflowType"]
    )
    assert expected["continuationAction"] in (
        request.terminal_continuation_authority.allowed_actions
    )

    async def read_existing_skillset(*_args: object, **_kwargs: object) -> object:
        return manifest["resolvedSkillSet"]

    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        read_existing_skillset,
    )
    existing_parent = MoonMindRunWorkflow()
    existing_parent._owner_id = "owner-replay-existing"
    existing_ref = await existing_parent._resolve_agent_node_skillset_ref(
        task_skills=None,
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        existing_skillset_ref=manifest["existingResolvedSkillsetRef"],
    )
    existing_request = existing_parent._build_agent_execution_request(
        node_inputs=manifest["planNodeInputs"],
        node_id=manifest["logicalStepId"],
        tool_name=manifest["planNodeInputs"]["targetRuntime"],
        resolved_skillset_ref=existing_ref,
        workflow_parameters={"mergeGate": manifest["mergeGate"]},
    )

    assert existing_ref == manifest["existingResolvedSkillsetRef"]
    assert existing_request.terminal_contract is not None
    assert (
        existing_request.terminal_contract.contract_id
        == expected["terminalContractId"]
    )
    assert existing_request.terminal_continuation_authority is not None
    assert (
        existing_request.terminal_continuation_authority.owner_workflow_type
        == expected["continuationOwnerWorkflowType"]
    )


async def test_retry_before_execution_captures_terminal_prior_workspace(
    tmp_path: Path,
) -> None:
    """Replay resolver PR 2189's retry checkpoint authority handoff."""

    replay_id = "managed-checkpoint-retry-baseline"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / manifest["incidentWorkflowId"] / "repo"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "resolver-result.txt").write_text(
        "first execution requested durable gate continuation\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=MoonMind Test", "-c",
            "user.email=test@example.invalid", "commit", "-qm",
            "checkpoint replay",
        ],
        cwd=workspace,
        check=True,
    )

    now = datetime.now(timezone.utc)
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId=manifest["incidentWorkflowId"],
            workflowId=f"{manifest['incidentWorkflowId']}:agent:node-1",
            ownerRunId=manifest["incidentRunId"],
            logicalStepId=manifest["logicalStepId"],
            executionOrdinal=manifest["completedExecutionOrdinal"],
            agentId=manifest["runtime"],
            runtimeId=manifest["runtime"],
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(workspace),
            sessionId=f"sess:{manifest['incidentWorkflowId']}:codex_cli",
            sessionEpoch=1,
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
        artifact_service=object(),
        client_adapter=object(),
    )

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        return f"artifact://{kind}/{hashlib.sha256(payload).hexdigest()}"

    activities._put_managed_checkpoint_artifact = put
    capabilities = resolve_runtime_execution_capabilities(manifest["runtime"])
    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": manifest["incidentWorkflowId"],
                "runId": manifest["incidentRunId"],
                "logicalStepId": manifest["logicalStepId"],
                "executionOrdinal": manifest["retryExecutionOrdinal"],
            },
            "boundary": manifest["checkpointBoundary"],
            "checkpointKind": "worktree_archive",
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": manifest["runtime"],
                "agentRunId": manifest["incidentWorkflowId"],
                "relativePath": "repo",
            },
            "expectedRuntimeId": manifest["runtime"],
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "step-checkpoints/node-1",
            "idempotencyKey": (
                f"{manifest['incidentWorkflowId']}:{manifest['incidentRunId']}:"
                f"{manifest['logicalStepId']}:execution:"
                f"{manifest['retryExecutionOrdinal']}:checkpoint:"
                "before_execution:capture"
            ),
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )

    assert capture["status"] == expected["checkpointStatus"]
    assert capture["workspace"]["kind"] == expected["checkpointKind"]


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

    activity_calls: list[str] = []

    async def managed_checkpoint_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        activity_calls.append(activity_type)
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return {
                "status": "captured",
                "workspace": {
                    "kind": "worktree_archive",
                    "baseCommit": "abc123",
                    "archiveRef": "artifact://managed/archive",
                    "archiveDigest": "sha256:" + ("a" * 64),
                    "manifestRef": "artifact://managed/manifest",
                    "manifestDigest": "sha256:" + ("b" * 64),
                    "includesUntracked": True,
                    "includesIgnoredFiles": False,
                },
                "diagnosticRefs": ["artifact://managed/manifest"],
                "idempotencyKey": payload["idempotencyKey"],
            }
        if activity_type == "step_checkpoint.create":
            return {
                "checkpointRef": "artifact://checkpoint/after_execution",
                "checkpointId": payload["idempotencyKey"],
            }
        raise AssertionError(f"unexpected activity: {activity_type}")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        managed_checkpoint_activity,
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

    assert checkpoint_ref == "artifact://checkpoint/after_execution"
    assert activity_calls == [
        "agent_runtime.capture_workspace_checkpoint",
        "step_checkpoint.create",
    ]


async def test_checkpoint_capture_heartbeat_backpressure_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:c2723c5c through the managed checkpoint activity boundary."""

    replay_id = "checkpoint-heartbeat-backpressure"
    manifest = load_replay(replay_id, "manifest.json")
    workspace_manifest = load_replay(replay_id, "workspace-manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    workspace = tmp_path / manifest["incidentWorkflowId"] / "repo"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    for artifact in workspace_manifest["artifacts"]:
        (workspace / artifact["path"]).write_text(
            artifact["content"], encoding="utf-8"
        )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=MoonMind Test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "checkpoint replay",
        ],
        cwd=workspace,
        check=True,
    )

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    now = datetime.now(timezone.utc)
    run_store.save(
        ManagedRunRecord(
            runId=manifest["incidentWorkflowId"],
            workflowId=manifest["incidentWorkflowId"],
            ownerRunId=manifest["incidentRunId"],
            logicalStepId=manifest["logicalStepId"],
            executionOrdinal=1,
            agentId=manifest["runtime"],
            runtimeId=manifest["runtime"],
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(workspace),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=run_store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        return f"artifact://{kind}/{hashlib.sha256(payload).hexdigest()}"

    activities._put_managed_checkpoint_artifact = put
    heartbeat_queue: asyncio.Queue[object] = asyncio.Queue(
        maxsize=manifest["heartbeatQueueCapacity"]
    )
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity, "in_activity", lambda: True
    )
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: heartbeat_queue.put_nowait(payload),
    )
    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        manifest["heartbeatIntervalSeconds"],
    )
    capabilities = resolve_runtime_execution_capabilities(manifest["runtime"])

    capture = await activities.agent_runtime_capture_workspace_checkpoint(
        {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": manifest["incidentWorkflowId"],
                "runId": manifest["incidentRunId"],
                "logicalStepId": manifest["logicalStepId"],
                "executionOrdinal": 1,
            },
            "boundary": "before_publication",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": manifest["runtime"],
                "agentRunId": manifest["incidentWorkflowId"],
                "relativePath": "repo",
            },
            "expectedRuntimeId": manifest["runtime"],
            "capabilitySetVersion": capabilities.capability_set_version,
            "capabilityDigest": capabilities.capability_digest,
            "artifactNamespace": "step-checkpoints/assessment",
            "idempotencyKey": "checkpoint-heartbeat-backpressure:capture",
            "capturePolicy": {
                "includeTracked": True,
                "includeUntracked": True,
                "includeIgnored": False,
                "redactionProfile": "managed-code-workspace-v1",
            },
        }
    )

    assert capture["status"] == expected["checkpointStatus"]
    assert heartbeat_queue.qsize() <= expected["maxQueuedHeartbeats"]


async def test_checkpoint_multipart_failure_replay_preserves_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay mm:2ca7d450 without allowing a transient summary to win."""

    replay_id = "checkpoint-multipart-finalization-summary"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    parent = MoonMindRunWorkflow()
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    parent._initialize_step_ledger(
        ordered_nodes=[{"id": manifest["logicalStepId"], "inputs": {}}],
        dependency_map={manifest["logicalStepId"]: []},
        updated_at=now,
    )
    row = parent._step_ledger_row_for(manifest["logicalStepId"])
    assert row is not None
    row["finalizationOutcome"] = manifest["finalizationOutcome"]
    parent._publish_status = manifest["publishStatus"]
    parent._publish_reason = manifest["publishReason"]
    parent._summary = manifest["transientSummary"]

    status, message, publish_failure = parent._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == expected["status"]
    assert message == expected["message"]
    assert publish_failure is expected["publishFailure"]
    assert message != expected["forbiddenSummary"]

    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: now)
    summary = await _finalize_and_capture_summary(
        monkeypatch,
        parent,
        parameters={"publishMode": "pr"},
        status=status,
        error=message,
    )
    assert summary["finishOutcome"]["reason"] == expected["message"]
    assert summary["publish"]["reason"] == expected["publishReason"]


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
