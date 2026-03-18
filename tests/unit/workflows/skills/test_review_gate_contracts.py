"""Tests for review gate contracts: ReviewRequest, ReviewVerdict, builders."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.tool_plan_contracts import (
    ContractValidationError,
    ReviewGatePolicy,
)
from moonmind.workflows.skills.review_gate import (
    ReviewRequest,
    ReviewVerdict,
    build_feedback_input,
    build_feedback_instruction,
    build_review_prompt,
    parse_review_verdict,
)


# ── ReviewGatePolicy ──────────────────────────────────────────────────


class TestReviewGatePolicy:
    def test_defaults(self):
        p = ReviewGatePolicy()
        assert p.enabled is False
        assert p.max_review_attempts == 2
        assert p.reviewer_model == "default"
        assert p.review_timeout_seconds == 120
        assert p.skip_tool_types == ()

    def test_enabled(self):
        p = ReviewGatePolicy(enabled=True, max_review_attempts=3)
        assert p.enabled is True
        assert p.max_review_attempts == 3

    def test_negative_max_attempts_rejected(self):
        with pytest.raises(ContractValidationError, match="max_review_attempts"):
            ReviewGatePolicy(max_review_attempts=-1)

    def test_zero_max_attempts_allowed(self):
        p = ReviewGatePolicy(max_review_attempts=0)
        assert p.max_review_attempts == 0

    def test_zero_timeout_rejected(self):
        with pytest.raises(ContractValidationError, match="review_timeout_seconds"):
            ReviewGatePolicy(review_timeout_seconds=0)

    def test_to_payload(self):
        p = ReviewGatePolicy(enabled=True, skip_tool_types=("agent_runtime",))
        payload = p.to_payload()
        assert payload["enabled"] is True
        assert payload["skip_tool_types"] == ["agent_runtime"]


# ── ReviewRequest ─────────────────────────────────────────────────────


class TestReviewRequest:
    def _make(self, **overrides):
        defaults = {
            "node_id": "n1",
            "step_index": 1,
            "total_steps": 3,
            "review_attempt": 1,
            "tool_name": "repo.apply_patch",
            "tool_version": "1.0.0",
            "tool_type": "skill",
            "inputs": {"repo_ref": "git:org/repo#branch"},
            "execution_result": {"status": "SUCCEEDED", "outputs": {}},
            "workflow_context": {"workflow_id": "wf-1", "plan_title": "Fix tests"},
        }
        defaults.update(overrides)
        return ReviewRequest(**defaults)

    def test_valid_construction(self):
        req = self._make()
        assert req.node_id == "n1"
        assert req.step_index == 1

    def test_blank_node_id_rejected(self):
        with pytest.raises(ContractValidationError, match="node_id"):
            self._make(node_id="")

    def test_zero_step_index_rejected(self):
        with pytest.raises(ContractValidationError, match="step_index"):
            self._make(step_index=0)

    def test_to_payload_round_trips(self):
        req = self._make(previous_feedback="try harder")
        payload = req.to_payload()
        assert payload["previous_feedback"] == "try harder"
        assert payload["tool_type"] == "skill"


# ── ReviewVerdict ─────────────────────────────────────────────────────


class TestReviewVerdict:
    def test_pass(self):
        v = ReviewVerdict(verdict="PASS", confidence=0.95)
        assert v.verdict == "PASS"

    def test_fail_with_feedback(self):
        v = ReviewVerdict(verdict="FAIL", confidence=0.8, feedback="Missing import")
        assert v.feedback == "Missing import"

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ContractValidationError, match="verdict"):
            ReviewVerdict(verdict="MAYBE")

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ContractValidationError, match="confidence"):
            ReviewVerdict(verdict="PASS", confidence=1.5)

    def test_to_payload(self):
        v = ReviewVerdict(
            verdict="FAIL",
            confidence=0.7,
            feedback="bad",
            issues=({"severity": "error", "description": "missing"},),
        )
        p = v.to_payload()
        assert p["verdict"] == "FAIL"
        assert len(p["issues"]) == 1


# ── parse_review_verdict ──────────────────────────────────────────────


class TestParseReviewVerdict:
    def test_parse_valid(self):
        v = parse_review_verdict(
            {"verdict": "pass", "confidence": 0.9, "feedback": None, "issues": []}
        )
        assert v.verdict == "PASS"
        assert v.confidence == 0.9

    def test_parse_unknown_verdict_becomes_inconclusive(self):
        v = parse_review_verdict({"verdict": "MAYBE"})
        assert v.verdict == "INCONCLUSIVE"

    def test_parse_missing_verdict(self):
        v = parse_review_verdict({})
        assert v.verdict == "INCONCLUSIVE"

    def test_parse_bad_confidence_clamped(self):
        v = parse_review_verdict({"verdict": "PASS", "confidence": 5.0})
        assert v.confidence == 1.0

    def test_parse_issues_skips_non_dicts(self):
        v = parse_review_verdict(
            {"verdict": "FAIL", "issues": [{"severity": "error"}, "not-a-dict", 42]}
        )
        assert len(v.issues) == 1


# ── build_feedback_input ──────────────────────────────────────────────


class TestBuildFeedbackInput:
    def test_injects_review_feedback_key(self):
        result = build_feedback_input(
            {"repo_ref": "git:org/repo#branch"},
            attempt=1,
            feedback="Missing import in utils.py",
        )
        assert "_review_feedback" in result
        assert result["_review_feedback"]["attempt"] == 1
        assert result["_review_feedback"]["feedback"] == "Missing import in utils.py"
        # Original keys preserved
        assert result["repo_ref"] == "git:org/repo#branch"

    def test_does_not_mutate_original(self):
        original = {"key": "value"}
        build_feedback_input(original, attempt=1, feedback="f")
        assert "_review_feedback" not in original


# ── build_feedback_instruction ────────────────────────────────────────


class TestBuildFeedbackInstruction:
    def test_appends_feedback_block(self):
        result = build_feedback_instruction(
            "Fix the tests", attempt=2, feedback="Still failing"
        )
        assert result.startswith("Fix the tests")
        assert "REVIEW FEEDBACK (attempt 2)" in result
        assert "Still failing" in result


# ── build_review_prompt ───────────────────────────────────────────────


class TestBuildReviewPrompt:
    def test_produces_populated_prompt(self):
        req = ReviewRequest(
            node_id="n1",
            step_index=1,
            total_steps=3,
            review_attempt=1,
            tool_name="repo.apply_patch",
            tool_version="1.0.0",
            tool_type="skill",
            inputs={"repo_ref": "git:org/repo#branch"},
            execution_result={"status": "SUCCEEDED"},
            workflow_context={"plan_title": "Fix tests"},
        )
        prompt = build_review_prompt(req)
        assert "repo.apply_patch" in prompt
        assert "Step 1 of 3" in prompt
        assert '"repo_ref"' in prompt
        assert "Fix tests" in prompt
