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

    _workflow_type, _parameters, input_ref, plan_ref, registry_snapshot_ref = (
        workflow._initialize_from_payload(
            {
                "workflowType": "MoonMind.Run",
                "initialParameters": {"repo": "MoonLadderStudios/MoonMind"},
                "inputArtifactRef": "art_input_1",
                "planArtifactRef": "art_plan_1",
                "registrySnapshotRef": "art_reg_1",
            }
        )
    )

    assert input_ref == "art_input_1"
    assert plan_ref == "art_plan_1"
    assert registry_snapshot_ref == "art_reg_1"
    assert workflow._input_ref == "art_input_1"
    assert workflow._plan_ref == "art_plan_1"
    assert workflow._registry_snapshot_ref == "art_reg_1"


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
        return {"plan_ref": "art_plan_2", "registry_snapshot_ref": "art_reg_2"}

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

    resolved_plan_ref, resolved_registry_snapshot_ref = (
        await workflow._run_planning_stage(
            parameters={"repo": "MoonLadderStudios/MoonMind"},
            input_ref="art_input_1",
            plan_ref=None,
            registry_snapshot_ref=None,
        )
    )

    assert captured["activity_type"] == "plan.generate"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["inputs_ref"] == "art_input_1"
    assert "workflow_id" in payload["execution_ref"]
    assert resolved_plan_ref == "art_plan_2"
    assert resolved_registry_snapshot_ref == "art_reg_2"
    assert workflow._plan_ref == "art_plan_2"
    assert workflow._registry_snapshot_ref == "art_reg_2"


@pytest.mark.asyncio
async def test_run_execution_stage_extracts_logs_ref_from_activity_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    captured: dict[str, object] = {}

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, str | int]:
        captured["activity_type"] = activity_type
        captured["payload"] = payload
        return {"diagnostics_ref": "art_logs_1", "exit_code": 0}

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
        registry_snapshot_ref="art_reg_1",
    )

    assert captured["activity_type"] == "sandbox.run_command"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["cmd"] == "echo executing"
    assert payload["registry_snapshot_ref"] == "art_reg_1"
    assert workflow._logs_ref == "art_logs_1"
