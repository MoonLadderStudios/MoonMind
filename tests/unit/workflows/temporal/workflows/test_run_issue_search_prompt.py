"""Selected-issue agents need selection scope, not other candidates' histories."""

import copy
import json
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.workflows import run as run_module

PROMPT_PATCH = "run-selected-issue-search-prompt-v1"


def selected_issue_outputs():
    return {
        "trustedSource": "moonmind.github.get_issue",
        "issue": {
            "repository": "example/repo",
            "number": 42,
            "title": "Preserve pending work",
            "body": "Retain all original requirements and completion dependencies.",
            "labels": ["priority: high"],
        },
        "issueClaimLease": {"owner": "default/current", "attemptId": "attempt-42"},
        "presetBrief": "Original issue scope and constraints.",
        "artifactPath": "artifacts/github-issue-implement-brief.json",
        "searchEvidence": {
            "authorScope": "authenticated_user",
            "authenticatedUser": {"id": 1, "login": "operator"},
            "selectedIssueAuthor": {"id": 1, "login": "operator"},
            "authorMismatchesSkipped": 2,
            "fallbackScanning": False,
            "pagesExamined": 2,
            "candidatesExamined": 120,
            "rejectionCounts": {"live_reservation": 20, "author_mismatch": 2},
            "project": {
                "url": "https://github.com/orgs/example/projects/1",
                "order": "priority",
            },
            "rejectedCandidates": [
                {
                    "issueNumber": 100 + index,
                    "reasonCode": "live_reservation",
                    "reservationEvidence": {
                        "reasonCode": "live_or_legacy_owner",
                        "owner": {
                            "workflowId": f"other-workflow-{index}",
                            "runId": f"other-run-{index}",
                        },
                        "attemptId": f"other-attempt-{index}",
                        "summary": "A different workflow still owns this rejected issue.",
                    },
                }
                for index in range(20)
            ],
            "rejectedCandidatesTruncated": True,
            "additionalSelectionConstraint": "Preserve an unfamiliar scope field.",
        },
    }


def configure(monkeypatch, *, compact=True):
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default", workflow_id="parent", run_id="run", parent=None
        ),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda marker: compact if marker == PROMPT_PATCH else True,
    )


def build_request(parent, previous, *, stage="implement", runtime="omnigent"):
    return parent._build_agent_execution_request(
        node_inputs={
            "targetRuntime": runtime,
            "repository": "example/repo",
            "instructions": f"Perform the {stage} stage for the selected issue.",
            "previousOutputs": previous,
        },
        node_id=stage,
        tool_name=runtime,
    )


def prompt_context(request):
    return json.loads(
        request.instruction_ref.split("```json\n", 1)[1].split("\n```", 1)[0]
    )


@pytest.mark.parametrize("runtime", ["omnigent", "codex_cli"])
@pytest.mark.parametrize(
    "stage", ["assess", "implement", "verify", "publish", "remediate"]
)
def test_agent_prompt_omits_other_candidates_but_keeps_selected_authority(
    monkeypatch, runtime, stage
):
    configure(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    parent._record_trusted_issue_context(outputs)
    parent._record_assessment_context(
        {
            "briefArtifactRef": "art_brief",
            "assessmentArtifactRef": "art_assessment",
            "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
        }
    )
    previous = parent._merge_trusted_issue_context(
        {"summary": "Selected issue assessment complete."}
    )
    original = copy.deepcopy(previous)
    request = build_request(parent, previous, runtime=runtime, stage=stage)
    context = prompt_context(request)

    expected_search = {
        key: value
        for key, value in outputs["searchEvidence"].items()
        if key not in {"rejectedCandidates", "rejectedCandidatesTruncated"}
    }
    assert context["searchEvidence"] == expected_search
    for key in ("issue", "issueClaimLease", "presetBrief", "artifactPath"):
        assert context[key] == outputs[key]
    for key in ("briefArtifactRef", "assessmentArtifactRef", "assessmentVerdict"):
        assert context[key] == previous[key]
    assert "artifact://art_brief" in request.input_refs
    assert "artifact://art_assessment" in request.input_refs
    assert previous == original
    assert parent._trusted_issue_context["searchEvidence"] == outputs["searchEvidence"]
    # Trusted tool consumers continue receiving full owner/retry diagnostics.
    assert (
        parent._merge_trusted_issue_context({})["searchEvidence"]
        == outputs["searchEvidence"]
    )
    assert "other-workflow-" not in request.instruction_ref


def test_search_prompt_reduction_is_measured_without_changing_source(monkeypatch):
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    parent._record_trusted_issue_context(outputs)
    configure(monkeypatch, compact=False)
    legacy = build_request(parent, outputs)
    configure(monkeypatch)
    current = build_request(parent, outputs)
    assert (
        len(current.instruction_ref.encode()) < len(legacy.instruction_ref.encode()) / 2
    )
    legacy_context = prompt_context(legacy)
    current_context = prompt_context(current)
    legacy_search = legacy_context.pop("searchEvidence")
    current_search = current_context.pop("searchEvidence")
    assert legacy_context == current_context
    assert {
        key: value
        for key, value in legacy_search.items()
        if key not in {"rejectedCandidates", "rejectedCandidatesTruncated"}
    } == current_search


@pytest.mark.parametrize("durable_gate", [False, True])
def test_projection_preserves_verifier_proof_and_recovery_handoff(
    monkeypatch, durable_gate
):
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    parent._record_trusted_issue_context(outputs)
    gate = {
        "verdict": "ADDITIONAL_WORK_NEEDED",
        "recommendedNextAction": "reattempt_current_step",
        "remainingWork": [
            {"requirement": "AC-1", "remainingWork": "Preserve retry recovery."}
        ],
    }
    if durable_gate:
        gate["gateResultRef"] = "art_complete_report"
    parent._publish_context["moonSpecGate"] = copy.deepcopy(gate)
    configure(monkeypatch, compact=False)
    previous = parent._merge_trusted_issue_context({})
    legacy = build_request(parent, previous, stage="remediate")
    configure(monkeypatch)
    current = build_request(parent, previous, stage="remediate")
    assert (
        prompt_context(current)["moonSpecVerify"]
        == prompt_context(legacy)["moonSpecVerify"]
    )
    assert current.input_refs == legacy.input_refs
    assert parent._publish_context["moonSpecGate"] == gate
    assert previous["moonSpecVerify"] == gate
    if durable_gate:
        assert "artifact://art_complete_report" in current.input_refs
        assert (
            "Read the complete controlling verifier attachment"
            in current.instruction_ref
        )
    else:
        assert "Preserve retry recovery." in current.instruction_ref


def test_unselected_or_unowned_diagnostics_are_not_pruned(monkeypatch):
    configure(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    assert (
        prompt_context(build_request(parent, outputs))["searchEvidence"]
        == outputs["searchEvidence"]
    )
    parent._record_trusted_issue_context(
        {
            "trustedSource": "moonmind.github.get_issue",
            "searchEvidence": outputs["searchEvidence"],
        }
    )
    assert (
        prompt_context(build_request(parent, parent._trusted_issue_context))[
            "searchEvidence"
        ]
        == outputs["searchEvidence"]
    )


def test_historical_agent_request_keeps_rejected_candidate_details(monkeypatch):
    configure(monkeypatch, compact=False)
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    parent._record_trusted_issue_context(outputs)
    assert (
        prompt_context(build_request(parent, outputs))["searchEvidence"]
        == outputs["searchEvidence"]
    )


def test_direct_issue_without_search_evidence_is_unchanged(monkeypatch):
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    outputs.pop("searchEvidence")
    parent._record_trusted_issue_context(outputs)
    configure(monkeypatch, compact=False)
    legacy = build_request(parent, outputs)
    configure(monkeypatch)
    assert build_request(parent, outputs).instruction_ref == legacy.instruction_ref


def test_empty_rejections_do_not_add_prompt_overhead(monkeypatch):
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    outputs["searchEvidence"]["rejectedCandidates"] = []
    outputs["searchEvidence"]["rejectedCandidatesTruncated"] = False
    parent._record_trusted_issue_context(outputs)
    configure(monkeypatch, compact=False)
    legacy = build_request(parent, outputs)
    configure(monkeypatch)
    assert build_request(parent, outputs).instruction_ref == legacy.instruction_ref


@pytest.mark.parametrize("changed", ["source", "issue", "search"])
def test_mismatched_context_cannot_borrow_selected_issue_projection(
    monkeypatch, changed
):
    configure(monkeypatch)
    parent = run_module.MoonMindRunWorkflow()
    outputs = selected_issue_outputs()
    parent._record_trusted_issue_context(outputs)
    previous = copy.deepcopy(outputs)
    if changed == "source":
        previous["trustedSource"] = "moonmind.jira.get_issue"
    elif changed == "issue":
        previous["issue"]["number"] = 99
    else:
        previous["searchEvidence"]["authorScope"] = "all"
    configure(monkeypatch, compact=False)
    legacy = build_request(parent, previous)
    configure(monkeypatch)
    assert build_request(parent, previous).instruction_ref == legacy.instruction_ref
