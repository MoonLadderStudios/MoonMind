"""Tests for PlanPolicy approval_policy parsing and serialization."""

from __future__ import annotations

from moonmind.workflows.skills.tool_plan_contracts import (
    DEFAULT_SKIP_TOOL_TYPES,
    PlanPolicy,
    ApprovalPolicyPolicy,
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

class TestPlanPolicyApprovalPolicy:
    def test_default_plan_policy_has_none_approval_policy(self):
        policy = PlanPolicy()
        assert policy.approval_policy is None

    def test_plan_policy_with_enabled_approval_policy(self):
        rg = ApprovalPolicyPolicy(enabled=True, max_review_attempts=5)
        policy = PlanPolicy(approval_policy=rg)
        assert policy.approval_policy is not None
        assert policy.approval_policy.enabled is True
        assert policy.approval_policy.max_review_attempts == 5

    def test_to_payload_omits_approval_policy_when_none(self):
        policy = PlanPolicy()
        payload = policy.to_payload()
        assert "approval_policy" not in payload

    def test_to_payload_omits_approval_policy_when_disabled(self):
        rg = ApprovalPolicyPolicy(enabled=False)
        policy = PlanPolicy(approval_policy=rg)
        payload = policy.to_payload()
        assert "approval_policy" not in payload

    def test_to_payload_includes_approval_policy_when_enabled(self):
        rg = ApprovalPolicyPolicy(enabled=True)
        policy = PlanPolicy(approval_policy=rg)
        payload = policy.to_payload()
        assert "approval_policy" in payload
        assert payload["approval_policy"]["enabled"] is True

class TestParsePlanDefinitionApprovalPolicy:
    def test_parse_without_approval_policy_returns_none(self):
        """When plan omits approval_policy, PlanPolicy.approval_policy is None."""
        plan_payload = {**_MINIMAL_PLAN, "policy": {"failure_mode": "FAIL_FAST"}}
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.approval_policy is None

    def test_parse_with_approval_policy_enabled(self):
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "approval_policy": {
                    "enabled": True,
                    "max_review_attempts": 3,
                    "reviewer_model": "gpt-4",
                    "review_timeout_seconds": 60,
                    "skip_tool_types": ["agent_runtime"],
                },
            },
        }
        plan = parse_plan_definition(plan_payload)
        rg = plan.policy.approval_policy
        assert rg.enabled is True
        assert rg.max_review_attempts == 3
        assert rg.reviewer_model == "gpt-4"
        assert rg.review_timeout_seconds == 60
        assert rg.skip_tool_types == ("agent_runtime",)

    def test_parse_max_review_attempts_zero_preserved(self):
        """Explicit max_review_attempts: 0 must not be overwritten to 2."""
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "approval_policy": {
                    "enabled": True,
                    "max_review_attempts": 0,
                },
            },
        }
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.approval_policy.max_review_attempts == 0

    def test_parse_with_approval_policy_disabled_explicitly(self):
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "CONTINUE",
                "approval_policy": {"enabled": False},
            },
        }
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.approval_policy.enabled is False

    def test_parse_approval_policy_defaults_on_empty_object(self):
        """Explicit empty object means 'specified but with defaults'."""
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "approval_policy": {},
            },
        }
        plan = parse_plan_definition(plan_payload)
        rg = plan.policy.approval_policy
        assert rg is not None          # Present, not None
        assert rg.enabled is False
        assert rg.max_review_attempts == 2
        assert rg.reviewer_model == "default"
        assert rg.skip_tool_types == DEFAULT_SKIP_TOOL_TYPES

    def test_parse_invalid_skip_tool_types_falls_back_to_defaults(self):
        """Invalid skip_tool_types (non-list) falls back to DEFAULT_SKIP_TOOL_TYPES."""
        plan_payload = {
            **_MINIMAL_PLAN,
            "policy": {
                "failure_mode": "FAIL_FAST",
                "approval_policy": {"enabled": True, "skip_tool_types": "not-a-list"},
            },
        }
        plan = parse_plan_definition(plan_payload)
        assert plan.policy.approval_policy.skip_tool_types == DEFAULT_SKIP_TOOL_TYPES
