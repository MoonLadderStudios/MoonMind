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

    _workflow_type, _parameters, input_ref, plan_ref = (
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

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, str]:
        captured["activity_type"] = activity_type
        captured["payload"] = payload
        return {"plan_ref": "art_plan_2"}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)

    resolved = await workflow._run_planning_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        input_ref="art_input_1",
        plan_ref=None,
    )

    assert captured["activity_type"] == "plan.generate"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["inputs_ref"] == "art_input_1"
    assert "workflow_id" in payload["execution_ref"]
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
        return {"status": "SUCCEEDED", "outputs": {}}

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
