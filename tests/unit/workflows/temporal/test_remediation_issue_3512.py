"""Controller evidence takes precedence without changing retained histories."""

import pytest

from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.temporal.workflows.test_run_recover_from_failed_step import (
    _configure_workflow_runtime,
)


@pytest.mark.parametrize("current_evidence", [True, False])
@pytest.mark.parametrize("remaining", ["artifact://current-remaining", None])
def test_current_controller_evidence_overrides_stale_runtime_defaults(
    monkeypatch, current_evidence, remaining
):
    _configure_workflow_runtime(monkeypatch)
    enabled = {run_module.RUN_REMEDIATION_EXPLICIT_EVIDENCE_INPUTS_PATCH}
    if current_evidence:
        enabled.add("run-remediation-current-evidence-inputs-v1")
    monkeypatch.setattr(run_module.workflow, "patched", lambda patch: patch in enabled)
    request = MoonMindRunWorkflow()._build_agent_execution_request(
        node_id="repair",
        tool_name="omnigent",
        node_inputs={
            "instructions": "Repair the original objective.",
            "remediationLoopId": "loop-3512",
            "gateResultRef": "artifact://current-verifier",
            "remainingWorkRef": remaining,
            "runtime": {
                "mode": "omnigent",
                "gateResultRef": "artifact://stale-verifier",
                "remainingWorkRef": "artifact://stale-remaining",
            },
        },
    )
    assert request.parameters["gateResultRef"] == (
        "artifact://current-verifier"
        if current_evidence
        else "artifact://stale-verifier"
    )
    assert request.parameters.get("remainingWorkRef") == (
        remaining if current_evidence else "artifact://stale-remaining"
    )
