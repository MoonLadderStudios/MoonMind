"""Tests for PlanPolicy review_gate parsing and serialization."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.tool_plan_contracts import (
    PlanPolicy,
    ReviewGatePolicy,
    parse_plan_definition,
)

# Minimal valid plan payload for reuse
_MINIMAL_PLAN = {
    "plan_version": "1.0",
    "metadata": {
        "title": "Test plan",
        "created_at": "2026-03-18T00:00:00Z",
        "registry_snapshot": {
            "digest": "reg:sha256:abc",
            "artifact_ref": "art:sha256:def",
        },
    },
    "nodes": [
        {
            "id": "n1",
            "tool": {"type": "skill", "name": "repo.run_tests", "version": "1.0"},
            "inputs": {},
        }
    ],
    "edges": [],
}


class TestPlanPolicyReviewGate:
    def test_default_plan_policy_has_disabled_review_gate(self):
        policy = PlanPolicy()
        assert policy.review_gate.enabled is False

    def test_plan_policy_with_enabled_review_gate(self):
        rg = ReviewGatePolicy(enabled=True, max_review_attempts=5)
        policy = PlanPolicy(review_gate=rg)
        assert policy.review_gate.enabled is True
        assert policy.review_gate.max_review_attempts == 5

    def test_to_payload_omits_review_gate_when_disabled(self):
        policy = PlanPolicy()
        payload = policy.to_payload()
        assert "review_gate" not in payload

    def test_to_payload_includes_review_gate_when_enabled(self):
        rg = ReviewGatePolicy(enabled=True)
        policy = PlanPolicy(review_gate=rg)
        payload = policy.to_payload()
        assert "review_gate" in payload
        assert payload["review_gate"]["enabled"] is True


class TestParsePlanDefinitionReviewGate:
    def test_parse_without_review_gate(self):
        plan_payload = {**_MINIMAL_PLAN, "policy": {"failure_mode": "FAIL_FAST"}}
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.review_gate.enabled is False

    def test_parse_with_review_gate_enabled(self):
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "review_gate": {
                    "enabled": True,
                    "max_review_attempts": 3,
                    "reviewer_model": "gpt-4",
                    "review_timeout_seconds": 60,
                    "skip_tool_types": ["agent_runtime"],
                },
            },
        }
        plan = parse_plan_definition(plan_payload)
        rg = plan.policy.review_gate
        assert rg.enabled is True
        assert rg.max_review_attempts == 3
        assert rg.reviewer_model == "gpt-4"
        assert rg.review_timeout_seconds == 60
        assert rg.skip_tool_types == ("agent_runtime",)

    def test_parse_with_review_gate_disabled_explicitly(self):
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "CONTINUE",
                "review_gate": {"enabled": False},
            },
        }
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.review_gate.enabled is False

    def test_parse_review_gate_defaults_on_empty_object(self):
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "review_gate": {},
            },
        }
        plan = parse_plan_definition(plan_payload)
        rg = plan.policy.review_gate
        assert rg.enabled is False
        assert rg.max_review_attempts == 2
        assert rg.reviewer_model == "default"

    def test_parse_invalid_skip_tool_types_defaults_to_empty(self):
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "review_gate": {"enabled": True, "skip_tool_types": "not-a-list"},
            },
        }
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.review_gate.skip_tool_types == ()
