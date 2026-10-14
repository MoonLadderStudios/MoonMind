"""Controller evidence takes precedence without changing retained histories."""

import pytest

from moonmind.omnigent.git_identity import resolve_execution_git_identity
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.temporal.workflows.test_run_recover_from_failed_step import (
    _configure_workflow_runtime,
)


@pytest.mark.parametrize("patched", [False, True])
def test_publication_identity_payload_keeps_pre_patch_shape(
    monkeypatch,
    patched,
):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch: (
            patched if patch == "run-publication-git-identity-payload-v1" else False
        ),
    )
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
    assert request.parameters.get("_publicationGitIdentity") == (
        identity if patched else None
    )


@pytest.mark.parametrize("source", ["node", "workflow"])
@pytest.mark.parametrize(
    "forged", [{"name": "forged", "email": "forged@example.invalid"}, "not-an-identity"]
)
@pytest.mark.parametrize("admitted", [False, True])
def test_publication_identity_survives_current_runtime_parameter_flattening(
    monkeypatch,
    source,
    forged,
    admitted,
):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
    identity = {"name": "Admitted operator", "email": "admitted@example.invalid"}
    node = {"instructions": "Publish", "runtime": {"mode": "omnigent"}}
    parent = {"workflow": {"runtime": {"mode": "omnigent"}}}
    if admitted:
        parent["_publicationGitIdentity"] = identity
    runtime = node["runtime"] if source == "node" else parent["workflow"]["runtime"]
    runtime["parameters"] = {"_publicationGitIdentity": forged, "surviving": "value"}
    request = MoonMindRunWorkflow()._build_agent_execution_request(
        node_id="publish",
        tool_name="omnigent",
        node_inputs=node,
        workflow_parameters=parent,
    )
    assert request.parameters.get("_publicationGitIdentity") == (
        identity if admitted else None
    )
    assert "_publicationGitIdentity" not in request.parameters["runtime"]["parameters"]
    assert request.parameters["surviving"] == "value"


@pytest.mark.parametrize("malformed", ["not-an-identity", {"name": "operator"}])
def test_malformed_admitted_identity_reaches_existing_fail_closed_reader(
    monkeypatch,
    malformed,
):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
    request = MoonMindRunWorkflow()._build_agent_execution_request(
        node_id="publish",
        tool_name="omnigent",
        node_inputs={"instructions": "Publish", "runtime": {"mode": "omnigent"}},
        workflow_parameters={"_publicationGitIdentity": malformed},
    )
    with pytest.raises(ValueError, match="admitted publication Git identity"):
        resolve_execution_git_identity(request.parameters)


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
