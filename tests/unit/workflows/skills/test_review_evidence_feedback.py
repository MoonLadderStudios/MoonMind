"""Value/negative tests for the #3943 review/evidence/feedback seam.

Covers the pure value helpers in ``approval_policy`` that
``MoonMindRunWorkflow`` delegates to. The workflow retains step-ledger reads
and Temporal scheduling; these tests pin the value contracts, including
normalization, precedence, alias handling, and mutation semantics.
"""

from __future__ import annotations

import inspect

import moonmind.workflows.skills.approval_policy as approval_policy


class TestGateTransitionAllowsReviewRetry:
    def test_disabled_routing_always_allows(self):
        assert (
            approval_policy.gate_transition_allows_review_retry(
                plan_routed_moonspec_remediation_enabled=False,
                transition_disposition="accept",
            )
            is True
        )

    def test_enabled_allows_generic_and_retry(self):
        for disposition in ("generic", "retry"):
            assert (
                approval_policy.gate_transition_allows_review_retry(
                    plan_routed_moonspec_remediation_enabled=True,
                    transition_disposition=disposition,
                )
                is True
            )

    def test_enabled_blocks_accept_and_invalid(self):
        for disposition in ("accept", "invalid", "", "  ", "  retry  "):
            assert (
                approval_policy.gate_transition_allows_review_retry(
                    plan_routed_moonspec_remediation_enabled=True,
                    transition_disposition=disposition,
                )
                is False
            )


class TestDirectOutputEvidenceAliases:
    def test_snake_and_camel_aliases(self):
        aliases = approval_policy.direct_output_evidence_aliases(
            {"primary_report_ref": "  art_1  ", "summaryRef": "art_2"}
        )
        assert aliases == {"primaryRef": "art_1", "summaryRef": "art_2"}

    def test_first_alias_wins_for_primary_ref(self):
        aliases = approval_policy.direct_output_evidence_aliases(
            {"primary_report_ref": "art_first", "primaryReportRef": "art_second"}
        )
        assert aliases == {"primaryRef": "art_first"}

    def test_blank_and_non_string_ignored(self):
        assert approval_policy.direct_output_evidence_aliases({}) == {}
        assert (
            approval_policy.direct_output_evidence_aliases(
                {
                    "primary_report_ref": "   ",
                    "summary_ref": None,
                    "summaryRef": 123,
                    "unrelated": "art_x",
                }
            )
            == {}
        )

    def test_merge_preserves_existing_target(self):
        merged: dict = {"primaryRef": "art_existing"}
        approval_policy.merge_direct_output_evidence(
            merged, {"primary_report_ref": "art_new", "summary_ref": "art_s"}
        )
        assert merged == {"primaryRef": "art_existing", "summaryRef": "art_s"}

    def test_merge_does_not_mutate_source(self):
        source = {"primaryReportRef": "art_1"}
        merged: dict = {}
        approval_policy.merge_direct_output_evidence(merged, source)
        assert source == {"primaryReportRef": "art_1"}
        assert merged == {"primaryRef": "art_1"}


class TestMergeAcceptedOutputEvidence:
    def test_execution_plus_aliases_plus_ledger(self):
        merged = approval_policy.merge_accepted_output_evidence(
            execution_outputs={
                "commitSha": "abc",
                "primary_report_ref": "art_1",
            },
            ledger_output_refs={"summaryRef": "art_ledger"},
        )
        assert merged == {
            "commitSha": "abc",
            "primary_report_ref": "art_1",
            "primaryRef": "art_1",
            "summaryRef": "art_ledger",
        }

    def test_ledger_overrides_execution_on_conflict(self):
        merged = approval_policy.merge_accepted_output_evidence(
            execution_outputs={"summaryRef": "art_exec"},
            ledger_output_refs={"summaryRef": "art_ledger"},
        )
        assert merged["summaryRef"] == "art_ledger"

    def test_none_inputs_yield_empty(self):
        assert (
            approval_policy.merge_accepted_output_evidence(
                execution_outputs=None, ledger_output_refs=None
            )
            == {}
        )

    def test_non_mapping_execution_ignored(self):
        merged = approval_policy.merge_accepted_output_evidence(
            execution_outputs=None,
            ledger_output_refs={"primaryRef": "art_1"},
        )
        assert merged == {"primaryRef": "art_1"}

    def test_returns_new_dict_without_mutating_inputs(self):
        execution = {"commitSha": "abc"}
        ledger = {"summaryRef": "art_1"}
        merged = approval_policy.merge_accepted_output_evidence(
            execution_outputs=execution, ledger_output_refs=ledger
        )
        assert merged is not execution
        assert merged is not ledger
        assert execution == {"commitSha": "abc"}
        assert ledger == {"summaryRef": "art_1"}


class TestInjectReviewFeedbackIntoInputs:
    def test_non_agent_tool_only_adds_review_feedback(self):
        merged = approval_policy.inject_review_feedback_into_inputs(
            tool_type="skill",
            original_inputs={"query": "q"},
            attempt=2,
            feedback="try again",
            issues=({"description": "missing tests"},),
        )
        assert merged["query"] == "q"
        assert merged["_review_feedback"] == {
            "attempt": 2,
            "feedback": "try again",
            "issues": [{"description": "missing tests"}],
        }
        assert "instructions" not in merged

    def test_agent_runtime_appends_to_first_instruction_key(self):
        merged = approval_policy.inject_review_feedback_into_inputs(
            tool_type="agent_runtime",
            original_inputs={
                "instructions": "do work",
                "instruction": "should not be touched",
            },
            attempt=1,
            feedback="fix it",
            issues=(),
        )
        assert "REVIEW FEEDBACK (attempt 1)" in merged["instructions"]
        assert "fix it" in merged["instructions"]
        assert merged["instruction"] == "should not be touched"

    def test_agent_runtime_falls_through_blank_instruction(self):
        merged = approval_policy.inject_review_feedback_into_inputs(
            tool_type="agent_runtime",
            original_inputs={"instructions": "   ", "instruction": "real work"},
            attempt=3,
            feedback="again",
            issues=(),
        )
        assert merged["instructions"] == "   "
        assert "REVIEW FEEDBACK (attempt 3)" in merged["instruction"]

    def test_agent_runtime_without_instruction_key(self):
        merged = approval_policy.inject_review_feedback_into_inputs(
            tool_type="agent_runtime",
            original_inputs={"other": "value"},
            attempt=1,
            feedback="fb",
            issues=(),
        )
        assert merged["_review_feedback"]["feedback"] == "fb"
        assert merged["other"] == "value"

    def test_tool_type_whitespace_does_not_trigger_agent_path(self):
        merged = approval_policy.inject_review_feedback_into_inputs(
            tool_type=" agent_runtime ",
            original_inputs={"instructions": "do work"},
            attempt=1,
            feedback="fb",
            issues=(),
        )
        # Matches workflow semantics: exact ``== "agent_runtime"``.
        assert merged["instructions"] == "do work"

    def test_original_inputs_not_mutated(self):
        original = {"query": "q"}
        approval_policy.inject_review_feedback_into_inputs(
            tool_type="skill",
            original_inputs=original,
            attempt=1,
            feedback="fb",
            issues=(),
        )
        assert original == {"query": "q"}


class TestValueSeamSandboxConstraints:
    """No helper owns a workflow object, callback surface, or IO imports."""

    def test_no_workflow_or_callback_parameters(self):
        for name in (
            "gate_transition_allows_review_retry",
            "direct_output_evidence_aliases",
            "merge_direct_output_evidence",
            "merge_accepted_output_evidence",
            "inject_review_feedback_into_inputs",
        ):
            params = inspect.signature(getattr(approval_policy, name)).parameters
            assert "self" not in params
            assert "workflow" not in params
            assert "callback" not in params
            assert "callbacks" not in params

    def test_module_has_no_io_or_client_imports(self):
        source = inspect.getsource(approval_policy)
        for banned in (
            "temporalio",
            "open(",
            "socket",
            "requests",
            "httpx",
            "os.environ",
        ):
            assert banned not in source
