import json
from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    dump_method = getattr(payload, 'model_dump', getattr(payload, 'dict', None))
    return dump_method() if dump_method else payload

async def _immediate_wait_condition(
    predicate: Callable[[], bool],
    **_kwargs: Any,
) -> None:
    assert predicate() is True

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
                "workflowType": "MoonMind.Run",
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
            "workflowType": "MoonMind.Run",
            "initialParameters": {
                "repo": "MoonLadderStudios/MoonMind",
                "task": {"dependsOn": ["mm:dep-1", "mm:dep-2"]},
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
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda patch_id: True)
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
        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "version": "1.0.0",
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
                    "nodes": [
                        {
                            "id": "step-1",
                            "skill": {"name": "repo.run_tests", "version": "1.0.0"},
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

    # provider_profile.list calls (3) happen first, then artifact.read for plan,
    # artifact.read for registry, then mm.skill.execute
    assert captured[3][0] == "artifact.read"
    assert captured[3][1]["artifact_ref"] == "art_plan_1"
    assert captured[4][0] == "artifact.read"
    assert captured[4][1]["artifact_ref"] == "artifact://registry/1"
    assert captured[5][0] == "mm.skill.execute"
    assert captured[5][1]["registry_snapshot_ref"] == "artifact://registry/1"

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
async def test_run_execution_stage_routes_mm_tool_execute_from_registry(
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
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                                "description": "Run tests",
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
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                                "version": "1.0.0",
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

    # provider_profile.list calls (3) happen first, then artifact.read for plan,
    # artifact.read for registry, then mm.tool.execute
    assert captured[5][0] == "mm.tool.execute"
    assert captured[5][1]["registry_snapshot_ref"] == "artifact://registry/1"

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
                                "version": "1.0",
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

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        normalized = _normalize_payload(payload)
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
                                "version": "1.0",
                            },
                            "inputs": {"instructions": "Check blockers."},
                        },
                        {
                            "id": "implement",
                            "tool": {
                                "type": "agent_runtime",
                                "name": "codex_cli",
                                "version": "1.0",
                            },
                            "inputs": {"instructions": "Implement changes."},
                        },
                    ],
                    "edges": [{"from": "check-blockers", "to": "implement"}],
                }
            ).encode("utf-8")
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

    async def fake_bind_task_scoped_session(
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
        "_maybe_bind_task_scoped_session",
        fake_bind_task_scoped_session,
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
    assert steps[0]["status"] == "succeeded"
    assert steps[1]["status"] == "skipped"

@pytest.mark.asyncio
async def test_run_execution_stage_preserves_registry_read_for_unpatched_histories(
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
                                "version": "1.0.0",
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
                                "version": "1.0",
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
    assert artifact_reads == ["art_plan_1", "artifact://registry/empty"]

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
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                                "description": "Run tests",
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
                            "id": "step-1",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch"},
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {
            "status": "FAILED",
            "outputs": {"error": "gemini CLI command failed"},
            "progress": {"details": "Failed to execute generic LLM handler"},
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
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
        match="plan node execution returned status FAILED",
    ):
        await workflow._run_execution_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            plan_ref="art_plan_1",
        )

@pytest.mark.asyncio
async def test_run_execution_stage_continue_mode_keeps_running_after_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    skill_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        nonlocal skill_calls
        if activity_type == "artifact.read":
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                                "description": "Run tests",
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
                    "policy": {"failure_mode": "CONTINUE", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch"},
                            "options": {},
                        },
                        {
                            "id": "step-2",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                                "version": "1.0.0",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch"},
                            "options": {},
                        },
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        if activity_type == "mm.tool.execute":
            skill_calls += 1
            if skill_calls == 1:
                return {
                    "status": "FAILED",
                    "outputs": {"error": "first step failed"},
                    "progress": {"details": "intentional failure"},
                }
            return {"status": "COMPLETED", "outputs": {}}
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
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

    assert skill_calls == 2

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
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.publish",
                                "version": "1.0.0",
                                "description": "Publish",
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
                                "type": "skill",
                                "name": "repo.publish",
                                "version": "1.0.0",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch"},
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {
            "status": "COMPLETED",
            "outputs": {"stdout_tail": "Applied requested changes."},
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
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
        match="publishMode 'pr' requested but no PR URL was returned, and missing repo/branch to create it natively",
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
            if (payload.get("artifact_ref") if isinstance(payload, dict) else getattr(payload, "artifact_ref", None)) == "artifact://registry/1":
                return json.dumps(
                    {
                        "skills": [
                            {
                                "name": "repo.publish",
                                "version": "1.0.0",
                                "description": "Publish",
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
                                "type": "skill",
                                "name": "repo.publish",
                                "version": "1.0.0",
                            },
                            "inputs": {"repo_ref": "git:org/repo#branch"},
                            "options": {},
                        }
                    ],
                    "edges": [],
                }
            ).encode("utf-8")
        return {
            "status": "COMPLETED",
            "outputs": {
                "stdout_tail": "Opened PR: https://github.com/org/repo/pull/123"
            },
        }

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)
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
                                "version": "1.0",
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
                                "version": "1.0",
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
            "task": {
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
                                "version": "1.0",
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
                                "version": "1.0",
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
            "task": {
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

    async def fake_bind_task_scoped_session(
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
                                "version": "1.0",
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
                                "version": "1.0",
                            },
                            "inputs": {
                                "runtime": {"mode": "codex_cli", "model": "gpt-5.4"},
                                "instructions": "Tighten the Mission Control title size.",
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
        "_maybe_bind_task_scoped_session",
        fake_bind_task_scoped_session,
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
  "details": {"source": "jira", "attempt": 1},
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

    assert status == "blocked"
    assert reason == "Workflow blocked by plan step: blocked upstream."
    assert failed is False

def test_publish_completion_accepts_jira_output_without_pr_url() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._publish_status = "published"
    workflow._publish_reason = "Jira issue output succeeded; no PR output required"
    workflow._pull_request_url = None

    status, reason, failed = workflow._determine_publish_completion(
        parameters={"publishMode": "pr"}
    )

    assert status == "success"
    assert reason == "Workflow completed successfully"
    assert failed is False

@pytest.mark.parametrize("story_status", ["jira_created", "jira_partial"])
def test_jira_story_output_status_satisfies_pr_publish(story_status: str) -> None:
    assert MoonMindRunWorkflow._is_successful_jira_story_output(
        mode="jira",
        status=story_status,
    )

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
                                "version": "1.0.0",
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
                                "version": "1.0.0",
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
                                "version": "1.0",
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
                                "version": "1.0",
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
                                "version": "1.0",
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
                                "version": "1.0",
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
async def test_run_proposals_stage_global_disable_halts_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings
    workflow = MoonMindRunWorkflow()
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", False)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda x: True)
    
    # Enable proposing tasks in params, but global switch should stop it
    await workflow._run_proposals_stage(parameters={"proposeTasks": True})
    
    assert workflow._state == "initializing"  # State shouldn't change to PROPOSALS

@pytest.mark.asyncio
async def test_run_proposals_stage_ignores_legacy_fallback_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", True)

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
            return [{"title": "t1", "summary": "s1", "taskCreateRequest": {}}]
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
async def test_run_proposals_stage_uses_task_proposal_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", True)

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
            return [{"title": "t1", "summary": "s1", "taskCreateRequest": {}}]
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
            "task": {
                "proposalPolicy": {
                    "maxItems": {"project": 12},
                    "targets": ["project"],
                    "defaultRuntime": "gemini_cli",
                }
            }
        }
    )
    
    assert captured_policy is not None
    assert captured_policy["maxItems"] == {"project": 12}
    assert captured_policy["targets"] == ["project"]
    assert captured_policy["defaultRuntime"] == "gemini_cli"
    
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
