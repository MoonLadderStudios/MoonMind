import asyncio
import inspect
import json
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable

import pytest

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityRetries,
    TemporalActivityRoute,
    TemporalActivityTimeouts,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


@pytest.fixture(autouse=True)
def _stub_resilience_policy_compile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the MM-880 ResiliencePolicy compile for execution-stage tests here.

    These tests exercise publish/PR/failure behavior with generic
    ``execute_activity`` mocks and do not assert on the compiled
    ResiliencePolicy. Stubbing the compile keeps the generic mock's fallback
    payload from being validated as a ResiliencePolicy envelope. Dedicated
    coverage for the compile lives in ``test_run_step_ledger.py``.
    """

    async def _noop(self, *args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        run_workflow_module.MoonMindRunWorkflow,
        "_compile_and_record_resilience_policy",
        _noop,
    )


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    dump_method = getattr(payload, 'model_dump', getattr(payload, 'dict', None))
    return dump_method() if dump_method else payload

def _step_execution_artifact_create_result(
    payload: Any,
    artifact_ids: count,
) -> tuple[dict[str, str], dict[str, str]] | None:
    normalized = _normalize_payload(payload)
    if (
        normalized.get("content_type")
        != "application/vnd.moonmind.step-execution+json;version=1"
        or (normalized.get("metadata_json") or {}).get("artifact_kind")
        != "step_execution_manifest"
    ):
        return None
    return (
        {"artifact_id": f"art_step_execution_{next(artifact_ids)}"},
        {"upload_url": "unused"},
    )

def _checkpoint_create_result(payload: Any) -> dict[str, Any]:
    normalized = _normalize_payload(payload)
    boundary = str(normalized.get("boundary") or "unknown")
    checkpoint_id = str(normalized.get("idempotencyKey") or f"checkpoint:{boundary}")
    workspace = normalized.get("workspace")
    workspace_kind = (
        workspace.get("kind")
        if isinstance(workspace, dict)
        else "ephemeral_workspace_ref"
    )
    return {
        "checkpointRef": f"artifact://checkpoint/{boundary}",
        "checkpointId": checkpoint_id,
        "contentType": "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
        "workspaceKind": workspace_kind,
        "diagnosticRefs": [],
        "idempotencyKey": checkpoint_id,
    }


def _managed_checkpoint_capture_result(payload: Any) -> dict[str, Any]:
    normalized = _normalize_payload(payload)
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
        "idempotencyKey": normalized["idempotencyKey"],
    }

async def _immediate_wait_condition(
    predicate: Callable[[], bool],
    **_kwargs: Any,
) -> None:
    assert predicate() is True

async def _timeout_wait_condition(
    predicate: Callable[[], bool],
    **_kwargs: Any,
) -> None:
    assert predicate() is False
    raise asyncio.TimeoutError

def _agent_runtime_step(
    step_id: str = "step-1",
    *,
    instructions: str = "Run the requested work.",
) -> dict[str, Any]:
    return {
        "id": step_id,
        "tool": {
            "type": "agent_runtime",
            "name": "codex_cli",
        },
        "inputs": {"instructions": instructions},
        "options": {},
    }

async def _completed_child_workflow(
    _workflow_type: str,
    _args: object,
    **_kwargs: object,
) -> object:
    return {
        "summary": "Agent finished",
        "metadata": {"push_status": "not_requested"},
        "output_refs": [],
    }

def test_initialize_from_payload_captures_input_and_plan_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(run_workflow_module.workflow, "memo", lambda: {})
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_trusted_owner_metadata",
        lambda self: ("user", "owner-1"),
    )

    _workflow_type, _parameters, input_ref, plan_ref, _scheduled_for = (
        workflow._initialize_from_payload(
            {
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": {"repo": "MoonLadderStudios/MoonMind"},
                "inputArtifactRef": "art_input_1",
                "planArtifactRef": "art_plan_1",
            }
        )
    )

    assert input_ref == "art_input_1"
    assert plan_ref == "art_plan_1"
    assert workflow._input_ref == "art_input_1"
    assert workflow._plan_ref == "art_plan_1"

def test_initialize_from_payload_tracks_declared_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(run_workflow_module.workflow, "memo", lambda: {})
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_trusted_owner_metadata",
        lambda self: ("user", "owner-1"),
    )

    workflow._initialize_from_payload(
        {
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "repo": "MoonLadderStudios/MoonMind",
                "workflow": {"dependsOn": ["mm:dep-1", "mm:dep-2"]},
            },
        }
    )

    captured_memo: list[dict[str, Any]] = []
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda memo: captured_memo.append(dict(memo)),
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)

    workflow._update_memo()

    assert workflow._declared_dependencies == ["mm:dep-1", "mm:dep-2"]
    dependencies = captured_memo[-1]["dependencies"]
    assert dependencies["declaredIds"] == ["mm:dep-1", "mm:dep-2"]
    assert dependencies["waited"] is False

@pytest.mark.asyncio
async def test_run_planning_stage_extracts_plan_ref_from_activity_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: dict[str, object] = {}

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: object,
        **_kwargs: object,
    ) -> dict[str, str]:
        captured["activity_type"] = activity_type
        captured["payload"] = payload
        return {"plan_ref": "art_plan_2"}

    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: False)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)

    resolved = await workflow._run_planning_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        input_ref="art_input_1",
        plan_ref=None,
    )

    assert captured["activity_type"] == "plan.generate"
    payload = captured["payload"]
    from moonmind.schemas.temporal_activity_models import PlanGenerateInput
    assert isinstance(payload, PlanGenerateInput)
    assert payload.inputs_ref == "art_input_1"
    assert "workflow_id" in payload.execution_ref
    assert resolved == "art_plan_2"
    assert workflow._plan_ref == "art_plan_2"

@pytest.mark.asyncio
async def test_run_execution_stage_reads_plan_and_dispatches_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        if activity_type == "artifact.read":
            if (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            ) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "description": "Run tests",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.skill.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 3},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [_agent_runtime_step()],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        _completed_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    # Provider profile probes happen before plan loading, but the exact runtime set
    # can change as runtimes are added or removed.
    plan_read = next(
        payload
        for activity_type, payload in captured
        if activity_type == "artifact.read" and payload.get("artifact_ref") == "art_plan_1"
    )
    assert plan_read["artifact_ref"] == "art_plan_1"
    manifest_create = next(
        payload
        for activity_type, payload in captured[4:]
        if activity_type == "artifact.create"
        and payload.get("content_type")
        == "application/vnd.moonmind.step-execution+json;version=1"
    )
    assert manifest_create["content_type"] == (
        "application/vnd.moonmind.step-execution+json;version=1"
    )
    assert manifest_create["metadata_json"]["artifact_kind"] == (
        "step_execution_manifest"
    )
    assert not any(
        payload.get("artifact_ref") == "artifact://registry/1"
        for activity_type, payload in captured
        if activity_type == "artifact.read"
    )

@pytest.mark.asyncio
async def test_run_finalizing_stage_writes_dependency_summary_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._state = "waiting_on_dependencies"
    workflow._declared_dependencies = ["mm:dep-1", "mm:dep-2"]
    workflow._dependency_wait_occurred = True
    workflow._dependency_wait_duration_ms = 4200
    workflow._dependency_resolution = "dependency_failed"
    workflow._failed_dependency_id = "mm:dep-2"

    written_payloads: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.create":
            return ({"artifact_id": "art_summary_1"}, {"upload_url": "unused"})
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, bool]:
        assert activity_type == "artifact.write_complete"
        written_payloads.append(json.loads(payload.payload.decode("utf-8")))
        return {"ok": True}

    start_time = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    finish_time = datetime(2026, 3, 29, 12, 0, 5, tzinfo=timezone.utc)
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "mm:wf-1",
            "run_id": "run-1",
            "start_time": start_time,
        },
    )

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: finish_time)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_finalizing_stage(
        parameters={"runtime": {"mode": "codex"}, "publishMode": "none"},
        status="failed",
        error="dependency failed",
    )

    assert written_payloads
    dependencies = written_payloads[-1]["dependencies"]
    assert dependencies == {
        "declaredIds": ["mm:dep-1", "mm:dep-2"],
        "waited": True,
        "waitDurationMs": 4200,
        "resolution": "dependency_failed",
        "failedDependencyId": "mm:dep-2",
        "outcomes": [],
    }

@pytest.mark.asyncio
async def test_run_finalizing_stage_writes_structured_moonspec_failure_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._state = "finalizing"
    workflow._publish_status = "not_required"
    workflow._publish_reason = (
        "MoonSpec verification did not approve publication. verdict BLOCKED. "
        "Classification: environment failure / validation infrastructure unavailable. "
        "Docker UE image access failed: GHCR returned `unauthorized`; native UE "
        "paths missing."
    )
    workflow._publish_context = {
        "publicationBlockedBy": "moonspec_verify",
        "branch": "jira-orchestrate-thor-509-c173f338",
        "baseRef": "origin/main",
        "headSha": "2c6584dc4487629eaee43bd4a9bcb8974e7e8dae",
        "commitCount": 6,
        "pullRequestUrl": "https://github.com/MoonLadderStudios/Tactics/pull/1845",
        "moonSpecGate": {
            "logicalStepId": "tpl:jira-orchestrate:1.0.0:23:ceb64b9f",
            "verdict": "BLOCKED",
            "classification": (
                "environment failure / validation infrastructure unavailable."
            ),
            "diagnosticsRef": "art_verify_report",
            "summary": (
                "Native UE/toolchain NOT RUN; all required native Windows/WSL "
                "paths missing. Docker UE image access FAIL: GHCR returned "
                "`unauthorized`; Docker fallback cannot start Unreal."
            ),
        },
    }

    written_payloads: list[dict[str, Any]] = []
    captured_memo: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.create":
            return ({"artifact_id": "art_summary_1"}, {"upload_url": "unused"})
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, bool]:
        assert activity_type == "artifact.write_complete"
        written_payloads.append(json.loads(payload.payload.decode("utf-8")))
        return {"ok": True}

    start_time = datetime(2026, 6, 15, 2, 41, tzinfo=timezone.utc)
    finish_time = datetime(2026, 6, 15, 9, 7, tzinfo=timezone.utc)
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "mm:wf-1",
            "run_id": "run-1",
            "start_time": start_time,
        },
    )

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: finish_time)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda memo: captured_memo.append(dict(memo)),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )

    await workflow._run_finalizing_stage(
        parameters={"runtime": {"mode": "codex"}, "publishMode": "pr"},
        status="failed",
        error=workflow._publish_reason,
    )

    assert written_payloads
    failure_summary = written_payloads[-1]["failureSummary"]
    assert failure_summary == {
        "type": "moonspec_verification_gate",
        "category": "validation_environment_blocked",
        "blockedBy": "moonspec_verify",
        "verdict": "BLOCKED",
        "classification": "environment failure / validation infrastructure unavailable.",
        "diagnosticsRef": "art_verify_report",
        "recommendedNextAction": "restore_validation_environment",
        "summary": (
            "MoonSpec verification blocked publication: BLOCKED. "
            "environment failure / validation infrastructure unavailable."
        ),
        "blockers": [
            "native_unreal_toolchain_missing",
            "docker_registry_unauthorized",
        ],
        "publishContext": {
            "branch": "jira-orchestrate-thor-509-c173f338",
            "baseRef": "origin/main",
            "headSha": "2c6584dc4487629eaee43bd4a9bcb8974e7e8dae",
            "commitCount": 6,
            "pullRequestUrl": "https://github.com/MoonLadderStudios/Tactics/pull/1845",
        },
    }
    workflow._set_state("failed", summary="blocked")
    assert captured_memo[-1]["summary_artifact_ref"] == "art_summary_1"


@pytest.mark.asyncio
async def test_run_finalizing_stage_writes_transient_codex_failure_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._state = "finalizing"
    workflow._pull_request_url = "https://github.com/MoonLadderStudios/Tactics/pull/1844"
    workflow._publish_context = {
        "branch": "jira-orchestrate-thor-508-ab3bfbd4",
        "baseRef": "origin/main",
        "headSha": "e35729fa58541d0eeca33f4f91015c9d06fc0569",
        "commitCount": 4,
        "pullRequestUrl": "https://github.com/MoonLadderStudios/Tactics/pull/1844",
        "moonSpecGate": {
            "logicalStepId": "tpl:jira-orchestrate:1.0.0:21:6e2de025",
            "verdict": "FULLY_IMPLEMENTED",
            "diagnosticsRef": "art_verify_pass",
        },
    }
    workflow._failure_diagnostic = {
        "stage": "finalizing",
        "category": "execution_error",
        "source": "child_workflow",
        "stepId": "tpl:jira-orchestrate:1.0.0:22:6e2de025",
        "message": (
            "Agent runtime failed with execution_error for profile codex_default "
            "due to app_server_protocol_empty_turn: codex app-server "
            "turn/completed produced no assistant output "
            "(retryRecommendedAction: clear_session; diagnosticsRef: art_empty_turn)"
        ),
        "diagnosticsRef": "art_empty_turn",
    }

    written_payloads: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.create":
            return ({"artifact_id": "art_summary_2"}, {"upload_url": "unused"})
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, bool]:
        assert activity_type == "artifact.write_complete"
        written_payloads.append(json.loads(payload.payload.decode("utf-8")))
        return {"ok": True}

    start_time = datetime(2026, 6, 15, 2, 41, tzinfo=timezone.utc)
    finish_time = datetime(2026, 6, 15, 8, 46, tzinfo=timezone.utc)
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "mm:wf-2",
            "run_id": "run-2",
            "start_time": start_time,
        },
    )

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: finish_time)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )

    await workflow._run_finalizing_stage(
        parameters={"runtime": {"mode": "codex"}, "publishMode": "pr"},
        status="failed",
        error=workflow._failure_diagnostic["message"],
    )

    failure_summary = written_payloads[-1]["failureSummary"]
    assert failure_summary["type"] == "agent_runtime_failure"
    assert failure_summary["category"] == "transient_agent_runtime"
    assert failure_summary["failureCause"] == "app_server_protocol_empty_turn"
    assert failure_summary["retryRecommendedAction"] == "clear_session"
    assert failure_summary["diagnosticsRef"] == "art_empty_turn"
    assert failure_summary["recommendedNextAction"] == (
        "retry_finalization_after_clear_session"
    )
    assert failure_summary["partialSuccess"] == {
        "moonSpecVerdict": "FULLY_IMPLEMENTED",
        "pullRequestUrl": "https://github.com/MoonLadderStudios/Tactics/pull/1844",
        "branch": "jira-orchestrate-thor-508-ab3bfbd4",
        "headSha": "e35729fa58541d0eeca33f4f91015c9d06fc0569",
    }

@pytest.mark.asyncio
async def test_run_execution_stage_rejects_legacy_skill_registry_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            ) if payload is not None else None
            if artifact_ref == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "description": "Run repository tests",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.skill.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch"},
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert any(
        activity_type == "mm.skill.execute" for activity_type, _payload in captured
    )

@pytest.mark.asyncio
async def test_run_execution_stage_skips_empty_registry_for_agent_runtime_only_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        normalized = _normalize_payload(payload)
        captured.append((activity_type, normalized))
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "artifact://registry/empty":
                raise AssertionError(
                    "agent_runtime-only plans should not read the empty registry snapshot"
                )
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Runtime Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/empty",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "claude",
                            },
                            "inputs": {
                                "instructions": "Adjust the dashboard pagination control.",
                                "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                                "repository": "MoonLadderStudios/MoonMind",
                            },
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Agent finished",
            "metadata": {"push_status": "not_requested"},
            "output_refs": [],
        }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    artifact_reads = [
        payload["artifact_ref"]
        for activity_type, payload in captured
        if activity_type == "artifact.read"
    ]
    assert artifact_reads == ["art_plan_1"]

@pytest.mark.asyncio
async def test_run_execution_stage_stops_plan_after_structured_blocked_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._integration = None
    child_calls: list[str] = []
    step_execution_artifact_ids = count(1)

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        normalized = _normalize_payload(payload)
        if activity_type == "artifact.create":
            artifact_create_result = _step_execution_artifact_create_result(
                payload,
                step_execution_artifact_ids,
            )
            if artifact_create_result is not None:
                return artifact_create_result
        if activity_type == "artifact.read":
            assert normalized["artifact_ref"] == "art_plan_1"
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Runtime Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/unused",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "check-blockers",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "codex_cli",
                            },
                            "inputs": {"instructions": "Check blockers."},
                        },
                        {
                            "id": "implement",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "codex_cli",
                            },
                            "inputs": {"instructions": "Implement changes."},
                        },
                    ],
                    "edges": [{"from": "check-blockers", "to": "implement"}],
                }
            ).encode("utf-8")
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity {activity_type}")

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        node_id = str(_kwargs["id"]).rsplit(":agent:", 1)[1]
        child_calls.append(node_id)
        return {
            "summary": "Agent finished",
            "metadata": {
                "operator_summary": (
                    '{"decision":"blocked","summary":"Required dependency is not done."}'
                ),
                "push_status": "no_commits",
                "push_branch": "generated-branch",
                "push_base_ref": "origin/main",
                "push_commit_count": 0,
            },
            "output_refs": [],
        }

    async def fake_bind_workflow_scoped_session(
        self: MoonMindRunWorkflow,
        request: object,
    ) -> object:
        return request

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> object:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return await fake_execute_activity(activity_type, payload, **kwargs)

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.RUN_WORKFLOW_PUBLISH_OUTCOME_PATCH,
            run_workflow_module.RUN_BLOCKED_OUTCOME_SHORT_CIRCUIT_PATCH,
        },
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    steps = workflow.get_step_ledger()["steps"]
    assert child_calls == ["check-blockers"]
    assert workflow._plan_blocked_message == (
        "Workflow blocked by plan step: Required dependency is not done."
    )
    assert workflow._publish_status == "not_required"
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "skipped"

@pytest.mark.asyncio
async def test_run_execution_stage_rejects_legacy_jira_blocker_skill_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    skill_calls: list[tuple[str, str | None]] = []
    step_execution_artifact_ids = count(1)

    registry_payload = {
        "skills": [
            {
                "name": "jira.check_blockers",
                                "description": "Check Jira blockers",
                "inputs": {"schema": {"type": "object"}},
                "outputs": {"schema": {"type": "object"}},
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["integration:jira"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 60,
                        "schedule_to_close_seconds": 120,
                    },
                    "retries": {"max_attempts": 1},
                },
            },
            {
                "name": "repo.run_tests",
                                "description": "Run tests",
                "inputs": {"schema": {"type": "object"}},
                "outputs": {"schema": {"type": "object"}},
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 60,
                        "schedule_to_close_seconds": 120,
                    },
                    "retries": {"max_attempts": 1},
                },
            },
        ]
    }

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        normalized = _normalize_payload(payload)
        if activity_type == "artifact.create":
            artifact_create_result = _step_execution_artifact_create_result(
                payload,
                step_execution_artifact_ids,
            )
            if artifact_create_result is not None:
                return artifact_create_result
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "artifact://registry/jira":
                return json.dumps(registry_payload).encode("utf-8")
            assert artifact_ref == "art_plan_1"
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Jira Wait Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/jira",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "check-blockers",
                            "tool": {
                                "type": "skill",
                                "name": "jira.check_blockers",
                            },
                            "inputs": {"targetIssueKey": "MM-686"},
                        },
                        {
                            "id": "implement",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                            },
                            "inputs": {"instructions": "Continue after blockers."},
                        },
                    ],
                    "edges": [{"from": "check-blockers", "to": "implement"}],
                }
            ).encode("utf-8")
        if activity_type == "mm.skill.execute":
            invocation = normalized["invocation_payload"]
            tool_name = invocation["tool"]["name"]
            skill_calls.append((tool_name, normalized.get("idempotency_key")))
            if tool_name == "jira.check_blockers" and len(skill_calls) == 1:
                return {
                    "status": "COMPLETED",
                    "outputs": {
                        "targetIssueKey": "MM-686",
                        "decision": "blocked",
                        "blockingIssues": [
                            {
                                "issueKey": "MM-685",
                                "status": "In Progress",
                                "done": False,
                            }
                        ],
                        "summary": (
                            "MM-686 is blocked by unresolved Jira issue(s): "
                            "MM-685 (In Progress)."
                        ),
                    },
                }
            if tool_name == "jira.check_blockers":
                return {
                    "status": "COMPLETED",
                    "outputs": {
                        "targetIssueKey": "MM-686",
                        "decision": "continue",
                        "blockingIssues": [],
                        "summary": "All Jira blockers for MM-686 are Done.",
                    },
                }
            return {"status": "COMPLETED", "outputs": {"summary": "Implemented."}}
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        raise AssertionError(f"unexpected activity {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> object:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return await fake_execute_activity(activity_type, payload, **kwargs)

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.RUN_JIRA_BLOCKER_RECHECK_PATCH,
            run_workflow_module.RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
            "idempotency_key_phase3",
        },
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _timeout_wait_condition,
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "none"},
        plan_ref="art_plan_1",
    )

    assert [call[0] for call in skill_calls] == [
        "jira.check_blockers",
        "jira.check_blockers",
        "repo.run_tests",
    ]
    assert skill_calls[1][1] == (
        "wf-1:run-1:check-blockers:execution:1:execute_jira_blocker_recheck_1"
    )
    assert workflow._plan_blocked_message is None
    assert workflow._jira_blocker_wait_active is False
    assert workflow._waiting_reason is None

@pytest.mark.asyncio
async def test_jira_blocker_recheck_wait_honors_pause_before_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    events: list[str] = []
    agent_request = type("AgentRequest", (), {"agent_id": "agent-1"})()
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "decision": "blocked",
            "blockingIssues": [{"issueKey": "MM-685", "done": False}],
            "summary": "Blocked by MM-685.",
        },
    }
    continue_result = {
        "status": "COMPLETED",
        "outputs": {"decision": "continue", "blockingIssues": []},
    }

    async def fake_wait_condition(
        predicate: Callable[[], bool],
        **_kwargs: Any,
    ) -> None:
        assert predicate() is False
        workflow._paused = True
        assert predicate() is True
        events.append("pause-observed")

    async def fake_pause_boundary(self: MoonMindRunWorkflow) -> None:
        assert self._paused is True
        events.append("pause-boundary")
        self._paused = False

    async def fake_execute_child_workflow(
        workflow_type: str,
        request: Any,
        **kwargs: Any,
    ) -> Any:
        events.append(f"child:{workflow_type}")
        assert events[-2:] == ["pause-boundary", "child:MoonMind.AgentRun"]
        assert request is agent_request
        assert kwargs["id"].endswith(":agent:check-blockers:jira-blocker-recheck1")
        return {
            "metadata": {
                "decision": "continue",
                "blockingIssues": [],
            }
        }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    def fake_workflow_info() -> Any:
        return type("WorkflowInfo", (), {"workflow_id": "wf-1"})()

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        fake_workflow_info,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_pause_boundary,
    )

    result, skipped = await workflow._wait_for_jira_blocker_resolution(
        execution_result=blocked_result,
        agent_request=agent_request,
        node_id="check-blockers",
        tool_name="jira.check_blockers",
        tool_type="agent_runtime",
        failure_mode="FAIL_FAST",
    )

    assert result["status"] == continue_result["status"]
    assert result["outputs"]["decision"] == "continue"
    assert result["outputs"]["blockingIssues"] == []
    assert skipped is False
    assert events == [
        "pause-observed",
        "pause-boundary",
        "child:MoonMind.AgentRun",
    ]
    assert workflow._jira_blocker_wait_active is False
    assert workflow._jira_blocker_wait_started_at is None
    assert workflow._jira_blocker_wait_issue_keys == []
    assert workflow._jira_blocker_wait_summary is None


def test_compact_jira_blocker_recheck_payload_strips_large_context() -> None:
    workflow = MoonMindRunWorkflow()
    payload = {
        "registry_snapshot_ref": "art_registry",
        "principal": "owner-1",
        "invocation_payload": {
            "id": "check-blockers",
            "tool": {
                "type": "skill",
                "name": "jira.check_blockers",
            },
            "skill": {"name": "jira.check_blockers"},
            "inputs": {
                "targetIssueKey": "MM-686",
                "blockerPreflight": {
                    "targetIssueKey": "MM-686",
                    "linkType": "Blocks",
                },
                "instructions": "large prompt" * 100,
                "previousOutputs": {"summary": "large previous output" * 100},
            },
            "options": {"dryRun": False},
        },
        "context": {
            "namespace": "default",
            "workflow_id": "wf-1",
            "run_id": "run-1",
            "node_id": "check-blockers",
        },
        "idempotency_key": "base-key",
    }

    compact = workflow._compact_jira_blocker_recheck_payload(payload)

    invocation = compact["invocation_payload"]
    assert invocation["tool"] == payload["invocation_payload"]["tool"]
    assert invocation["skill"] == payload["invocation_payload"]["skill"]
    assert invocation["options"] == payload["invocation_payload"]["options"]
    assert invocation["inputs"] == {
        "targetIssueKey": "MM-686",
        "blockerPreflight": {
            "targetIssueKey": "MM-686",
            "linkType": "Blocks",
        },
        "linkType": "Blocks",
    }
    assert "previousOutputs" not in invocation["inputs"]
    assert "instructions" not in invocation["inputs"]
    assert compact["registry_snapshot_ref"] == "art_registry"
    assert compact["principal"] == "owner-1"
    assert compact["context"] == payload["context"]


def test_jira_blocker_wait_coalesces_unchanged_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    published: list[str] = []
    waiting_rows: list[str] = []
    metadata_updates: list[str] = []
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "blockingIssues": [
                {"issueKey": "MM-685", "status": "In Progress", "done": False}
            ],
            "summary": "MM-686 is blocked by MM-685.",
        },
    }
    changed_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "blockingIssues": [
                {"issueKey": "MM-685", "status": "Review", "done": False}
            ],
            "summary": "MM-686 is still blocked by MM-685.",
        },
    }

    def fake_set_state(self: MoonMindRunWorkflow, state: str, summary: str) -> None:
        self._state = state
        self._summary = summary
        published.append(summary)

    def fake_mark_waiting(
        _logical_step_id: str,
        **kwargs: Any,
    ) -> None:
        waiting_rows.append(str(kwargs.get("summary") or ""))

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id
            == run_workflow_module.RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH
        ),
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_set_state", fake_set_state)
    monkeypatch.setattr(workflow, "_mark_step_waiting", fake_mark_waiting)
    monkeypatch.setattr(
        workflow,
        "_update_search_attributes",
        lambda: metadata_updates.append("search"),
    )
    monkeypatch.setattr(
        workflow,
        "_update_memo",
        lambda: metadata_updates.append("memo"),
    )

    workflow._enter_jira_blocker_wait(
        node_id="check-blockers",
        execution_result=blocked_result,
    )
    workflow._enter_jira_blocker_wait(
        node_id="check-blockers",
        execution_result=blocked_result,
    )
    workflow._enter_jira_blocker_wait(
        node_id="check-blockers",
        execution_result=changed_result,
    )

    assert published == [
        "Workflow blocked by plan step: MM-686 is blocked by MM-685.",
        "Workflow blocked by plan step: MM-686 is still blocked by MM-685.",
    ]
    assert waiting_rows == published
    assert metadata_updates == ["search", "memo", "search", "memo"]


def test_jira_blocker_wait_preserves_operator_bypass_on_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._jira_blocker_wait_active = True
    workflow._jira_blocker_wait_skipped = True
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "blockingIssues": [
                {"issueKey": "MM-685", "status": "In Progress", "done": False}
            ],
            "summary": "MM-686 is blocked by MM-685.",
        },
    }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id
            == run_workflow_module.RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH
        ),
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_set_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "_mark_step_waiting", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(workflow, "_update_memo", lambda: None)

    workflow._enter_jira_blocker_wait(
        node_id="check-blockers",
        execution_result=blocked_result,
    )

    assert workflow._jira_blocker_wait_skipped is True


def test_jira_blocker_wait_unpatched_histories_publish_each_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    published: list[str] = []
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "blockingIssues": [
                {"issueKey": "MM-685", "status": "In Progress", "done": False}
            ],
            "summary": "MM-686 is blocked by MM-685.",
        },
    }

    def fake_set_state(self: MoonMindRunWorkflow, state: str, summary: str) -> None:
        self._state = state
        self._summary = summary
        published.append(summary)

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_set_state", fake_set_state)
    monkeypatch.setattr(workflow, "_mark_step_waiting", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(workflow, "_update_memo", lambda: None)

    workflow._enter_jira_blocker_wait(
        node_id="check-blockers",
        execution_result=blocked_result,
    )
    workflow._enter_jira_blocker_wait(
        node_id="check-blockers",
        execution_result=blocked_result,
    )

    assert published == [
        "Workflow blocked by plan step: MM-686 is blocked by MM-685.",
        "Workflow blocked by plan step: MM-686 is blocked by MM-685.",
    ]


def _captured_search_attribute_pairs(
    monkeypatch: pytest.MonkeyPatch,
    workflow_instance: MoonMindRunWorkflow,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda pairs: captured.__setitem__("pairs", pairs),
    )
    workflow_instance._update_search_attributes()
    return {pair.key.name: pair.value for pair in captured["pairs"]}


def test_update_search_attributes_includes_title_tokens_for_visibility_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._title = "Release the Kraken"

    by_name = _captured_search_attribute_pairs(monkeypatch, workflow_instance)

    # mm_title is a KeywordList of lowercased, deduped word tokens so operators
    # can word-match titles in Temporal SQL visibility.
    assert by_name["mm_title"] == ["release", "the", "kraken"]


def test_update_search_attributes_omits_title_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_instance = MoonMindRunWorkflow()
    # _title defaults to None; the title pair must be omitted rather than
    # upserting an empty KeywordList.

    by_name = _captured_search_attribute_pairs(monkeypatch, workflow_instance)

    assert "mm_title" not in by_name


@pytest.mark.asyncio
async def test_jira_blocker_wait_rechecks_with_compact_payload_and_no_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured_payloads: list[dict[str, Any]] = []
    manifest_calls = 0
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "blockingIssues": [
                {"issueKey": "MM-685", "status": "In Progress", "done": False}
            ],
            "summary": "MM-686 is blocked by MM-685.",
        },
    }
    continue_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "continue",
            "blockingIssues": [],
            "summary": "All Jira blockers are Done.",
        },
    }
    execute_payload = {
        "registry_snapshot_ref": "art_registry",
        "principal": "owner-1",
        "invocation_payload": {
            "id": "check-blockers",
            "tool": {
                "type": "skill",
                "name": "jira.check_blockers",
            },
            "skill": {"name": "jira.check_blockers"},
            "inputs": {
                "targetIssueKey": "MM-686",
                "blockerPreflight": {
                    "targetIssueKey": "MM-686",
                    "linkType": "Blocks",
                },
                "instructions": "large prompt" * 100,
                "previousOutputs": {"summary": "large previous output" * 100},
            },
            "options": {},
        },
        "context": {
            "namespace": "default",
            "workflow_id": "wf-1",
            "run_id": "run-1",
            "node_id": "check-blockers",
        },
        "idempotency_key": "base-key",
    }
    route = TemporalActivityRoute(
        activity_type="mm.tool.execute",
        task_queue="mm.activity.integrations",
        fleet="integrations",
        capability_class="tools",
        timeouts=TemporalActivityTimeouts(
            start_to_close_seconds=30,
            schedule_to_close_seconds=30,
        ),
        retries=TemporalActivityRetries(max_attempts=1, max_interval_seconds=1),
    )

    async def fake_execute_activity(
        _activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured_payloads.append(_normalize_payload(payload))
        return blocked_result if len(captured_payloads) == 1 else continue_result

    async def fake_record_manifest(*_args: Any, **_kwargs: Any) -> None:
        nonlocal manifest_calls
        manifest_calls += 1

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _timeout_wait_condition,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id
            == run_workflow_module.RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH
        ),
    )
    def fake_workflow_info() -> Any:
        return type(
            "WorkflowInfo",
            (),
            {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
        )()

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        fake_workflow_info,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_record_step_execution_manifest",
        fake_record_manifest,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )

    result, skipped = await workflow._wait_for_jira_blocker_resolution(
        execution_result=blocked_result,
        node_id="check-blockers",
        tool_name="jira.check_blockers",
        tool_type="skill",
        failure_mode="FAIL_FAST",
        route=route,
        execute_payload=execute_payload,
    )

    assert skipped is False
    assert result == continue_result
    assert manifest_calls == 0
    assert len(captured_payloads) == 2
    for index, payload in enumerate(captured_payloads, start=1):
        inputs = payload["invocation_payload"]["inputs"]
        assert inputs == {
            "targetIssueKey": "MM-686",
            "blockerPreflight": {
                "targetIssueKey": "MM-686",
                "linkType": "Blocks",
            },
            "linkType": "Blocks",
        }
        assert "previousOutputs" not in inputs
        assert "instructions" not in inputs
        assert payload["idempotency_key"] == (
            f"base-key_jira_blocker_recheck_{index}"
        )


@pytest.mark.asyncio
async def test_jira_blocker_recheck_preserves_assessment_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    captured_payloads: list[dict[str, Any]] = []
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
            "blockingIssues": [
                {"issueKey": "MM-685", "status": "In Progress", "done": False}
            ],
            "summary": "MM-686 is blocked by MM-685.",
        },
    }
    continue_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "continue",
            "blockingIssues": [],
            "summary": "All Jira blockers are Done.",
        },
    }
    execute_payload = {
        "registry_snapshot_ref": "art_registry",
        "principal": "owner-1",
        "invocation_payload": {
            "id": "check-blockers",
            "tool": {
                "type": "skill",
                "name": "jira.check_blockers",
            },
            "skill": {"name": "jira.check_blockers"},
            "inputs": {
                "targetIssueKey": "MM-686",
                "blockerPreflight": {
                    "targetIssueKey": "MM-686",
                    "linkType": "Blocks",
                },
                "assessmentArtifactPath": "artifacts/assessment.json",
                "previousOutputs": {
                    "lastAssistantText": "Verdict: `PARTIALLY_IMPLEMENTED`."
                },
            },
            "options": {},
        },
        "idempotency_key": "base-key",
    }
    route = TemporalActivityRoute(
        activity_type="mm.tool.execute",
        task_queue="mm.activity.integrations",
        fleet="integrations",
        capability_class="tools",
        timeouts=TemporalActivityTimeouts(
            start_to_close_seconds=30,
            schedule_to_close_seconds=30,
        ),
        retries=TemporalActivityRetries(max_attempts=1, max_interval_seconds=1),
    )

    async def fake_execute_activity(
        _activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured_payloads.append(_normalize_payload(payload))
        return continue_result

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _timeout_wait_condition,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH,
            run_workflow_module.RUN_JIRA_BLOCKER_RECHECK_ASSESSMENT_CONTEXT_PATCH,
        },
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )

    result, skipped = await workflow._wait_for_jira_blocker_resolution(
        execution_result=blocked_result,
        node_id="check-blockers",
        tool_name="jira.check_blockers",
        tool_type="skill",
        failure_mode="FAIL_FAST",
        route=route,
        execute_payload=execute_payload,
    )

    assert skipped is False
    assert result["outputs"]["decision"] == "continue"
    assert result["outputs"]["assessmentVerdict"] == "PARTIALLY_IMPLEMENTED"
    assert workflow._assessment_context["assessmentVerdict"] == (
        "PARTIALLY_IMPLEMENTED"
    )
    compact_inputs = captured_payloads[0]["invocation_payload"]["inputs"]
    assert compact_inputs == {
        "targetIssueKey": "MM-686",
        "blockerPreflight": {
            "targetIssueKey": "MM-686",
            "linkType": "Blocks",
        },
        "linkType": "Blocks",
    }


def test_assessment_context_merge_treats_aliases_as_one_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        == run_workflow_module.RUN_JIRA_BLOCKER_RECHECK_ASSESSMENT_CONTEXT_ALIAS_PATCH,
    )
    workflow = MoonMindRunWorkflow()
    workflow._record_assessment_context(
        {"assessmentVerdict": "PARTIALLY_IMPLEMENTED"}
    )

    result = workflow._merge_assessment_context_into_result(
        {
            "status": "COMPLETED",
            "outputs": {"assessment_verdict": "NOT_IMPLEMENTED"},
        }
    )

    assert result["outputs"]["assessment_verdict"] == "NOT_IMPLEMENTED"
    assert "assessmentVerdict" not in result["outputs"]
    workflow._record_assessment_context(result["outputs"])
    assert workflow._assessment_context["assessmentVerdict"] == "NOT_IMPLEMENTED"
    assert workflow._assessment_context["assessment_verdict"] == "NOT_IMPLEMENTED"


def test_assessment_context_keeps_recorded_shape_when_alias_patch_unapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)
    workflow = MoonMindRunWorkflow()

    workflow._record_assessment_context(
        {"assessmentVerdict": "PARTIALLY_IMPLEMENTED"}
    )

    assert workflow._assessment_context == {
        "assessmentVerdict": "PARTIALLY_IMPLEMENTED"
    }


@pytest.mark.asyncio
async def test_jira_blocker_recheck_applies_retry_floor_for_one_attempt_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    captured_max_attempts: list[int] = []
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "blocked",
            "blockingIssues": [{"issueKey": "MM-685", "done": False}],
            "summary": "MM-686 is blocked by MM-685.",
        },
    }
    continue_result = {
        "status": "COMPLETED",
        "outputs": {
            "targetIssueKey": "MM-686",
            "decision": "continue",
            "blockingIssues": [],
            "summary": "All Jira blockers are Done.",
        },
    }
    execute_payload = {
        "registry_snapshot_ref": "art_registry",
        "principal": "owner-1",
        "invocation_payload": {
            "id": "check-blockers",
            "tool": {
                "type": "skill",
                "name": "jira.check_blockers",
            },
            "inputs": {
                "targetIssueKey": "MM-686",
                "blockerPreflight": {
                    "targetIssueKey": "MM-686",
                    "linkType": "Blocks",
                },
            },
            "options": {},
        },
        "idempotency_key": "base-key",
    }
    route = TemporalActivityRoute(
        activity_type="mm.tool.execute",
        task_queue="mm.activity.integrations",
        fleet="integrations",
        capability_class="tools",
        timeouts=TemporalActivityTimeouts(
            start_to_close_seconds=30,
            schedule_to_close_seconds=120,
        ),
        retries=TemporalActivityRetries(max_attempts=1, max_interval_seconds=30),
    )

    async def fake_execute_activity(
        _activity_type: str,
        _payload: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured_max_attempts.append(kwargs["retry_policy"].maximum_attempts)
        return continue_result

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _timeout_wait_condition,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH,
            run_workflow_module.RUN_JIRA_BLOCKER_RECHECK_RETRY_FLOOR_PATCH,
        },
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )

    result, skipped = await workflow._wait_for_jira_blocker_resolution(
        execution_result=blocked_result,
        node_id="check-blockers",
        tool_name="jira.check_blockers",
        tool_type="skill",
        failure_mode="FAIL_FAST",
        route=route,
        execute_payload=execute_payload,
    )

    assert skipped is False
    assert result == continue_result
    assert captured_max_attempts == [
        run_workflow_module.JIRA_BLOCKER_RECHECK_MIN_ACTIVITY_ATTEMPTS
    ]


@pytest.mark.asyncio
async def test_jira_blocker_recheck_activity_failure_respects_failure_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    agent_request = type("AgentRequest", (), {"agent_id": "agent-1"})()
    blocked_result = {
        "status": "COMPLETED",
        "outputs": {
            "decision": "blocked",
            "blockingIssues": [{"issueKey": "MM-685", "done": False}],
            "summary": "Blocked by MM-685.",
        },
    }

    async def fake_execute_child_workflow(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("worker unavailable")

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _timeout_wait_condition,
    )
    def fake_workflow_info() -> Any:
        return type("WorkflowInfo", (), {"workflow_id": "wf-1"})()

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        fake_workflow_info,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )

    result, skipped = await workflow._wait_for_jira_blocker_resolution(
        execution_result=blocked_result,
        agent_request=agent_request,
        node_id="check-blockers",
        tool_name="jira.check_blockers",
        tool_type="agent_runtime",
        failure_mode="CONTINUE",
    )

    assert result == blocked_result
    assert skipped is False
    assert workflow._jira_blocker_wait_active is False
    assert workflow._jira_blocker_wait_started_at is None
    assert workflow._jira_blocker_wait_issue_keys == []
    assert workflow._jira_blocker_wait_summary is None

    with pytest.raises(RuntimeError, match="worker unavailable"):
        await workflow._wait_for_jira_blocker_resolution(
            execution_result=blocked_result,
            agent_request=agent_request,
            node_id="check-blockers",
            tool_name="jira.check_blockers",
            tool_type="agent_runtime",
            failure_mode="FAIL_FAST",
        )


def test_skip_dependency_wait_allows_active_jira_wait_without_issue_keys() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._state = run_workflow_module.STATE_WAITING_ON_DEPENDENCIES
    workflow._jira_blocker_wait_active = True
    workflow._jira_blocker_wait_issue_keys = []

    workflow.validate_skip_dependency_wait()

@pytest.mark.asyncio
async def test_run_execution_stage_skips_registry_read_for_unpatched_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        normalized = _normalize_payload(payload)
        captured.append((activity_type, normalized))
        if activity_type == "artifact.read":
            artifact_ref = normalized.get("artifact_ref")
            if artifact_ref == "artifact://registry/empty":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "description": "Run tests",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.skill.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 3},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Runtime Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/empty",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "claude",
                            },
                            "inputs": {
                                "instructions": "Adjust the dashboard pagination control.",
                                "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                                "repository": "MoonLadderStudios/MoonMind",
                            },
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Agent finished",
            "metadata": {"push_status": "not_requested"},
            "output_refs": [],
        }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: False)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    artifact_reads = [
        payload["artifact_ref"]
        for activity_type, payload in captured
        if activity_type == "artifact.read"
    ]
    assert artifact_reads == ["art_plan_1"]

def test_build_agent_execution_request_includes_bundle_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)

    request = workflow._build_agent_execution_request(
        node_inputs={
            "instructions": "Bundled work",
            "repository": "org/repo",
            "startingBranch": "main",
            "publishMode": "branch",
            "bundleId": "bundle-1",
            "bundledNodeIds": ["node-1", "node-2"],
            "bundleManifestRef": "artifact://bundle/1",
            "bundleStrategy": "one_shot_jules",
        },
        node_id="bundle-1",
        tool_name="jules",
    )

    moonmind_meta = request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["bundleId"] == "bundle-1"
    assert moonmind_meta["bundledNodeIds"] == ["node-1", "node-2"]
    assert moonmind_meta["bundleManifestRef"] == "artifact://bundle/1"
    assert moonmind_meta["bundleStrategy"] == "one_shot_jules"

@pytest.mark.asyncio
async def test_run_execution_stage_fail_fast_raises_when_tool_returns_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [_agent_runtime_step()],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Failed to execute generic LLM handler",
            "failureClass": "gemini CLI command failed",
            "metadata": {},
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    with pytest.raises(ValueError) as exc_info:
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            plan_ref="art_plan_1",
        )

    message = str(exc_info.value)
    assert "Plan step 'step-1' (codex_cli) returned status FAILED" in message
    assert "Failed to execute generic LLM handler" in message
    assert "lastError=gemini CLI command failed" in message
    assert "childWorkflowId=wf-1:agent:step-1" in message

@pytest.mark.asyncio
async def test_run_execution_stage_continue_mode_keeps_running_after_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    child_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.create":
            return (
                {"artifact_id": f"art_step_execution_{child_calls}"},
                {"upload_url": "unused"},
            )
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "CONTINUE", "max_concurrency": 1},
                    "nodes": [
                        _agent_runtime_step("step-1"),
                        _agent_runtime_step("step-2"),
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal child_calls
        child_calls += 1
        if child_calls == 1:
            return {
                "summary": "intentional failure",
                "failureClass": "first step failed",
                "metadata": {},
                "output_refs": [],
            }
        return {
            "summary": "Agent finished",
            "metadata": {"push_status": "not_requested"},
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert child_calls == 2

@pytest.mark.asyncio
async def test_run_execution_stage_publish_none_blocks_after_failed_agent_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    child_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.create":
            return (
                {"artifact_id": f"art_step_execution_{child_calls}"},
                {"upload_url": "unused"},
            )
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Dry Run Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "CONTINUE", "max_concurrency": 1},
                    "nodes": [_agent_runtime_step("step-1")],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal child_calls
        child_calls += 1
        return {
            "summary": "Process exited with code 1",
            "failureClass": "execution_error",
            "diagnosticsRef": "art_failed_agent_result",
            "metadata": {
                "childWorkflowId": "wf-1:agent:step-1",
                "outputAgentResultRef": "art_failed_agent_result",
            },
            "outputRefs": ["run-1/stdout.log", "run-1/stderr.log"],
        }

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> object:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return await fake_execute_activity(activity_type, payload, **kwargs)

    async def fake_bind_workflow_scoped_session(
        _self: MoonMindRunWorkflow,
        request: object,
    ) -> object:
        return request

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == run_workflow_module.RUN_FAILED_RESULT_BLOCKER_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "none"},
        plan_ref="art_plan_1",
    )

    status, message, publish_failure = workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )

    assert child_calls == 4
    assert workflow._publish_status == "not_required"
    expected_message = (
        "Agent runtime failed with execution_error: Process exited with code 1 "
        "(diagnosticsRef: art_failed_agent_result)"
    )
    assert workflow._plan_blocked_message == expected_message
    assert status == "failed"
    assert message == expected_message
    assert publish_failure is True
    assert workflow._failure_diagnostic == {
        "stage": "executing",
        "category": "execution_error",
        "source": "child_workflow",
        "stepId": "step-1",
        "stepTitle": "codex_cli",
        "childWorkflowId": "wf-1:agent:step-1:attempt4:retry3",
        "message": expected_message,
        "rootCauseType": "AgentRunResult",
        "diagnosticsRef": "art_failed_agent_result",
    }

@pytest.mark.asyncio
async def test_run_execution_stage_publish_pr_blocks_after_failed_continue_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    step_execution_artifact_ids = count(1)
    child_calls = 0
    create_pr_called = False
    executed_steps: list[str] = []

    def _request_step_id(request: object) -> str:
        parameters = getattr(request, "parameters", {})
        metadata = parameters.get("metadata") if isinstance(parameters, dict) else {}
        moonmind = metadata.get("moonmind") if isinstance(metadata, dict) else {}
        step_ledger = moonmind.get("stepLedger") if isinstance(moonmind, dict) else {}
        return str(step_ledger.get("logicalStepId") or "")

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}
        if activity_type == "artifact.create":
            artifact_create_result = _step_execution_artifact_create_result(
                payload,
                step_execution_artifact_ids,
            )
            if artifact_create_result is not None:
                return artifact_create_result
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Jira Orchestrate Publish Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "CONTINUE", "max_concurrency": 1},
                    "nodes": [
                        _agent_runtime_step(
                            "tpl:jira-orchestrate:1.0.0:01:update"
                        ),
                        _agent_runtime_step(
                            "tpl:jira-orchestrate:1.0.0:02:verify"
                        ),
                        _agent_runtime_step(
                            "tpl:jira-orchestrate:1.0.0:03:notify"
                        ),
                    ],
                    "edges": [
                        {
                            "from": "tpl:jira-orchestrate:1.0.0:01:update",
                            "to": "tpl:jira-orchestrate:1.0.0:02:verify",
                        }
                    ],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal child_calls
        child_calls += 1
        step_id = _request_step_id(_args)
        executed_steps.append(step_id)
        if step_id.endswith(":03:notify"):
            return {
                "summary": "Independent notification completed.",
                "metadata": {"push_status": "not_requested"},
                "output_refs": [],
            }
        return {
            "summary": "codex app-server turn/completed produced no assistant output",
            "failureClass": "execution_error",
            "diagnosticsRef": "art_empty_turn",
            "metadata": {
                "profileId": "codex_default",
                "turnStatus": "failed",
                "turnMetadata": {
                    "failureCause": "app_server_protocol_empty_turn",
                    "retryRecommendedAction": "clear_session",
                },
            },
            "output_refs": [],
        }

    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
        },
        plan_ref="art_plan_1",
    )

    status, message, publish_failure = workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert child_calls >= 1
    assert any(step.endswith(":03:notify") for step in executed_steps)
    assert not any(step.endswith(":02:verify") for step in executed_steps)
    assert not create_pr_called
    assert workflow._publish_status == "not_required"
    assert status == "failed"
    assert publish_failure is True
    assert "app_server_protocol_empty_turn" in message
    assert "no PR was created" not in message

@pytest.mark.asyncio
async def test_run_execution_stage_publish_mode_pr_requires_pull_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Publish Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [_agent_runtime_step()],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Applied requested changes.",
            "metadata": {"stdout_tail": "Applied requested changes."},
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    with pytest.raises(
        ValueError,
        match="publishMode 'pr' requested but no PR head branch could be resolved",
    ):
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
            plan_ref="art_plan_1",
        )

@pytest.mark.asyncio
async def test_run_execution_stage_publish_mode_pr_accepts_github_pull_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Publish Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [_agent_runtime_step()],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Opened PR: https://github.com/org/repo/pull/123",
            "metadata": {
                "stdout_tail": "Opened PR: https://github.com/org/repo/pull/123"
            },
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

@pytest.mark.asyncio
async def test_run_execution_stage_publish_mode_pr_uses_publish_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    captured_create_payload: dict[str, Any] = {}
    captured_merge_payload: dict[str, Any] = {}

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "repo.create_pr":
            captured_create_payload["payload"] = _normalize_payload(payload)
            return {
                "url": "https://github.com/MoonLadderStudios/MoonMind/pull/999",
                "created": True,
                "headSha": "created-head-sha",
            }

        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "auto",
                                "description": "Auto",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "node-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "claude",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                                "instructions": "Remove spec from the specs directory",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        if workflow_type == "MoonMind.MergeAutomation":
            captured_merge_payload["payload"] = _normalize_payload(args)
            return {
                "status": "merged",
                "prNumber": 999,
                "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/999",
            }
        return {
            "summary": "Completed with status completed",
            "metadata": {"branch": "auto-0526d401", "push_status": "pushed"},
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
            "workflow": {
                "mergeAutomation": {"enabled": True},
                "publish": {
                    "prTitle": "OAuth redirect cleanup",
                    "prBody": "Adds regression coverage for callback URL selection.",
                },
            },
        },
        plan_ref="art_plan_1",
    )

    assert captured_create_payload["payload"]["title"] == "OAuth redirect cleanup"
    assert (
        captured_create_payload["payload"]["body"]
        == "Adds regression coverage for callback URL selection."
    )
    assert captured_merge_payload["payload"]["pullRequest"]["headSha"] == (
        "created-head-sha"
    )

@pytest.mark.asyncio
async def test_run_execution_stage_publish_mode_pr_defaults_title_from_task_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    captured_create_payload: dict[str, Any] = {}

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "repo.create_pr":
            captured_create_payload["payload"] = _normalize_payload(payload)
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/1000", "created": True}

        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "auto",
                                "description": "Auto",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "node-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "claude",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                                "instructions": "Remove spec from the specs directory",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Completed with status completed",
            "metadata": {"branch": "auto-0526d401", "push_status": "pushed"},
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
            "workflow": {
                "title": "Refactor callback handling",
                "instructions": "Update callback handler to support edge cases.",
            },
        },
        plan_ref="art_plan_1",
    )

    assert captured_create_payload["payload"]["title"] == "Refactor callback handling"
    assert (
        captured_create_payload["payload"]["body"]
        == "Update callback handler to support edge cases."
    )

@pytest.mark.asyncio
async def test_run_execution_stage_publish_mode_pr_prefers_pushed_branch_for_native_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    captured_create_payload: dict[str, Any] = {}

    async def fake_bind_workflow_scoped_session(
        self: MoonMindRunWorkflow,
        request: object,
    ) -> object:
        return request

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "repo.create_pr":
            captured_create_payload["payload"] = _normalize_payload(payload)
            return {
                "url": "https://github.com/MoonLadderStudios/MoonMind/pull/1001",
                "created": True,
            }

        if activity_type == "artifact.read":
            if (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            ) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "auto",
                                "description": "Auto",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Publish Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "node-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "codex_cli",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex_cli", "model": "gpt-5.4"},
                                "instructions": "Tighten the dashboard title size.",
                                "targetBranch": "planned-title-branch",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Completed with status completed",
            "metadata": {
                "push_status": "pushed",
                "push_branch": "actual-pushed-branch",
            },
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
            "workspaceSpec": {"targetBranch": "planned-title-branch"},
        },
        plan_ref="art_plan_1",
    )

    assert captured_create_payload["payload"]["head"] == "actual-pushed-branch"

def test_activity_result_failure_message_prefers_stderr_tail_over_progress_details() -> None:
    workflow = MoonMindRunWorkflow()
    message = workflow._activity_result_failure_message(
        {
            "status": "FAILED",
            "outputs": {
                "exit_code": 1,
                "stderr_tail": "gemini quota exceeded",
            },
            "progress": {"details": "Executed generic LLM handler via gemini"},
        }
    )
    assert message == "gemini quota exceeded"

def test_activity_result_provider_failure_summary_includes_auth_action() -> None:
    workflow = MoonMindRunWorkflow()

    message = workflow._activity_result_provider_failure_summary(
        {
            "status": "FAILED",
            "outputs": {
                "error": "user_error",
                "summary": "http 401",
                "providerErrorCode": "401",
                "retryRecommendation": "reauthenticate",
                "profileId": "codex_default",
            },
        }
    )

    assert (
        message
        == "Provider authentication failed with HTTP 401 for profile codex_default: "
        "http 401 (retryRecommendation: reauthenticate)"
    )

def test_activity_result_provider_failure_summary_reads_profile_from_failure() -> None:
    workflow = MoonMindRunWorkflow()

    message = workflow._activity_result_provider_failure_summary(
        {
            "status": "FAILED",
            "outputs": {
                "error": "user_error",
                "providerFailure": {
                    "providerErrorCode": "401",
                    "profileId": "codex_default",
                    "sanitizedSummary": "http 401",
                    "retryRecommendation": "reauthenticate",
                },
            },
        }
    )

    assert (
        message
        == "Provider authentication failed with HTTP 401 for profile codex_default: "
        "http 401 (retryRecommendation: reauthenticate)"
    )


def test_activity_result_provider_failure_summary_includes_agent_runtime_reason() -> None:
    workflow = MoonMindRunWorkflow()

    message = workflow._activity_result_provider_failure_summary(
        {
            "status": "FAILED",
            "outputs": {
                "error": "execution_error",
                "summary": "codex app-server turn/completed produced no assistant output",
                "diagnosticsRef": "art_empty_turn",
                "profileId": "codex_default",
                "turnStatus": "failed",
                "turnMetadata": {
                    "failureCause": "app_server_protocol_empty_turn",
                    "retryRecommendedAction": "clear_session",
                },
            },
        }
    )

    assert message == (
        "Agent runtime failed with execution_error for profile codex_default "
        "due to app_server_protocol_empty_turn: codex app-server turn/completed "
        "produced no assistant output (retryRecommendedAction: clear_session; "
        "diagnosticsRef: art_empty_turn)"
    )

def test_activity_result_retryable_blocks_permanent_agent_runtime_failure() -> None:
    workflow = MoonMindRunWorkflow()

    retryable = workflow._activity_result_retryable(
        {
            "status": "FAILED",
            "outputs": {
                "error": "execution_error",
                "summary": (
                    "Your access token could not be refreshed because your "
                    "refresh token was revoked."
                ),
                "profileId": "codex_default",
                "turnStatus": "failed",
                "turnMetadata": {"failureClass": "permanent"},
            },
        },
        failure_message="execution_error",
        tool_type="agent_runtime",
    )

    assert retryable is False


def test_activity_result_retryable_allows_empty_assistant_turn_recovery() -> None:
    workflow = MoonMindRunWorkflow()

    retryable = workflow._activity_result_retryable(
        {
            "status": "FAILED",
            "outputs": {
                "error": "execution_error",
                "summary": "codex app-server turn/completed produced no assistant output",
                "profileId": "codex_default",
                "turnStatus": "failed",
                "turnMetadata": {
                    "failureClass": "transient",
                    "failureCause": "app_server_protocol_empty_turn",
                    "retryRecommendedAction": "clear_session",
                },
            },
        },
        failure_message="execution_error",
        tool_type="agent_runtime",
    )

    assert retryable is True


def test_agent_runtime_retry_classification_is_patch_gated() -> None:
    source = inspect.getsource(MoonMindRunWorkflow._run_execution_stage)

    assert "workflow.patched(RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH)" in source
    assert "self._activity_result_retryable(" in source


def test_activity_result_provider_failure_summary_ignores_generic_activity_failure() -> None:
    workflow = MoonMindRunWorkflow()

    message = workflow._activity_result_provider_failure_summary(
        {
            "status": "FAILED",
            "outputs": {
                "error": "execution_error",
                "summary": "activity exited with code 1",
            },
        }
    )

    assert message is None


@pytest.mark.asyncio
async def test_skipped_jira_blocker_wait_restores_executing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()

    async def fake_wait_condition(
        predicate: Callable[[], bool],
        **_kwargs: Any,
    ) -> None:
        assert predicate() is False
        workflow._bypass_dependencies(
            {"payload": {"reason": "Operator override from the dashboard."}}
        )
        assert predicate() is True

    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "logger",
        type(
            "Logger",
            (),
            {
                "warning": lambda *args, **kwargs: None,
                "info": lambda *args, **kwargs: None,
            },
        )(),
    )

    current_result, skipped = await workflow._wait_for_jira_blocker_resolution(
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "decision": "blocked",
                "summary": "Blocked by upstream Jira work.",
            },
        },
        agent_request=type("AgentRequest", (), {"agent_id": "agent-1"})(),
        node_id="check-blockers",
        tool_name=run_workflow_module.JIRA_CHECK_BLOCKERS_TOOL_NAME,
        tool_type="agent_runtime",
        failure_mode="FAIL_FAST",
    )

    assert skipped is True
    assert current_result["outputs"]["decision"] == "blocked"
    assert workflow._state == run_workflow_module.STATE_EXECUTING
    assert workflow._dependency_resolution == "bypassed"
    assert workflow._jira_blocker_wait_active is False
    assert workflow._waiting_reason is None

@pytest.mark.asyncio
async def test_run_execution_stage_fail_fast_raises_provider_failure_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    step_execution_artifact_ids = count(1)

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.create":
            artifact_create_result = _step_execution_artifact_create_result(
                payload,
                step_execution_artifact_ids,
            )
            if artifact_create_result is not None:
                return artifact_create_result
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        assert activity_type == "artifact.read"
        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Test Plan",
                    "created_at": "2026-03-12T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("a" * 64),
                        "artifact_ref": "artifact://registry/1",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "nodes": [
                    {
                        "id": "node-1",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "codex_cli",
                        },
                        "inputs": {
                            "instructions": "Resolve PR",
                            "repository": "MoonLadderStudios/MoonMind",
                            "runtime": {
                                "mode": "codex_cli",
                                "executionProfileRef": "codex_default",
                            },
                        },
                        "options": {},
                    }
                ],
                "edges": [],
            }
        ).encode("utf-8")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> object:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return await fake_execute_activity(activity_type, payload, **kwargs)

    async def fake_execute_child_workflow(*_args: object, **_kwargs: object) -> AgentRunResult:
        return AgentRunResult(
            summary="http 401",
            failureClass="user_error",
            providerErrorCode="401",
            retryRecommendation="reauthenticate",
            metadata={
                "profileId": "codex_default",
                "providerFailure": {
                    "providerErrorCode": "401",
                    "retryRecommendation": "reauthenticate",
                    "sanitizedSummary": "http 401",
                },
            },
        )

    async def fake_resolve_skillset_ref(_self: object, **_kwargs: object) -> str:
        return "art_skillset_1"

    async def fake_bind_workflow_scoped_session(_self: object, request: object) -> object:
        return request

    async def fake_fetch_profile_snapshots(_self: object) -> None:
        return None

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_resolve_agent_node_skillset_ref",
        fake_resolve_skillset_ref,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_fetch_profile_snapshots",
        fake_fetch_profile_snapshots,
    )

    with pytest.raises(
        ValueError,
        match=(
            "Provider authentication failed with HTTP 401 for profile "
            "codex_default: http 401"
        ),
    ):
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            plan_ref="art_plan_1",
        )


@pytest.mark.asyncio
async def test_run_execution_stage_fail_fast_raises_agent_runtime_failure_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    step_execution_artifact_ids = count(1)
    child_attempts = 0

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.create":
            artifact_create_result = _step_execution_artifact_create_result(
                payload,
                step_execution_artifact_ids,
            )
            if artifact_create_result is not None:
                return artifact_create_result
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        assert activity_type == "artifact.read"
        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Test Plan",
                    "created_at": "2026-03-12T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("a" * 64),
                        "artifact_ref": "artifact://registry/1",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "nodes": [
                    {
                        "id": "node-1",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "codex_cli",
                        },
                        "inputs": {
                            "instructions": "Resolve PR",
                            "repository": "MoonLadderStudios/MoonMind",
                            "runtime": {
                                "mode": "codex_cli",
                                "executionProfileRef": "codex_default",
                            },
                        },
                        "options": {},
                    }
                ],
                "edges": [],
            }
        ).encode("utf-8")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> object:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        if activity_type == "agent_runtime.load_session_snapshot":
            return {
                "binding": {
                    "workflowId": "wf-1:session:codex_cli",
                    "agentRunId": "wf-1",
                    "sessionId": "sess:wf-1:codex_cli",
                    "sessionEpoch": 2,
                    "runtimeId": "codex_cli",
                    "executionProfileRef": "codex_default",
                },
                "status": "active",
                "containerId": "container-1",
                "threadId": "thread-1",
                "activeTurnId": None,
                "lastControlAction": "clear_session",
                "lastControlReason": "retry_after_empty_assistant_output",
                "terminationRequested": False,
                "requestTrackingState": [],
            }
        if activity_type == "agent_runtime.clear_session":
            return {
                "runtimeFamily": "codex",
                "protocol": "codex_app_server",
                "containerBackend": "docker",
                "controlMode": "remote_container",
                "sessionState": {
                    "sessionId": "sess:wf-1:codex_cli",
                    "sessionEpoch": 3,
                    "containerId": "container-1",
                    "threadId": "thread-cleared",
                    "activeTurnId": None,
                },
                "status": "ready",
                "metadata": {},
            }
        if activity_type == "agent_runtime.terminate_session":
            return {
                "runtimeFamily": "codex",
                "protocol": "codex_app_server",
                "containerBackend": "docker",
                "controlMode": "remote_container",
                "sessionState": {
                    "sessionId": "sess:wf-1:codex_cli",
                    "sessionEpoch": 3,
                    "containerId": "container-1",
                    "threadId": "thread-cleared",
                    "activeTurnId": None,
                },
                "status": "terminated",
                "metadata": {},
            }
        return await fake_execute_activity(activity_type, payload, **kwargs)

    async def fake_execute_child_workflow(*_args: object, **_kwargs: object) -> AgentRunResult:
        nonlocal child_attempts
        child_attempts += 1
        return AgentRunResult(
            summary="codex app-server turn/completed produced no assistant output",
            failureClass="execution_error",
            diagnosticsRef="art_empty_turn",
            metadata={
                "profileId": "codex_default",
                "stateCheckpointRef": f"art_checkpoint_{child_attempts}",
                "turnStatus": "failed",
                "turnMetadata": {
                    "failureCause": "app_server_protocol_empty_turn",
                    "retryRecommendedAction": "clear_session",
                },
            },
        )

    async def fake_resolve_skillset_ref(_self: object, **_kwargs: object) -> str:
        return "art_skillset_1"

    async def fake_bind_workflow_scoped_session(_self: object, request: object) -> object:
        return request

    async def fake_fetch_profile_snapshots(_self: object) -> None:
        return None

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_resolve_agent_node_skillset_ref",
        fake_resolve_skillset_ref,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_fetch_profile_snapshots",
        fake_fetch_profile_snapshots,
    )

    with pytest.raises(
        ValueError,
        match=(
            "Agent runtime failed with execution_error for profile codex_default "
            "due to app_server_protocol_empty_turn: codex app-server "
            "turn/completed produced no assistant output"
        ),
    ):
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            plan_ref="art_plan_1",
        )

    assert child_attempts == 4


@pytest.mark.asyncio
async def test_run_execution_stage_does_not_retry_permanent_agent_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    step_execution_artifact_ids = count(1)
    child_attempts = 0

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        if activity_type == "artifact.create":
            artifact_create_result = _step_execution_artifact_create_result(
                payload,
                step_execution_artifact_ids,
            )
            if artifact_create_result is not None:
                return artifact_create_result
        if activity_type == "agent_runtime.capture_workspace_checkpoint":
            return _managed_checkpoint_capture_result(payload)
        if activity_type == "step_checkpoint.create":
            return _checkpoint_create_result(payload)
        assert activity_type == "artifact.read"
        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Test Plan",
                    "created_at": "2026-03-12T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("a" * 64),
                        "artifact_ref": "artifact://registry/1",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "nodes": [
                    {
                        "id": "node-1",
                        "tool": {"type": "agent_runtime", "name": "codex_cli"},
                        "inputs": {
                            "instructions": "Resolve PR",
                            "repository": "MoonLadderStudios/MoonMind",
                            "runtime": {
                                "mode": "codex_cli",
                                "executionProfileRef": "codex_default",
                            },
                        },
                        "options": {},
                    }
                ],
                "edges": [],
            }
        ).encode("utf-8")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> object:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return await fake_execute_activity(activity_type, payload, **kwargs)

    async def fake_execute_child_workflow(
        *_args: object,
        **_kwargs: object,
    ) -> AgentRunResult:
        nonlocal child_attempts
        child_attempts += 1
        return AgentRunResult(
            summary=(
                "Your access token could not be refreshed because your refresh "
                "token was revoked."
            ),
            failureClass="execution_error",
            metadata={
                "profileId": "codex_default",
                "turnStatus": "failed",
                "turnMetadata": {"failureClass": "permanent"},
            },
        )

    async def fake_resolve_skillset_ref(_self: object, **_kwargs: object) -> str:
        return "art_skillset_1"

    async def fake_bind_workflow_scoped_session(_self: object, request: object) -> object:
        return request

    async def fake_fetch_profile_snapshots(_self: object) -> None:
        return None

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_resolve_agent_node_skillset_ref",
        fake_resolve_skillset_ref,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_fetch_profile_snapshots",
        fake_fetch_profile_snapshots,
    )

    with pytest.raises(
        ValueError,
        match=(
            "Agent runtime failed with execution_error for profile codex_default: "
            "Your access token could not be refreshed"
        ),
    ):
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            plan_ref="art_plan_1",
        )

    assert child_attempts == 1

def test_blocked_outcome_message_detects_structured_agent_report() -> None:
    workflow = MoonMindRunWorkflow()

    message = workflow._blocked_outcome_message(
        {
            "status": "COMPLETED",
            "outputs": {
                "operator_summary": """
```json
{
  "targetIssueKey": "THOR-354",
  "decision": "blocked",
  "blockingIssues": [
    {"issueKey": "THOR-355", "status": "Backlog"}
  ],
  "summary": "THOR-354 is blocked by THOR-355."
}
```
""",
                "push_status": "no_commits",
            },
        }
    )

    assert message == "Workflow blocked by plan step: THOR-354 is blocked by THOR-355."

def test_blocked_outcome_message_detects_nested_json_code_fence() -> None:
    workflow = MoonMindRunWorkflow()

    message = workflow._blocked_outcome_message(
        {
            "status": "COMPLETED",
            "outputs": {
                "operator_summary": """
```json
{
  "decision": "blocked",
  "details": {"source": "jira", "executionOrdinal": 1},
  "summary": "Nested structured blocker parsed."
}
```
""",
            },
        }
    )

    assert message == "Workflow blocked by plan step: Nested structured blocker parsed."

def test_publish_completion_reports_blocked_outcome_without_pr_failure() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._integration = None
    workflow._publish_status = "not_required"
    workflow._publish_reason = "Workflow blocked by plan step: blocked upstream."
    workflow._plan_blocked_message = (
        "Workflow blocked by plan step: blocked upstream."
    )

    status, reason, failed = workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert reason == "Workflow blocked by plan step: blocked upstream."
    assert failed is True

@pytest.mark.asyncio
async def test_run_marks_blocked_outcome_as_failed_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    blocker_summary = "Workflow blocked by plan step: blocked upstream."
    finalizing_calls: list[dict[str, Any]] = []
    terminal_calls: list[dict[str, Any]] = []
    states: list[tuple[str, str | None]] = []

    def fake_initialize(
        _self: MoonMindRunWorkflow,
        _payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any], None, str, None]:
        _self._owner_type = "user"
        _self._owner_id = "owner-1"
        _self._entry = "run"
        return (
            "MoonMind.UserWorkflow",
            {"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
            None,
            "art_plan_1",
            None,
        )

    async def fake_run_execution_stage(
        self: MoonMindRunWorkflow,
        *,
        parameters: dict[str, Any],
        plan_ref: str | None,
    ) -> None:
        self._plan_blocked_message = blocker_summary
        self._publish_status = "not_required"
        self._publish_reason = blocker_summary

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_run_finalizing_stage(
        self: MoonMindRunWorkflow,
        *,
        parameters: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> None:
        finalizing_calls.append({"status": status, "error": error})

    async def fake_record_terminal_state(
        self: MoonMindRunWorkflow,
        *,
        state: str,
        close_status: str,
        summary: str | None,
        error_category: str | None = None,
    ) -> None:
        terminal_calls.append(
            {
                "state": state,
                "close_status": close_status,
                "summary": summary,
                "error_category": error_category,
            }
        )

    original_set_state = MoonMindRunWorkflow._set_state

    def capture_set_state(
        self: MoonMindRunWorkflow,
        state: str,
        summary: str | None = None,
    ) -> None:
        states.append((state, summary))
        original_set_state(self, state, summary)

    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-blocked",
            "run_id": "run-blocked",
            "task_queue": "mm.workflow",
            "search_attributes": {},
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: True,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_initialize_from_payload",
        fake_initialize,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_run_execution_stage",
        fake_run_execution_stage,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_run_proposals_stage",
        fake_noop_async,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_run_finalizing_stage",
        fake_run_finalizing_stage,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_record_terminal_state",
        fake_record_terminal_state,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_set_state", capture_set_state)

    with pytest.raises(run_workflow_module.exceptions.ApplicationError) as exc_info:
        await workflow.run({"workflowType": "MoonMind.UserWorkflow"})

    assert str(exc_info.value) == blocker_summary
    assert finalizing_calls == [{"status": "failed", "error": blocker_summary}]
    assert terminal_calls == [
        {
            "state": "failed",
            "close_status": "failed",
            "summary": blocker_summary,
            "error_category": "user_error",
        }
    ]
    assert states[-1] == ("failed", blocker_summary)
    assert workflow._close_status == "failed"

@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_stage", ["proposals", "finalizing"])
async def test_run_observes_cancel_after_late_stages_before_completion(
    monkeypatch: pytest.MonkeyPatch,
    cancel_stage: str,
) -> None:
    workflow = MoonMindRunWorkflow()
    finalizing_calls: list[dict[str, str | None]] = []
    terminal_calls: list[dict[str, str | None]] = []
    states: list[tuple[str, str | None]] = []

    def fake_initialize(
        _self: MoonMindRunWorkflow,
        _payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str, str, None]:
        _self._owner_type = "user"
        _self._owner_id = "owner-1"
        _self._entry = "run"
        return (
            "MoonMind.UserWorkflow",
            {"repo": "MoonLadderStudios/MoonMind"},
            "artifact://input/1",
            "artifact://plan/1",
            None,
        )

    async def fake_run_planning_stage(*_args: Any, **_kwargs: Any) -> str:
        return "artifact://plan/1"

    async def fake_run_execution_stage(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_run_proposals_stage(
        self: MoonMindRunWorkflow,
        *,
        parameters: dict[str, Any],
    ) -> None:
        if cancel_stage == "proposals":
            self._cancel_requested = True

    async def fake_run_finalizing_stage(
        self: MoonMindRunWorkflow,
        *,
        parameters: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> None:
        finalizing_calls.append({"status": status, "error": error})
        if cancel_stage == "finalizing":
            self._cancel_requested = True

    async def fake_record_terminal_state(
        self: MoonMindRunWorkflow,
        *,
        state: str,
        close_status: str,
        summary: str | None,
        error_category: str | None = None,
    ) -> None:
        terminal_calls.append(
            {
                "state": state,
                "close_status": close_status,
                "summary": summary,
                "error_category": error_category,
            }
        )

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    original_set_state = MoonMindRunWorkflow._set_state

    def capture_set_state(
        self: MoonMindRunWorkflow,
        state: str,
        summary: str | None = None,
    ) -> None:
        states.append((state, summary))
        original_set_state(self, state, summary)

    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-cancel-after-proposals",
            "run_id": "run-cancel-after-proposals",
            "task_queue": "mm.workflow",
            "search_attributes": {},
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_initialize_from_payload", fake_initialize)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_run_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_run_execution_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_proposals_stage", fake_run_proposals_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_finalizing_stage", fake_run_finalizing_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_record_terminal_state", fake_record_terminal_state)
    monkeypatch.setattr(MoonMindRunWorkflow, "_set_state", capture_set_state)

    result = await workflow.run({"workflowType": "MoonMind.UserWorkflow"})

    assert result == {"status": "canceled"}
    expected_finalizing_status = "canceled" if cancel_stage == "proposals" else "success"
    assert finalizing_calls == [{"status": expected_finalizing_status, "error": None}]
    assert terminal_calls == [
        {
            "state": "canceled",
            "close_status": "canceled",
            "summary": "Execution canceled.",
            "error_category": None,
        }
    ]
    assert states[-1] == ("canceled", "Execution canceled.")
    assert ("completed", "Workflow completed successfully") not in states


@pytest.mark.asyncio
async def test_run_records_no_commit_terminal_state_for_skipped_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    finalizing_calls: list[dict[str, str | None]] = []
    terminal_calls: list[dict[str, str | None]] = []
    states: list[tuple[str, str | None]] = []

    def fake_initialize(
        _self: MoonMindRunWorkflow,
        _payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str, str, None]:
        _self._owner_type = "user"
        _self._owner_id = "owner-1"
        _self._entry = "run"
        return (
            "MoonMind.UserWorkflow",
            {"publishMode": "none", "repo": "MoonLadderStudios/MoonMind"},
            "artifact://input/1",
            "artifact://plan/1",
            None,
        )

    async def fake_run_planning_stage(*_args: Any, **_kwargs: Any) -> str:
        return "artifact://plan/1"

    async def fake_run_execution_stage(
        self: MoonMindRunWorkflow,
        *,
        parameters: dict[str, Any],
        plan_ref: str | None,
    ) -> None:
        self._publish_status = "skipped"
        self._publish_reason = "No repository changes were produced."

    async def fake_run_finalizing_stage(
        self: MoonMindRunWorkflow,
        *,
        parameters: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> None:
        finalizing_calls.append({"status": status, "error": error})

    async def fake_record_terminal_state(
        self: MoonMindRunWorkflow,
        *,
        state: str,
        close_status: str,
        summary: str | None,
        error_category: str | None = None,
    ) -> None:
        terminal_calls.append(
            {
                "state": state,
                "close_status": close_status,
                "summary": summary,
                "error_category": error_category,
            }
        )

    async def fake_noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    original_set_state = MoonMindRunWorkflow._set_state

    def capture_set_state(
        self: MoonMindRunWorkflow,
        state: str,
        summary: str | None = None,
    ) -> None:
        states.append((state, summary))
        original_set_state(self, state, summary)

    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-no-commit",
            "run_id": "run-no-commit",
            "task_queue": "mm.workflow",
            "search_attributes": {},
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_initialize_from_payload", fake_initialize)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_wait_if_paused_at_safe_boundary",
        fake_noop_async,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_run_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_run_execution_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_proposals_stage", fake_noop_async)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_finalizing_stage", fake_run_finalizing_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_record_terminal_state", fake_record_terminal_state)
    monkeypatch.setattr(MoonMindRunWorkflow, "_set_state", capture_set_state)

    result = await workflow.run({"workflowType": "MoonMind.UserWorkflow"})

    assert result == {
        "status": "no_commit",
        "message": "No repository commit was needed.",
    }
    assert finalizing_calls == [
        {"status": "success", "error": "No repository changes were produced."}
    ]
    assert terminal_calls == [
        {
            "state": "no_commit",
            "close_status": "completed",
            "summary": "No repository commit was needed.",
            "error_category": None,
        }
    ]
    assert states[-1] == ("no_commit", "No repository commit was needed.")
    assert ("completed", "No repository commit was needed.") not in states
    assert workflow._close_status == "completed"

def test_publish_completion_requires_pr_url_for_pr_publish_mode() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._publish_status = "published"
    workflow._publish_reason = "Jira issue output succeeded; no PR output required"
    workflow._pull_request_url = None

    status, reason, failed = workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert reason == "publishMode 'pr' requested but no PR was created"
    assert failed is True

@pytest.mark.parametrize(
    ("mode", "story_status"),
    [
        ("jira", "jira_created"),
        ("jira", "jira_partial"),
        ("github", "github_created"),
        ("github", "github_partial"),
        ("github", "github_noop"),
    ],
)
def test_story_output_status_satisfies_pr_publish(mode: str, story_status: str) -> None:
    assert MoonMindRunWorkflow._is_successful_jira_story_output(
        mode=mode,
        status=story_status,
    )


def test_story_output_status_rejects_cross_mode_status() -> None:
    assert not MoonMindRunWorkflow._is_successful_jira_story_output(
        mode="jira",
        status="github_created",
    )
    assert not MoonMindRunWorkflow._is_successful_jira_story_output(
        mode="github",
        status="jira_created",
    )


def test_pr_publish_optional_for_task_requires_all_skills_pr_optional() -> None:
    workflow = MoonMindRunWorkflow()

    pure_task = {
        "workflow": {"skills": {"include": [{"name": "jira-implement"}]}}
    }
    mixed_task = {
        "workflow": {
            "skills": {
                "include": [
                    {"name": "jira-implement"},
                    {"name": "general-coder"},
                ]
            }
        }
    }
    empty_task = {"workflow": {"skills": {"include": []}}}

    assert workflow._pr_publish_optional_for_task(pure_task) is True
    assert workflow._pr_publish_optional_for_task(mixed_task) is False
    assert workflow._pr_publish_optional_for_task(empty_task) is False


def test_publish_not_required_reason_reads_flattened_outputs() -> None:
    workflow = MoonMindRunWorkflow()

    reason = workflow._publish_not_required_reason(
        {
            "publish_status": "not_required",
            "publish_reason": "Jira issue agent completed; no PR output required",
        }
    )

    assert reason == "Jira issue agent completed; no PR output required"


def test_publish_not_required_reason_reads_required_false_in_outputs() -> None:
    workflow = MoonMindRunWorkflow()

    reason = workflow._publish_not_required_reason(
        {
            "required": False,
            "summary": "Plan blocked upstream; nothing to publish.",
        }
    )

    assert reason == "Plan blocked upstream; nothing to publish."


def test_publish_not_required_reason_ignores_unrelated_status_in_outputs() -> None:
    workflow = MoonMindRunWorkflow()

    reason = workflow._publish_not_required_reason(
        {"status": "COMPLETED", "summary": "Agent finished."}
    )

    assert reason is None


@pytest.mark.asyncio
async def test_run_execution_stage_jira_implement_not_required_skips_native_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}
        if activity_type == "agent_skill.resolve":
            return {"manifestRef": "art_skill_snapshot_1"}

        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Jira Implement",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "tpl:jira-implement:1.0.0:08:abc12345",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "claude",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude"},
                                "selectedSkill": "auto",
                                "annotations": {
                                    "jiraImplementRole": "final-transition",
                                },
                                "instructions": "Finalize Jira status.",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Completed with status completed",
            "metadata": {
                "operator_summary": "MM-697 was transitioned to Done.",
                "push_status": "no_commits",
                "push_branch": "jira-implement-mm-697",
                "push_base_ref": "origin/main",
                "push_commit_count": 0,
            },
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.NATIVE_PR_BRANCH_DEFAULTS_PATCH,
        },
    )

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-697"},
            "workflow": {
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.0.0",
                        "composition": {"includes": []},
                    }
                ],
            },
        },
        plan_ref="art_plan_1",
    )

    assert not create_pr_called
    assert workflow._publish_status == "not_required"
    assert workflow._publish_context["mergeAutomationStatus"] == "not_applicable"


@pytest.mark.asyncio
async def test_run_execution_stage_reopens_pr_gate_after_later_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for workflow mm:ede07a85-049a-4860-ae0b-b016c488cda0."""

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/Tactics"
    workflow._canonical_no_commit_outcome_enabled = True
    workflow._authoritative_publish_outcome_enabled = True
    create_pr_payloads: list[dict[str, object]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> object:
        if activity_type == "repo.create_pr":
            assert payload is not None
            create_pr_payloads.append(payload)
            return {
                "url": "https://github.com/MoonLadderStudios/Tactics/pull/2240",
                "created": True,
                "headSha": "abc123",
            }
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "GitHub Issue Implement",
                        "created_at": "2026-07-21T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        _agent_runtime_step("assess"),
                        _agent_runtime_step("publish"),
                    ],
                    "edges": [{"from": "assess", "to": "publish"}],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    child_calls = 0

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal child_calls
        child_calls += 1
        if child_calls == 1:
            return {
                "summary": "Assessment completed without repository changes.",
                "metadata": {
                    "push_status": "no_commits",
                    "push_branch": "feature/issue-2231",
                    "push_commit_count": 0,
                },
                "output_refs": [],
            }
        return {
            "summary": "Implementation branch published.",
            "metadata": {
                "push_status": "pushed",
                "push_branch": "feature/issue-2231",
                "push_commit_count": 6,
                "push_head_sha": "abc123",
            },
            "output_refs": [],
        }

    async def bind_publish_request(request: object) -> object:
        return request

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        workflow,
        "_maybe_bind_workflow_scoped_session",
        bind_publish_request,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.RUN_AUTHORITATIVE_PR_REQUIREMENT_PATCH,
            run_workflow_module.RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
        },
    )

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/Tactics",
            "publishMode": "pr",
            "workflow": {
                "appliedStepTemplates": [
                    {
                        "slug": "github-issue-implement",
                        "version": "1.0.0",
                        "composition": {"includes": []},
                    }
                ]
            },
        },
        plan_ref="art_plan_1",
    )

    assert child_calls == 2
    assert len(create_pr_payloads) == 1
    assert create_pr_payloads[0]["head"] == "feature/issue-2231"
    assert workflow._pull_request_url == (
        "https://github.com/MoonLadderStudios/Tactics/pull/2240"
    )
    assert workflow._publish_context["commitCount"] == 6
    assert "noCommitPublish" not in workflow._publish_context


def test_authoritative_pr_requirement_prefers_commits_over_stale_story_output() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._integration = None
    workflow._publish_status = "published"
    workflow._publish_context = {
        "storyOutputMode": "github",
        "commitCount": 2,
    }

    assert workflow._authoritative_pr_requirement(
        publish_mode="pr",
        pr_publish_optional=False,
    ) is True


@pytest.mark.asyncio
async def test_run_execution_stage_moonspec_verify_blocks_native_pr_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}
        if activity_type == "agent_skill.resolve":
            return {
                "manifestRef": "art_skill_snapshot_1",
                "skills": [{"name": "moonspec-verify"}],
            }

        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            ) if payload is not None else None
            if artifact_ref == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "auto",
                                "description": "Auto",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "MoonSpec Verify",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "tpl:jira-orchestrate:1.0.0:13:verify",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex"},
                                "selectedSkill": "moonspec-verify",
                                "targetBranch": "generated-target",
                                "instructions": "Run MoonSpec verify.",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": (
                "Opened https://github.com/MoonLadderStudios/MoonMind/pull/999. "
                "Verdict: ADDITIONAL_WORK_NEEDED"
            ),
            "metadata": {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "operator_summary": "Overview route still renders full detail sections.",
                "diagnostics_ref": "art_verify_report",
                "push_status": "pushed",
                "branch": "804-workflow-detail-tabs",
                "baseRef": "origin/main",
            },
            "output_refs": [],
        }

    async def fake_bind_workflow_scoped_session(
        self: MoonMindRunWorkflow,
        request: object,
    ) -> object:
        return request

    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.NATIVE_PR_BRANCH_DEFAULTS_PATCH,
            run_workflow_module.RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH,
            run_workflow_module.RUN_MOONSPEC_VERIFY_REMEDIATION_INDEX_PATCH,
        },
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    status, message, publish_failure = workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert not create_pr_called
    assert workflow._pull_request_url is None
    assert workflow._publish_status == "not_required"
    assert workflow._publish_context["publicationBlockedBy"] == "moonspec_verify"
    assert status == "failed"
    assert publish_failure is True
    assert "MoonSpec verification did not approve publication" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recovery_handoff_enabled", "expect_create_pr"),
    [(False, False), (True, True)],
)
async def test_run_execution_stage_additional_work_publishes_pushed_branch_as_draft(
    monkeypatch: pytest.MonkeyPatch,
    recovery_handoff_enabled: bool,
    expect_create_pr: bool,
) -> None:
    """A skipped normal handoff must not suppress the recovery draft."""
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    workflow._publish_status = "not_required"
    workflow._publish_reason = "Earlier issue update required no PR output."
    create_pr_payload: dict[str, object] | None = None

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_payload
        if activity_type == "repo.create_pr":
            create_pr_payload = dict(payload or {})
            return {
                "url": "https://github.com/MoonLadderStudios/MoonMind/pull/999",
                "created": True,
                "adopted": False,
                "headSha": "abc123",
            }
        if activity_type == "agent_skill.resolve":
            return {
                "manifestRef": "art_skill_snapshot_1",
                "skills": [{"name": "moonspec-verify"}],
            }
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            ) if payload is not None else None
            if artifact_ref == "artifact://registry/1":
                return json.dumps({"skills": []}).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "MoonSpec exhausted remediation draft",
                        "created_at": "2026-07-17T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {
                        "failure_mode": "FAIL_FAST",
                        "max_concurrency": 1,
                    },
                    "nodes": [
                        {
                            "id": "verify-remediation-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex"},
                                "selectedSkill": "moonspec-verify",
                                "targetBranch": "partial-work",
                                "title": "Verify remediation attempt 1 of 1",
                                "annotations": {
                                    "issueImplementRole": (
                                        "moonspec-verification-gate"
                                    ),
                                    "moonSpecRemediationAttempt": 1,
                                    "moonSpecRemediationMaxAttempts": 1,
                                    "moonSpecFinalRemediationGate": True,
                                },
                                "instructions": "Verify the final remediation.",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _request: object,
        **_kwargs: object,
    ) -> object:
        push_info = {
            "push_status": "pushed",
            "push_branch": "partial-work",
            "push_base_branch": "main",
            "push_base_ref": "origin/main",
            "push_commit_count": 2,
            "push_head_sha": "abc123",
        }
        accepted_repository_evidence = (
            TemporalAgentRuntimeActivities._accepted_repository_evidence(push_info)
        )
        assert accepted_repository_evidence is not None
        return {
            "summary": "Verdict: ADDITIONAL_WORK_NEEDED",
            "metadata": {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "recommendedNextAction": "reattempt_current_step",
                "operator_summary": "One retry-stability gap remains.",
                "diagnostics_ref": "art_verify_remaining_work",
                **push_info,
                "acceptedRepositoryEvidence": accepted_repository_evidence,
            },
            "output_refs": [],
        }

    async def fake_bind_workflow_scoped_session(
        self: MoonMindRunWorkflow,
        request: object,
    ) -> object:
        return request

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    enabled_patches = {
        run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
        run_workflow_module.NATIVE_PR_BRANCH_DEFAULTS_PATCH,
        run_workflow_module.RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH,
        run_workflow_module.RUN_MOONSPEC_VERIFY_REMEDIATION_INDEX_PATCH,
        run_workflow_module.RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH,
        run_workflow_module.RUN_MOONSPEC_ADDITIONAL_WORK_DRAFT_PUBLISH_PATCH,
    }
    if recovery_handoff_enabled:
        enabled_patches.add(
            run_workflow_module.RUN_MOONSPEC_DRAFT_PUBLISH_RECOVERY_HANDOFF_PATCH
        )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id in enabled_patches,
    )

    await workflow._run_execution_stage(
        parameters={
            "repo": "MoonLadderStudios/MoonMind",
            "publishMode": "pr",
        },
        plan_ref="art_plan_1",
    )

    if not expect_create_pr:
        assert create_pr_payload is None
        assert workflow._pull_request_url is None
        assert workflow._publish_status == "not_required"
        assert workflow._publish_context["moonSpecDraftPublication"]["policy"] == (
            "draft_pr_on_additional_work_needed"
        )
        return

    assert create_pr_payload is not None
    assert create_pr_payload["head"] == "partial-work"
    assert create_pr_payload["base"] == "main"
    assert create_pr_payload["draft"] is True
    assert "MoonSpec verification incomplete" in str(create_pr_payload["body"])
    assert workflow._pull_request_url == (
        "https://github.com/MoonLadderStudios/MoonMind/pull/999"
    )
    assert workflow._publish_status == "published"
    assert workflow._publish_context["moonSpecDraftPublication"]["policy"] == (
        "draft_pr_on_additional_work_needed"
    )


@pytest.mark.asyncio
async def test_run_execution_stage_moonspec_verify_uses_remaining_remediation_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    create_pr_called = False
    executed_steps: list[str] = []

    def _request_step_id(request: object) -> str:
        parameters = getattr(request, "parameters", {})
        metadata = parameters.get("metadata") if isinstance(parameters, dict) else {}
        moonmind = metadata.get("moonmind") if isinstance(metadata, dict) else {}
        step_ledger = moonmind.get("stepLedger") if isinstance(moonmind, dict) else {}
        return str(step_ledger.get("logicalStepId") or "")

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}
        if activity_type == "agent_skill.resolve":
            return {
                "manifestRef": "art_skill_snapshot_1",
                "skills": [
                    {"name": "moonspec-implement"},
                    {"name": "moonspec-verify"},
                ],
            }

        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            ) if payload is not None else None
            if artifact_ref == "artifact://registry/1":
                return json.dumps({"skills": []}).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "MoonSpec Remediation Budget",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "tpl:jira-orchestrate:1.0.0:21:verify",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex"},
                                "selectedSkill": "moonspec-verify",
                                "instructions": "Verify remediation attempt 5.",
                            },
                            "options": {},
                        },
                        {
                            "id": "tpl:jira-orchestrate:1.0.0:22:remediate",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex"},
                                "selectedSkill": "moonspec-implement",
                                "title": "Remediate remaining gaps — attempt 6 of 6",
                                "annotations": {
                                    "jiraOrchestrateRole": "moonspec-remediation",
                                },
                                "instructions": "Remediate remaining gaps attempt 6.",
                            },
                            "options": {},
                        },
                        {
                            "id": "tpl:jira-orchestrate:1.0.0:23:verify",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex"},
                                "selectedSkill": "moonspec-verify",
                                "instructions": "Verify remediation attempt 6.",
                            },
                            "options": {},
                        },
                    ],
                    "edges": [
                        {
                            "from": "tpl:jira-orchestrate:1.0.0:21:verify",
                            "to": "tpl:jira-orchestrate:1.0.0:22:remediate",
                        },
                        {
                            "from": "tpl:jira-orchestrate:1.0.0:22:remediate",
                            "to": "tpl:jira-orchestrate:1.0.0:23:verify",
                        },
                    ],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        request: object,
        **_kwargs: object,
    ) -> object:
        step_id = _request_step_id(request)
        executed_steps.append(step_id)
        if step_id.endswith(":21:verify"):
            return {
                "summary": "Verdict: ADDITIONAL_WORK_NEEDED",
                "metadata": {
                    "verdict": "ADDITIONAL_WORK_NEEDED",
                    "operator_summary": "One remediation attempt remains.",
                    "diagnostics_ref": "art_verify_5",
                    "push_status": "pushed",
                    "push_branch": "209-botplanner-trace-contracts",
                    "push_base_ref": "origin/main",
                },
                "output_refs": [],
            }
        if step_id.endswith(":23:verify"):
            return {
                "summary": "Verdict: FULLY_IMPLEMENTED",
                "metadata": {
                    "verdict": "FULLY_IMPLEMENTED",
                    "operator_summary": "Verification passed.",
                    "diagnostics_ref": "art_verify_6",
                    "push_status": "pushed",
                    "push_branch": "209-botplanner-trace-contracts",
                    "push_base_ref": "origin/main",
                },
                "output_refs": [],
            }
        return {
            "summary": "Remediation completed.",
            "metadata": {
                "operator_summary": "Remediation completed.",
                "push_status": "pushed",
                "push_branch": "209-botplanner-trace-contracts",
                "push_base_ref": "origin/main",
            },
            "output_refs": [],
        }

    async def fake_bind_workflow_scoped_session(
        self: MoonMindRunWorkflow,
        request: object,
    ) -> object:
        return request

    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.NATIVE_PR_BRANCH_DEFAULTS_PATCH,
            run_workflow_module.RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH,
            run_workflow_module.RUN_MOONSPEC_VERIFY_REMEDIATION_INDEX_PATCH,
        },
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert executed_steps == [
        "tpl:jira-orchestrate:1.0.0:21:verify",
        "tpl:jira-orchestrate:1.0.0:22:remediate",
        "tpl:jira-orchestrate:1.0.0:23:verify",
    ]
    assert create_pr_called
    assert workflow._publish_context["moonSpecGate"]["verdict"] == (
        "FULLY_IMPLEMENTED"
    )
    assert "publicationBlockedBy" not in workflow._publish_context


@pytest.mark.asyncio
async def test_run_execution_stage_publish_mode_pr_jules_skips_native_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True

        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "jules",
                                "description": "Jules",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Publish Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "jules",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch", "runtime": {"mode": "jules"}},
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {
            "status": "COMPLETED",
            "outputs": {
                "stdout_tail": "Skipped PR native creation.",
            },
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Agent finished",
            "metadata": {"jules_session_id": "session-123", "branch": "jules-test-branch"},
            "output_refs": []
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert not create_pr_called, "repo.create_pr should not have been called"


@pytest.mark.asyncio
async def test_run_execution_stage_jules_pr_extracts_url_from_diagnostics_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True

        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "diag-1":
                return (
                    "Jules completed and created "
                    "https://github.com/MoonLadderStudios/MoonMind/pull/679"
                )
            if artifact_ref == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "jules",
                                "description": "Jules",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Publish Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "jules",
                            },
                            "inputs": {
                                "repo_ref": "git:org/repo#branch",
                                "runtime": {"mode": "jules"},
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Agent finished",
            "diagnostics_ref": "diag-1",
            "metadata": {"jules_session_id": "session-123"},
            "output_refs": [],
        }

    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert not create_pr_called, "repo.create_pr should not have been called"
    assert workflow._pull_request_url == (
        "https://github.com/MoonLadderStudios/MoonMind/pull/679"
    )
    assert workflow._publish_status == "published"


@pytest.mark.asyncio
async def test_run_execution_stage_non_jules_agent_with_session_id_creates_native_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: a non-Jules agent_runtime (e.g. Claude) that returns
    jules_session_id in result metadata must NOT suppress native PR creation.

    Root cause: agent_run.py unconditionally injected jules_session_id into
    every AgentRunResult.metadata, and run.py used ``or jules_session_id`` to
    skip PR creation.  This test ensures the fix works: the PR-skip guard
    should NOT trigger for non-Jules agents even when the metadata contains
    a jules_session_id.
    """
    wf = MoonMindRunWorkflow()
    wf._owner_id = "owner-1"
    wf._repo = "MoonLadderStudios/MoonMind"
    wf._title = "Automated changes"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999", "created": True}

        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "auto",
                                "description": "Auto",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "node-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                                "instructions": "Remove spec from the specs directory",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        # Simulates a Claude agent result that spuriously includes
        # jules_session_id (the old bug).
        return {
            "summary": "Completed with status completed",
            "metadata": {
                "push_status": "pushed",
                "push_branch": "auto-0526d401",
                "branch": "auto-0526d401",
                "jules_session_id": "cb4cca35-fake-session-id",
            },
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await wf._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert create_pr_called, (
        "repo.create_pr SHOULD have been called for a non-Jules agent "
        "even when child result metadata contains jules_session_id"
    )


@pytest.mark.asyncio
async def test_run_execution_stage_skips_native_pr_after_prepublication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "MoonLadderStudios/MoonMind"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}
        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Checkpoint Publish Plan",
                        "created_at": "2026-07-14T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [_agent_runtime_step("assess")],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Assessment completed",
            "metadata": {
                "push_status": "pushed",
                "push_branch": "feature/checkpoint-failure",
            },
            "output_refs": [],
        }

    async def fail_prepublication_checkpoint(
        _logical_step_id: str,
        *,
        publish_mode: str,
        updated_at: datetime,
    ) -> bool:
        assert publish_mode == "pr"
        workflow._publish_status = "failed"
        workflow._publish_reason = "pre-publication checkpoint failed"
        return True

    async def no_checkpoint(*_args: object, **_kwargs: object) -> None:
        return None

    async def bind_existing_request(request: object) -> object:
        return request

    monkeypatch.setattr(
        workflow,
        "_record_prepublication_checkpoint",
        fail_prepublication_checkpoint,
    )
    monkeypatch.setattr(workflow, "_record_canonical_step_checkpoint", no_checkpoint)
    monkeypatch.setattr(
        workflow,
        "_maybe_bind_workflow_scoped_session",
        bind_existing_request,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {
            run_workflow_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
            run_workflow_module.RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
            run_workflow_module.RUN_PREPUBLICATION_FAILURE_BLOCKS_PUBLISH_PATCH,
        },
    )

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert not create_pr_called
    assert workflow._publish_status == "failed"
    assert workflow._pull_request_url is None


@pytest.mark.asyncio
async def test_run_execution_stage_skips_native_pr_after_push_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = MoonMindRunWorkflow()
    wf._owner_id = "owner-1"
    wf._repo = "MoonLadderStudios/MoonMind"
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}

        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "auto",
                                "description": "Auto",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {"capabilities": ["sandbox"]},
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 1800,
                                        "schedule_to_close_seconds": 3600,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Test Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "node-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude"},
                                "instructions": "Do work",
                            },
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: object,
    ) -> object:
        return {
            "summary": "Completed with status completed",
            "metadata": {
                "push_status": "protected_branch",
                "push_branch": "main",
            },
            "output_refs": [],
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await wf._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert not create_pr_called
    assert wf._publish_status == "failed"
    assert wf._publish_reason == "publish failed: working branch 'main' is protected"

@pytest.mark.asyncio
async def test_run_execution_stage_stops_after_publish_lease_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = MoonMindRunWorkflow()
    wf._owner_id = "owner-1"
    wf._repo = "MoonLadderStudios/MoonMind"
    child_workflow_ids: list[str] = []
    create_pr_called = False

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal create_pr_called
        if activity_type == "repo.create_pr":
            create_pr_called = True
            return {"url": "https://github.com/MoonLadderStudios/MoonMind/pull/999"}

        if activity_type == "artifact.read":
            return json.dumps(
                {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Two Step Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:" + ("a" * 64),
                            "artifact_ref": "artifact://registry/1",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "node-1",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude"},
                                "instructions": "Create spec artifacts",
                            },
                            "options": {},
                        },
                        {
                            "id": "node-2",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "auto",
                            },
                            "inputs": {
                                "runtime": {"mode": "claude"},
                                "instructions": "Implement the spec",
                            },
                            "options": {},
                        },
                    ],
                    "edges": [{"from": "node-1", "to": "node-2"}],
                }
            ).encode("utf-8")
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **kwargs: object,
    ) -> object:
        child_workflow_ids.append(str(kwargs.get("id") or ""))
        return {
            "summary": "Completed with status completed",
            "metadata": {
                "push_status": "lease_conflict",
                "push_branch": "feature/stale",
                "push_error": "! [rejected] feature/stale (stale info)",
                "diagnostic_kind": "publish_lease_conflict",
                "retryable": True,
            },
            "output_refs": [],
        }

    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)

    await wf._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind", "publishMode": "pr"},
        plan_ref="art_plan_1",
    )

    assert len(child_workflow_ids) == 1
    assert child_workflow_ids[0].endswith(":agent:node-1")
    assert not create_pr_called
    assert wf._publish_status == "failed"
    assert "remote branch 'feature/stale' changed" in (wf._publish_reason or "")
    steps = wf.get_step_ledger()["steps"]
    assert steps[0]["status"] == "failed"
    assert steps[0]["lastError"] == "publish_failed"
    assert steps[1]["status"] == "skipped"

@pytest.mark.asyncio
async def test_run_proposals_stage_global_disable_halts_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(settings.workflow, "enable_proposals", False)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda x: True)
    
    # Enable proposing tasks in params, but global switch should stop it
    await workflow._run_proposals_stage(parameters={"proposeTasks": True})
    
    assert workflow._state == "initializing"  # State shouldn't change to PROPOSALS

@pytest.mark.asyncio
async def test_run_proposals_stage_ignores_legacy_fallback_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings
    monkeypatch.setattr(settings.workflow, "enable_proposals", True)

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._owner_type = "user"
    workflow_info = type("WorkflowInfo", (), {"workflow_id": "wf-1", "run_id": "run-1", "namespace": "default"})()
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda x: True)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    
    captured_policy = None
    async def mock_execute_activity(activity, payload, **kwargs):
        nonlocal captured_policy
        if activity == "proposal.generate":
            return [{"title": "t1", "summary": "s1", "workflowCreateRequest": {}}]
        if activity == "proposal.submit":
            captured_policy = payload["policy"]
            return {"submitted_count": 1, "errors": []}
        return None
            
    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", mock_execute_activity)
    
    await workflow._run_proposals_stage(
        parameters={
            "proposeTasks": True,
            "proposalMaxItems": 8,
            "proposalTargets": "file",
            "proposalDefaultRuntime": "gemini",
        }
    )
    
    assert captured_policy is not None
    assert captured_policy == {}

@pytest.mark.asyncio
async def test_run_proposals_stage_uses_workflow_proposal_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings
    monkeypatch.setattr(settings.workflow, "enable_proposals", True)

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._owner_type = "user"
    workflow_info = type("WorkflowInfo", (), {"workflow_id": "wf-1", "run_id": "run-1", "namespace": "default"})()
    monkeypatch.setattr(run_workflow_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda x: True)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    
    captured_policy = None
    captured_origin = None
    async def mock_execute_activity(activity, payload, **kwargs):
        nonlocal captured_policy, captured_origin
        if activity == "proposal.generate":
            return [{"title": "t1", "summary": "s1", "workflowCreateRequest": {}}]
        if activity == "proposal.submit":
            captured_policy = payload["policy"]
            captured_origin = payload["origin"]
            return {"submitted_count": 1, "errors": []}
        return None
            
    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", mock_execute_activity)
    
    await workflow._run_proposals_stage(
        parameters={
            "proposeTasks": True,
            # Legacy fields should be ignored
            "proposalMaxItems": 8,
            "proposalTargets": "file",
            "proposalDefaultRuntime": "gemini",
            "workflow": {
                "proposeTasks": True,
                "proposalPolicy": {
                    "maxItems": {"workflow_repo": 12},
                    "targets": ["workflow_repo"],
                    "defaultRuntime": "claude_code",
                }
            }
        }
    )
    
    assert captured_policy is not None
    assert captured_policy["maxItems"] == {"workflow_repo": 12}
    assert captured_policy["targets"] == ["workflow_repo"]
    assert captured_policy["defaultRuntime"] == "claude_code"
    
    # Also verify origin format DOC-REQ-005
    assert captured_origin["source"] == "workflow"
    assert "workflow_id" in captured_origin
    assert "temporal_run_id" in captured_origin
    assert "trigger_repo" in captured_origin

def test_update_memo_persists_pull_request_url_under_canonical_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._title = "Temporal task"
    workflow._summary = "Waiting on review."
    workflow._pull_request_url = "https://github.com/MoonLadderStudios/MoonMind/pull/321"
    captured_memo: list[dict[str, Any]] = []

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_memo",
        lambda memo: captured_memo.append(dict(memo)),
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)

    workflow._update_memo()

    assert captured_memo[-1]["pull_request_url"] == "https://github.com/MoonLadderStudios/MoonMind/pull/321"
    assert "pullRequestUrl" not in captured_memo[-1]


def test_moonspec_verify_gate_result_skips_empty_verdict_key() -> None:
    # A blank verdict key must not short-circuit the gate to a degraded
    # result when another key in the same source carries the real verdict.
    workflow = MoonMindRunWorkflow()
    outputs = {
        "moonSpecVerify": {
            "verdict": "",
            "gateVerdict": "FULLY_IMPLEMENTED",
            "confidence": 0.95,
        }
    }

    gate = workflow._moonspec_verify_gate_result(outputs)

    assert gate.verdict == "FULLY_IMPLEMENTED"
    assert gate.invalid is False
    assert gate.degraded is False


def test_moonspec_verify_gate_result_fails_closed_without_verdict() -> None:
    # When no source carries a truthy verdict, the gate still fails closed.
    workflow = MoonMindRunWorkflow()
    outputs = {"moonSpecVerify": {"verdict": "", "gateVerdict": None}}

    gate = workflow._moonspec_verify_gate_result(outputs)

    assert gate.verdict == "NO_DETERMINATION"
    assert gate.invalid is True
    assert gate.degraded is True


def test_agent_run_result_mapping_preserves_canonical_diagnostics_ref() -> None:
    workflow = MoonMindRunWorkflow()

    mapped = workflow._map_agent_run_result(
        {
            "summary": "Completed with status completed",
            "diagnosticsRef": "art_verify_report",
            "metadata": {
                "diagnosticsRef": "sess:mm-example:codex_cli/diagnostics.json",
                "lastAssistantText": "# MoonSpec Verification Report\n\n**Verdict**: `FULLY_IMPLEMENTED`",
            },
            "outputRefs": [],
        }
    )

    outputs = mapped["outputs"]
    assert outputs["diagnosticsRef"] == "art_verify_report"
    assert (
        outputs["lastAssistantText"]
        == "# MoonSpec Verification Report\n\n**Verdict**: `FULLY_IMPLEMENTED`"
    )


def test_agent_run_result_mapping_ignores_malformed_metadata() -> None:
    workflow = MoonMindRunWorkflow()

    mapped = workflow._map_agent_run_result(
        {
            "summary": "Completed with status completed",
            "diagnosticsRef": "art_verify_report",
            "metadata": ["not", "a", "mapping"],
            "outputRefs": [],
        }
    )

    outputs = mapped["outputs"]
    assert outputs["diagnosticsRef"] == "art_verify_report"
    assert outputs["summary"] == "Completed with status completed"
