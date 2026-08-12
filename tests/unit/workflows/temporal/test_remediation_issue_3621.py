"""Remediation ownership contracts for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.api.routers.executions import _serialize_remediation_link_summary
from api_service.services.remediation_actions import TemporalRemediationControlPlane
from moonmind.workflows.temporal.remediation_actions import (
    RemediationCapabilityContext,
    remediation_action_capability,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationTargetHealthSnapshot,
)


ACTION = "checkpoint_branch.create_from_remediation_context"


def _target() -> RemediationTargetHealthSnapshot:
    return RemediationTargetHealthSnapshot(
        workflow_id="target-workflow",
        pinned_run_id="target-run",
        current_run_id="target-run",
        state="failed",
        close_status="failed",
        title=None,
        summary=None,
        target_run_changed=False,
        runtime="omnigent",
    )


def _request() -> dict:
    return {
        "actionKind": ACTION,
        "actionId": "repair-1",
        "params": {
            "expectedRunId": "target-run",
            "remediationWorkflowId": "remediation-workflow",
            "remediationContextRef": "artifact://remediation/context",
            "checkpointRef": "artifact://checkpoint/source",
            "checkpointDigest": "sha256:" + "a" * 64,
            "checkpointBoundary": "after_execution",
            "logicalStepId": "implement",
            "executionOrdinal": 2,
            "instructionRef": "artifact://instruction/repair",
            "instructionDigest": "sha256:" + "b" * 64,
            "repository": "MoonLadderStudios/MoonMind",
            "baseBranch": "main",
            "baseCommit": "abc123",
            "gitWorkBranch": "remediation/repair-1",
            "providerProfileRef": "profile-1",
        },
    }


@pytest.mark.asyncio
async def test_remediation_action_dispatches_the_shared_server_owner() -> None:
    graph_service = AsyncMock()
    graph_service.create_branch_graph.return_value = SimpleNamespace(
        branch=SimpleNamespace(branch_id="remediation-repair-1")
    )
    owner = AsyncMock()
    owner.launch.return_value = SimpleNamespace(
        created_step_execution_id=(
            "checkpoint-branch-turn:remediation-repair-1-turn-1:"
            "branch-turn-remediation-repair-1-turn-1:implement:execution:1"
        )
    )
    plane = TemporalRemediationControlPlane(
        client=AsyncMock(),
        checkpoint_branch_service=graph_service,
        checkpoint_branch_turn_owner=owner,
    )

    result = await plane.handlers()[ACTION](_request(), {}, _target())

    assert result["status"] == "accepted"
    assert result["deliveryStage"] == "branch_turn_dispatched"
    assert result["branchTurnLaunched"] is True
    assert result["terminalBranchResultAvailable"] is False
    assert result["verificationRequired"] is True
    owner.launch.assert_awaited_once_with(
        workflow_id="target-workflow",
        branch_id="remediation-repair-1",
        branch_turn_id="remediation-repair-1-turn-1",
        intent={"idempotencyKey": "repair-1"},
    )
    graph_service.configure_server_launch_authority.assert_awaited_once_with(
        workflow_id="target-workflow",
        branch_id="remediation-repair-1",
        branch_turn_id="remediation-repair-1-turn-1",
        repository="MoonLadderStudios/MoonMind",
        base_branch="main",
        base_commit="abc123",
        work_branch="remediation/repair-1",
        provider_profile_ref="profile-1",
        remediation_context_ref="artifact://remediation/context",
    )
    graph_payload = graph_service.create_branch_graph.await_args.args[0]
    assert graph_payload["runtimeContextPolicy"] == "fresh_agent_run"
    assert not {
        "createdStepExecutionId",
        "runtimeAgentRunId",
        "providerSessionId",
        "diagnosticsRef",
    }.intersection(graph_payload)


@pytest.mark.asyncio
async def test_remediation_action_fails_closed_without_execution_owner() -> None:
    plane = TemporalRemediationControlPlane(
        client=AsyncMock(),
        checkpoint_branch_service=AsyncMock(),
        checkpoint_branch_turn_owner=None,
    )

    result = await plane.handlers()[ACTION](_request(), {}, _target())

    assert result["status"] == "delivery_unknown"
    assert "RuntimeError" in result["reason"]
    assert result["afterEvidenceRefs"] == []


def test_checkpoint_branch_capability_requires_owner_and_verifier() -> None:
    unavailable_owner = remediation_action_capability(
        ACTION,
        context=RemediationCapabilityContext(
            execution_backend_readiness={ACTION: False},
            verification_backend_readiness={ACTION: True},
        ),
    )
    unavailable_verifier = remediation_action_capability(
        ACTION,
        context=RemediationCapabilityContext(
            execution_backend_readiness={ACTION: True},
            verification_backend_readiness={ACTION: False},
        ),
    )
    ready = remediation_action_capability(
        ACTION,
        context=RemediationCapabilityContext(
            execution_backend_readiness={ACTION: True},
            verification_backend_readiness={ACTION: True},
        ),
    )

    assert unavailable_owner["requestable"] is False
    assert "execution_backend_unavailable" in unavailable_owner["blockedReasons"]
    assert unavailable_verifier["requestable"] is False
    assert "authoritative_verifier_unavailable" in unavailable_verifier[
        "blockedReasons"
    ]
    assert ready["requestable"] is True
    assert ready["blockedReasons"] == []


def test_checkpoint_branch_link_projection_requires_both_wired_boundaries() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    base = {
        "remediation_workflow_id": "remediation-1",
        "remediation_run_id": "remediation-run-1",
        "target_workflow_id": "target-1",
        "target_run_id": "target-run-1",
        "mode": "repair",
        "authority_mode": "admin_auto",
        "status": "acting",
        "allowed_actions": [ACTION],
        "current_target_state": "failed",
        "target_runtime": "omnigent",
        "host_mode": "on_demand",
        "evidence_degraded": False,
        "unavailable_evidence_classes": [],
        "checkpoint_branch_links": [],
        "created_at": now,
        "updated_at": now,
    }

    for owner_ready, verifier_ready, blocked_reason in (
        (False, True, "execution_backend_unavailable"),
        (True, False, "authoritative_verifier_unavailable"),
    ):
        result = _serialize_remediation_link_summary(
            SimpleNamespace(
                **base,
                checkpoint_branch_owner_ready=owner_ready,
                checkpoint_branch_verifier_ready=verifier_ready,
            )
        )
        capability = next(
            item for item in result.actionCapabilities if item.actionKind == ACTION
        )
        assert capability.requestable is False
        assert blocked_reason in capability.blockedReasons

    ready = _serialize_remediation_link_summary(
        SimpleNamespace(
            **base,
            checkpoint_branch_owner_ready=True,
            checkpoint_branch_verifier_ready=True,
        )
    )
    capability = next(
        item for item in ready.actionCapabilities if item.actionKind == ACTION
    )
    assert capability.requestable is True
