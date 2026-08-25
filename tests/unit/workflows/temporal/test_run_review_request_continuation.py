"""`MoonMind.UserWorkflow` handling of the review-request continuation.

The resolver child normalizes and republishes the typed continuation. A
``request_review`` continuation must survive that boundary with its schema
version and provider intact, must be rejected when no merge-automation gate owns
the run, and must not change how the previously recorded ``reenter_gate``
contract replays.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    GATED_CONTINUATION_GATE_REGISTRY,
    MERGE_AUTOMATION_CONTINUATION_DISPOSITIONS,
    MoonMindRunWorkflow,
)


def _request_review_outputs(**overrides: Any) -> dict[str, Any]:
    continuation = {
        "schemaVersion": "gated-continuation/v2",
        "gateType": "merge_automation",
        "action": "request_review",
        "provider": "codex",
        "reason": "fresh_review_required_after_remediation",
        "executionRef": "step-execution-id",
        "headSha": "abc1234",
        "progressSignature": "abc1234||",
    }
    continuation.update(overrides)
    return {"gatedContinuation": continuation}


def test_request_review_is_a_registered_merge_automation_action() -> None:
    assert "request_review" in MERGE_AUTOMATION_CONTINUATION_DISPOSITIONS
    assert "request_review" in GATED_CONTINUATION_GATE_REGISTRY["merge_automation"]


def test_request_review_continuation_preserves_schema_and_provider() -> None:
    workflow = MoonMindRunWorkflow()

    continuation = workflow._normalize_gated_continuation_request(
        _request_review_outputs(),
        node_id="node-1",
    )

    assert continuation["schemaVersion"] == "gated-continuation/v2"
    assert continuation["action"] == "request_review"
    assert continuation["provider"] == "codex"
    assert continuation["headSha"] == "abc1234"
    assert continuation["progressSignature"] == "abc1234||"
    assert "validationError" not in continuation


def test_reenter_gate_continuation_still_normalizes_to_v1() -> None:
    workflow = MoonMindRunWorkflow()

    continuation = workflow._normalize_gated_continuation_request(
        {
            "gatedContinuation": {
                "schemaVersion": "gated-continuation/v1",
                "gateType": "merge_automation",
                "action": "reenter_gate",
                "reason": "codex_review_grace_wait",
                "notBefore": "2026-08-24T22:30:00Z",
                "executionRef": "step-execution-id",
                "headSha": "abc1234",
            }
        },
        node_id="node-1",
    )

    assert continuation["schemaVersion"] == "gated-continuation/v1"
    assert continuation["action"] == "reenter_gate"
    assert continuation["notBefore"] == "2026-08-24T22:30:00Z"


def test_unknown_schema_version_falls_back_to_v1() -> None:
    """An unrecognized version never widens what MoonMind will validate."""

    workflow = MoonMindRunWorkflow()

    continuation = workflow._normalize_gated_continuation_request(
        _request_review_outputs(schemaVersion="gated-continuation/v99"),
        node_id="node-1",
    )

    assert continuation["schemaVersion"] == "gated-continuation/v1"


def test_unsupported_action_is_marked_invalid() -> None:
    workflow = MoonMindRunWorkflow()

    continuation = workflow._normalize_gated_continuation_request(
        _request_review_outputs(action="post_arbitrary_comment"),
        node_id="node-1",
    )

    assert continuation["validationError"] == "unsupported_gate_action"


def test_ungated_request_review_run_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_info = type("WorkflowInfo", (), {"parent": None})
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)

    workflow = MoonMindRunWorkflow()
    workflow._gated_continuation_request = None
    workflow._merge_automation_disposition = "request_review"

    message = workflow._continuation_disposition_failure_message(
        {
            "requestType": "task",
            "workflow": {
                "tool": {"type": "skill", "name": "pr-resolver"},
                "publish": {"mode": "none"},
            },
        }
    )

    assert message is not None
    assert "request_review" in message
    assert "MoonMind.MergeAutomation" in message


def test_gated_resolver_children_may_request_a_review() -> None:
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "mm:resolver",
            "idempotencyKey": "mm:resolver:node-1:execution:1",
            "terminalContinuationAuthority": {
                "schemaVersion": "terminal-continuation-authority/v1",
                "gateType": "merge_automation",
                "ownerWorkflowId": "merge-automation:1",
                "ownerRunId": "merge-run-1",
                "ownerWorkflowType": "MoonMind.MergeAutomation",
                "allowedActions": ["reenter_gate", "request_review"],
                "source": "validated_temporal_parent",
            },
        }
    )

    assert request.terminal_continuation_authority.allows(
        gate_type="merge_automation",
        action="request_review",
    )
    instruction = MoonMindRunWorkflow._terminal_continuation_authority_instruction(
        request
    )
    assert "request_review" in instruction
