from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.schemas.managed_session_models import SendCodexManagedSessionTurnRequest
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
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
from tests.integration.reliability.helpers import NestedYieldProcess, load_replay
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

    durable_execution_result = load_replay(replay_id, "execution-result.json")
    original_capture = activities._capture_workspace_evidence
    calls = 0

    async def fail_once(model: object, workspace: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected finalization failure")
        return await original_capture(model, workspace)

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
    assert calls == 2


async def test_source_destroying_cold_resume_restores_durable_workspace_once(
    tmp_path: Path,
) -> None:
    """Cold-resume replay: restoration cannot observe the source workspace."""
    replay = load_replay("cold-resume-worktree-archive", "manifest.json")
    workspace_root = tmp_path / "workspaces"
    source = workspace_root / "temporal_sandbox" / "source-run" / "repo"
    destination = workspace_root / "temporal_sandbox" / "destination-run" / "repo"
    source.mkdir(parents=True)

    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "replay@moonmind.local"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "MoonMind Replay"], cwd=source, check=True)
    (source / "tracked.txt").write_text("accepted prepare\n", encoding="utf-8")
    (source / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=source, check=True)
    base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()

    # Uncommitted working-tree state that exists *before* the implement step
    # runs. A restore_pre_execution retry must rewind the workspace to exactly
    # this snapshot so the failed step reruns from its clean starting point, not
    # from the failed attempt's dirty output.
    (source / "tracked.txt").write_text("prepare stage working copy\n", encoding="utf-8")
    (source / "untracked file.txt").write_text("untracked\n", encoding="utf-8")
    (source / "binary.bin").write_bytes(bytes(range(256)))
    executable = source / "scripts" / "run me.sh"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    (source / "nested" / "unicode").mkdir(parents=True)
    (source / "nested" / "unicode" / "café.txt").write_text("moon\n", encoding="utf-8")
    os.symlink("tracked.txt", source / "safe-link")
    (source / "deleted.txt").unlink()
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "excluded.pyc").write_bytes(b"cache")
    provider_home = tmp_path / "provider-home"
    provider_home.mkdir()
    (provider_home / "credentials.json").write_text("excluded", encoding="utf-8")

    activities = TemporalSandboxActivities(workspace_root=workspace_root)
    identity = {
        "workflowId": replay["sourceWorkflowId"],
        "runId": replay["sourceAgentRunId"],
        "logicalStepId": "implement",
        "executionOrdinal": 1,
    }
    captured = await activities.workspace_capture_checkpoint(
        {
            "identity": identity,
            "boundary": "before_execution",
            "kind": "worktree_archive",
            "workspacePath": str(source),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": replay["captureIdempotencyKey"],
        }
    )
    assert captured["status"] == "captured"
    manifest = json.loads(
        (await activities._read_checkpoint_bytes(captured["workspace"]["manifestRef"])).decode()
    )
    assert manifest["pathCount"] == len(manifest["entries"])
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    assert entries["binary.bin"]["sha256"] == hashlib.sha256(bytes(range(256))).hexdigest()
    assert entries["scripts/run me.sh"]["mode"] & stat.S_IXUSR
    assert entries["safe-link"]["target"] == "tracked.txt"
    assert "__pycache__/excluded.pyc" not in entries
    assert all(str(provider_home) not in entry["path"] for entry in manifest["entries"])

    checkpoint_ref = await activities._put_checkpoint_bytes(
        json.dumps(
            {
                "checkpointId": replay["checkpointId"],
                "boundary": "before_execution",
                "baseCommit": base_commit,
                "workspace": captured["workspace"],
            }
        ).encode(),
        content_type="application/json",
        metadata={"artifact_kind": "step_execution_checkpoint"},
    )
    shutil.rmtree(source)
    del captured
    assert not source.exists()

    # Seed the destination with the failed implement attempt's dirty output.
    # restore_pre_execution must fully rewind to the captured before-execution
    # snapshot, discarding this residue rather than merging with it.
    destination.mkdir(parents=True)
    (destination / "tracked.txt").write_text("failed implement attempt\n", encoding="utf-8")
    (destination / "leftover.txt").write_text("dirty residue\n", encoding="utf-8")

    restored = await activities.workspace_apply_policy(
        {
            "identity": {**identity, "runId": replay["destinationAgentRunId"], "executionOrdinal": 2},
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": checkpoint_ref,
            "targetWorkspaceRef": str(destination),
            "idempotencyKey": replay["restoreIdempotencyKey"],
        }
    )
    restored_again = await activities.workspace_apply_policy(
        {
            "identity": {**identity, "runId": replay["destinationAgentRunId"], "executionOrdinal": 2},
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": checkpoint_ref,
            "targetWorkspaceRef": str(destination),
            "idempotencyKey": replay["restoreIdempotencyKey"],
        }
    )

    assert restored == restored_again
    assert restored["status"] == "applied"
    assert replay["sourceAgentRunId"] != replay["destinationAgentRunId"]
    assert not source.exists()
    # The captured before-execution working copy is restored, and the failed
    # attempt's dirty residue is gone: restore_pre_execution rewinds the tree.
    assert (destination / "tracked.txt").read_text() == "prepare stage working copy\n"
    assert not (destination / "leftover.txt").exists()
    assert (destination / "binary.bin").read_bytes() == bytes(range(256))
    assert os.readlink(destination / "safe-link") == "tracked.txt"
    assert os.access(destination / "scripts" / "run me.sh", os.X_OK)
    assert not (destination / "__pycache__").exists()
    # The worktree_archive capture path intentionally excludes VCS metadata, so a
    # restored checkpoint is a file snapshot rather than a usable git worktree.
    # Pin that contract explicitly so this replay is not mistaken for coverage of
    # resuming git-dependent tooling on the restored tree.
    assert not (destination / ".git").exists()
