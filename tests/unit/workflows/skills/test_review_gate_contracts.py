"""Tests for approval policy contracts: ReviewRequest, ReviewVerdict, builders."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.tool_plan_contracts import (
    ContractValidationError,
    DEFAULT_SKIP_TOOL_TYPES,
    ApprovalPolicyPolicy,
)
from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    StepGateResult,
    ReviewVerdict,
    build_feedback_input,
    build_feedback_instruction,
    build_review_prompt,
    parse_step_gate_result,
    parse_review_verdict,
)

# ── ApprovalPolicyPolicy ──────────────────────────────────────────────────

class TestApprovalPolicyPolicy:
    def test_defaults(self):
        p = ApprovalPolicyPolicy()
        assert p.enabled is False
        assert p.max_review_attempts == 2
        assert p.max_consecutive_no_progress_attempts is None
        assert p.reviewer_model == "default"
        assert p.review_timeout_seconds == 120
        assert p.skip_tool_types == DEFAULT_SKIP_TOOL_TYPES

    def test_enabled(self):
        p = ApprovalPolicyPolicy(
            enabled=True,
            max_review_attempts=3,
            max_consecutive_no_progress_attempts=2,
        )
        assert p.enabled is True
        assert p.max_review_attempts == 3
        assert p.max_consecutive_no_progress_attempts == 2

    def test_negative_max_attempts_rejected(self):
        with pytest.raises(ContractValidationError, match="max_review_attempts"):
            ApprovalPolicyPolicy(max_review_attempts=-1)

    @pytest.mark.parametrize("value", [-1, 0])
    def test_invalid_no_progress_attempts_rejected(self, value):
        with pytest.raises(
            ContractValidationError,
            match="max_consecutive_no_progress_attempts",
        ):
            ApprovalPolicyPolicy(max_consecutive_no_progress_attempts=value)

    def test_zero_max_attempts_allowed(self):
        p = ApprovalPolicyPolicy(max_review_attempts=0)
        assert p.max_review_attempts == 0

    def test_zero_timeout_rejected(self):
        with pytest.raises(ContractValidationError, match="review_timeout_seconds"):
            ApprovalPolicyPolicy(review_timeout_seconds=0)

    def test_to_payload(self):
        p = ApprovalPolicyPolicy(enabled=True, skip_tool_types=("agent_runtime",))
        payload = p.to_payload()
        assert payload["enabled"] is True
        assert "max_consecutive_no_progress_attempts" not in payload
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
            "tool_type": "skill",
            "inputs": {"repo_ref": "git:org/repo#branch"},
            "execution_result": {"status": "COMPLETED", "outputs": {}},
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
    def test_fully_implemented(self):
        v = ReviewVerdict(verdict="FULLY_IMPLEMENTED", confidence=0.95)
        assert v.verdict == "FULLY_IMPLEMENTED"

    def test_additional_work_with_feedback(self):
        v = ReviewVerdict(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            feedback="Missing import",
            remaining_work_ref="artifact://remaining",
            recommended_next_action="reattempt_current_step",
            recoverable_in_current_runtime=True,
        )
        assert v.feedback == "Missing import"
        assert v.to_payload()["remainingWorkRef"] == "artifact://remaining"
        assert v.to_payload()["recoverableInCurrentRuntime"] is True

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ContractValidationError, match="verdict"):
            ReviewVerdict(verdict="MAYBE")

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ContractValidationError, match="confidence"):
            ReviewVerdict(verdict="FULLY_IMPLEMENTED", confidence=1.5)

    def test_to_payload(self):
        v = ReviewVerdict(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence=0.7,
            feedback="bad",
            issues=({"severity": "error", "description": "missing"},),
        )
        p = v.to_payload()
        assert p["verdict"] == "ADDITIONAL_WORK_NEEDED"
        assert len(p["issues"]) == 1

# ── StepGateResult ────────────────────────────────────────────────────

class TestStepGateResult:
    def test_to_payload_includes_canonical_gate_fields(self):
        gate = StepGateResult(
            verdict="ADDITIONAL_WORK_NEEDED",
            confidence="medium",
            feedback="Tests still fail",
            validated_refs={"testReportRef": "artifact://tests"},
            invalidated_refs=("artifact://old-report",),
            remaining_work_ref="artifact://remaining",
            blocking_evidence_refs=("artifact://verification",),
            recommended_next_action="reattempt_current_step",
            target_logical_step_id="implement",
            workspace_policy_recommendation=(
                "apply_previous_execution_diff_to_clean_baseline"
            ),
            recoverable_in_current_runtime=True,
        )

        payload = gate.to_payload()

        assert payload == {
            "schemaVersion": "v1",
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "confidence": "medium",
            "feedback": "Tests still fail",
            "issues": [],
            "recoverableInCurrentRuntime": True,
            "invalid": False,
            "degraded": False,
            "validatedRefs": {"testReportRef": "artifact://tests"},
            "invalidatedRefs": ["artifact://old-report"],
            "remainingWorkRef": "artifact://remaining",
            "blockingEvidenceRefs": ["artifact://verification"],
            "recommendedNextAction": "reattempt_current_step",
            "targetLogicalStepId": "implement",
            "workspacePolicyRecommendation": (
                "apply_previous_execution_diff_to_clean_baseline"
            ),
        }

    def test_writer_rejects_unknown_verdict(self):
        with pytest.raises(ContractValidationError, match="verdict"):
            StepGateResult(verdict="MAYBE")

    def test_writer_rejects_unknown_recommended_action(self):
        with pytest.raises(ContractValidationError, match="recommendedNextAction"):
            StepGateResult(
                verdict="ADDITIONAL_WORK_NEEDED",
                recommended_next_action="do_whatever",
            )

# ── parse_review_verdict ──────────────────────────────────────────────

class TestParseReviewVerdict:
    def test_parse_valid(self):
        v = parse_review_verdict(
            {
                "verdict": "fully_implemented",
                "confidence": "high",
                "feedback": None,
                "issues": [],
                "validatedRefs": {"testReportRef": "artifact://tests"},
            }
        )
        assert v.verdict == "FULLY_IMPLEMENTED"
        assert v.confidence == "high"
        assert v.validated_refs == {"testReportRef": "artifact://tests"}

    def test_parse_legacy_pass_maps_to_fully_implemented(self):
        v = parse_review_verdict({"verdict": "PASS", "confidence": 0.9})
        assert v.verdict == "FULLY_IMPLEMENTED"

    def test_parse_unknown_verdict_becomes_inconclusive(self):
        v = parse_review_verdict({"verdict": "MAYBE"})
        assert v.verdict == "NO_DETERMINATION"
        assert v.invalid is True
        assert v.degraded is True
        assert v.recommended_next_action == "blocked"

    def test_parse_missing_verdict(self):
        v = parse_review_verdict({})
        assert v.verdict == "NO_DETERMINATION"
        assert v.invalid is True
        assert v.degraded is True

    def test_parse_step_gate_result_legacy_pass_fail(self):
        passed = parse_step_gate_result({"verdict": "PASS", "confidence": 0.9})
        failed = parse_step_gate_result({"verdict": "FAIL", "confidence": "low"})

        assert passed.verdict == "FULLY_IMPLEMENTED"
        assert passed.invalid is False
        assert failed.verdict == "ADDITIONAL_WORK_NEEDED"
        assert failed.invalid is False

    @pytest.mark.parametrize(
        "payload",
        [
            {"verdict": "approved in prose"},
            {"verdict": "FUTURE_VERDICT"},
            {"verdict": ""},
            {},
        ],
    )
    def test_parse_step_gate_result_unknown_values_fail_closed(self, payload):
        gate = parse_step_gate_result(payload)

        assert gate.verdict == "NO_DETERMINATION"
        assert gate.invalid is True
        assert gate.degraded is True
        assert gate.recommended_next_action == "blocked"
        assert gate.recoverable_in_current_runtime is False

    def test_parse_unknown_recommended_action_fails_closed(self):
        # An unrecognized recommendedNextAction must not raise a hard
        # ContractValidationError; it should downgrade the gate to a
        # blocked/invalid/degraded result instead.
        gate = parse_step_gate_result(
            {
                "verdict": "FULLY_IMPLEMENTED",
                "confidence": 0.95,
                "recommendedNextAction": "do_whatever",
            }
        )
        assert gate.verdict == "NO_DETERMINATION"
        assert gate.invalid is True
        assert gate.degraded is True
        assert gate.recommended_next_action == "blocked"

    @pytest.mark.parametrize("flag", ["invalid", "degraded"])
    def test_parse_passing_verdict_with_failure_flag_downgrades(self, flag):
        # A passing verdict that arrives already marked invalid/degraded must
        # not retain FULLY_IMPLEMENTED, since downstream branching keys on the
        # verdict alone and would otherwise approve publication.
        gate = parse_step_gate_result(
            {"verdict": "FULLY_IMPLEMENTED", "confidence": 0.99, flag: True}
        )
        assert gate.verdict == "NO_DETERMINATION"
        assert gate.recommended_next_action == "blocked"
        assert getattr(gate, flag) is True

    def test_parse_bad_confidence_clamped(self):
        v = parse_review_verdict({"verdict": "PASS", "confidence": 5.0})
        assert v.confidence == 1.0

    def test_parse_issues_skips_non_dicts(self):
        v = parse_review_verdict(
            {"verdict": "FAIL", "issues": [{"severity": "error"}, "not-a-dict", 42]}
        )
        assert v.verdict == "ADDITIONAL_WORK_NEEDED"
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
            tool_type="skill",
            inputs={"repo_ref": "git:org/repo#branch"},
            execution_result={"status": "COMPLETED"},
            workflow_context={"plan_title": "Fix tests"},
        )
        prompt = build_review_prompt(req)
        assert "repo.apply_patch" in prompt
        assert "Step 1 of 3" in prompt
        assert '"repo_ref"' in prompt
        assert "Fix tests" in prompt
