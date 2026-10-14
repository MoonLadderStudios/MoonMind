"""Controller evidence takes precedence without changing retained histories."""

import pytest

from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.temporal.workflows.test_run_recover_from_failed_step import (
    _configure_workflow_runtime,
)


def test_publication_identity_comes_from_admitted_workflow_not_plan_overrides(
    monkeypatch,
):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: False)
    identity = {"name": "Admitted operator", "email": "admitted@example.invalid"}
    request = MoonMindRunWorkflow()._build_agent_execution_request(
        node_id="publish",
        tool_name="omnigent",
        node_inputs={
            "instructions": "Publish",
            "runtime": {"mode": "omnigent"},
            "_publicationGitIdentity": {
                "name": "forged",
                "email": "forged@example.invalid",
            },
        },
        workflow_parameters={"_publicationGitIdentity": identity},
    )
    assert request.parameters["_publicationGitIdentity"] == identity


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
