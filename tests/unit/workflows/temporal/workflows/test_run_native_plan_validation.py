"""Final executable plans are checked against native grants before dispatch."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.workflows import run as run_module


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True])
async def test_bound_native_plan_validation_precedes_effective_plan_execution(
    monkeypatch, patched
):
    owner = run_module.MoonMindRunWorkflow()
    owner._owner_id = "owner-1"
    calls = []

    async def execute(name, payload, **_kwargs):
        calls.append((name, payload))
        if name == "plan.validate":
            raise ValueError("native effective plan conflicts with its frozen graph")
        raise ValueError("retained execution path")

    monkeypatch.setattr(run_module.workflow, "execute_activity", execute)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: patched and name == "run-native-repository-plan-validation-v1",
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(task_queue="mm.workflow.user.v2", workflow_id="wf", run_id="run", namespace="default"),
    )
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda *_: None)
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", lambda *_: None
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    binding = {"planRef": "admitted-native-plan"}
    with pytest.raises(
        ValueError,
        match="native effective plan" if patched else "retained execution path",
    ):
        await owner._run_execution_stage(
            parameters={"omnigentExecutionPlan": binding},
            plan_ref="caller-supplied-alternative-plan",
        )
    assert calls[0][0] == ("plan.validate" if patched else "artifact.read")
    if patched:
        assert calls[0][1]["plan_ref"] == "caller-supplied-alternative-plan"
        assert calls[0][1]["omnigent_execution_plan"] == binding
        assert calls[0][1]["execution_parameters"] == {"omnigentExecutionPlan": binding}
