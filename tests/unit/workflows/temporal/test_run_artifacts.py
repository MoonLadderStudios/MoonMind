import json
from datetime import datetime, timezone

import pytest

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


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
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        captured.append((activity_type, payload))
        if activity_type == "artifact.read":
            if payload.get("artifact_ref") == "artifact://registry/1":
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

    assert captured[0][0] == "artifact.read"
    assert captured[0][1]["artifact_ref"] == "art_plan_1"
    assert captured[1][0] == "artifact.read"
    assert captured[1][1]["artifact_ref"] == "artifact://registry/1"
    assert captured[2][0] == "mm.skill.execute"
    assert captured[2][1]["registry_snapshot_ref"] == "artifact://registry/1"


@pytest.mark.asyncio
async def test_run_execution_stage_routes_mm_tool_execute_from_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> object:
        captured.append((activity_type, payload))
        if activity_type == "artifact.read":
            if payload.get("artifact_ref") == "artifact://registry/1":
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

    assert captured[2][0] == "mm.tool.execute"
    assert captured[2][1]["registry_snapshot_ref"] == "artifact://registry/1"


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
            if payload.get("artifact_ref") == "artifact://registry/1":
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
            if payload.get("artifact_ref") == "artifact://registry/1":
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
            if payload.get("artifact_ref") == "artifact://registry/1":
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
            if payload.get("artifact_ref") == "artifact://registry/1":
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
            if payload.get("artifact_ref") == "artifact://registry/1":
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
            if payload.get("artifact_ref") == "artifact://registry/1":
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
