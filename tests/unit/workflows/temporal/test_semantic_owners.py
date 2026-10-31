"""Production owners supply decisions; orchestration applies them unchanged."""

from unittest.mock import Mock

import pytest

from moonmind.runtime_intent import step_profile_intent
from moonmind.workflows.skills import approval_policy as gates
from moonmind.workflows.temporal import publication_recovery as publication
from moonmind.workflows.temporal.bounded_story_loop import PublicationFeasibility
from moonmind.workflows.temporal.workflows import run as run_module


@pytest.mark.parametrize("container", [
    "moonSpecVerify", "moonspecVerify", "moonspec_verify", "verification",
    "verificationResult", "verification_result", "gate", "review", None,
])
def test_gate_owner_preserves_structured_stop_and_saved_evidence(container):
    payload = gates.canonicalize_step_gate_payload({
        "verdict": "ADDITIONAL_WORK_NEEDED",
        "recommended_next_action": "blocked",
        "gateResultRef": "art_saved_report",
        "operator_summary": " Original   verifier finding. ",
        "diagnostics_ref": "art_original_error",
        "issues": [{"description": "Preserve this finding"}],
    })
    outputs = {container: payload} if container else payload
    result = gates.parse_step_gate_result(gates.step_gate_payload(outputs))
    decision = gates.prepare_step_gate_record(
        outputs, logical_step_id="verify", result=result,
        remaining_work_evidence_enabled=True, evidence_continuation_enabled=True,
    )
    assert decision.result.verdict == "ADDITIONAL_WORK_NEEDED"
    assert decision.result.recommended_next_action == "blocked"
    assert decision.result_ref == "art_saved_report"
    assert decision.context["remainingWorkRef"] == "artifact://art_saved_report"
    assert decision.context["diagnosticsRef"] == "art_original_error"
    assert decision.context["summary"] == "Original verifier finding."
    assert decision.result.issues == ({"description": "Preserve this finding"},)


def test_workflow_applies_skill_owner_projection_without_rediscovering_it(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    result = gates.StepGateResult(verdict="ADDITIONAL_WORK_NEEDED")
    context = {"verdict": result.verdict, "summary": "Owner's original diagnosis"}
    owner = Mock(return_value=gates.PreparedStepGate(result, "art_saved", context))
    monkeypatch.setattr(run_module, "prepare_step_gate_record", owner)
    parser = Mock(wraps=run_module.parse_step_gate_result)
    monkeypatch.setattr(run_module, "parse_step_gate_result", parser)
    workflow = run_module.MoonMindRunWorkflow()
    decision = workflow._record_moonspec_verify_gate(
        node_id="verify", outputs={"summary": "FULLY_IMPLEMENTED", "gate": {"verdict": "BLOCKED"}},
    )
    assert workflow._publish_context["moonSpecGate"] == context
    assert workflow._moonspec_gate_verdict == result.verdict
    assert workflow._moonspec_gate_reason == context["summary"]
    assert decision is owner.return_value
    owner.assert_called_once()
    parser.assert_called_once()


@pytest.mark.parametrize(("evidence", "reason", "feasible"), [
    ({"publicationAuthorized": False, "pushStatus": "pushed"}, "publication_unauthorized", False),
    ({"candidateContaminated": True, "pushStatus": "pushed"}, "candidate_contaminated", False),
    ({"commitsAheadOfBase": 0}, "no_candidate_change", False),
    ({"commitsAheadOfBase": 2}, "commits_ahead_of_base", True),
    ({"candidateDiffRef": "art_diff"}, "safe_candidate_diff", True),
    ({"repositoryChanged": False}, "no_candidate_change", False),
    ({}, "publication_state_ambiguous", False),
])
def test_publication_owner_classifies_evidence(evidence, reason, feasible):
    decision = publication.prepare_publication_feasibility({
        "acceptedRepositoryEvidence": {**evidence, "evidenceRef": "art_receipt"},
    })
    assert decision.reason == reason
    assert decision.feasible is feasible
    assert decision.evidence_refs[0] == "artifact://art_receipt"
    assert decision.attempted is False
    resolved = publication.resolve_run_publication_feasibility(
        decision, accepted_head_available=True,
    )
    if reason == "publication_state_ambiguous":
        assert resolved.feasible is True
        assert resolved.reason == "verified_remote_head"
    else:
        assert resolved == decision


def test_workflow_applies_publication_owner_decision(monkeypatch):
    decision = PublicationFeasibility(feasible=False, reason="publication_unauthorized")
    owner = Mock(return_value=decision)
    monkeypatch.setattr(run_module, "prepare_publication_feasibility", owner)
    workflow = run_module.MoonMindRunWorkflow()
    result = {"outputs": {"acceptedRepositoryEvidence": {"pushStatus": "pushed"}}}
    assert workflow._publication_feasibility(result) == decision.model_dump(by_alias=True)
    owner.assert_called_once_with(result["outputs"])


@pytest.mark.parametrize("normalized,inheritance,expected", [
    (True, True, "authored"), (False, True, "authored"), (False, False, "recorded"),
])
def test_profile_owner_preserves_recorded_alias_precedence(normalized, inheritance, expected):
    decision = step_profile_intent(
        {"providerProfileRef": "authored", "executionProfileRef": "recorded"}, {},
        normalized_account_intent=normalized, selection_inheritance=inheritance,
    )
    assert decision.profile_ref == expected


@pytest.mark.parametrize("normalized,inheritance,expected", [
    (True, True, True), (False, True, False), (False, False, True),
])
def test_profile_owner_preserves_empty_selector_inheritance(normalized, inheritance, expected):
    decision = step_profile_intent(
        {"profileSelector": {"providerId": None, "tagsAll": [], "tagsAny": []}}, {},
        normalized_account_intent=normalized, selection_inheritance=inheritance,
    )
    assert decision.profile_ref is None
    assert decision.inherit_workflow_profile is expected


@pytest.mark.parametrize("value,retained", [
    ({"moonSpecVerify": {"verdict": "BLOCKED"}}, False),
    ({"moonSpecVerify": {"gateVerdict": "BLOCKED"}}, True),
    ({"verification": {"verdict": "BLOCKED"}}, True),
    ({"verdict": "BLOCKED"}, True),
    ({"previousOutputs": [{"gate": {"verdict": "BLOCKED"}}]}, True),
    (b'{"outputs":{"verification_result":{"verdict":"BLOCKED"}}}', True),
    ({"summary": "BLOCKED"}, False),
])
def test_gate_owner_identifies_retained_saved_shapes(value, retained):
    assert gates.has_retained_step_gate_shape(value) is retained


@pytest.mark.parametrize("root_key", [
    "verdict", "gateVerdict", "gate_verdict", "moonSpecVerdict",
    "moonspecVerdict", "verificationVerdict", "verification_verdict",
])
@pytest.mark.parametrize("canonical", [
    {}, {"summary": "Saved verifier diagnostics"}, {"verdict": ""},
], ids=["empty", "diagnostics-only", "blank-verdict"])
def test_gate_owner_detects_root_fallback_beneath_incomplete_envelope(root_key, canonical):
    outputs = {"moonSpecVerify": canonical, root_key: "BLOCKED"}

    assert gates.step_gate_payload(outputs) == {**outputs, "verdict": "BLOCKED"}
    assert gates.has_retained_step_gate_shape(outputs)


def test_gate_owner_keeps_canonical_verdict_precedence_over_root_fallback():
    canonical = {"verdict": "FULLY_IMPLEMENTED"}
    outputs = {"moonSpecVerify": canonical, "gateVerdict": "BLOCKED"}

    assert gates.step_gate_payload(outputs) == canonical
    assert not gates.has_retained_step_gate_shape(outputs)
