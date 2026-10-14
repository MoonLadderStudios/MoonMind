"""Source authority and malformed historical handoffs at the host boundary."""

from __future__ import annotations

from copy import deepcopy

import pytest

from moonmind.workflows.skills.acceptance_contract import assessment_scope_violations


@pytest.fixture
def source_scope():
    return {
        "sourceRef": "example/repo#1",
        "sourceDigest": "sha256:original",
        "complete": True,
        "requirementIds": ["AC-1", "AC-2"],
    }


def _success(scope):
    return {
        "verdict": "FULLY_IMPLEMENTED",
        "validatedRefs": {"acceptance": {"scope": scope}},
    }


def test_current_source_scope_controls_instead_of_stale_assessment(source_scope):
    old = deepcopy(source_scope)
    old["requirementIds"].append("UNRELATED-RELEASE")
    assessment = {"verdict": "BLOCKED", "scope": old}
    assert (
        assessment_scope_violations(
            _success(source_scope), assessment, source_scope=source_scope
        )
        == []
    )
    assert assessment["scope"] == old


def test_current_source_recovery_can_complete_initially_incomplete_scope(source_scope):
    old = {"verdict": "BLOCKED", "scope": {**source_scope, "complete": False}}
    assert (
        assessment_scope_violations(
            _success(source_scope), old, source_scope=source_scope
        )
        == []
    )


@pytest.mark.parametrize(
    "history", [{}, {"scope": None}, {"scope": {"complete": False}}]
)
def test_legacy_assessment_without_scope_is_context_not_a_new_gate(
    source_scope, history
):
    # Objective acceptance is checked separately; absent old setup cannot add
    # a new prerequisite to a modern complete verifier report.
    assert assessment_scope_violations(_success(source_scope), history) == []


@pytest.mark.parametrize(
    "bad_scope",
    [
        {
            "sourceRef": "example/repo#1",
            "sourceDigest": "sha256:original",
            "complete": True,
            "requirementIds": ["AC-1", {}],
        },
        {
            "sourceRef": "example/repo#1",
            "sourceDigest": "sha256:original",
            "complete": True,
            "requirementIds": "AC-1",
        },
        {
            "sourceRef": "example/repo#1",
            "sourceDigest": "sha256:original",
            "complete": True,
            "requirementIds": ["AC-1", "AC-1"],
        },
    ],
)
def test_malformed_complete_admitted_scope_requests_repair_without_crashing(bad_scope):
    gate = {
        "verdict": "BLOCKED",
        "scope": bad_scope,
        "remainingWork": [{"requirement": "AC-1"}],
    }
    assert assessment_scope_violations(gate, {"scope": bad_scope})


@pytest.mark.parametrize(
    "refs", [[], {"acceptance": []}, {"acceptance": None}, "invalid"]
)
def test_malformed_success_scope_never_raises(source_scope, refs):
    assert assessment_scope_violations(
        {"verdict": "FULLY_IMPLEMENTED", "validatedRefs": refs}, {"scope": source_scope}
    )


def test_requirement_order_does_not_change_scope_identity(source_scope):
    reordered = {**source_scope, "requirementIds": ["AC-2", "AC-1"]}
    assert (
        assessment_scope_violations(_success(reordered), {"scope": source_scope}) == []
    )


def test_incomplete_current_source_cannot_be_accepted(source_scope):
    assert assessment_scope_violations(
        _success(source_scope), {}, source_scope={**source_scope, "complete": False}
    )


def test_current_source_keeps_explicit_proof_requirement_blocking(source_scope):
    gate = {
        "verdict": "BLOCKED",
        "scope": source_scope,
        "remainingWork": [
            {
                "requirementIds": ["AC-2"],
                "gapType": "environment",
                "remainingWork": "Obtain the explicitly required live provider proof",
            }
        ],
    }
    assert assessment_scope_violations(gate, {}, source_scope=source_scope) == []
    gate["remainingWork"][0]["requirementIds"] = ["PARENT-EPIC-GPU"]
    assert assessment_scope_violations(gate, {}, source_scope=source_scope)


def test_nonpassing_source_binding_can_use_the_shared_acceptance_envelope(source_scope):
    gate = {
        "verdict": "BLOCKED",
        "validatedRefs": {"acceptance": {"scope": source_scope}},
        "remainingWork": [
            {"requirement": "AC-2", "remainingWork": "Collect required proof"}
        ],
    }
    assert assessment_scope_violations(gate, {"scope": source_scope}) == []


def test_remaining_work_cannot_hide_its_primary_requirement_with_other_ids(
    source_scope,
):
    gate = {
        "verdict": "BLOCKED",
        "scope": source_scope,
        "remainingWork": [
            {
                "requirement": "AC-2",
                "requirementIds": ["AC-1"],
                "remainingWork": "AC-2 still needs explicit live proof",
            }
        ],
    }
    assert assessment_scope_violations(gate, {"scope": source_scope})
    gate["remainingWork"][0]["requirementIds"] = ["AC-1", "AC-2"]
    assert assessment_scope_violations(gate, {"scope": source_scope}) == []
