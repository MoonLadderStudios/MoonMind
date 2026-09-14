"""Caller-boundary characterization for the #3943 review/evidence/feedback seam.

Pins the workflow side of the responsibility boundary: ``MoonMindRunWorkflow``
owns step-ledger reads and delegates pure value computation to
``approval_policy``. These tests exercise the workflow wrapper methods
(the call sites at run.py review/evidence region), not just the module
helpers covered in ``test_review_evidence_feedback.py``.

No Temporal commands are emitted by this seam: the wrappers are synchronous
value delegations. They run on a bare workflow instance without a Temporal
context; any future scheduling/state side effect would break these tests.
"""

from __future__ import annotations

import moonmind.workflows.skills.approval_policy as approval_policy
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import (
    GateTransitionDecision,
    MoonMindRunWorkflow,
)


def _transition(disposition: str) -> GateTransitionDecision:
    return GateTransitionDecision(
        disposition=disposition,
        routing_disposition="stop_at_control_gate",
        reason_code="no_remediation_successor",
    )


class TestGateTransitionCallerBoundary:
    def test_workflow_passes_compact_disposition_verbatim(self):
        # Exact string pass-through: surrounding whitespace is NOT trimmed
        # (matches module semantics pinned in value tests).
        assert (
            MoonMindRunWorkflow._gate_transition_allows_review_retry(
                plan_routed_moonspec_remediation_enabled=True,
                transition=_transition("  retry  "),
            )
            is False
        )
        assert (
            MoonMindRunWorkflow._gate_transition_allows_review_retry(
                plan_routed_moonspec_remediation_enabled=True,
                transition=_transition("retry"),
            )
            is True
        )
        assert (
            MoonMindRunWorkflow._gate_transition_allows_review_retry(
                plan_routed_moonspec_remediation_enabled=False,
                transition=_transition("accept"),
            )
            is True
        )

    def test_workflow_does_not_mutate_transition(self):
        transition = _transition("accept")
        MoonMindRunWorkflow._gate_transition_allows_review_retry(
            plan_routed_moonspec_remediation_enabled=True,
            transition=transition,
        )
        assert transition.disposition == "accept"
        assert transition.routing_disposition == "stop_at_control_gate"

    def test_matches_module_helper(self):
        for enabled in (False, True):
            for disposition in ("generic", "retry", "accept", "invalid"):
                assert (
                    MoonMindRunWorkflow._gate_transition_allows_review_retry(
                        plan_routed_moonspec_remediation_enabled=enabled,
                        transition=_transition(disposition),
                    )
                    == approval_policy.gate_transition_allows_review_retry(
                        plan_routed_moonspec_remediation_enabled=enabled,
                        transition_disposition=disposition,
                    )
                )


class TestInjectFeedbackCallerBoundary:
    def test_workflow_delegates_with_identical_result(self):
        wf = MoonMindRunWorkflow()
        original = {"instructions": "do work", "query": "q"}
        result = wf._inject_review_feedback_into_inputs(
            tool_type="agent_runtime",
            original_inputs=dict(original),
            attempt=1,
            feedback="fix it",
            issues=(),
        )
        expected = approval_policy.inject_review_feedback_into_inputs(
            tool_type="agent_runtime",
            original_inputs=dict(original),
            attempt=1,
            feedback="fix it",
            issues=(),
        )
        assert result == expected
        assert "REVIEW FEEDBACK (attempt 1)" in result["instructions"]

    def test_workflow_preserves_exact_agent_runtime_match(self):
        wf = MoonMindRunWorkflow()
        result = wf._inject_review_feedback_into_inputs(
            tool_type=" agent_runtime ",
            original_inputs={"instructions": "do work"},
            attempt=1,
            feedback="fb",
            issues=(),
        )
        # Exact ``== "agent_runtime"``: padded value takes the non-agent path.
        assert result["instructions"] == "do work"

    def test_workflow_does_not_mutate_original(self):
        wf = MoonMindRunWorkflow()
        original = {"query": "q"}
        wf._inject_review_feedback_into_inputs(
            tool_type="skill",
            original_inputs=original,
            attempt=2,
            feedback="try again",
            issues=({"description": "missing tests"},),
        )
        assert original == {"query": "q"}


class TestStepEvidenceCallerBoundary:
    @staticmethod
    def _workflow_with_ledger(
        execution_outputs, ledger_refs
    ) -> MoonMindRunWorkflow:
        wf = MoonMindRunWorkflow()
        wf._effective_result_outputs = lambda _result: execution_outputs  # type: ignore[method-assign]
        wf._step_execution_compact_output_refs = (  # type: ignore[method-assign]
            lambda _step_id: ledger_refs
        )
        return wf

    def test_alias_expansion_through_workflow(self):
        # run.py:13281/13438 path: snake_case report ref becomes primaryRef.
        wf = self._workflow_with_ledger(
            {"primary_report_ref": "  art_1  "}, {}
        )
        assert wf._step_has_accepted_output_evidence("step-1", object()) is True

    def test_ledger_refs_supply_evidence(self):
        wf = self._workflow_with_ledger(None, {"summaryRef": "art_ledger"})
        assert wf._step_has_accepted_output_evidence("step-1", object()) is True

    def test_no_evidence_anywhere_is_false(self):
        wf = self._workflow_with_ledger(None, {})
        assert wf._step_has_accepted_output_evidence("step-1", object()) is False

    def test_blank_execution_values_do_not_count(self):
        wf = self._workflow_with_ledger({"summaryRef": "   "}, {})
        assert wf._step_has_accepted_output_evidence("step-1", object()) is False

    def test_non_mapping_execution_coerced_to_none(self):
        # Workflow passes ``outputs if isinstance(outputs, Mapping) else None``.
        wf = self._workflow_with_ledger("not-a-mapping", {})
        assert wf._step_has_accepted_output_evidence("step-1", object()) is False

    def test_ledger_overrides_execution_at_caller_boundary(self, monkeypatch):
        captured: dict = {}

        real = run_module.logical_step_success_allowed

        def _capture(*, outputs=None, git_effect=None):
            captured["outputs"] = dict(outputs or {})
            return real(outputs=outputs, git_effect=git_effect)

        monkeypatch.setattr(
            run_module, "logical_step_success_allowed", _capture
        )
        wf = self._workflow_with_ledger(
            {"summaryRef": "art_exec", "commitSha": "abc"},
            {"summaryRef": "art_ledger"},
        )
        assert wf._step_has_accepted_output_evidence("step-1", object()) is True
        assert captured["outputs"]["summaryRef"] == "art_ledger"
        assert captured["outputs"]["commitSha"] == "abc"


class TestDeadForwarderRemoved:
    def test_merge_forwarder_gone(self):
        # R7: the superseded static forwarder was removed; the module helper
        # is the single owner. Callers use merge_accepted_output_evidence.
        assert not hasattr(MoonMindRunWorkflow, "_merge_direct_output_evidence")
        assert callable(approval_policy.merge_direct_output_evidence)
        assert callable(approval_policy.merge_accepted_output_evidence)
