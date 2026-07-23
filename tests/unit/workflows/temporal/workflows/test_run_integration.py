import asyncio
import inspect
from datetime import datetime, timezone, timedelta
from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    INTEGRATION_POLL_LOOP_PATCH,
    NATIVE_PR_BRANCH_DEFAULTS_PATCH,
    NATIVE_PR_LEASE_CONFLICT_GATE_PATCH,
    NATIVE_PR_PUSH_STATUS_GATE_PATCH,
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_DIRECT_TOOL_REPORT_OUTPUTS_PATCH,
    RUN_DURABLE_PUBLISH_CONTEXT_MERGE_HANDOFF_PATCH,
    RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH,
    RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH,
    RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
    RUN_PUBLISH_REPAIR_FEEDBACK_PATCH,
    RUN_PR_RESOLVER_PUBLISH_EVIDENCE_REF_PATCH,
    RUN_REMEDIATION_LOOP_CONTINUE_AS_NEW_PATCH,
    RUN_STEP_RETRY_OVERRIDES_PATCH,
    RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH,
    RUN_AUTO_PUBLISH_METADATA_EVIDENCE_PATCH,
    RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH,
    MoonMindRunWorkflow,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.managed_session_models import CodexManagedSessionBinding
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityRetries,
    TemporalActivityRoute,
    TemporalActivityTimeouts,
)
from moonmind.workloads.tool_bridge import build_container_job_tool_definition_payload
from moonmind.workflows.temporal.remediation_workspace_head import (
    REMEDIATION_HEAD_MISMATCH,
    RemediationHeadError,
    RemediationWorkspaceHead,
)

def _mock_plan_payload(nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None) -> bytes:
    import json
    return json.dumps({
        "plan_version": "1.0",
        "metadata": {
            "title": "Test", 
            "created_at": "2024-01-01T00:00:00Z", 
            "registry_snapshot": {"digest": "reg:sha256:123", "artifact_ref": "art:sha256:456"}
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": nodes,
        "edges": edges or []
    }).encode("utf-8")

def _mock_resilience_policy_envelope(payload: Any) -> dict[str, Any]:
    """Return a valid compiled ResiliencePolicy envelope for the MM-880 activity."""
    from moonmind.schemas.resilience_policy_models import compile_resilience_policy

    data = payload if isinstance(payload, dict) else {}
    compiled_at = data.get("compiledAt") or "2026-04-07T12:00:00+00:00"
    return compile_resilience_policy(
        compiled_at=datetime.fromisoformat(compiled_at),
        workflow_id=data.get("workflowId"),
        run_id=data.get("runId"),
        policy_version=data.get("policyVersion", 1),
        attempts={
            "stepMaxAttempts": 3,
            "stepNoProgressLimit": 2,
            "jobSelfHealMaxResets": 1,
        },
        timeouts={"stepTimeoutSeconds": 900, "stepIdleTimeoutSeconds": 300},
        provider_cooldown={
            "cooldownAfter429Seconds": data.get("cooldownAfter429Seconds", 900),
            "providerProfileId": data.get("providerProfileId"),
            "rateLimitPolicy": data.get("rateLimitPolicy", {}),
        },
        checkpoints={
            "checkpointRequired": True,
            "requiredBoundaries": [
                "after_prepare",
                "before_execution",
                "after_execution",
            ],
        },
        idempotency={
            "sideEffectIdempotencyRequired": True,
            "keyStrategy": "step_execution_operation",
        },
        outbound_scanning={"highSecurityMode": False, "blockOnFinding": False},
        observability={
            "liveLogsTimelineEnabled": False,
            "structuredHistoryEnabled": True,
        },
        cost_attribution={
            "runtimeId": data.get("runtimeId"),
            "model": data.get("model"),
            "effort": data.get("effort"),
        },
    ).model_dump(by_alias=True, mode="json")


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    dump_method = getattr(payload, 'model_dump', getattr(payload, 'dict', None))
    return dump_method() if dump_method else payload


def _auto_publish_evidence(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "moonmind.publish.auto.v1",
        "mode": "auto",
        "owner": "agent",
        "skillId": "fix-ci",
        "status": "verified",
        "action": "push",
        "repository": "MoonLadderStudios/MoonMind",
        "branch": "feature/example",
        "localHead": "abc123",
        "remoteBranchHead": "abc123",
        "remoteVerified": True,
        "pushed": True,
        "merged": False,
        "prUrl": None,
        "blockedReason": None,
        "verificationCommands": [
            "git rev-parse HEAD",
            "git ls-remote origin refs/heads/feature/example",
        ],
    }
    payload.update(overrides)
    return payload


async def _finalize_and_capture_summary(
    monkeypatch: pytest.MonkeyPatch,
    workflow: MoonMindRunWorkflow,
    *,
    parameters: dict[str, Any],
    status: str = "success",
    error: str | None = None,
) -> dict[str, Any]:
    async def noop_terminate_sessions(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.create":
            return ({"artifact_id": "artifact://summary"}, {"upload_url": "unused"})
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        assert activity_type == "artifact.write_complete"
        return {"ok": True}

    workflow._state = "publish"
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-auto-publish",
            "run_id": "run-auto-publish",
            "search_attributes": {},
            "start_time": datetime(2026, 7, 3, tzinfo=timezone.utc),
        },
    )
    monkeypatch.setattr(
        workflow,
        "_terminate_workflow_scoped_sessions",
        noop_terminate_sessions,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
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

    await workflow._run_finalizing_stage(
        parameters=parameters,
        status=status,
        error=error,
    )

    return workflow._finish_summary

async def _immediate_wait_condition(
    predicate: Callable[[], bool],
    **_kwargs: Any,
) -> None:
    assert predicate() is True

def test_run_execution_stage_preserves_conditional_registry_patch_marker() -> None:
    source = inspect.getsource(MoonMindRunWorkflow._run_execution_stage)

    patch_call = f"workflow.patched({RUN_CONDITIONAL_REGISTRY_READ_PATCH!r})"
    constant_call = "workflow.patched(RUN_CONDITIONAL_REGISTRY_READ_PATCH)"
    assert constant_call in source or patch_call in source
    call_string = constant_call if constant_call in source else patch_call
    assert source.index('workflow.patched("jules-bundling-v1")') < source.index(
        call_string
    )
    assert source.index(call_string) < source.index("await load_registry_snapshot()")


def test_blocked_external_handoff_skips_current_step_from_call_site() -> None:
    source = inspect.getsource(MoonMindRunWorkflow._run_execution_stage)
    block_start = source.index("if handoff_block_reason:")
    block = source[block_start : source.index("original_node_inputs", block_start)]

    assert "completed_index=index - 2" in block


def test_run_workflow_child_task_queue_is_replay_patched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporal_settings = run_workflow_module.settings.temporal.model_copy(
        update={"user_workflow_v2_task_queue": "mm.workflow.custom.v2"}
    )
    monkeypatch.setattr(run_workflow_module.settings, "temporal", temporal_settings)
    workflow = MoonMindRunWorkflow()

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH,
    )
    assert workflow._workflow_child_task_queue() == "mm.workflow.custom.v2"

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    assert workflow._workflow_child_task_queue() == "mm.workflow"


def test_run_workflow_skill_context_includes_remediation_policy() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_remediation_context(
        {},
        {
            "remediation": {"target": {"workflowId": "mm:target"}},
            "remediationPolicy": {"allowOpsDiagnostics": True},
        },
    )

    assert workflow._skill_remediation_context() == {
        "is_remediation_workflow": True,
        "remediation": {"target": {"workflowId": "mm:target"}},
        "remediation_policy": {"allowOpsDiagnostics": True},
    }


def test_jira_integration_parameter_is_visibility_only() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_integration_from_parameters({"integration": "jira"})

    assert workflow._integration_label == "jira"
    assert workflow._integration is None


def test_jules_integration_parameter_enables_external_monitor() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_integration_from_parameters({"integration": "JULES"})

    assert workflow._integration_label == "jules"
    assert workflow._integration == "jules"
    assert workflow._integration_activity_type("start") == "integration.jules.start"


@pytest.fixture
def mock_run_workflow(monkeypatch: pytest.MonkeyPatch) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._integration = "jules"
    workflow._repo = "org/repo"
    
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "all_handlers_finished",
        lambda: True,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    
    # Mock logger
    logger = type("Logger", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})
    monkeypatch.setattr(run_workflow_module.workflow, "logger", logger)

    return workflow

@pytest.mark.asyncio
async def test_run_execution_stage_skips_integration_after_merge_gate_cancellation(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration_calls: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "art:sha256:456":
                return (
                    b'{"skills":[{"name":"repo.noop","version":"1.0",'
                    b'"description":"No-op","inputs":{"schema":{"type":"object"}},'
                    b'"outputs":{"schema":{"type":"object"}},'
                    b'"executor":{"activity_type":"mm.skill.execute",'
                    b'"selector":{"mode":"by_capability"}},'
                    b'"requirements":{"capabilities":["sandbox"]},'
                    b'"policies":{"timeouts":{"start_to_close_seconds":60,'
                    b'"schedule_to_close_seconds":120},"retries":{"max_attempts":1}}}]}'
                )
            return _mock_plan_payload(
                [
                    {
                        "id": "step-1",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "jules",
                        },
                        "inputs": {"instructions": "Do nothing."},
                    }
                ]
            )
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _args: Any,
        **_kwargs: Any,
    ) -> Any:
        return {
            "summary": "No-op completed",
            "metadata": {"push_status": "not_requested"},
            "output_refs": [],
        }

    async def fake_merge_gate(
        *,
        parameters: dict[str, Any],
        pull_request_url: str | None,
    ) -> None:
        mock_run_workflow._cancel_requested = True
        mock_run_workflow._set_state("canceled", summary="merge automation canceled")

    async def fake_integration_stage(
        *,
        parameters: dict[str, Any],
        plan_ref: str | None,
    ) -> None:
        integration_calls.append({"parameters": parameters, "plan_ref": plan_ref})

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
    monkeypatch.setattr(mock_run_workflow, "_maybe_start_merge_gate", fake_merge_gate)
    monkeypatch.setattr(
        mock_run_workflow,
        "_run_integration_stage",
        fake_integration_stage,
    )

    await mock_run_workflow._run_execution_stage(
        parameters={"publishMode": "none"},
        plan_ref="plan-1",
    )

    assert integration_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "durable_handoff_patch_enabled",
    [True, False],
    ids=["new-history", "legacy-replay"],
)
@pytest.mark.parametrize(
    "publication_blocked",
    [False, True],
    ids=["publication-allowed", "publication-blocked"],
)
async def test_run_execution_stage_recovers_merge_handoff_from_publish_context(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
    durable_handoff_patch_enabled: bool,
    publication_blocked: bool,
) -> None:
    pull_request_url = "https://github.com/org/repo/pull/3343"
    merge_gate_urls: list[str | None] = []
    mock_run_workflow._integration = None
    mock_run_workflow._publish_status = "not_required"
    mock_run_workflow._publish_reason = "Earlier assessment produced no commits."
    mock_run_workflow._publish_context.update(
        {
            "pullRequestUrl": pull_request_url,
            "headSha": "0fa9c6613",
            "noCommitPublish": {"status": "no_commits"},
        }
    )
    if publication_blocked:
        mock_run_workflow._publish_context["publicationBlockedBy"] = (
            "moonspec_verify"
        )

    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> Any:
        assert activity_type == "artifact.read"
        return _mock_plan_payload(
            [
                {
                    "id": "final-status",
                    "tool": {"type": "agent_runtime", "name": "jules"},
                    "inputs": {"instructions": "Finalize status."},
                }
            ]
        )

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _request: Any,
        **_kwargs: Any,
    ) -> Any:
        return {
            "summary": "Status finalized.",
            "metadata": {"push_status": "not_requested"},
            "output_refs": [],
        }

    async def fake_merge_gate(
        *,
        parameters: dict[str, Any],
        pull_request_url: str | None,
    ) -> None:
        assert parameters["mergeAutomation"]["enabled"] is True
        merge_gate_urls.append(pull_request_url)

    async def noop_step_manifest(*_args: Any, **_kwargs: Any) -> None:
        return None

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
        "patched",
        lambda patch_id: (
            durable_handoff_patch_enabled
            and patch_id == RUN_DURABLE_PUBLISH_CONTEXT_MERGE_HANDOFF_PATCH
        ),
    )
    monkeypatch.setattr(
        mock_run_workflow,
        "_pr_publish_optional_for_task",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        mock_run_workflow,
        "_record_step_execution_manifest",
        noop_step_manifest,
    )
    monkeypatch.setattr(mock_run_workflow, "_maybe_start_merge_gate", fake_merge_gate)

    await mock_run_workflow._run_execution_stage(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True},
        },
        plan_ref="plan-1",
    )

    expected_url = (
        pull_request_url
        if durable_handoff_patch_enabled and not publication_blocked
        else None
    )
    expected_status = "published" if expected_url else "not_required"
    assert merge_gate_urls == [expected_url]
    assert mock_run_workflow._pull_request_url == expected_url
    assert mock_run_workflow._publish_status == expected_status

@pytest.mark.asyncio
async def test_run_integration_stage_poll_driven_completion(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "artifact.read":
            return _mock_plan_payload([{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}])
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            # Simulate completion on the first poll
            return {"normalized_status": "completed", "tracking_ref": "track-2"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate timeout so we fall through to polling
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    # Expected activity calls: artifact.read, start, status
    assert len(captured) == 3
    assert captured[0][0] == "artifact.read"
    assert captured[1][0] == "integration.jules.start"
    assert captured[2][0] == "integration.jules.status"
    assert mock_run_workflow._external_status == "completed"

@pytest.mark.asyncio
async def test_run_integration_poll_completion_invokes_patched_with_stable_id(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compatibility: polling completion must use the replay-stable patch id."""
    patch_calls: list[str] = []

    def fake_patched(patch_id: str) -> bool:
        patch_calls.append(patch_id)
        return True

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                [{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed", "tracking_ref": "track-2"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )

    assert patch_calls, "workflow.patched should be evaluated for integration poll completion"
    assert INTEGRATION_POLL_LOOP_PATCH in patch_calls
    assert mock_run_workflow._external_status == "completed"

@pytest.mark.asyncio
async def test_run_integration_legacy_unpatched_poll_completion_still_completes(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-flight history without the patch marker: legacy resume path still reaches completed."""
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                [{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed", "tracking_ref": "track-2"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )

    assert mock_run_workflow._external_status == "completed"


@pytest.mark.asyncio
async def test_run_execution_stage_uses_user_max_attempts_for_skill_retry_policy(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run_workflow._integration = None
    captured: list[tuple[str, Any, dict[str, Any]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload), kwargs))
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "jira.check_blockers",
                                "description": "Check Jira blockers",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {
                                    "capabilities": ["integration:jira"]
                                },
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 60,
                                        "schedule_to_close_seconds": 120,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "check-blockers",
                        "tool": {
                            "type": "skill",
                            "name": "jira.check_blockers",
                        },
                        "inputs": {
                            "targetIssueKey": "MM-866",
                            "maxAttempts": 3,
                        },
                    }
                ]
            )
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_STEP_RETRY_OVERRIDES_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    await mock_run_workflow._run_execution_stage(
        parameters={},
        plan_ref="art:sha256:plan",
    )

    tool_calls = [call for call in captured if call[0] == "mm.tool.execute"]
    assert len(tool_calls) == 1
    retry_policy = tool_calls[0][2]["retry_policy"]
    assert retry_policy.maximum_attempts == 3
    assert tool_calls[0][1]["invocation_payload"]["inputs"]["maxAttempts"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handoff_enabled", "expects_verification"),
    [(True, True), (False, False)],
)
async def test_run_execution_stage_hands_controlling_verification_to_finalizer(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
    handoff_enabled: bool,
    expects_verification: bool,
) -> None:
    """Regression for workflow mm:341551dd-06d0-4c80-9563-77b12b144499."""

    mock_run_workflow._integration = None
    mock_run_workflow._publish_context["moonSpecGate"] = {
        "logicalStepId": "verify",
        "verdict": "FULLY_IMPLEMENTED",
        "gateResultRef": "art_verify_gate",
    }
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "github.update_issue_status",
                                "description": "Finalize a GitHub issue",
                                "inputs": {"schema": {"type": "object"}},
                                "outputs": {"schema": {"type": "object"}},
                                "executor": {
                                    "activity_type": "mm.tool.execute",
                                    "selector": {"mode": "by_capability"},
                                },
                                "requirements": {
                                    "capabilities": ["integration:github"]
                                },
                                "policies": {
                                    "timeouts": {
                                        "start_to_close_seconds": 60,
                                        "schedule_to_close_seconds": 120,
                                    },
                                    "retries": {"max_attempts": 1},
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "finalize-issue",
                        "tool": {
                            "type": "skill",
                            "name": "github.update_issue_status",
                        },
                        "inputs": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "issueNumber": 3258,
                            "mode": "finalize_after_pr_or_done",
                            "pullRequestArtifactPath": "artifacts/pr.json",
                            "verificationArtifactPath": "artifacts/verify.json",
                            "requireVerification": True,
                        },
                    }
                ]
            )
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: (
            handoff_enabled
            and patch_id == RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH
        ),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    await mock_run_workflow._run_execution_stage(
        parameters={},
        plan_ref="art:sha256:plan",
    )

    tool_call = next(call for call in captured if call[0] == "mm.tool.execute")
    inputs = tool_call[1]["invocation_payload"]["inputs"]
    if expects_verification:
        assert inputs["previousOutputs"]["moonSpecVerify"] == {
            "logicalStepId": "verify",
            "verdict": "FULLY_IMPLEMENTED",
            "gateResultRef": "art_verify_gate",
        }
        assert (
            inputs["previousOutputs"]["moonSpecVerifyArtifactRef"]
            == "art_verify_gate"
        )
    else:
        assert "previousOutputs" not in inputs


def test_execute_kwargs_retry_override_preserves_route_retry_policy() -> None:
    workflow = MoonMindRunWorkflow()
    route = TemporalActivityRoute(
        activity_type="mm.tool.execute",
        task_queue="mm.activity.integrations",
        fleet="integrations",
        capability_class="tools",
        timeouts=TemporalActivityTimeouts(
            start_to_close_seconds=30,
            schedule_to_close_seconds=90,
            heartbeat_timeout_seconds=10,
        ),
        retries=TemporalActivityRetries(
            max_attempts=2,
            max_interval_seconds=45,
            non_retryable_error_codes=("invalid_input",),
        ),
    )

    kwargs = workflow._execute_kwargs_for_route(route, max_attempts_override=4)

    retry_policy = kwargs["retry_policy"]
    assert retry_policy.maximum_attempts == 4
    assert retry_policy.initial_interval == timedelta(seconds=5)
    assert retry_policy.backoff_coefficient == 2.0
    assert retry_policy.maximum_interval == timedelta(seconds=45)
    assert retry_policy.non_retryable_error_types == ["invalid_input"]
    assert kwargs["heartbeat_timeout"] == timedelta(seconds=10)


@pytest.mark.asyncio
async def test_run_execution_stage_bundles_consecutive_jules_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    workflow._title = "Bundled Jules execution"

    child_calls: list[object] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "art:sha256:456":
                import json

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
            return _mock_plan_payload(
                nodes=[
                    {
                        "id": "jules-step-1",
                        "tool": {"type": "agent_runtime", "name": "jules"},
                        "inputs": {
                            "repository": "org/repo",
                            "startingBranch": "main",
                            "publishMode": "none",
                            "instructions": "Step 1",
                        },
                    },
                    {
                        "id": "jules-step-2",
                        "tool": {"type": "agent_runtime", "name": "jules"},
                        "inputs": {
                            "repository": "org/repo",
                            "startingBranch": "main",
                            "publishMode": "none",
                            "instructions": "Step 2",
                        },
                    },
                ]
            )
        if activity_type == "artifact.create":
            return ({"artifact_id": "artifact://bundle/1"}, {"upload_url": "unused"})
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        if activity_type == "resilience.compile_policy":
            return _mock_resilience_policy_envelope(payload)
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_execute_child_workflow(
        workflow_type: str,
        args: object,
        **_kwargs: Any,
    ) -> object:
        child_calls.append(args)
        return {"summary": "Bundled run complete", "metadata": {}, "output_refs": []}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_search_attributes", lambda _attributes: None)
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "org/repo", "publishMode": "none"},
        plan_ref="plan-1",
    )

    assert len(child_calls) == 1
    request = child_calls[0]
    assert "Ordered Checklist:" in request.instruction_ref
    assert "1. Step 1" in request.instruction_ref
    assert "2. Step 2" in request.instruction_ref
    assert request.parameters["metadata"]["moonmind"]["bundleManifestRef"] == "artifact://bundle/1"
    assert request.parameters["metadata"]["moonmind"]["bundleStrategy"] == "one_shot_jules"

@pytest.mark.asyncio
async def test_run_execution_stage_routes_generic_container_tool_to_durable_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    workflow._title = "DooD workload"
    workflow._target_runtime = "codex"
    captured: list[tuple[str, Any, dict[str, Any]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload), kwargs))
        if activity_type == "artifact.read":
            artifact_ref = (
                payload.get("artifact_ref")
                if isinstance(payload, dict)
                else getattr(payload, "artifact_ref", None)
            )
            if artifact_ref == "art:sha256:456":
                import json

                return json.dumps(
                    {
                        "skills": [
                            build_container_job_tool_definition_payload(
                                name="container.run_job",
                            )
                        ]
                    }
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {
                        "id": "workload-step",
                        "tool": {
                            "type": "skill",
                            "name": "container.run_job",
                        },
                        "inputs": {
                            "idempotencyKey": "wf-1:workload-step:1",
                            "spec": {
                                "image": "python:3.12",
                                "command": ["python", "-V"],
                                "resources": {"cpuMillis": 100, "memoryMiB": 64},
                            },
                        },
                    }
                ]
            )
        if activity_type == "container_job.submit":
            return {"jobId": "container-job:" + "1" * 32, "state": "queued"}
        if activity_type == "container_job.status":
            return {
                "jobId": "container-job:" + "1" * 32,
                "state": "succeeded",
                "terminal": {"exitCode": 0},
            }
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    async def fail_if_child_workflow_starts(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("DooD skill tools must not start MoonMind.AgentRun")

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fail_if_child_workflow_starts,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-1",
            "run_id": "run-1",
            "search_attributes": {},
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "logger",
        type(
            "Logger",
            (),
            {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
        ),
    )

    await workflow._run_execution_stage(parameters={}, plan_ref="art:sha256:plan")

    submit_calls = [call for call in captured if call[0] == "container_job.submit"]
    assert len(submit_calls) == 1
    request = submit_calls[0][1]["request"]
    assert request["source"] == {
        "source": "workflow",
        "workflowId": "wf-1",
        "runId": "run-1",
        "stepId": "workload-step",
    }
    assert request["spec"]["workspaceRef"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "wf-1",
        "relativePath": "repo",
    }
    assert [call[0] for call in captured].count("container_job.status") == 1

@pytest.mark.asyncio
async def test_run_execution_stage_skips_integration_after_merge_automation_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._integration = "jules"
    workflow._repo = "org/repo"
    integration_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> Any:
        assert activity_type == "artifact.read"
        return _mock_plan_payload(
            [
                {
                    "id": "noop",
                    "tool": {
                        "type": "agent_runtime",
                        "name": "codex",
                    },
                    "inputs": {"instructions": "No-op"},
                }
            ]
        )

    async def fake_maybe_start_merge_gate(
        *,
        parameters: dict[str, Any],
        pull_request_url: str | None,
    ) -> None:
        assert parameters["publishMode"] == "none"
        assert pull_request_url is None
        workflow._cancel_requested = True

    async def fake_run_integration_stage(
        *,
        parameters: dict[str, Any],
        plan_ref: str | None,
    ) -> None:
        nonlocal integration_calls
        integration_calls += 1

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        workflow,
        "_ordered_plan_node_payloads",
        lambda *, nodes, edges: [],
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == "run-conditional-registry-read-v1",
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        _immediate_wait_condition,
    )
    monkeypatch.setattr(workflow, "_maybe_start_merge_gate", fake_maybe_start_merge_gate)
    monkeypatch.setattr(workflow, "_run_integration_stage", fake_run_integration_stage)

    await workflow._run_execution_stage(
        parameters={"repo": "org/repo", "publishMode": "none"},
        plan_ref="plan-1",
    )

    assert integration_calls == 0


@pytest.mark.asyncio
async def test_wait_if_paused_uses_legacy_gate_when_patch_marker_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._paused = True
    wait_calls = 0

    async def fake_wait_condition(
        predicate: Callable[[], bool],
        **_kwargs: Any,
    ) -> None:
        nonlocal wait_calls
        assert predicate() is False
        wait_calls += 1
        workflow._paused = False
        assert predicate() is True

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )

    await workflow._wait_if_paused_at_safe_boundary()

    assert wait_calls == 1
    assert workflow._paused is False


@pytest.mark.asyncio
async def test_run_execution_stage_honors_pause_between_managed_session_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    child_workflow_ids: list[str] = []
    pause_wait_count = 0

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        assert activity_type == "artifact.read"
        assert getattr(payload, "artifact_ref", None) == "plan-ref"
        return _mock_plan_payload(
            [
                {
                    "id": "first",
                    "tool": {"type": "agent_runtime", "name": "codex_cli"},
                    "inputs": {"instructions": "Run the first managed step."},
                },
                {
                    "id": "second",
                    "tool": {"type": "agent_runtime", "name": "codex_cli"},
                    "inputs": {"instructions": "Run the second managed step."},
                },
            ],
            edges=[{"from": "first", "to": "second"}],
        )

    async def fake_execute_child_workflow(
        workflow_name: str,
        request: Any,
        **kwargs: Any,
    ) -> Any:
        assert workflow_name == "MoonMind.AgentRun"
        child_workflow_ids.append(str(kwargs["id"]))
        if len(child_workflow_ids) == 1:
            workflow._paused = True
        else:
            assert pause_wait_count == 1
        return {
            "summary": "Completed with status completed",
            "output_refs": [],
            "failure_class": None,
            "metadata": {"agentId": getattr(request, "agent_id", None)},
        }

    async def fake_wait_condition(
        predicate: Callable[[], bool],
        **_kwargs: Any,
    ) -> None:
        nonlocal pause_wait_count
        assert workflow._paused is True
        assert predicate() is False
        pause_wait_count += 1
        workflow._paused = False
        assert predicate() is True

    async def fake_bind_workflow_scoped_session(request: Any) -> Any:
        return request

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.create":
            return ({"artifact_id": "artifact://manifest"}, {"upload_url": "unused"})
        if activity_type == "step_checkpoint.create":
            normalized = _normalize_payload(payload)
            boundary = str(normalized.get("boundary") or "unknown")
            checkpoint_id = str(
                normalized.get("idempotencyKey") or f"checkpoint:{boundary}"
            )
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
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_write_manifest_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        return await fake_execute_typed_activity(activity_type, payload, **_kwargs)

    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-pause-boundary",
            "run_id": "run-pause-boundary",
            "search_attributes": {
                "mm_owner_type": ["user"],
                "mm_owner_id": ["owner-1"],
            },
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
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
        "logger",
        type(
            "Logger",
            (),
            {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
        ),
    )
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_write_manifest_activity,
    )
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
        fake_wait_condition,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        in {RUN_PAUSE_SAFE_BOUNDARIES_PATCH, "run-conditional-registry-read-v1"},
    )
    monkeypatch.setattr(
        workflow,
        "_maybe_bind_workflow_scoped_session",
        fake_bind_workflow_scoped_session,
    )

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"},
        plan_ref="plan-ref",
    )

    assert child_workflow_ids == [
        "wf-pause-boundary:agent:first",
        "wf-pause-boundary:agent:second",
    ]
    assert pause_wait_count == 1
    assert workflow._paused is False
    assert workflow._waiting_reason is None


@pytest.mark.asyncio
async def test_run_integration_stage_signal_driven_completion(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "artifact.read":
            return _mock_plan_payload([{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}])
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1", "tracking_ref": "track-1"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate an external event arriving during the wait
        mock_run_workflow.external_event({
            "correlation_id": "ext-1",
            "normalized_status": "completed"
        })
        return

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    # Expected activity calls: artifact.read, start only, because it woke up via signal and skipped polling
    assert len(captured) == 2
    assert captured[0][0] == "artifact.read"
    assert captured[1][0] == "integration.jules.start"
    assert mock_run_workflow._external_status == "completed"

@pytest.mark.asyncio
async def test_run_integration_stage_branch_publish_auto_merge_after_signal(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "artifact.read":
            return _mock_plan_payload([{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}])
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1"}
        if activity_type == "integration.jules.fetch_result":
            return {"url": "https://github.com/org/repo/pull/123", "summary": "Done"}
        if activity_type == "repo.merge_pr":
            return {"merged": True, "summary": "Merged successfully"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        # Simulate signal arriving
        mock_run_workflow.external_event({
            "correlation_id": "ext-1",
            "normalized_status": "completed"
        })
        return

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    await mock_run_workflow._run_integration_stage(
        parameters={
            "repo": "org/repo",
            "publishMode": "branch",
            "workspaceSpec": {"startingBranch": "feature-branch"}
        },
        plan_ref="plan-1",
    )
    
    # Expected activity calls: fetch plan, start, fetch_result, merge_pr
    assert len(captured) == 4
    assert captured[0][0] == "artifact.read"
    assert captured[1][0] == "integration.jules.start"
    assert captured[2][0] == "integration.jules.fetch_result"
    assert captured[3][0] == "repo.merge_pr"
    
    # Verify merge_pr payload
    merge_payload = captured[3][1]
    # No target_branch should be passed since we didn't override it
    assert merge_payload == {"pr_url": "https://github.com/org/repo/pull/123"}

@pytest.mark.asyncio
async def test_run_integration_stage_branch_publish_requires_pr_url(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                [{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1"}
        if activity_type == "integration.jules.fetch_result":
            return {"summary": "Done"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        mock_run_workflow.external_event(
            {"correlation_id": "ext-1", "normalized_status": "completed"}
        )

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    with pytest.raises(ValueError, match="no PR URL found"):
        await mock_run_workflow._run_integration_stage(
            parameters={
                "repo": "org/repo",
                "publishMode": "branch",
                "workspaceSpec": {"startingBranch": "feature-branch"},
            },
            plan_ref="plan-1",
        )

@pytest.mark.asyncio
async def test_run_integration_stage_branch_publish_requires_merge_success(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                [{"id": "1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Do something"}}]
            )
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-1"}
        if activity_type == "integration.jules.fetch_result":
            return {"url": "https://github.com/org/repo/pull/123", "summary": "Done"}
        if activity_type == "repo.merge_pr":
            return {"merged": False, "summary": "Merge rejected"}
        return {}

    async def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        mock_run_workflow.external_event(
            {"correlation_id": "ext-1", "normalized_status": "completed"}
        )

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)

    with pytest.raises(ValueError, match="merge failed"):
        await mock_run_workflow._run_integration_stage(
            parameters={
                "repo": "org/repo",
                "publishMode": "branch",
                "workspaceSpec": {"startingBranch": "feature-branch"},
            },
            plan_ref="plan-1",
        )

@pytest.mark.asyncio
async def test_run_integration_stage_multi_step_bundles_into_single_start(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    
    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        captured.append((activity_type, _normalize_payload(payload)))
        if activity_type == "artifact.read":
            return _mock_plan_payload(
                nodes=[
                    {"id": "step1", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Step 1"}},
                    {"id": "step2", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Step 2"}},
                    {"id": "step3", "tool": {"type": "skill", "name": "t"}, "inputs": {"instructions": "Step 3"}},
                ],
                edges=[
                    {"from": "step1", "to": "step2"},
                    {"from": "step2", "to": "step3"}
                ]
            )
        if activity_type == "artifact.create":
            return ({"artifact_id": "artifact://bundle/1"}, {"upload_url": "unused"})
        if activity_type == "artifact.write_complete":
            return {"ok": True}
        if activity_type == "integration.jules.start":
            return {"external_id": "ext-session-123", "tracking_ref": "track-1"}
        if activity_type == "integration.jules.status":
            return {"normalized_status": "completed"}
        return {}

    def fake_wait_condition(cond: Callable[[], bool], timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    def fake_patched(patch_id: str) -> bool:
        return True

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_workflow_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    await mock_run_workflow._run_integration_stage(
        parameters={"repo": "org/repo"},
        plan_ref="plan-1",
    )
    
    start_calls = [c for c in captured if c[0] == "integration.jules.start"]
    artifact_create_calls = [c for c in captured if c[0] == "artifact.create"]
    artifact_write_calls = [c for c in captured if c[0] == "artifact.write_complete"]
    status_calls = [c for c in captured if c[0] == "integration.jules.status"]
    
    assert len(start_calls) == 1
    description = start_calls[0][1]["parameters"]["description"]
    assert "Ordered Checklist:" in description
    assert "1. Step 1" in description
    assert "2. Step 2" in description
    assert "3. Step 3" in description
    assert len(artifact_create_calls) == 1
    assert len(artifact_write_calls) == 1
    assert all(c[0] != "integration.jules.send_message" for c in captured)
    assert len(status_calls) == 1
    assert mock_run_workflow._external_status == "completed"

def test_determine_publish_completion_fails_for_no_commit_pr_publish(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = {
        "outputs": {
            "push_status": "no_commits",
            "push_branch": "feature/no-op",
            "push_base_ref": "origin/main",
            "push_commit_count": 0,
            "operator_summary": "Files edited in this run: none.",
        }
    }
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result=execution_result,
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert "no publishable diff was produced" in message
    assert "feature/no-op" in message
    assert "origin/main" in message
    assert "has no commits ahead of origin/main" in message
    assert "0 commits ahead" not in message
    assert "Files edited in this run: none." in message
    assert publish_failure is True

def test_jira_implement_no_commit_pr_handoff_is_not_required(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._canonical_no_commit_outcome_enabled = True
    execution_result = {
        "outputs": {
            "push_status": "no_commits",
            "push_branch": "feature/no-op",
            "push_base_ref": "origin/main",
            "push_commit_count": 0,
            "operator_summary": "MM-675 was already implemented.",
        }
    }
    parameters = {
        "publishMode": "pr",
        "task": {
            "appliedStepTemplates": [
                {"slug": "jira-implement", "version": "1.0.0"},
            ],
        },
    }

    mock_run_workflow._record_execution_context(
        node_id="step-7",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result=execution_result,
    )
    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters=parameters
    )

    assert mock_run_workflow._publish_status == "not_required"
    assert status == "no_commit"
    assert "No pull request was required" in message
    assert (
        "issue implementation workflow completed without repository changes" in message
    )
    assert "MM-675 was already implemented" in message
    assert "no publishable diff was produced" not in message
    assert publish_failure is False


def test_jira_implement_no_commit_pr_handoff_without_agent_report_is_explicit(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._canonical_no_commit_outcome_enabled = True
    execution_result = {
        "outputs": {
            "push_status": "no_commits",
            "push_branch": "feature/no-op",
            "push_base_ref": "origin/main",
            "push_commit_count": 0,
        }
    }
    parameters = {
        "publishMode": "pr",
        "task": {
            "appliedStepTemplates": [
                {"slug": "jira-implement", "version": "1.0.0"},
            ],
        },
    }

    mock_run_workflow._record_execution_context(
        node_id="step-7",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result=execution_result,
    )
    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters=parameters
    )

    assert mock_run_workflow._publish_status == "not_required"
    assert status == "no_commit"
    assert "No pull request was required" in message
    assert (
        "issue implementation workflow completed without repository changes" in message
    )
    assert (
        "no structured agent report confirmed whether the issue was "
        "already implemented"
    ) in message
    assert publish_failure is False


@pytest.mark.asyncio
async def test_github_issue_implement_no_commit_closure_finalizes_as_no_commit(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run_workflow._canonical_no_commit_outcome_enabled = True
    parameters = {
        "publishMode": "pr",
        "workflow": {
            "tool": {"type": "skill", "name": "auto"},
            "skill": {"name": "auto"},
            "appliedStepTemplates": [
                {"slug": "github-issue-implement", "version": "1.0.0"},
            ],
        },
    }
    no_commit_result = {
        "outputs": {
            "push_status": "no_commits",
            "push_branch": "feature/no-op",
            "push_base_ref": "origin/main",
            "push_commit_count": 0,
            "operator_summary": "GitHub issue #3144 was already implemented.",
        }
    }
    mock_run_workflow._record_execution_context(
        node_id="create-pull-request",
        execution_result=no_commit_result,
    )
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result=no_commit_result,
    )
    mock_run_workflow._record_execution_context(
        node_id="finalize-github-issue",
        execution_result={
            "outputs": {
                "summary": (
                    "Updated GitHub issue MoonLadderStudios/MoonMind#3144 "
                    "with mode done."
                ),
                "sideEffect": {
                    "effectClass": "external_non_idempotent",
                    "kind": "github",
                    "operation": "github.issue.close",
                    "target": (
                        "https://github.com/MoonLadderStudios/MoonMind/issues/3144"
                    ),
                    "summary": "Closed GitHub issue MoonLadderStudios/MoonMind#3144.",
                },
            }
        },
    )
    mock_run_workflow._record_declared_side_effect(
        logical_step_id="finalize-github-issue",
        outputs={
            "sideEffect": {
                "effectClass": "external_non_idempotent",
                "kind": "github",
                "operation": "github.issue.close",
                "target": "https://github.com/MoonLadderStudios/MoonMind/issues/3144",
                "summary": "Closed GitHub issue MoonLadderStudios/MoonMind#3144.",
            }
        },
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters=parameters
    )
    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
    )

    assert mock_run_workflow._publish_status == "not_required"
    assert status == "no_commit"
    assert "No pull request was required" in message
    assert "Jira" not in message
    assert publish_failure is False
    assert summary["finishOutcome"]["code"] == "NO_COMMIT"
    assert summary["publish"]["status"] == "skipped"
    assert summary["publish"]["reasonCode"] == "no_commit"
    assert summary["publish"]["commitCreated"] is False
    assert summary["publish"]["branchPushed"] is False
    assert summary["publish"]["prUrl"] is None
    assert summary["sideEffects"] == [
        {
            "kind": "github",
            "status": "completed",
            "summary": "Closed GitHub issue MoonLadderStudios/MoonMind#3144.",
        }
    ]


@pytest.mark.asyncio
async def test_already_implemented_no_commit_pr_handoff_completes_jira_done(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_calls: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        activity_calls.append({"activityType": activity_type, "payload": payload})
        return {
            "status": "succeeded",
            "required": True,
            "issueKey": "MM-675",
            "issueKeySource": "explicit_post_merge",
            "alreadyDone": False,
            "transitioned": True,
            "transitionId": "41",
            "transitionName": "Done",
            "toStatusName": "Done",
            "toStatusCategory": "done",
        }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    mock_run_workflow._publish_status = "not_required"
    mock_run_workflow._publish_reason = (
        "No pull request was required because this Jira-oriented workflow completed "
        "without repository changes. final agent report: MM-675 was already implemented."
    )
    mock_run_workflow._publish_context["noCommitPublish"] = {"status": "no_commits"}
    expected_evidence = mock_run_workflow._publish_reason[:700]

    await mock_run_workflow._complete_already_implemented_jira_if_needed(
        parameters={
            "publishMode": "pr",
            "task": {
                "instructions": "Complete Jira issue MM-675.",
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        }
    )

    assert activity_calls == [
        {
            "activityType": "merge_automation.complete_post_merge_jira",
            "payload": {
                "parentWorkflowId": "wf-1",
                "parentRunId": "run-1",
                "resolverDisposition": "already_implemented_no_commit",
                "jiraIssueKey": "MM-675",
                "postMergeJira": {
                    "enabled": True,
                    "required": True,
                    "issueKey": "MM-675",
                    "strategy": "done_category",
                },
                "candidateContext": {
                    "taskOriginIssueKey": "MM-675",
                    "taskMetadataIssueKey": "MM-675",
                    "publishContextIssueKey": "MM-675",
                    "alreadyImplementedEvidence": expected_evidence,
                },
            },
        }
    ]
    assert mock_run_workflow._publish_context[
        "alreadyImplementedJiraCompletion"
    ] == {
        "status": "succeeded",
        "required": True,
        "issueKey": "MM-675",
        "issueKeySource": "explicit_post_merge",
        "alreadyDone": False,
        "transitioned": True,
        "transitionId": "41",
        "transitionName": "Done",
        "toStatusName": "Done",
        "toStatusCategory": "done",
    }
    assert "Jira issue MM-675 was moved to Done." in str(
        mock_run_workflow._publish_reason
    )


def test_no_commit_publish_evidence_accepts_legacy_context_key(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._publish_context["noChangePublish"] = {"status": "no_commits"}

    assert mock_run_workflow._has_no_commit_publish_evidence() is True


@pytest.mark.asyncio
async def test_already_implemented_jira_completion_requires_no_change_signal(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_calls: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        activity_calls.append({"activityType": activity_type, "payload": payload})
        return {"status": "succeeded"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    mock_run_workflow._publish_status = "not_required"
    mock_run_workflow._publish_reason = "MM-675 was already implemented."

    await mock_run_workflow._complete_already_implemented_jira_if_needed(
        parameters={
            "publishMode": "pr",
            "task": {
                "instructions": "Complete Jira issue MM-675.",
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        }
    )

    assert activity_calls == []


@pytest.mark.asyncio
async def test_ambiguous_no_commit_pr_handoff_does_not_complete_jira_done(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_calls: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        activity_calls.append({"activityType": activity_type, "payload": payload})
        return {"status": "succeeded"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    mock_run_workflow._publish_status = "not_required"
    mock_run_workflow._publish_reason = (
        "No pull request was required because this Jira-oriented workflow completed "
        "without repository changes. no structured agent report confirmed whether "
        "the Jira issue was already implemented."
    )
    mock_run_workflow._publish_context["noCommitPublish"] = {"status": "no_commits"}

    await mock_run_workflow._complete_already_implemented_jira_if_needed(
        parameters={
            "publishMode": "pr",
            "task": {
                "instructions": "Complete Jira issue MM-675.",
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        }
    )

    assert activity_calls == []
    assert "alreadyImplementedJiraCompletion" not in mock_run_workflow._publish_context


@pytest.mark.asyncio
async def test_uncertain_already_implemented_wording_does_not_complete_jira_done(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_calls: list[dict[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        activity_calls.append({"activityType": activity_type, "payload": payload})
        return {"status": "succeeded"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    mock_run_workflow._publish_status = "not_required"
    mock_run_workflow._publish_reason = (
        "No pull request was required because this Jira-oriented workflow completed "
        "without repository changes. The agent could not confirm if MM-675 was "
        "already implemented."
    )
    mock_run_workflow._publish_context["noCommitPublish"] = {"status": "no_commits"}

    await mock_run_workflow._complete_already_implemented_jira_if_needed(
        parameters={
            "publishMode": "pr",
            "task": {
                "instructions": "Complete Jira issue MM-675.",
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        }
    )

    assert activity_calls == []
    assert "alreadyImplementedJiraCompletion" not in mock_run_workflow._publish_context


@pytest.mark.asyncio
async def test_already_implemented_jira_completion_failure_blocks_success(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_activity(
        _activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "status": "blocked",
            "required": True,
            "issueKey": "MM-675",
            "reason": "Expected exactly one done-category Jira transition.",
        }

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    mock_run_workflow._publish_status = "not_required"
    mock_run_workflow._publish_reason = "MM-675 was already implemented."
    mock_run_workflow._publish_context["noCommitPublish"] = {"status": "no_commits"}

    with pytest.raises(ValueError, match="Expected exactly one done-category"):
        await mock_run_workflow._complete_already_implemented_jira_if_needed(
            parameters={
                "publishMode": "pr",
                "task": {
                    "instructions": "Complete Jira issue MM-675.",
                    "appliedStepTemplates": [
                        {"slug": "jira-implement", "version": "1.0.0"},
                    ],
                },
            }
        )

def test_structured_publish_not_required_satisfies_pr_publish_mode(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = {
        "outputs": {
            "publishOutcome": {
                "status": "not_required",
                "reason": "Jira issue already implemented; no pull request required.",
                "prRequired": False,
            },
            "operator_summary": "MM-697 was transitioned to Done.",
        }
    }

    mock_run_workflow._record_execution_context(
        node_id="step-8",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-697"},
        },
        execution_result=execution_result,
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-697"},
        }
    )

    assert mock_run_workflow._publish_status == "not_required"
    assert mock_run_workflow._publish_context["mergeAutomationStatus"] == "not_applicable"
    assert status == "success"
    assert "Jira issue already implemented" in message
    assert publish_failure is False

def test_record_publish_result_preserves_validated_pr_metadata_for_downstream(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = {
        "outputs": {
            "push_status": "pushed",
            "pullRequestUrl": "https://github.com/org/repo/pull/674",
            "prMetadata": {
                "title": "MM-674 Source PR metadata from agent semantics",
                "body": "Jira: MM-674\nSummary: Implemented.",
                "jiraIssueKey": "MM-674",
                "moonSpecPath": "specs/674-pr-metadata",
                "source": "pr_metadata.json",
            },
        }
    }

    mock_run_workflow._record_execution_context(
        node_id="step-2",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result=execution_result,
    )

    assert mock_run_workflow._publish_context["pullRequestUrl"] == (
        "https://github.com/org/repo/pull/674"
    )
    assert mock_run_workflow._publish_context["prMetadata"] == {
        "title": "MM-674 Source PR metadata from agent semantics",
        "body": "Jira: MM-674\nSummary: Implemented.",
        "jiraIssueKey": "MM-674",
        "moonSpecPath": "specs/674-pr-metadata",
        "source": "pr_metadata.json",
    }


@pytest.mark.asyncio
async def test_auto_publish_verified_push_maps_to_published_branch_finish_outcome(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto publish finish mapping source: docs/Workflows/WorkflowPublishing.md."""

    parameters = {"publishMode": "auto"}
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result={"outputs": {"publishResult": _auto_publish_evidence()}},
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
    )

    assert summary["finishOutcome"]["code"] == "PUBLISHED_BRANCH"
    assert summary["publish"] == {
        "mode": "auto",
        "status": "published",
        "reason": "auto publish verified push",
        "owner": "agent",
        "evidenceRequired": True,
    }
    assert summary["publishContext"]["pushed"] is True
    assert summary["publishContext"]["merged"] is False


@pytest.mark.asyncio
async def test_auto_publish_verified_merge_maps_to_published_pr_finish_outcome(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = {"publishMode": "auto"}
    evidence = _auto_publish_evidence(
        action="merge",
        pushed=False,
        merged=True,
        remoteVerified=False,
        remoteBranchHead=None,
        prUrl="https://github.com/MoonLadderStudios/MoonMind/pull/1086",
    )
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result={"outputs": {"publishResult": evidence}},
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
    )

    assert summary["finishOutcome"]["code"] == "PUBLISHED_PR"
    assert summary["publish"]["mode"] == "auto"
    assert summary["publish"]["status"] == "published"
    assert summary["publish"]["reason"] == "auto publish verified merge"
    assert summary["publish"]["owner"] == "agent"
    assert summary["publishContext"]["merged"] is True
    assert summary["publishContext"]["prUrl"] == (
        "https://github.com/MoonLadderStudios/MoonMind/pull/1086"
    )


@pytest.mark.asyncio
async def test_auto_publish_verified_merge_accepts_agent_result_metadata(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_AUTO_PUBLISH_METADATA_EVIDENCE_PATCH,
    )
    parameters = {"publishMode": "auto"}
    evidence = _auto_publish_evidence(
        skillId="pr-resolver",
        action="merge",
        pushed=False,
        merged=True,
        remoteVerified=False,
        remoteBranchHead=None,
        prUrl="https://github.com/MoonLadderStudios/MoonMind/pull/2970",
    )

    await mock_run_workflow._record_publish_result_from_execution(
        parameters=parameters,
        execution_result=AgentRunResult(metadata={"publishResult": evidence}),
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
    )

    assert summary["finishOutcome"]["code"] == "PUBLISHED_PR"
    assert summary["publish"]["status"] == "published"
    assert summary["publish"]["reason"] == "auto publish verified merge"
    assert summary["publishContext"]["skillId"] == "pr-resolver"
    assert summary["publishContext"]["prUrl"] == (
        "https://github.com/MoonLadderStudios/MoonMind/pull/2970"
    )


@pytest.mark.asyncio
async def test_auto_publish_metadata_evidence_preserves_unpatched_replay(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = {"publishMode": "auto"}
    evidence = _auto_publish_evidence(
        skillId="pr-resolver",
        action="merge",
        pushed=False,
        merged=True,
        remoteVerified=False,
        remoteBranchHead=None,
        prUrl="https://github.com/MoonLadderStudios/MoonMind/pull/2970",
    )

    await mock_run_workflow._record_publish_result_from_execution(
        parameters=parameters,
        execution_result=AgentRunResult(metadata={"publishResult": evidence}),
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
        status="failed",
        error=mock_run_workflow._publish_reason,
    )

    assert summary["finishOutcome"] == {
        "code": "FAILED",
        "stage": "publish",
        "reason": "auto_publish_evidence_missing",
    }
    assert summary["publish"]["status"] == "failed"
    assert summary["publish"]["reason"] == "auto_publish_evidence_missing"


@pytest.mark.asyncio
async def test_auto_publish_no_op_maps_to_no_commit_not_publish_disabled(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = {"publishMode": "auto"}
    evidence = _auto_publish_evidence(
        status="no_op_verified",
        action="none",
        pushed=False,
    )
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result={"outputs": {"publishResult": evidence}},
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
    )

    assert summary["finishOutcome"]["code"] == "NO_COMMIT"
    assert summary["publish"]["mode"] == "auto"
    assert summary["publish"]["status"] == "skipped"
    assert summary["publish"]["reason"] == "auto publish no-op verified"
    assert summary["publish"]["reasonCode"] == "no_commit"
    assert summary["publish"]["owner"] == "agent"


@pytest.mark.asyncio
async def test_auto_publish_missing_evidence_maps_to_publish_stage_failure(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = {"publishMode": "auto"}
    mock_run_workflow._record_publish_result(
        parameters=parameters,
        execution_result={"outputs": {}},
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters=parameters,
        status="failed",
        error=mock_run_workflow._publish_reason,
    )

    assert summary["finishOutcome"] == {
        "code": "FAILED",
        "stage": "publish",
        "reason": "auto_publish_evidence_missing",
    }
    assert summary["publish"]["mode"] == "auto"
    assert summary["publish"]["status"] == "failed"
    assert summary["publish"]["reason"] == "auto_publish_evidence_missing"
    assert summary["publish"]["owner"] == "agent"


@pytest.mark.asyncio
async def test_auto_publish_evidence_ref_is_loaded_before_recording(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    captured: list[tuple[str, Any]] = []

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> bytes:
        captured.append((activity_type, payload))
        assert activity_type == "artifact.read"
        assert getattr(payload, "artifact_ref", None) == "artifact://publish-result"
        return json.dumps(_auto_publish_evidence()).encode()

    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )

    await mock_run_workflow._record_publish_result_from_execution(
        parameters={"publishMode": "auto"},
        execution_result={
            "outputs": {
                "outputRefs": {"publish_result.json": "artifact://publish-result"}
            }
        },
    )

    assert captured
    assert mock_run_workflow._publish_status == "published"
    assert mock_run_workflow._publish_reason == "auto publish verified push"
    assert mock_run_workflow._publish_context["evidenceRef"] == (
        "artifact://publish-result"
    )


@pytest.mark.asyncio
async def test_pr_resolver_top_level_publish_evidence_ref_prevents_false_failure(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for merged resolver runs summarized as Finalizing execution."""

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_PR_RESOLVER_PUBLISH_EVIDENCE_REF_PATCH,
    )

    evidence = _auto_publish_evidence(
        skillId="pr-resolver",
        action="merge",
        pushed=False,
        merged=True,
        remoteVerified=False,
        remoteBranchHead=None,
        prUrl="https://github.com/MoonLadderStudios/MoonMind/pull/3199",
    )

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> bytes:
        assert activity_type == "artifact.read"
        assert getattr(payload, "artifact_ref", None) == "artifact://resolver-publish"
        import json

        return json.dumps(evidence).encode()

    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )

    await mock_run_workflow._record_publish_result_from_execution(
        parameters={"publishMode": "auto"},
        execution_result={
            "publishEvidence": "artifact://resolver-publish",
            "outputRefs": {
                "prResolverResult": "artifact://resolver-result",
                "publishEvidence": "artifact://resolver-publish",
            },
            "metadata": {"mergeAutomationDisposition": "merged"},
        },
    )

    assert mock_run_workflow._publish_status == "published"
    assert mock_run_workflow._publish_reason == "auto publish verified merge"
    assert mock_run_workflow._publish_context["evidenceRef"] == (
        "artifact://resolver-publish"
    )
    assert mock_run_workflow._publish_context["merged"] is True


@pytest.mark.asyncio
async def test_pr_resolver_publish_ref_preserves_unpatched_replay(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_activity = AsyncMock()
    monkeypatch.setattr(
        run_workflow_module,
        "execute_typed_activity",
        execute_activity,
    )

    await mock_run_workflow._record_publish_result_from_execution(
        parameters={"publishMode": "auto"},
        execution_result={"publishEvidence": "artifact://resolver-publish"},
    )

    execute_activity.assert_not_awaited()
    assert mock_run_workflow._publish_status == "failed"
    assert mock_run_workflow._publish_reason == "auto_publish_evidence_missing"


def test_auto_publish_record_keeps_loaded_evidence_ref_when_later_metadata_ref_exists(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_AUTO_PUBLISH_METADATA_EVIDENCE_PATCH,
    )
    mock_run_workflow._publish_context["autoPublishEvidence"] = (
        _auto_publish_evidence()
    )

    mock_run_workflow._record_auto_publish_result(
        {
            "outputs": {
                "outputRefs": {
                    "publish_result.json": "artifact://publish-result-first"
                }
            },
            "metadata": {
                "outputRefs": {
                    "publish_result.json": "artifact://publish-result-second"
                }
            },
        }
    )

    assert mock_run_workflow._publish_status == "published"
    assert mock_run_workflow._publish_context["evidenceRef"] == (
        "artifact://publish-result-first"
    )


def test_record_execution_context_preserves_provider_native_pr_record(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = mock_run_workflow._map_agent_run_result(
        AgentRunResult(
            summary="Provider created PR.",
            metadata={
                "prMetadata": {
                    "title": "Generic title should not win",
                    "body": "Generic body should not win.",
                },
                "providerNativePullRequest": {
                    "url": "https://github.com/org/repo/pull/676",
                    "readinessState": "pending",
                    "headBranch": "feature/provider-native",
                    "baseBranch": "main",
                    "source": "jules",
                    "metadata": {
                        "title": "MM-676 Capture provider-native PR metadata",
                        "body": "Jira: MM-676\nSummary: Captured provider metadata.",
                        "provider": "jules",
                    },
                },
            },
        )
    )

    mock_run_workflow._record_execution_context(
        node_id="step-provider-native",
        execution_result=execution_result,
    )

    assert mock_run_workflow._publish_context["pullRequestUrl"] == (
        "https://github.com/org/repo/pull/676"
    )
    assert mock_run_workflow._publish_context["readinessState"] == "pending"
    assert mock_run_workflow._publish_context["branch"] == "feature/provider-native"
    assert mock_run_workflow._publish_context["baseRef"] == "main"
    assert mock_run_workflow._publish_context["providerNativePullRequest"] == {
        "url": "https://github.com/org/repo/pull/676",
        "readinessState": "pending",
        "headBranch": "feature/provider-native",
        "baseBranch": "main",
        "source": "jules",
    }
    assert mock_run_workflow._publish_context["providerNativePrMetadata"] == {
        "title": "MM-676 Capture provider-native PR metadata",
        "body": "Jira: MM-676\nSummary: Captured provider metadata.",
        "provider": "jules",
    }
    assert mock_run_workflow._publish_context["prMetadata"] == {
        "title": "MM-676 Capture provider-native PR metadata",
        "body": "Jira: MM-676\nSummary: Captured provider metadata.",
    }


def test_jira_implement_task_makes_pr_publish_optional(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    assert mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "skills": {
                    "include": [
                        {"name": "jira-implement"},
                    ]
                }
            },
        }
    )
    # appliedStepTemplates alone must not relax PR creation for the whole run.
    assert not mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        }
    )
    # It only relaxes the no-commit publish fallback when opted in explicitly.
    assert mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        },
        include_applied_templates=True,
    )
    assert mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "tool": {"type": "skill", "name": "auto"},
                "skill": {"name": "auto"},
                "appliedStepTemplates": [
                    {"slug": "jira-implement", "version": "1.0.0"},
                ],
            },
        },
        include_applied_templates=True,
    )
    # Templates carrying only presetSlug must also relax the no-commit fallback.
    assert mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "tool": {"type": "skill", "name": "auto"},
                "skill": {"name": "auto"},
                "appliedStepTemplates": [
                    {"presetSlug": "jira-implement", "version": "1.0.0"},
                ],
            },
        },
        include_applied_templates=True,
    )
    assert mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "appliedStepTemplates": [
                    {
                        "slug": "leaf",
                        "version": "1.0.0",
                        "skill": {"name": "jira-implement"},
                    },
                ],
            },
        }
    )


def test_github_issue_template_only_relaxes_pr_after_no_commit_evidence(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._canonical_no_commit_outcome_enabled = True
    parameters = {
        "publishMode": "pr",
        "workflow": {
            "tool": {"type": "skill", "name": "auto"},
            "skill": {"name": "auto"},
            "appliedStepTemplates": [
                {"slug": "github-issue-implement", "version": "1.0.0"},
            ],
        },
    }

    assert not mock_run_workflow._pr_publish_optional_for_task(
        parameters,
        include_applied_templates=True,
    )
    assert mock_run_workflow._is_canonical_no_commit_task(parameters)


def test_record_declared_side_effect_tolerates_missing_record(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run_workflow._canonical_no_commit_outcome_enabled = True
    monkeypatch.setattr(
        mock_run_workflow,
        "_record_step_side_effect",
        lambda *_args, **_kwargs: None,
    )

    mock_run_workflow._record_declared_side_effect(
        logical_step_id="finalize-github-issue",
        outputs={
            "sideEffect": {
                "effectClass": "external_non_idempotent",
                "kind": "github",
                "operation": "github.issue.close",
            }
        },
    )


def test_jira_applied_template_without_composition_marks_task_jira_backed(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    assert (
        mock_run_workflow._canonical_jira_issue_key_from_parameters(
            {
                "task": {
                    "instructions": "Run Jira Implement for MM-719.",
                    "appliedStepTemplates": [
                        {"slug": "jira-implement", "version": "1.0.0"},
                    ],
                },
            }
        )
        == "MM-719"
    )


def test_plain_text_blocked_result_short_circuits_publish(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    message = mock_run_workflow._blocked_outcome_message(
        {
            "outputs": {
                "operator_summary": (
                    "## Result: BLOCKED - cannot transition Jira issue MM-675\n\n"
                    "The assessment verdict artifact is unavailable."
                )
            }
        }
    )

    assert message is not None
    assert message.startswith("Workflow blocked by plan step:")
    assert "cannot transition Jira issue MM-675" in message


def test_jira_implement_applied_template_makes_no_commit_publish_optional(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    assert mock_run_workflow._pr_publish_optional_for_task(
        {
            "publishMode": "pr",
            "task": {
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.0.0",
                        "composition": {"includes": []},
                    }
                ],
            },
        },
        include_applied_templates=True,
    )


def test_jira_implement_applied_template_legacy_shapes_make_pr_publish_optional(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    parameters = {
        "publishMode": "pr",
        "task": {
            "appliedStepTemplates": [
                {"presetSlug": "jira-implement", "version": "1.0.0"},
            ],
        },
    }

    assert mock_run_workflow._task_applied_template_slugs(
        parameters,
        parameters["task"],
    ) == {"jira-implement"}
    assert mock_run_workflow._pr_publish_optional_for_task(
        parameters,
        include_applied_templates=True,
    )


def test_jira_implement_applied_template_composition_includes_make_pr_publish_optional(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    parameters = {
        "publishMode": "pr",
        "task": {
            "appliedStepTemplates": [
                {
                    "slug": "parent-flow",
                    "composition": {
                        "includes": [
                            {"presetSlug": "jira-implement"},
                        ],
                    },
                }
            ],
        },
    }

    assert mock_run_workflow._task_applied_template_slugs(
        parameters,
        parameters["task"],
    ) == {"parent-flow", "jira-implement"}
    assert not mock_run_workflow._pr_publish_optional_for_task(
        parameters,
        include_applied_templates=True,
    )

    parameters["task"]["appliedStepTemplates"][0].pop("slug")
    assert mock_run_workflow._pr_publish_optional_for_task(
        parameters,
        include_applied_templates=True,
    )


def test_native_pr_branch_resolution_keeps_legacy_branch_only_replay_shape(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    head_branch, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={"push_base_branch": "trunk"},
        workspace_spec={"branch": "feature/existing"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "release"},
    )

    assert head_branch == "feature/existing"
    assert base_branch == "feature/existing"

def test_native_pr_branch_resolution_uses_patched_publish_defaults(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_patched(patch_id: str) -> bool:
        return patch_id == NATIVE_PR_BRANCH_DEFAULTS_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    head_branch, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={"push_base_branch": "trunk"},
        workspace_spec={"branch": "feature/existing"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "release"},
    )

    assert head_branch == ""
    assert base_branch == "release"

def test_native_pr_branch_resolution_mm669_uses_runtime_owned_head_sources(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_patched(patch_id: str) -> bool:
        return patch_id == NATIVE_PR_BRANCH_DEFAULTS_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    head_branch, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={"targetBranch": "legacy-top-level-head"},
        agent_outputs={},
        workspace_spec={},
        last_node_inputs={"targetBranch": "planner-generated-head"},
        publish_payload={"prBaseBranch": "main"},
    )

    assert head_branch == "planner-generated-head"
    assert base_branch == "main"

    head_branch, _ = mock_run_workflow._resolve_native_pr_branches(
        parameters={"targetBranch": "legacy-top-level-head"},
        agent_outputs={},
        workspace_spec={"targetBranch": "workspace-head"},
        last_node_inputs={"targetBranch": "planner-generated-head"},
        publish_payload={"prBaseBranch": "main"},
    )

    assert head_branch == "workspace-head"

    head_branch, _ = mock_run_workflow._resolve_native_pr_branches(
        parameters={"targetBranch": "legacy-top-level-head"},
        agent_outputs={"branch": "provider-head"},
        workspace_spec={"targetBranch": "workspace-head"},
        last_node_inputs={"targetBranch": "planner-generated-head"},
        publish_payload={"prBaseBranch": "main"},
    )

    assert head_branch == "provider-head"

def test_native_pr_push_status_gate_preserves_legacy_protected_branch_fallback(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={
            "outputs": {
                "push_status": "protected_branch",
                "push_branch": "feature/existing",
            }
        },
    )

    assert mock_run_workflow._native_pr_push_status_blocks_creation(
        "protected_branch"
    ) is False
    assert mock_run_workflow._publish_status is None

def test_native_pr_push_status_gate_blocks_protected_branch_when_patched(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_patched(patch_id: str) -> bool:
        return patch_id == NATIVE_PR_PUSH_STATUS_GATE_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={
            "outputs": {
                "push_status": "protected_branch",
                "push_branch": "feature/existing",
            }
        },
    )

    assert mock_run_workflow._native_pr_push_status_blocks_creation(
        "protected_branch"
    ) is True
    assert mock_run_workflow._publish_status == "failed"
    assert "feature/existing" in (mock_run_workflow._publish_reason or "")

def test_native_pr_push_status_gate_blocks_unrecovered_lease_conflict(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_patched(patch_id: str) -> bool:
        return patch_id == NATIVE_PR_LEASE_CONFLICT_GATE_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={
            "outputs": {
                "push_status": "lease_conflict",
                "push_branch": "feature/existing",
                "push_error": "! [rejected] feature/existing (stale info)",
            }
        },
    )

    assert mock_run_workflow._native_pr_push_status_blocks_creation(
        "lease_conflict"
    ) is True
    assert mock_run_workflow._publish_status == "failed"
    assert "remote branch 'feature/existing' changed" in (
        mock_run_workflow._publish_reason or ""
    )

def test_publish_failure_preservation_is_patch_gated(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run_workflow._publish_status = "failed"
    mock_run_workflow._publish_reason = "first publish failure"

    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: False)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={"outputs": {"push_status": "no_commits"}},
    )

    assert mock_run_workflow._publish_status == "skipped"

    mock_run_workflow._publish_status = "failed"
    mock_run_workflow._publish_reason = "first publish failure"

    def fake_patched(patch_id: str) -> bool:
        return patch_id == run_workflow_module.RUN_STOP_ON_PUBLISH_HANDOFF_FAILURE_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={"outputs": {"push_status": "no_commits"}},
    )

    assert mock_run_workflow._publish_status == "failed"
    assert mock_run_workflow._publish_reason == "first publish failure"

def test_record_execution_context_resets_last_step_fields_when_current_node_has_no_summary(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "summary": "Completed the substantive step.",
                "diagnostics_ref": "diag-1",
            }
        },
    )

    mock_run_workflow._record_execution_context(
        node_id="step-2",
        execution_result={"outputs": {}},
    )

    assert mock_run_workflow._last_step_id == "step-2"
    assert mock_run_workflow._last_step_summary is None
    assert mock_run_workflow._last_diagnostics_ref is None

def test_record_execution_context_summarizes_trusted_jira_downstream_outputs(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="create-implement-tasks",
        execution_result={
            "outputs": {
                "jiraOrchestration": {
                    "status": "no_downstream_tasks",
                    "workflowStatus": "no_downstream_workflows",
                    "storyCount": 7,
                    "createdTaskCount": 0,
                    "createdWorkflowCount": 0,
                    "dependencyCount": 0,
                    "failures": [
                        {
                            "errorCode": "task_creation_failed",
                            "message": "Missing required template input 'jira_issue_key'.",
                        }
                    ],
                }
            }
        },
    )

    assert mock_run_workflow._last_step_id == "create-implement-tasks"
    assert mock_run_workflow._last_step_summary == (
        "Jira downstream workflow creation no_downstream_workflows "
        "(createdWorkflows=0; stories=7; dependencies=0; "
        "firstFailure=task_creation_failed: Missing required template input "
        "'jira_issue_key')."
    )
    assert mock_run_workflow._operator_summary == mock_run_workflow._last_step_summary


def test_trusted_jira_output_summary_omits_empty_reason_suffix() -> None:
    summary = MoonMindRunWorkflow._trusted_jira_output_summary(
        {"storyOutput": {"status": "jira_blocked", "reason": ""}}
    )

    assert summary == "Jira story output finished with status jira_blocked."


def test_trusted_output_summary_uses_github_workflow_terminology() -> None:
    issue_summary = MoonMindRunWorkflow._trusted_jira_output_summary(
        {
            "storyOutput": {
                "status": "github_created",
                "createdCount": 2,
                "eligibleStoryCount": 2,
                "storyCount": 3,
                "dependencyMode": "none",
                "dependencyCount": 0,
            }
        }
    )
    workflow_summary = MoonMindRunWorkflow._trusted_jira_output_summary(
        {
            "githubWorkflowOrchestration": {
                "status": "completed",
                "createdWorkflowCount": 2,
                "storyCount": 2,
                "dependencyCount": 1,
            }
        }
    )

    assert issue_summary == (
        "Created GitHub issues; created=2; eligible=2; stories=3; "
        "dependencyMode=none; dependencyCount=0."
    )
    assert workflow_summary == (
        "GitHub downstream workflow creation completed "
        "(createdWorkflows=2; stories=2; workflowDependencies=1)."
    )


def test_record_execution_context_scrubs_operator_summary_and_ignores_negative_commit_count(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "operator_summary": "Final report token ghp_abcdefghijklmnopqrstuvwxyz123456",
                "push_branch": "feature/no-op",
                "push_base_ref": "origin/main",
                "push_commit_count": -1,
            }
        },
    )

    assert mock_run_workflow._operator_summary is not None
    assert "[REDACTED]" in mock_run_workflow._operator_summary
    assert "ghp_" not in mock_run_workflow._operator_summary
    assert "commitCount" not in mock_run_workflow._publish_context


def test_determine_publish_completion_fails_when_pr_publish_creates_no_pr(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._integration = None

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert message == "publishMode 'pr' requested but no PR was created"
    assert publish_failure is True


def test_moonspec_verify_gate_blocks_pr_publish_completion(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="tpl:jira-orchestrate:1.0.0:13:verify",
        outputs={
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "operator_summary": "Overview route still renders full detail sections.",
            "diagnostics_ref": "art_verify_report",
        },
    )

    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert publish_failure is True
    assert "MoonSpec verification did not approve publication" in message
    assert "ADDITIONAL_WORK_NEEDED" in message
    assert "art_verify_report" in message
    assert mock_run_workflow._publish_status == "not_required"
    assert mock_run_workflow._publish_context["publicationBlockedBy"] == (
        "moonspec_verify"
    )


@pytest.mark.parametrize(
    "verdict",
    [
        "ADDITIONAL_WORK_NEEDED",
        "BLOCKED",
        "FAILED_UNRECOVERABLE",
        "NO_DETERMINATION",
        "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
    ],
)
def test_accepted_verifier_control_evidence_preserves_semantic_verdict(
    mock_run_workflow: MoonMindRunWorkflow,
    verdict: str,
) -> None:
    assert mock_run_workflow._accepted_verifier_semantic_verdict(verdict) == verdict


def test_moonspec_verify_gate_detects_remaining_remediation_budget(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    ordered_nodes = [
        {
            "id": "verify-1",
            "inputs": {
                "title": "Verify remediation attempt 1 of 6",
                "selectedSkill": "moonspec-verify",
            },
        },
        {
            "id": "remediate-2",
            "annotations": {"jiraOrchestrateRole": "moonspec-remediation"},
            "skill": {"id": "moonspec-implement"},
            "inputs": {
                "title": "Remediate remaining gaps — attempt 2 of 6",
            },
        },
        {
            "id": "create-pr",
            "inputs": {
                "title": "Create pull request",
                "annotations": {"jiraOrchestrateRole": "pull-request-handoff"},
            },
        },
    ]

    assert mock_run_workflow._has_remaining_moonspec_remediation_step(
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    assert not mock_run_workflow._has_remaining_moonspec_remediation_step(
        ordered_nodes=ordered_nodes,
        current_index=1,
    )


def test_moonspec_verify_gate_ignores_remediation_steps_over_budget(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    ordered_nodes = [
        {
            "id": "verify-1",
            "inputs": {
                "title": "Verify remediation attempt 1 of 1",
                "selectedSkill": "moonspec-verify",
            },
        },
        {
            "id": "remediate-2",
            "annotations": {
                "jiraOrchestrateRole": "moonspec-remediation",
                "moonSpecRemediationAttempt": 2,
                "moonSpecRemediationMaxAttempts": 1,
            },
            "skill": {"id": "moonspec-implement"},
            "inputs": {"title": "Remediate remaining gaps — attempt 2 of 1"},
        },
    ]

    assert not mock_run_workflow._has_remaining_moonspec_remediation_step(
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    reason = mock_run_workflow._moonspec_remediation_loop_skip_reason(
        ordered_nodes[1],
        tool_name="auto",
        node_inputs=ordered_nodes[1]["inputs"],
    )
    assert reason == (
        "Skipped MoonSpec remediation loop step 2; configured maximum is 1."
    )


def test_moonspec_verify_gate_skips_loop_steps_after_passing_verdict(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    verify_node = {
        "id": "verify-remediation-2",
        "annotations": {
            "issueImplementRole": "moonspec-verification-gate",
            "moonSpecRemediationAttempt": 2,
            "moonSpecRemediationMaxAttempts": 6,
        },
        "inputs": {
            "title": "Verify remediation attempt 2 of 6",
            "selectedSkill": "moonspec-verify",
        },
    }
    mock_run_workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    reason = mock_run_workflow._moonspec_remediation_loop_skip_reason(
        verify_node,
        tool_name="auto",
        node_inputs=verify_node["inputs"],
    )

    assert reason == (
        "Skipped MoonSpec remediation loop step because verification already "
        "passed with verdict FULLY_IMPLEMENTED."
    )


def test_user_workflow_initializes_one_dynamic_loop_and_materializes_one_pair(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    loop = {
        "kind": "remediation_loop",
        "loopId": "issue-implementation-remediation",
        "remediationTool": {"type": "skill", "name": "auto"},
        "verificationTool": {"type": "skill", "name": "moonspec-verify"},
        "workspacePolicy": "continue_from_loop_head",
        "budgets": {
            "hardMaxAttempts": 6,
            "maxConsecutiveSemanticNoProgress": 2,
            "maxRepeatedFailureSignature": 2,
        },
        "terminalPolicy": {
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        "sideEffectPolicy": "workflow_owned",
        "publicationPolicy": "evaluate_after_terminal",
        "continueAsNewAttemptThreshold": 3,
    }
    mock_run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[
            {
                "id": "controller",
                "annotations": {"remediationLoop": loop},
            }
        ]
    )

    projection = mock_run_workflow._publish_context["remediationLoop"]
    assert projection["status"] == "initial_verification_pending"
    assert projection["attemptOrdinal"] == 0
    remediation, verification = (
        mock_run_workflow._materialize_remediation_attempt(ordinal=1)
    )
    assert remediation["annotations"]["moonSpecRemediationAttempt"] == 1
    assert verification["dependsOn"] == [remediation["id"]]

@pytest.mark.asyncio
async def test_dynamic_verifier_persists_decision_and_appends_only_admitted_pair(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    loop = {
        "kind": "remediation_loop",
        "loopId": "issue-implementation-remediation",
        "remediationTool": {"type": "skill", "name": "auto"},
        "verificationTool": {"type": "skill", "name": "moonspec-verify"},
        "workspacePolicy": "continue_from_loop_head",
        "budgets": {"hardMaxAttempts": 6},
        "terminalPolicy": {
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        "sideEffectPolicy": "workflow_owned",
        "publicationPolicy": "evaluate_after_terminal",
    }
    mock_run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[
            {"id": "controller", "annotations": {"remediationLoop": loop}}
        ]
    )
    mock_run_workflow._step_ledger_rows = []
    mock_run_workflow._write_json_artifact = AsyncMock(
        return_value="artifact://decision/D0"
    )
    ordered_nodes: list[dict[str, Any]] = []

    admitted = await (
        mock_run_workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=ordered_nodes,
            verdict="ADDITIONAL_WORK_NEEDED",
            gate_result_ref="artifact://verification/V0",
            remaining_work_ref="artifact://remaining/R0",
        )
    )

    assert admitted is True
    assert len(ordered_nodes) == 2
    remediation, verification = ordered_nodes
    assert remediation["id"].endswith(":remediation:1")
    assert verification["dependsOn"] == [remediation["id"]]
    assert [row["status"] for row in mock_run_workflow._step_ledger_rows] == [
        "ready",
        "pending",
    ]
    projection = mock_run_workflow._publish_context["remediationLoop"]
    assert projection["attemptOrdinal"] == 1
    assert projection["status"] == "remediation_running"
    assert projection["latestVerdict"] == "ADDITIONAL_WORK_NEEDED"
    assert projection["continuationDecisionRef"] == "artifact://decision/D0"
    mock_run_workflow._write_json_artifact.assert_awaited_once()


@pytest.mark.asyncio
async def test_dynamic_verifier_inserts_attempt_before_handoff_nodes(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    loop = {
        "kind": "remediation_loop",
        "loopId": "issue-implementation-remediation",
        "remediationTool": {"type": "agent_runtime", "name": "auto"},
        "verificationTool": {"type": "agent_runtime", "name": "moonspec-verify"},
        "workspacePolicy": "continue_from_loop_head",
        "budgets": {"hardMaxAttempts": 2},
        "terminalPolicy": {
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        "sideEffectPolicy": "workflow_owned",
        "publicationPolicy": "evaluate_after_terminal",
    }
    mock_run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[{"id": "controller", "annotations": {"remediationLoop": loop}}]
    )
    mock_run_workflow._step_ledger_rows = []
    mock_run_workflow._write_json_artifact = AsyncMock(
        return_value="artifact://decision/D0"
    )
    ordered_nodes = [
        {"id": "initial-verifier"},
        {"id": "controller"},
        {"id": "create-pr"},
    ]

    admitted = await mock_run_workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V0",
        remaining_work_ref="artifact://remaining/R0",
        current_index=1,
    )

    assert admitted is True
    assert ordered_nodes[1]["id"].endswith(":remediation:1")
    assert ordered_nodes[2]["id"].endswith(":verification:1")
    assert [node["id"] for node in ordered_nodes[3:]] == [
        "controller",
        "create-pr",
    ]


@pytest.mark.asyncio
async def test_dynamic_verifier_terminal_decision_does_not_append_attempt(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    loop = {
        "kind": "remediation_loop",
        "loopId": "issue-implementation-remediation",
        "remediationTool": {"type": "skill", "name": "auto"},
        "verificationTool": {"type": "skill", "name": "moonspec-verify"},
        "workspacePolicy": "continue_from_loop_head",
        "budgets": {"hardMaxAttempts": 1},
        "terminalPolicy": {
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        "sideEffectPolicy": "workflow_owned",
        "publicationPolicy": "evaluate_after_terminal",
    }
    mock_run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[
            {"id": "controller", "annotations": {"remediationLoop": loop}}
        ]
    )
    mock_run_workflow._step_ledger_rows = []
    mock_run_workflow._write_json_artifact = AsyncMock(
        return_value="artifact://decision/pass"
    )
    ordered_nodes: list[dict[str, Any]] = []

    admitted = await (
        mock_run_workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=ordered_nodes,
            verdict="FULLY_IMPLEMENTED",
            gate_result_ref="artifact://verification/pass",
            remaining_work_ref=None,
        )
    )

    assert admitted is False
    assert ordered_nodes == []
    assert (
        mock_run_workflow._publish_context["remediationLoop"]["status"]
        == "accepted"
    )


@pytest.mark.asyncio
async def test_dynamic_attempt_captures_head_verifies_and_admits_next_pair(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    loop = {
        "kind": "remediation_loop",
        "loopId": "issue-implementation-remediation",
        "remediationTool": {"type": "skill", "name": "auto"},
        "verificationTool": {"type": "skill", "name": "moonspec-verify"},
        "workspacePolicy": "continue_from_loop_head",
        "budgets": {"hardMaxAttempts": 6},
        "terminalPolicy": {
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        "sideEffectPolicy": "workflow_owned",
        "publicationPolicy": "evaluate_after_terminal",
    }
    mock_run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[
            {"id": "controller", "annotations": {"remediationLoop": loop}}
        ]
    )
    mock_run_workflow._step_ledger_rows = []
    mock_run_workflow._write_json_artifact = AsyncMock(
        side_effect=["artifact://decision/D0", "artifact://decision/D1"]
    )
    ordered_nodes: list[dict[str, Any]] = []
    await mock_run_workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V0",
        remaining_work_ref="artifact://remaining/R0",
    )
    remediation, verification = ordered_nodes
    mock_run_workflow._remediation_workspace_head = (
        RemediationWorkspaceHead.model_validate(_remediation_head_payload())
    )
    remediation_inputs = dict(remediation["inputs"])
    remediation_inputs["remediationWorkspaceHead"] = _remediation_head_payload()
    mock_run_workflow._inject_remediation_workspace_baseline(
        node=remediation,
        node_inputs=remediation_inputs,
    )
    mock_run_workflow._advance_remediation_workspace_head(
        node=remediation,
        node_inputs=remediation_inputs,
        execution_result={
            "outputs": {
                "remediationAttemptOutput": {
                    "attemptEvidenceRef": "artifact://attempt/1",
                    "parentCheckpointRef": "artifact://workspace/C0",
                    "parentWorkspaceDigest": "sha256:c0",
                    "outputCheckpointRef": "artifact://workspace/C1",
                    "outputWorkspaceDigest": "sha256:c1",
                    "checkpointManifestRef": "artifact://manifest/C1",
                    "outcome": "candidate_captured",
                }
            }
        },
        step_execution_id="wf:run:loop:remediation:1",
    )
    assert mock_run_workflow._publish_context["remediationLoop"]["status"] == (
        "verification_pending"
    )
    verification_inputs = dict(verification["inputs"])
    mock_run_workflow._inject_remediation_verification_baseline(
        node=verification,
        node_inputs=verification_inputs,
    )
    assert verification_inputs["remediationWorkspaceHeadRef"] == (
        "artifact://workspace/C1"
    )

    admitted = await mock_run_workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V1",
        remaining_work_ref="artifact://remaining/R1",
    )

    assert admitted is True
    assert len(ordered_nodes) == 4
    assert ordered_nodes[2]["id"].endswith(":remediation:2")
    projection = mock_run_workflow._publish_context["remediationLoop"]
    assert projection["attemptOrdinal"] == 2
    assert projection["workspaceHeadRef"] == "artifact://workspace/C1"
    assert len(projection["materializedAttempts"]) == 2


@pytest.mark.parametrize(
    "phase",
    [
        "initial_verification_pending",
        "initial_verification_running",
        "initial_verification_evaluating",
        "remediation_pending",
        "remediation_running",
        "candidate_capturing",
        "verification_pending",
        "verification_running",
        "verification_evaluating",
        "continuation_deciding",
    ],
)
def test_dynamic_loop_restart_at_each_nonterminal_phase_preserves_identity_and_evidence(
    mock_run_workflow: MoonMindRunWorkflow,
    phase: str,
) -> None:
    """A worker restart restores state; it never admits or duplicates a pair."""
    from moonmind.workflows.temporal.remediation_loop import RemediationLoopSpec

    mock_run_workflow._remediation_loop_spec = RemediationLoopSpec.model_validate(
        {
            "loopId": "loop",
            "remediationTool": {"type": "skill", "name": "auto"},
            "verificationTool": {"type": "skill", "name": "moonspec-verify"},
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {"hardMaxAttempts": 6},
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )
    carried_nodes = [
        {"id": "wf:old-run:loop:remediation:1"},
        {
            "id": "wf:old-run:loop:verification:1",
            "dependsOn": ["wf:old-run:loop:remediation:1"],
        },
    ]
    carried_rows = [
        {
            "logicalStepId": node["id"],
            "status": "executing" if index == 0 else "pending",
            "annotations": {
                "remediationLoopId": "loop",
                "moonSpecRemediationAttempt": 1,
                "issueImplementRole": (
                    "moonspec-remediation"
                    if index == 0
                    else "moonspec-verification-gate"
                ),
            },
        }
        for index, node in enumerate(carried_nodes)
    ]
    mock_run_workflow._remediation_loop_continuation = {
        "schemaVersion": 1,
        "state": {
            "loopId": "loop",
            "attemptOrdinal": 1,
            "phase": phase,
            "workspaceHeadRef": "artifact://workspace/C1",
            "latestVerificationRef": "artifact://verification/V0",
            "continuationDecisionRef": "artifact://decision/D0",
            "consumedBudgets": {"attempts": 1},
            "sourceRunId": "old-run",
        },
        "orderedNodes": carried_nodes,
        "stepLedgerRows": carried_rows,
    }
    restored_nodes: list[dict[str, Any]] = []

    mock_run_workflow._restore_remediation_loop_continuation(
        ordered_nodes=restored_nodes
    )

    state = mock_run_workflow._remediation_loop_state
    assert state is not None
    assert state.phase.value == phase
    assert state.attempt_ordinal == 1
    assert state.consumed_budgets.attempts == 1
    assert state.workspace_head_ref == "artifact://workspace/C1"
    assert state.latest_verification_ref == "artifact://verification/V0"
    assert state.continuation_decision_ref == "artifact://decision/D0"
    assert restored_nodes == carried_nodes
    assert [row["logicalStepId"] for row in mock_run_workflow._step_ledger_rows] == [
        node["id"] for node in carried_nodes
    ]
    assert len(
        mock_run_workflow._publish_context["remediationLoop"][
            "materializedAttempts"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_dynamic_loop_pause_before_next_attempt_preserves_loop_head(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopState,
    )

    mock_run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        phase="remediation_pending",
        attemptOrdinal=1,
        workspaceHeadRef="artifact://workspace/C1",
        latestVerificationRef="artifact://verification/V1",
        continuationDecisionRef="artifact://decision/D1",
        consumedBudgets=ConsumedRemediationBudgets(attempts=1),
    )
    mock_run_workflow._paused = True
    waits = 0

    async def resume_at_boundary(predicate: Callable[[], bool]) -> None:
        nonlocal waits
        waits += 1
        assert predicate() is False
        mock_run_workflow._paused = False
        assert predicate() is True

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", resume_at_boundary
    )

    await mock_run_workflow._wait_if_paused_at_safe_boundary()

    state = mock_run_workflow._remediation_loop_state
    assert waits == 1
    assert state is not None
    assert state.phase.value == "remediation_pending"
    assert state.attempt_ordinal == 1
    assert state.workspace_head_ref == "artifact://workspace/C1"
    assert state.continuation_decision_ref == "artifact://decision/D1"


@pytest.mark.asyncio
async def test_dynamic_loop_active_cancellation_keeps_terminal_evidence_and_pair(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopState,
    )

    mock_run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        phase="verification_running",
        attemptOrdinal=1,
        workspaceHeadRef="artifact://workspace/C1",
        latestVerificationRef="artifact://verification/V0",
        continuationDecisionRef="artifact://decision/D0",
        consumedBudgets=ConsumedRemediationBudgets(attempts=1),
    )
    mock_run_workflow._step_ledger_rows = [
        {"logicalStepId": "wf:run:loop:remediation:1", "status": "completed"},
        {"logicalStepId": "wf:run:loop:verification:1", "status": "executing"},
    ]
    mock_run_workflow._paused = True

    async def cancel_at_boundary(predicate: Callable[[], bool]) -> None:
        assert predicate() is False
        mock_run_workflow._cancel_requested = True
        assert predicate() is True

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", cancel_at_boundary
    )

    await mock_run_workflow._wait_if_paused_at_safe_boundary()

    state = mock_run_workflow._remediation_loop_state
    assert mock_run_workflow._cancel_requested is True
    assert state is not None
    assert state.phase.value == "verification_running"
    assert state.attempt_ordinal == 1
    assert state.workspace_head_ref == "artifact://workspace/C1"
    assert state.latest_verification_ref == "artifact://verification/V0"
    assert state.continuation_decision_ref == "artifact://decision/D0"
    assert [row["logicalStepId"] for row in mock_run_workflow._step_ledger_rows] == [
        "wf:run:loop:remediation:1",
        "wf:run:loop:verification:1",
    ]


@pytest.mark.asyncio
async def test_dynamic_loop_continue_as_new_carries_compact_state_without_reset(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = {
        "kind": "remediation_loop",
        "loopId": "issue-implementation-remediation",
        "remediationTool": {"type": "skill", "name": "auto"},
        "verificationTool": {"type": "skill", "name": "moonspec-verify"},
        "workspacePolicy": "continue_from_loop_head",
        "budgets": {"hardMaxAttempts": 6},
        "terminalPolicy": {
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        "sideEffectPolicy": "workflow_owned",
        "publicationPolicy": "evaluate_after_terminal",
        "continueAsNewAttemptThreshold": 1,
    }
    mock_run_workflow._original_input_payload = {
        "workflow_type": "MoonMind.UserWorkflow",
        "initial_parameters": {"repo": "org/repo"},
        "plan_artifact_ref": "artifact://plan/1",
    }
    mock_run_workflow._initialize_remediation_loop_controller(
        ordered_nodes=[
            {"id": "controller", "annotations": {"remediationLoop": loop}}
        ]
    )
    mock_run_workflow._step_ledger_rows = []
    mock_run_workflow._write_json_artifact = AsyncMock(
        side_effect=["artifact://decision/D0", "artifact://decision/D1"]
    )
    ordered_nodes: list[dict[str, Any]] = []
    await mock_run_workflow._evaluate_dynamic_remediation_verification(
        ordered_nodes=ordered_nodes,
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://verification/V0",
        remaining_work_ref="artifact://remaining/R0",
    )
    mock_run_workflow._remediation_loop_state = (
        mock_run_workflow._remediation_loop_state.model_copy(
            update={"phase": "verification_pending"}
        )
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_REMEDIATION_LOOP_CONTINUE_AS_NEW_PATCH,
    )
    carried: list[dict[str, Any]] = []

    def capture_continue(payload: dict[str, Any]) -> None:
        carried.append(payload)
        raise RuntimeError("continued")

    monkeypatch.setattr(
        run_workflow_module.workflow, "continue_as_new", capture_continue
    )

    with pytest.raises(RuntimeError, match="continued"):
        await mock_run_workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=ordered_nodes,
            verdict="ADDITIONAL_WORK_NEEDED",
            gate_result_ref="artifact://verification/V1",
            remaining_work_ref="artifact://remaining/R1",
        )

    continuation = carried[0]["remediation_loop_continuation"]
    assert continuation["schemaVersion"] == 1
    assert continuation["state"]["attemptOrdinal"] == 2
    assert continuation["state"]["consumedBudgets"]["attempts"] == 2
    assert continuation["state"]["continuationDecisionRef"] == (
        "artifact://decision/D1"
    )
    assert len(continuation["orderedNodes"]) == 4


def test_persisted_loop_decision_is_authority_for_publication_projection(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopState,
    )

    mock_run_workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"
    mock_run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        phase="stopped_remaining_work",
        consumedBudgets=ConsumedRemediationBudgets(),
        latestVerdict="ADDITIONAL_WORK_NEEDED",
        continuationDecisionRef="artifact://decision/terminal",
    )

    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    assert "ADDITIONAL_WORK_NEEDED" in mock_run_workflow._publish_reason


@pytest.mark.asyncio
async def test_duplicate_verifier_delivery_cannot_admit_an_attempt_twice(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopSpec,
        RemediationLoopState,
    )

    mock_run_workflow._remediation_loop_spec = RemediationLoopSpec.model_validate(
        {
            "loopId": "loop",
            "remediationTool": {"type": "skill", "name": "auto"},
            "verificationTool": {"type": "skill", "name": "moonspec-verify"},
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {"hardMaxAttempts": 6},
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )
    mock_run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        phase="remediation_running",
        attemptOrdinal=1,
        consumedBudgets=ConsumedRemediationBudgets(attempts=1),
        latestVerdict="ADDITIONAL_WORK_NEEDED",
        latestVerificationRef="artifact://verification/V0",
        continuationDecisionRef="artifact://decision/D0",
    )
    mock_run_workflow._step_ledger_rows = [
        {
            "logicalStepId": "wf:run:loop:remediation:1",
            "status": "ready",
            "annotations": {
                "remediationLoopId": "loop",
                "moonSpecRemediationAttempt": 1,
                "issueImplementRole": "moonspec-remediation",
            },
        },
        {
            "logicalStepId": "wf:run:loop:verification:1",
            "status": "pending",
            "annotations": {
                "remediationLoopId": "loop",
                "moonSpecRemediationAttempt": 1,
                "issueImplementRole": "moonspec-verification-gate",
            },
        },
    ]
    ordered_nodes = [{"id": "remediation:1"}, {"id": "verification:1"}]

    with pytest.raises(ValueError):
        await mock_run_workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=ordered_nodes,
            verdict="ADDITIONAL_WORK_NEEDED",
            gate_result_ref="artifact://verification/V0",
            remaining_work_ref="artifact://remaining/R0",
        )

    assert len(ordered_nodes) == 2
    assert mock_run_workflow._remediation_loop_state.consumed_budgets.attempts == 1


def test_contract_repair_and_evidence_retry_do_not_consume_semantic_attempt_budget(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopState,
    )

    mock_run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        phase="verification_running",
        attemptOrdinal=2,
        consumedBudgets=ConsumedRemediationBudgets(
            attempts=2,
            evidenceRetries=1,
            contractRepairs=1,
        ),
    )
    before = mock_run_workflow._remediation_loop_state.consumed_budgets.attempts
    mock_run_workflow._prepare_moonspec_contract_repair_attempt("verify-2")

    assert mock_run_workflow._step_execution_uses_fresh_source(
        "verify-2", attempt=1
    )
    assert mock_run_workflow._remediation_loop_state.consumed_budgets.attempts == before


@pytest.mark.asyncio
async def test_finish_summary_reuses_persisted_loop_decision_projection(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import (
        ConsumedRemediationBudgets,
        RemediationLoopState,
    )

    mock_run_workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        phase="stopped_remaining_work",
        attemptOrdinal=2,
        consumedBudgets=ConsumedRemediationBudgets(attempts=2),
        latestVerdict="ADDITIONAL_WORK_NEEDED",
        continuationDecisionRef="artifact://decision/terminal",
    )
    mock_run_workflow._sync_remediation_loop_projection = (
        MoonMindRunWorkflow._sync_remediation_loop_projection.__get__(
            mock_run_workflow
        )
    )
    # The persisted projection is the durable input consumed by finish detail.
    mock_run_workflow._publish_context["remediationLoop"] = {
        "latestVerdict": "ADDITIONAL_WORK_NEEDED",
        "continuationDecisionRef": "artifact://decision/terminal",
        "continuationReason": "hard_attempt_limit_reached",
    }

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters={"runtime": {"mode": "managed"}},
        status="failed",
        error="remaining work",
    )

    assert summary["publishContext"]["remediationLoop"] == {
        "latestVerdict": "ADDITIONAL_WORK_NEEDED",
        "continuationDecisionRef": "artifact://decision/terminal",
        "continuationReason": "hard_attempt_limit_reached",
    }


def test_incident_control_stop_reuses_persisted_loop_decision_ref(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._workflow_control_stop = {
        "kind": "workflow_gate",
        "reasonCode": "hard_attempt_limit_reached",
        "logicalStepId": "verify-2",
        "verdict": "ADDITIONAL_WORK_NEEDED",
        "continuationDecisionRef": "artifact://decision/terminal",
    }

    assert mock_run_workflow._workflow_control_stop["continuationDecisionRef"] == (
        "artifact://decision/terminal"
    )


def _remediation_head_payload(
    *, checkpoint: str = "C0", version: int = 1
) -> dict[str, object]:
    return {
        "loopId": "loop-3473",
        "branchRef": "checkpoint-branch:loop-3473",
        "rootCheckpointRef": "artifact://workspace/C0",
        "rootWorkspaceDigest": "sha256:c0",
        "headCheckpointRef": f"artifact://workspace/{checkpoint}",
        "headWorkspaceDigest": f"sha256:{checkpoint.lower()}",
        "headAttemptOrdinal": version - 1,
        "headVersion": version,
    }


def test_remediation_step_receives_frozen_workflow_owned_candidate_baseline(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    node = {
        "id": "remediation-2",
        "annotations": {
            "issueImplementRole": "moonspec-remediation",
            "moonSpecRemediationAttempt": 2,
            "moonSpecRemediationMaxAttempts": 6,
        },
        "inputs": {},
    }
    inputs = {
        "remediationWorkspaceHead": _remediation_head_payload(
            checkpoint="C1", version=2
        )
    }

    mock_run_workflow._inject_remediation_workspace_baseline(
        node=node, node_inputs=inputs
    )

    assert inputs["remediationAttemptInput"] == {
        "loopId": "loop-3473",
        "attemptOrdinal": 2,
        "baseCheckpointRef": "artifact://workspace/C1",
        "expectedBaseDigest": "sha256:c1",
        "expectedHeadVersion": 2,
        "latestVerificationRef": None,
        "workspacePolicy": "continue_from_loop_head",
    }
    assert inputs["remediationWorkspaceHead"]["nextActionBaseline"][
        "checkpointRef"
    ] == "artifact://workspace/C1"


def test_completed_remediation_advances_baseline_before_next_attempt(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    node = {
        "id": "remediation-1",
        "annotations": {
            "issueImplementRole": "moonspec-remediation",
            "moonSpecRemediationAttempt": 1,
        },
        "inputs": {},
    }
    inputs = {"remediationWorkspaceHead": _remediation_head_payload()}
    mock_run_workflow._inject_remediation_workspace_baseline(
        node=node, node_inputs=inputs
    )

    mock_run_workflow._advance_remediation_workspace_head(
        node=node,
        node_inputs=inputs,
        execution_result={
            "outputs": {
                "remediationAttemptOutput": {
                    "attemptEvidenceRef": "artifact://attempt/1",
                    "parentCheckpointRef": "artifact://workspace/C0",
                    "parentWorkspaceDigest": "sha256:c0",
                    "outputCheckpointRef": "artifact://workspace/C1",
                    "outputWorkspaceDigest": "sha256:c1",
                    "checkpointManifestRef": "artifact://manifest/C1",
                    "candidateDiffRef": "artifact://diff/C1",
                    "changedFilesRef": "artifact://changed/C1",
                    "targetedChecksRef": "artifact://checks/C1",
                    "outcome": "candidate_captured",
                }
            }
        },
        step_execution_id="wf:run:remediation-1:execution:1",
    )

    next_node = {
        "id": "remediation-2",
        "annotations": {
            "issueImplementRole": "moonspec-remediation",
            "moonSpecRemediationAttempt": 2,
        },
        "inputs": {},
    }
    next_inputs: dict[str, object] = {}
    mock_run_workflow._inject_remediation_workspace_baseline(
        node=next_node, node_inputs=next_inputs
    )
    assert next_inputs["remediationAttemptInput"]["baseCheckpointRef"] == (
        "artifact://workspace/C1"
    )
    assert next_inputs["remediationAttemptInput"]["expectedHeadVersion"] == 2


def test_remediation_step_rejects_root_fallback_after_workflow_head_is_owned(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    node = {
        "id": "remediation-2",
        "annotations": {
            "issueImplementRole": "moonspec-remediation",
            "moonSpecRemediationAttempt": 2,
        },
        "inputs": {},
    }
    mock_run_workflow._remediation_workspace_head = RemediationWorkspaceHead.model_validate(
        _remediation_head_payload(checkpoint="C1", version=2)
    )
    inputs = {"remediationWorkspaceHead": _remediation_head_payload()}

    with pytest.raises(RemediationHeadError) as exc:
        mock_run_workflow._inject_remediation_workspace_baseline(
            node=node, node_inputs=inputs
        )

    assert exc.value.code == REMEDIATION_HEAD_MISMATCH


def test_moonspec_verify_gate_detects_issue_implement_remediation_role(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    ordered_nodes = [
        {
            "id": "verify-1",
            "inputs": {
                "title": "Verify implementation",
                "selectedSkill": "moonspec-verify",
            },
        },
        {
            "id": "remediate-1",
            "annotations": {"issueImplementRole": "moonspec-remediation"},
            "skill": {"id": "auto"},
            "inputs": {"title": "Remediate verification gaps"},
        },
    ]

    assert mock_run_workflow._has_remaining_moonspec_remediation_step(
        ordered_nodes=ordered_nodes,
        current_index=0,
    )


def test_moonspec_verify_gate_detects_unannotated_issue_implement_remediation_title(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mock_run_workflow,
        "_moonspec_title_remediation_detection_enabled",
        lambda: True,
    )
    ordered_nodes = [
        {
            "id": "verify-implementation",
            "inputs": {
                "title": "Verify implementation",
                "selectedSkill": "moonspec-verify",
            },
        },
        {
            "id": "remediate-1",
            "skill": {"id": "auto"},
            "inputs": {"title": "Remediate verification gaps — attempt 1 of 6"},
        },
    ]

    assert mock_run_workflow._has_remaining_moonspec_remediation_step(
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    assert mock_run_workflow._moonspec_remediation_attempt_metadata(
        ordered_nodes[1]
    ) == (1, 6)


def test_moonspec_verify_gate_skips_unannotated_remediation_gate_after_pass(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mock_run_workflow,
        "_moonspec_title_remediation_detection_enabled",
        lambda: True,
    )
    verify_node = {
        "id": "verify-remediation-1",
        "skill": {"id": "moonspec-verify"},
        "inputs": {"title": "Verify remediation attempt 1 of 6"},
    }
    mock_run_workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    reason = mock_run_workflow._moonspec_remediation_loop_skip_reason(
        verify_node,
        tool_name="moonspec-verify",
        node_inputs=verify_node["inputs"],
    )

    assert reason == (
        "Skipped MoonSpec remediation loop step because verification already "
        "passed with verdict FULLY_IMPLEMENTED."
    )


def test_moonspec_verify_text_verdict_parser_is_not_a_branch_boundary(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    verdict = mock_run_workflow._extract_moonspec_verify_verdict_from_text(
        "Current verdict: ADDITIONAL_WORK_NEEDED. Prior run was FULLY_IMPLEMENTED."
    )

    assert verdict == "ADDITIONAL_WORK_NEEDED"
    assert (
        mock_run_workflow._extract_moonspec_verify_verdict(
            {"summary": "Current verdict: ADDITIONAL_WORK_NEEDED."}
        )
        is None
    )


def test_moonspec_verify_gate_degrades_report_only_markdown(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "lastAssistantText": (
                "# MoonSpec Verification Report\n\n"
                "**Feature**: MM-1099\n"
                "**Verdict**: `FULLY_IMPLEMENTED`\n"
                "**Confidence**: HIGH\n"
            ),
            "operator_summary": "stale session text from an earlier step",
            "diagnosticsRef": "art_verify_report",
        },
    )

    gate_context = mock_run_workflow._publish_context["moonSpecGate"]
    assert gate_context["verdict"] == "NO_DETERMINATION"
    assert gate_context["summary"] == (
        "MoonSpec verification report was present but structured gate output "
        "was missing."
    )
    assert gate_context["diagnosticsRef"] == "art_verify_report"
    assert gate_context["invalid"] is True
    assert gate_context["degraded"] is True
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True


def test_moonspec_verify_gate_report_only_markdown_ignores_stale_operator_summary(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "lastAssistantText": (
                "# MoonSpec Verification Report\n\n"
                "**Feature**: MM-1099\n"
                "**Verdict**: ADDITIONAL_WORK_NEEDED\n"
                "**Confidence**: MEDIUM\n"
            ),
            "operator_summary": "stale Jira updater transcript",
            "diagnosticsRef": "art_verify_report",
        },
    )

    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    blocked_message = mock_run_workflow._plan_blocked_message or ""
    assert "NO_DETERMINATION" in blocked_message
    assert "structured gate output was missing" in blocked_message
    assert "stale Jira updater transcript" not in blocked_message
    assert "art_verify_report" in blocked_message


def test_moonspec_verify_gate_records_nested_summary_and_report(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="tpl:jira-orchestrate:1.0.0:13:verify",
        outputs={
            "verification": {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "operator_summary": "Nested verifier summary.",
                "diagnostics_ref": "art_nested_verify_report",
                "gateResultRef": "art_nested_gate_result",
            }
        },
    )

    gate_context = mock_run_workflow._publish_context["moonSpecGate"]
    assert gate_context["summary"] == "Nested verifier summary."
    assert gate_context["diagnosticsRef"] == "art_nested_verify_report"
    assert gate_context["gateResultRef"] == "art_nested_gate_result"
    assert gate_context["recommendedNextAction"] == "reattempt_current_step"
    assert gate_context["invalid"] is False
    assert gate_context["degraded"] is False


def test_moonspec_verify_gate_fails_closed_for_verdict_looking_prose_output(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "operator_summary": (
                "Verdict: ADDITIONAL_WORK_NEEDED. The implementation still has "
                "unchecked gaps."
            ),
            "diagnostics_ref": "art_verify_prose_only",
        },
    )

    gate_context = mock_run_workflow._publish_context["moonSpecGate"]
    assert gate_context["verdict"] == "NO_DETERMINATION"
    assert gate_context["recommendedNextAction"] == "blocked"
    assert gate_context["invalid"] is True
    assert gate_context["degraded"] is True
    assert gate_context["diagnosticsRef"] == "art_verify_prose_only"
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    assert "NO_DETERMINATION" in (mock_run_workflow._plan_blocked_message or "")


def test_moonspec_verify_default_artifact_path_is_added_to_parameters(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    parameters: dict[str, Any] = {"verify_artifact_path": ""}

    mock_run_workflow._ensure_moonspec_verify_parameters(
        parameters=parameters,
        node_inputs={"inputs": {}},
        node_id="tpl:jira-orchestrate:11:e7cc7ca9",
    )

    assert parameters["verify_artifact_path"] == (
        "var/artifacts/moonspec-verify/"
        "tpl-jira-orchestrate-11-e7cc7ca9.json"
    )


def test_moonspec_verify_artifact_path_uses_nested_skill_args(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    parameters: dict[str, Any] = {}

    mock_run_workflow._ensure_moonspec_verify_parameters(
        parameters=parameters,
        node_inputs={"inputs": {"verifyArtifactPath": "var/artifacts/custom.json"}},
        node_id="verify-final",
    )

    assert parameters["verify_artifact_path"] == "var/artifacts/custom.json"


def test_moonspec_verify_blocked_attempt_one_stops_with_remaining_budget(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    ordered_nodes = [
        {
            "id": "verify-1",
            "inputs": {
                "title": "Verify remediation attempt 1 of 6",
                "selectedSkill": "moonspec-verify",
            },
        },
        {
            "id": "remediate-2",
            "annotations": {"jiraOrchestrateRole": "moonspec-remediation"},
            "skill": {"id": "moonspec-implement"},
            "inputs": {"title": "Remediate remaining gaps — attempt 2 of 6"},
        },
    ]

    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-1",
        outputs={
            "verdict": "BLOCKED",
            "operator_summary": "UE Docker sidecar is not reachable.",
            "diagnostics_ref": "art_verify_blocked",
        },
    )

    assert mock_run_workflow._has_remaining_moonspec_remediation_step(
        ordered_nodes=ordered_nodes,
        current_index=0,
    )
    assert mock_run_workflow._normalize_moonspec_verify_verdict(
        mock_run_workflow._moonspec_gate_verdict
    ) == "BLOCKED"
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    assert mock_run_workflow._publish_status == "not_required"
    assert mock_run_workflow._publish_context["publicationBlockedBy"] == (
        "moonspec_verify"
    )
    assert "UE Docker sidecar is not reachable" in (
        mock_run_workflow._plan_blocked_message or ""
    )


def test_moonspec_verify_gate_degrades_unknown_verdict_to_no_determination(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-1",
        outputs={
            "verdict": "UNREAL_VALIDATION_ENVIRONMENT_MISSING",
            "operator_summary": "Verifier returned an unsupported environment verdict.",
            "diagnostics_ref": "art_unknown_verdict",
        },
    )

    gate_context = mock_run_workflow._publish_context["moonSpecGate"]
    assert gate_context["verdict"] == "NO_DETERMINATION"
    assert gate_context["recommendedNextAction"] == "blocked"
    assert gate_context["invalid"] is True
    assert gate_context["degraded"] is True
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    assert "NO_DETERMINATION" in (mock_run_workflow._plan_blocked_message or "")
    assert "art_unknown_verdict" in (mock_run_workflow._plan_blocked_message or "")


def test_moonspec_verify_gate_accepts_fully_implemented_publish(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="tpl:jira-orchestrate:1.0.0:13:verify",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )

    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is False
    assert mock_run_workflow._publish_context["moonSpecGate"]["verdict"] == (
        "FULLY_IMPLEMENTED"
    )


def test_moonspec_gate_downgrade_reason_surfaces_in_blocking_message(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    # Regression: an approving verifier report with a non-canonical
    # recommendedNextAction must explain the downgrade instead of surfacing
    # an unexplained NO_DETERMINATION.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "moonSpecVerify": {
                "verdict": "FULLY_IMPLEMENTED",
                "recommendedNextAction": "create_pull_request",
                "summary": "Issue fully implemented after remediation.",
            },
            "diagnostics_ref": "art_verify_report",
        },
    )

    gate_context = mock_run_workflow._publish_context["moonSpecGate"]
    assert gate_context["verdict"] == "NO_DETERMINATION"
    assert "create_pull_request" in gate_context["downgradeReason"]
    assert "FULLY_IMPLEMENTED" in gate_context["downgradeReason"]

    reason = mock_run_workflow._blocking_moonspec_gate_reason()
    assert reason is not None
    assert "FULLY_IMPLEMENTED" in reason
    assert "create_pull_request" in reason
    assert "art_verify_report" in reason


def test_moonspec_contract_repair_feedback_for_degraded_verify_output(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = {
        "outputs": {
            "moonSpecVerify": {
                "verdict": "FULLY_IMPLEMENTED",
                "recommendedNextAction": "create_pull_request",
            }
        }
    }

    feedback = mock_run_workflow._moonspec_verify_contract_repair_feedback(
        execution_result=execution_result,
        tool_name="auto",
        node_inputs={"selectedSkill": "moonspec-verify"},
    )
    assert feedback is not None
    assert "create_pull_request" in feedback
    assert "reattempt_current_step" in feedback
    assert "FULLY_IMPLEMENTED" in feedback

    # Contract-clean verify output requires no repair.
    assert (
        mock_run_workflow._moonspec_verify_contract_repair_feedback(
            execution_result={
                "outputs": {
                    "moonSpecVerify": {
                        "verdict": "FULLY_IMPLEMENTED",
                        "recommendedNextAction": "advance",
                    }
                }
            },
            tool_name="auto",
            node_inputs={"selectedSkill": "moonspec-verify"},
        )
        is None
    )

    # Non-verify steps never trigger contract repair, even with odd outputs.
    assert (
        mock_run_workflow._moonspec_verify_contract_repair_feedback(
            execution_result=execution_result,
            tool_name="auto",
            node_inputs={"selectedSkill": "jira-issue-updater"},
        )
        is None
    )


def test_moonspec_contract_repair_reexecutes_from_fresh_source_without_checkpoint(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    now = datetime.now(timezone.utc)
    node_id = "verify-final"
    mock_run_workflow._initialize_step_ledger(
        ordered_nodes=[{"id": node_id, "title": "Verify completion"}],
        dependency_map={node_id: []},
        updated_at=now,
    )
    mock_run_workflow._mark_step_running(node_id, updated_at=now)

    mock_run_workflow._prepare_moonspec_contract_repair_attempt(node_id)
    mock_run_workflow._mark_step_running(node_id, updated_at=now)

    source_execution = mock_run_workflow._step_execution_source_identity(
        node_id,
        attempt=2,
    )
    workspace = mock_run_workflow._step_execution_workspace(
        node_id,
        attempt=2,
        source_execution_ordinal=source_execution,
    )

    assert workspace["policy"] == "fresh_branch_from_source"
    assert workspace["evidenceRequired"] is False
    assert workspace["evidenceAccepted"] is True
    assert workspace["sourceExecutionOrdinal"] == source_execution
    assert not mock_run_workflow._workspace_policy_launch_blocked(workspace)


def test_moonspec_gate_draft_publish_qualification(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    # Verifier-declared environment failure qualifies.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "BLOCKED"},
    )
    assert mock_run_workflow._moonspec_gate_qualifies_for_draft_publish() is True

    # Degraded downgrade to NO_DETERMINATION qualifies.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "moonSpecVerify": {
                "verdict": "FULLY_IMPLEMENTED",
                "recommendedNextAction": "create_pull_request",
            }
        },
    )
    assert mock_run_workflow._moonspec_gate_qualifies_for_draft_publish() is True

    # A degraded implementation-gap verdict is still an implementation gap,
    # not an environment-class failure eligible for draft publication.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "moonSpecVerify": {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "recommendedNextAction": "create_pull_request",
            }
        },
    )
    assert mock_run_workflow._publish_context["moonSpecGate"][
        "declaredVerdict"
    ] == "ADDITIONAL_WORK_NEEDED"
    assert mock_run_workflow._moonspec_gate_qualifies_for_draft_publish() is False

    # Genuine implementation gaps never qualify.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "ADDITIONAL_WORK_NEEDED"},
    )
    assert mock_run_workflow._moonspec_gate_qualifies_for_draft_publish() is False

    # A verifier-declared NO_DETERMINATION (clean payload) stays fail-closed.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "moonSpecVerify": {
                "verdict": "NO_DETERMINATION",
                "recommendedNextAction": "blocked",
            }
        },
    )
    assert mock_run_workflow._moonspec_gate_qualifies_for_draft_publish() is False

    mock_run_workflow._moonspec_gate_verdict = None
    assert mock_run_workflow._moonspec_gate_qualifies_for_draft_publish() is False


def test_moonspec_environment_blocked_publish_action_defaults_to_fail(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    assert (
        mock_run_workflow._moonspec_environment_blocked_publish_action() == "fail"
    )
    mock_run_workflow._moonspec_environment_blocked_publish_action_snapshot = (
        "draft_pr"
    )
    assert (
        mock_run_workflow._moonspec_environment_blocked_publish_action()
        == "draft_pr"
    )
    mock_run_workflow._moonspec_environment_blocked_publish_action_snapshot = (
        "unsupported_value"
    )
    assert (
        mock_run_workflow._moonspec_environment_blocked_publish_action() == "fail"
    )


def test_additional_work_needed_publishes_draft_after_remediation_exhaustion(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "diagnostics_ref": "art_verify_remaining_work",
        },
    )
    blocking_reason = mock_run_workflow._blocking_moonspec_gate_reason()
    assert blocking_reason is not None

    policy = mock_run_workflow._moonspec_draft_publication_policy(
        environment_blocked_enabled=False,
        additional_work_enabled=True,
    )

    assert policy == "draft_pr_on_additional_work_needed"
    summary = mock_run_workflow._activate_moonspec_draft_publication(
        blocking_reason,
        policy=policy,
    )
    assert "draft pull request" in summary
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is False
    assert mock_run_workflow._publish_context["moonSpecGate"][
        "publicationPolicy"
    ] == "draft_pr_on_additional_work_needed"
    mock_run_workflow._publish_context["moonSpecGate"].update(
        {
            "gateResultRef": "artifact://gate/result",
            "remainingWorkRef": "artifact://remaining/work",
        }
    )
    body = mock_run_workflow._moonspec_draft_publication_body_section()
    assert "bounded remediation budget was exhausted" in body
    assert "Remaining work: artifact://remaining/work" in body
    assert "Verification gate result: artifact://gate/result" in body


@pytest.mark.asyncio
async def test_moonspec_draft_publication_disables_merge_automation(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._moonspec_draft_publication_reason = (
        "Additional implementation work remains."
    )

    await mock_run_workflow._maybe_start_merge_gate(
        parameters={"mergeAutomation": {"enabled": True}},
        pull_request_url="https://github.com/org/repo/pull/123",
    )

    assert (
        mock_run_workflow._publish_context["mergeAutomationStatus"]
        == "not_applicable"
    )
    assert "disabled" in mock_run_workflow._publish_context[
        "mergeAutomationSummary"
    ]


def test_additional_work_draft_publish_patch_preserves_old_history_behavior(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "ADDITIONAL_WORK_NEEDED"},
    )

    assert (
        mock_run_workflow._moonspec_draft_publication_policy(
            environment_blocked_enabled=True,
            additional_work_enabled=False,
        )
        is None
    )


def test_moonspec_draft_publication_supersedes_blocking_gate(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={
            "verdict": "BLOCKED",
            "operator_summary": "Docker validation wrapper cannot mount repo.",
            "diagnostics_ref": "art_verify_blocked",
        },
    )
    blocking_reason = mock_run_workflow._blocking_moonspec_gate_reason()
    assert blocking_reason is not None

    summary = mock_run_workflow._activate_moonspec_draft_publication(
        blocking_reason
    )
    assert "draft pull request" in summary

    # The fail-closed block is superseded: publication proceeds.
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is False
    assert mock_run_workflow._plan_blocked_message is None

    # Simulate the state the run loop records after native draft-PR creation.
    mock_run_workflow._pull_request_url = "https://github.com/org/repo/pull/7"
    mock_run_workflow._publish_status = "published"
    mock_run_workflow._authoritative_publish_outcome_enabled = True
    status, _message, publish_failure = (
        mock_run_workflow._determine_publish_completion(
            parameters={"publishMode": "pr"}
        )
    )
    assert publish_failure is True
    assert status == "failed"
    assert "preserved in draft pull request" in _message

    assert mock_run_workflow._attention_required is True
    draft_context = mock_run_workflow._publish_context["moonSpecDraftPublication"]
    assert draft_context["policy"] == "draft_pr_on_environment_blocked"
    assert mock_run_workflow._publish_context["moonSpecGate"][
        "publicationPolicy"
    ] == "draft_pr_on_environment_blocked"

    body_section = mock_run_workflow._moonspec_draft_publication_body_section()
    assert "MoonSpec verification incomplete" in body_section
    assert "BLOCKED" in body_section
    assert "art_verify_blocked" in body_section


def test_pushed_commits_supersede_stale_no_commit_before_draft_failure(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._authoritative_publish_outcome_enabled = True

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={"outputs": {"push_status": "no_commits"}},
    )
    assert mock_run_workflow._publish_status == "skipped"
    assert mock_run_workflow._publish_context["noCommitPublish"] == {
        "status": "no_commits"
    }

    mock_run_workflow._record_publish_result(
        parameters={"publishMode": "pr"},
        execution_result={
            "outputs": {
                "push_status": "pushed",
                "push_branch": "workflow-branch",
                "push_commit_count": 2,
                "push_head_sha": "abc123",
            }
        },
    )

    assert mock_run_workflow._publish_status is None
    assert mock_run_workflow._publish_reason is None
    assert "noCommitPublish" not in mock_run_workflow._publish_context

    mock_run_workflow._moonspec_draft_publication_reason = (
        "MoonSpec verdict ADDITIONAL_WORK_NEEDED."
    )
    mock_run_workflow._pull_request_url = "https://github.com/org/repo/pull/8"
    mock_run_workflow._publish_status = "published"

    status, message, publish_failure = (
        mock_run_workflow._determine_publish_completion(
            parameters={"publishMode": "pr"}
        )
    )

    assert status == "failed"
    assert publish_failure is True
    assert "https://github.com/org/repo/pull/8" in message


def test_pushed_commits_supersede_empty_stale_no_change_evidence(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._authoritative_publish_outcome_enabled = True
    mock_run_workflow._publish_context["noChangePublish"] = {}
    mock_run_workflow._publish_status = "skipped"
    mock_run_workflow._publish_reason = "No repository changes were detected."

    mock_run_workflow._record_no_commit_publish_evidence(
        {"push_status": "pushed"}
    )

    assert "noChangePublish" not in mock_run_workflow._publish_context
    assert mock_run_workflow._publish_status is None
    assert mock_run_workflow._publish_reason is None


def test_authoritative_publish_outcome_patch_preserves_legacy_draft_completion(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._moonspec_draft_publication_reason = (
        "MoonSpec verdict ADDITIONAL_WORK_NEEDED."
    )
    mock_run_workflow._pull_request_url = "https://github.com/org/repo/pull/9"
    mock_run_workflow._publish_status = "published"

    status, _message, publish_failure = (
        mock_run_workflow._determine_publish_completion(
            parameters={"publishMode": "pr"}
        )
    )

    assert mock_run_workflow._authoritative_publish_outcome_enabled is False
    assert status == "success"
    assert publish_failure is False


def test_moonspec_blocking_gate_unchanged_without_draft_activation(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    # In-flight compatibility: when the draft-publish path never activates
    # (patch off or policy 'fail'), the gate blocks exactly as before.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "BLOCKED", "diagnostics_ref": "art_verify_blocked"},
    )
    assert mock_run_workflow._moonspec_draft_publication_reason is None
    assert mock_run_workflow._apply_blocking_moonspec_gate_to_publish() is True
    assert mock_run_workflow._publish_status == "not_required"
    status, message, publish_failure = (
        mock_run_workflow._determine_publish_completion(
            parameters={"publishMode": "pr"}
        )
    )
    assert publish_failure is True
    assert "BLOCKED" in message


def test_jira_orchestrate_external_handoff_requires_passing_moonspec_gate(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    handoff_node = {
        "id": "code-review",
        "inputs": {
            "annotations": {"jiraOrchestrateRole": "code-review-handoff"},
        },
    }

    assert (
        mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
            handoff_node
        )
        == (
            "Jira Orchestrate external handoff requires an accepted MoonSpec "
            "verification terminal disposition; no controlling verification gate "
            "has approved advancement."
        )
    )

    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "ADDITIONAL_WORK_NEEDED"},
    )

    blocked_reason = (
        mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
            handoff_node
        )
    )
    assert blocked_reason is not None
    assert "latest verdict was ADDITIONAL_WORK_NEEDED" in blocked_reason

    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )

    assert (
        mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
            handoff_node
        )
        is None
    )


def test_jira_orchestrate_external_handoff_uses_preserved_verify_gate_state(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._step_ledger_rows = [
        {
            "logicalStepId": "verify-final",
            "status": "succeeded",
            "terminalDisposition": "accepted",
            "preservedFrom": {
                "workflowId": "mm:source",
                "runId": "run-source",
                "logicalStepId": "verify-final",
                "executionOrdinal": 1,
            },
        }
    ]
    mock_run_workflow._rebuild_step_ledger_index()

    mock_run_workflow._record_preserved_step_terminal_state(
        "verify-final",
        {
            "id": "verify-final",
            "tool": {"name": "moonspec-verify"},
            "inputs": {},
        },
    )

    assert mock_run_workflow._step_terminal_dispositions["verify-final"] == "accepted"
    assert mock_run_workflow._publish_context["moonSpecGate"] == {
        "logicalStepId": "verify-final",
        "verdict": "FULLY_IMPLEMENTED",
    }
    assert (
        mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
            {
                "id": "code-review",
                "inputs": {
                    "annotations": {"jiraOrchestrateRole": "code-review-handoff"},
                },
            }
        )
        is None
    )


def _handoff_node(role: str = "pull-request-handoff", node_id: str = "pr-handoff") -> dict[str, Any]:
    return {
        "id": node_id,
        "inputs": {"annotations": {"jiraOrchestrateRole": role}},
    }


def test_handoff_blocked_when_producing_step_not_accepted_despite_passing_verdict(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strengthened gate: a passing verdict alone is not enough — the producing
    # MoonSpec verify Step Execution must also be at the accepted terminal
    # disposition.
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH,
    )
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    # Producing step did not reach accepted (e.g. failed/partial attempt).
    mock_run_workflow._step_terminal_dispositions["verify-final"] = "candidate"

    node = _handoff_node()
    reason = mock_run_workflow._jira_orchestrate_external_handoff_block_reason(node)

    assert reason is not None
    assert "accepted terminal disposition" in reason
    assert "candidate" in reason

    # The denied non-idempotent external action is recorded as a blocked side
    # effect at the boundary.
    records = mock_run_workflow._step_side_effect_records.get("pr-handoff", [])
    assert records, "expected a blocked side-effect record at the handoff boundary"
    blocked = records[-1]
    assert blocked["disposition"] == "blocked"
    assert blocked["class"] == "external_non_idempotent"
    assert blocked["operation"] == "repo.publish"


def test_handoff_blocked_side_effect_recording_is_idempotent(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH,
    )
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    mock_run_workflow._step_terminal_dispositions["verify-final"] = "candidate"

    node = _handoff_node()
    first_reason = mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
        node
    )
    second_reason = mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
        node
    )

    assert first_reason == second_reason
    records = mock_run_workflow._step_side_effect_records.get("pr-handoff", [])
    assert len(records) == 1
    assert records[0]["class"] == "external_non_idempotent"
    assert records[0]["operation"] == "repo.publish"
    assert records[0]["disposition"] == "blocked"


@pytest.mark.parametrize(
    "disposition",
    ["candidate", "discarded", "superseded", "blocked", "failed_with_remaining_work"],
)
def test_handoff_blocked_for_each_non_accepted_disposition(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH,
    )
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    mock_run_workflow._step_terminal_dispositions["verify-final"] = disposition

    reason = mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
        _handoff_node(node_id=f"handoff-{disposition}")
    )
    assert reason is not None


def test_handoff_allowed_when_producing_step_accepted_and_gate_approved(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH,
    )
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    mock_run_workflow._step_terminal_dispositions["verify-final"] = "accepted"

    node = _handoff_node()
    assert (
        mock_run_workflow._jira_orchestrate_external_handoff_block_reason(node) is None
    )
    # No blocked record is created for an allowed handoff.
    assert not mock_run_workflow._step_side_effect_records.get("pr-handoff")


def test_handoff_accepted_disposition_gate_is_replay_guarded(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    # Default fixture leaves workflow.patched -> False, so in-flight runs keep
    # the legacy verdict-only decision even when the producing step is not yet
    # recorded as accepted.
    mock_run_workflow._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    assert (
        mock_run_workflow._jira_orchestrate_external_handoff_block_reason(
            _handoff_node()
        )
        is None
    )


def test_step_side_effect_defaults_to_terminal_disposition_gate(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._step_terminal_dispositions["code-review"] = (
        "failed_with_remaining_work"
    )

    blocked = mock_run_workflow._record_step_side_effect(
        "code-review",
        effect_class="external_idempotent",
        operation="jira.transition_issue",
        target="MM-826",
        idempotency_key="wf:run:code-review:execution:1:jira-transition:Code Review",
    )

    assert blocked["workflowStateAccepted"] is False
    assert blocked["disposition"] == "blocked"

    mock_run_workflow._step_terminal_dispositions["code-review"] = "accepted"
    accepted = mock_run_workflow._record_step_side_effect(
        "code-review",
        effect_class="external_idempotent",
        operation="jira.transition_issue",
        target="MM-826",
        idempotency_key="wf:run:code-review:execution:2:jira-transition:Code Review",
    )

    assert accepted["workflowStateAccepted"] is True
    assert accepted["disposition"] == "accepted"


def test_native_pr_branch_resolution_prefers_publish_context_branch(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    mock_run_workflow._publish_context["branch"] = "804-workflow-detail-tabs"
    mock_run_workflow._publish_context["baseRef"] = "origin/main"

    head_branch, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={"targetBranch": "generated-target"},
        agent_outputs={"targetBranch": "generated-target"},
        workspace_spec={
            "targetBranch": "change-jira-issue-mm-804-to-status-in-pr-c31f93a5",
            "startingBranch": "main",
        },
        last_node_inputs={
            "targetBranch": "change-jira-issue-mm-804-to-status-in-pr-c31f93a5"
        },
        publish_payload={},
    )

    assert head_branch == "804-workflow-detail-tabs"
    assert base_branch == "main"


def test_native_pr_branch_resolution_normalizes_base_ref_candidates(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)

    _, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={},
        workspace_spec={"targetBranch": "feature/expected"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "refs/remotes/origin/release/1.2"},
    )

    assert base_branch == "release/1.2"

    _, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={},
        workspace_spec={"targetBranch": "feature/expected"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "refs/heads/main"},
    )

    assert base_branch == "main"

    _, base_branch = mock_run_workflow._resolve_native_pr_branches(
        parameters={},
        agent_outputs={},
        workspace_spec={"targetBranch": "feature/expected"},
        last_node_inputs={},
        publish_payload={"prBaseBranch": "refs/heads/origin/main"},
    )

    assert base_branch == "main"


def test_publish_repair_feedback_names_branch_and_managed_publish_contract(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._publish_context["branch"] = "feature/expected"
    mock_run_workflow._publish_context["baseRef"] = "origin/main"

    feedback = mock_run_workflow._publish_repair_feedback_instruction(
        failure_message=(
            "publishMode 'pr' requested, but no publishable diff was produced."
        )
    )

    assert "Publish postcondition repair required" in feedback
    assert "feature/expected" in feedback
    assert "origin/main" in feedback
    assert "cherry-pick" in feedback
    assert "managed publishing will push and create the PR" in feedback
    assert "Do not transition Jira" in feedback


def test_publish_repair_identifies_jira_agent_skill_names(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    assert mock_run_workflow._is_jira_agent_skill_name("jira-issue-updater") is True
    assert mock_run_workflow._is_jira_agent_skill_name("moonspec-implement") is False


@pytest.mark.asyncio
async def test_publish_repair_runs_one_managed_child_and_returns_result(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = CodexManagedSessionBinding(
        workflowId="wf-1:session:codex_cli",
        agentRunId="wf-1",
        sessionId="sess:wf-1:codex_cli",
        runtimeId="codex_cli",
    )
    mock_run_workflow._codex_session_binding = binding
    mock_run_workflow._last_publish_repair_node_id = "step-2"
    mock_run_workflow._last_publish_repair_request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="wf-1",
        idempotencyKey="wf-1:step-2:run-1",
        instructionRef="original instructions",
        managedSession=binding,
        parameters={"publishMode": "pr"},
    )
    mock_run_workflow._publish_context["branch"] = "feature/expected"

    child_requests: list[AgentExecutionRequest] = []

    async def fake_execute_child_workflow(
        _workflow_type: str,
        request: AgentExecutionRequest,
        **_kwargs: Any,
    ) -> AgentRunResult:
        child_requests.append(request)
        return AgentRunResult(
            summary="repair complete",
            metadata={"push_status": "pushed", "push_branch": "feature/expected"},
        )

    def fake_patched(patch_id: str) -> bool:
        return patch_id == RUN_PUBLISH_REPAIR_FEEDBACK_PATCH

    monkeypatch.setattr(run_workflow_module.workflow, "patched", fake_patched)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )

    result = await mock_run_workflow._execution_publish_repair(
        parameters={"publishMode": "pr"},
        failure_message="branch has no commits ahead of origin/main",
    )

    assert result is not None
    assert result["status"] == "COMPLETED"
    assert result["outputs"]["push_status"] == "pushed"
    assert len(child_requests) == 1
    assert "branch has no commits" in (child_requests[0].instruction_ref or "")
    assert (
        child_requests[0].parameters["metadata"]["moonmind"]["publishRepair"][
            "sourceNodeId"
        ]
        == "step-2"
    )


def test_determine_publish_completion_requires_integration_pr_url(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._integration = "jules"

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "failed"
    assert message == "publishMode 'pr' requested but no PR was created"
    assert publish_failure is True

def test_determine_publish_completion_succeeds_when_pr_was_created_after_skips(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._integration = "jules"
    mock_run_workflow._publish_status = "published"
    mock_run_workflow._pull_request_url = "https://github.com/org/repo/pull/123"
    mock_run_workflow._publish_context["pullRequestUrl"] = (
        "https://github.com/org/repo/pull/123"
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "success"
    assert "Pull request: https://github.com/org/repo/pull/123" in message
    assert publish_failure is False

def test_determine_publish_completion_requires_merge_when_requested(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._pull_request_url = "https://github.com/org/repo/pull/123"
    mock_run_workflow._publish_status = "published"

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-1"},
        }
    )

    assert status == "failed"
    assert message == "merge automation requested but PR was not merged"
    assert publish_failure is True

def test_determine_publish_completion_accepts_completed_merge_outcome(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._pull_request_url = "https://github.com/org/repo/pull/123"
    mock_run_workflow._publish_status = "published"
    mock_run_workflow._publish_context["mergeAutomationStatus"] = "merged"

    status, _message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "pr",
            "mergeAutomation": {"enabled": True, "jiraIssueKey": "MM-1"},
        }
    )

    assert status == "success"
    assert publish_failure is False

def test_determine_publish_completion_requires_requested_report(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "none",
            "reportOutput": {"enabled": True, "required": True},
        }
    )

    assert status == "failed"
    assert message == "reportOutput requested but no final report was created"
    assert publish_failure is True

def test_record_execution_context_tracks_created_report(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {"summary": "Report published."},
            "metadata": {"primaryReportRef": "art_report_1"},
        },
    )

    status, _message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "none",
            "reportOutput": {"enabled": True, "required": True},
        }
    )

    assert mock_run_workflow._report_created is True
    assert mock_run_workflow._report_ref == "art_report_1"
    assert status == "success"
    assert publish_failure is False


def test_record_execution_context_tracks_mapped_agent_run_report(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    execution_result = mock_run_workflow._map_agent_run_result(
        {
            "summary": "Report published.",
            "metadata": {"primaryReportRef": "art_report_2"},
        }
    )

    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result=execution_result,
    )

    status, _message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "none",
            "reportOutput": {"enabled": True, "required": True},
        }
    )

    assert mock_run_workflow._report_created is True
    assert mock_run_workflow._report_ref == "art_report_2"
    assert status == "success"
    assert publish_failure is False


def test_record_execution_context_tracks_direct_pentest_report_result(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DIRECT_TOOL_REPORT_OUTPUTS_PATCH,
    )

    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "status": "completed",
            "primary_report_ref": "art_pentest_report_1",
            "report_type": "security_pentest_report",
            "report_scope": "final",
            "report_bundle": {
                "report_bundle_v": 1,
                "primary_report_ref": {
                    "artifact_ref_v": 1,
                    "artifact_id": "art_pentest_report_1",
                },
                "report_type": "security_pentest_report",
                "report_scope": "final",
            },
        },
    )

    status, _message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "none",
            "reportOutput": {"enabled": True, "required": True},
        }
    )

    assert mock_run_workflow._report_created is True
    assert mock_run_workflow._report_ref == "art_pentest_report_1"
    assert status == "success"
    assert publish_failure is False


def test_pentest_report_result_makes_pr_publish_not_required(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id == RUN_DIRECT_TOOL_REPORT_OUTPUTS_PATCH,
    )
    execution_result = {
        "status": "completed",
        "push_status": "no_commits",
        "primary_report_ref": "art_pentest_report_1",
        "report_type": "security_pentest_report",
        "report_scope": "final",
        "report_bundle": {
            "report_bundle_v": 1,
            "primary_report_ref": {
                "artifact_ref_v": 1,
                "artifact_id": "art_pentest_report_1",
            },
            "report_type": "security_pentest_report",
            "report_scope": "final",
        },
    }

    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result=execution_result,
    )
    mock_run_workflow._record_publish_result(
        parameters={
            "publishMode": "pr",
            "reportOutput": {"enabled": True, "required": True},
        },
        execution_result=execution_result,
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={
            "publishMode": "pr",
            "reportOutput": {"enabled": True, "required": True},
        }
    )

    assert mock_run_workflow._publish_status == "not_required"
    assert mock_run_workflow._publish_reason == (
        "security.pentest.run produced a final report artifact; "
        "PR/branch publication is not applicable"
    )
    assert status == "success"
    assert "PR/branch publication is not applicable" in message
    assert publish_failure is False


def test_determine_publish_completion_includes_operator_summary_for_report_runs(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "summary": "Completed with status completed",
                "operator_summary": (
                    "The report explains that lunar regolith can shield habitats "
                    "from radiation and micrometeorites."
                ),
            }
        },
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )

    assert status == "success"
    assert (
        "Final result: The report explains that lunar regolith can shield habitats"
        in message
    )
    assert "Completed with status completed" not in message
    assert publish_failure is False

def test_determine_publish_completion_prefers_latest_step_summary_over_stale_operator_summary(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "operator_summary": "Prepared the initial deployment notes.",
            }
        },
    )
    mock_run_workflow._record_execution_context(
        node_id="step-2",
        execution_result={
            "outputs": {
                "summary": "Published the final report bundle.",
            }
        },
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )

    assert status == "success"
    assert "Final result: Published the final report bundle" in message
    assert "Prepared the initial deployment notes" not in message
    assert publish_failure is False

def test_determine_publish_completion_uses_meaningful_summary_after_generic_operator_summary(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "operator_summary": "Completed.",
                "summary": "Wrote the operator-facing completion report.",
            }
        },
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )

    assert status == "success"
    assert "Final result: Wrote the operator-facing completion report" in message
    assert "Final result: Completed" not in message
    assert publish_failure is False

def test_determine_publish_completion_omits_pull_request_url_when_publish_mode_none(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    mock_run_workflow._publish_context["pullRequestUrl"] = (
        "https://github.com/org/repo/pull/123"
    )
    mock_run_workflow._record_execution_context(
        node_id="step-1",
        execution_result={
            "outputs": {
                "summary": "Referenced an existing pull request.",
            }
        },
    )

    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )

    assert status == "success"
    assert "Referenced an existing pull request" in message
    assert "Pull request:" not in message
    assert publish_failure is False

def test_determine_publish_completion_fails_for_unknown_branch_publish_outcome(
    mock_run_workflow: MoonMindRunWorkflow,
) -> None:
    status, message, publish_failure = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "branch"}
    )

    assert status == "failed"
    assert message == "branch publish outcome unknown"
    assert publish_failure is True


# ---------------------------------------------------------------------------
# Regression: a failed step result that carries only a bare error-category
# token (e.g. a managed runtime that timed out and emitted "execution_error"
# with no provider detail) must never surface that token as the operator
# summary. Otherwise the terminal summary, finish-outcome reason, and the
# workflow's ApplicationError message all collapse to "execution_error" — with
# nothing actionable for an operator. See mm:4b897068 troubleshooting.
# ---------------------------------------------------------------------------

def test_humanize_step_failure_summary_replaces_bare_category_token() -> None:
    summary = MoonMindRunWorkflow._humanize_step_failure_summary(
        summary="execution_error",
        tool_name="jira-orchestrate",
        failure_message="execution_error",
    )
    assert summary not in MoonMindRunWorkflow._ERROR_CATEGORY_TOKENS
    assert "jira-orchestrate failed" in summary
    assert "(execution_error)" in summary
    assert "diagnostic" in summary.lower()


def test_humanize_step_failure_summary_preserves_real_provider_summary() -> None:
    provider_summary = (
        "pr-resolver reported status 'blocked'; ci_running; "
        "next_step=retry_finalize_after_backoff"
    )
    summary = MoonMindRunWorkflow._humanize_step_failure_summary(
        summary=provider_summary,
        tool_name="pr-resolver",
        failure_message="user_error",
    )
    assert summary == provider_summary


def test_step_failure_summary_preserves_timed_out_child_output_summary() -> None:
    workflow = MoonMindRunWorkflow()
    timeout_summary = (
        "Managed session turn exceeded execution budget 3600s after 9932s; "
        "request intervention or rerun with a larger budget."
    )
    execution_result = {
        "status": "FAILED",
        "outputs": {
            "error": "execution_error",
            "summary": timeout_summary,
        },
    }

    failure_message = workflow._activity_result_failure_message(execution_result)
    provider_failure_summary = workflow._activity_result_provider_failure_summary(
        execution_result
    )
    operator_failure_summary = (
        provider_failure_summary
        or workflow._activity_result_operator_summary(execution_result)
        or failure_message
    )
    summary = MoonMindRunWorkflow._humanize_step_failure_summary(
        summary=operator_failure_summary,
        tool_name="codex_cli",
        failure_message=failure_message,
    )

    assert failure_message == "execution_error"
    assert provider_failure_summary is None
    assert summary == timeout_summary


def test_humanize_step_failure_summary_blank_inputs_fall_back_to_tool_failed() -> None:
    summary = MoonMindRunWorkflow._humanize_step_failure_summary(
        summary=None,
        tool_name="codex-runtime",
        failure_message=None,
    )
    assert summary == "codex-runtime failed"


def test_humanize_step_failure_summary_handles_all_category_tokens() -> None:
    for token in ("user_error", "integration_error", "execution_error", "system_error"):
        summary = MoonMindRunWorkflow._humanize_step_failure_summary(
            summary=token,
            tool_name="agent_runtime",
            failure_message=token,
        )
        assert summary not in MoonMindRunWorkflow._ERROR_CATEGORY_TOKENS
        assert f"({token})" in summary


@pytest.mark.asyncio
async def test_terminal_evidence_failure_projects_partial_fanout_consistently(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = [
        {"ref": "THOR-705", "workflowId": "child-1"},
        {"ref": "THOR-706", "workflowId": "child-2"},
    ]
    diagnostic = mock_run_workflow._record_result_failure_diagnostic(
        stage="execute",
        category="execution_error",
        source="child_workflow",
        step_id="batch-workflows",
        step_title="batch-workflows",
        message="Agent completed without required terminal evidence",
        child_workflow_id="agent-run-mm-1201",
        diagnostics_ref="artifact://mm-1201-diagnostics",
        terminal_evidence={
            "failureCode": "BATCH_FANOUT_PARTIAL_FAILURE",
            "terminalContractId": "batch_workflows_fanout.v1",
            "terminalContractMissingEvidence": [],
            "queuedChildCount": 2,
            "queuedChildren": queued,
        },
    )

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters={"publishMode": "none"},
        status="failed",
        error=diagnostic["message"],
    )

    assert summary["finishOutcome"]["code"] == "FAILED"
    assert summary["failure"]["failureCode"] == "BATCH_FANOUT_PARTIAL_FAILURE"
    assert summary["failure"]["queuedChildCount"] == len(queued)
    assert summary["failure"]["queuedChildren"] == queued
    assert summary["lastStep"]["diagnosticsRef"] == "artifact://mm-1201-diagnostics"


@pytest.mark.asyncio
async def test_failed_finish_summary_keeps_terminal_publication_evidence(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run_workflow._publish_context["terminalPublication"] = {
        "intent": "terminal_checkpoint",
        "status": "pushed",
        "reasonCode": "graceful_failure_checkpoint_pushed",
        "source": "live_workspace",
        "attempted": True,
        "branchPushed": True,
        "branchName": "mm/workflow/recovered-work",
        "headSha": "abc123",
        "baseBranch": "main",
        "remoteVerified": True,
        "evidenceRef": "artifact://terminal-publication",
        "idempotencyKey": "terminal-checkpoint-v1:run-1",
    }

    summary = await _finalize_and_capture_summary(
        monkeypatch,
        mock_run_workflow,
        parameters={"publishMode": "branch"},
        status="failed",
        error="validation failed",
    )

    assert summary["finishOutcome"]["code"] == "FAILED"
    assert summary["finishOutcome"]["reason"] == "validation failed"
    assert summary["publish"]["intent"] == "terminal_checkpoint"
    assert summary["publish"]["status"] == "pushed"
    assert summary["publish"]["branchName"] == "mm/workflow/recovered-work"
    assert summary["publish"]["remoteVerified"] is True
    assert summary["publish"]["evidenceRef"] == "artifact://terminal-publication"
