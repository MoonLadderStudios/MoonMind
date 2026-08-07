"""Contract tests for the remediation action capability matrix.

Ref: GitHub issue MoonLadderStudios/MoonMind#3624.

These tests keep the advertised remediation catalog capability-truthful: the
per-action readiness model, the canonical catalog, and the owning-adapter
handler map are pinned together so docs and implementation cannot drift. Any
action advertised as executable must have a ready owning execution adapter, and
any action whose owning adapter still rejects must be advertised as unavailable
with a bounded reason.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from api_service.services.remediation_actions import TemporalRemediationControlPlane
from moonmind.workflows.temporal.remediation_actions import (
    action_capability,
    remediation_action_capability_matrix,
    remediation_action_kinds,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationTargetHealthSnapshot,
)

pytestmark = pytest.mark.asyncio

_CAPABILITY_FIELDS = {
    "actionKind",
    "requestable",
    "available",
    "dryRunSupported",
    "executionBackendReady",
    "approvalBackendReady",
    "verificationBackendReady",
    "supportedTargetRuntimes",
    "supportedHostModes",
    "requiredEvidenceClasses",
    "blockedReasons",
}

# Actions whose owning execution adapter is not ready yet. Implementing a real
# owning adapter requires removing the action from this set (and the module-level
# `_EXECUTION_BACKEND_UNAVAILABLE` map) in the same change.
EXPECTED_UNAVAILABLE_ACTIONS = {
    "session.terminate",
    "session.restart_container",
    "cleanup.request_janitor",
    "cleanup.verify",
    "target.annotate",
    "target.verify",
}


def _target() -> RemediationTargetHealthSnapshot:
    return RemediationTargetHealthSnapshot(
        workflow_id="target",
        pinned_run_id="target-run",
        current_run_id="target-run",
        state="RUNNING",
        close_status=None,
        title=None,
        summary=None,
        target_run_changed=False,
        runtime="managed_temporal",
    )


def test_capability_matrix_covers_exactly_the_enabled_catalog() -> None:
    matrix = remediation_action_capability_matrix()
    covered = {item["actionKind"] for item in matrix}
    assert covered == set(remediation_action_kinds())
    for item in matrix:
        assert set(item) == _CAPABILITY_FIELDS
        assert isinstance(item["supportedTargetRuntimes"], tuple)
        assert item["supportedTargetRuntimes"]
        assert isinstance(item["supportedHostModes"], tuple)
        assert item["supportedHostModes"]
        assert isinstance(item["blockedReasons"], tuple)


def test_adapter_handler_map_matches_the_enabled_catalog() -> None:
    plane = TemporalRemediationControlPlane(client=AsyncMock())
    handler_kinds = set(plane.handlers().keys())
    assert handler_kinds == set(remediation_action_kinds())


def test_unavailable_actions_are_disabled_with_bounded_reasons() -> None:
    matrix = {item["actionKind"]: item for item in remediation_action_capability_matrix()}
    unavailable = {
        kind
        for kind, item in matrix.items()
        if not item["executionBackendReady"]
    }
    assert unavailable == EXPECTED_UNAVAILABLE_ACTIONS
    for kind in EXPECTED_UNAVAILABLE_ACTIONS:
        item = matrix[kind]
        assert item["available"] is False
        assert item["requestable"] is False
        assert item["verificationBackendReady"] is False
        assert item["blockedReasons"], f"{kind} must carry a bounded reason"


def test_available_actions_declare_ready_execution_and_verification() -> None:
    for kind in remediation_action_kinds():
        capability = action_capability(kind)
        assert capability is not None
        if capability["available"]:
            assert capability["executionBackendReady"] is True
            assert capability["verificationBackendReady"] is True
            assert capability["approvalBackendReady"] is True


async def test_unavailable_action_adapters_reject_before_success() -> None:
    """Every capability-unavailable action must fail closed in its adapter.

    This is the anti-drift guard: if an owning adapter is ever implemented, this
    assertion fails until the capability map is updated to advertise it.
    """

    plane = TemporalRemediationControlPlane(client=AsyncMock())
    handlers = plane.handlers()
    session_params = {
        "expectedRunId": "target-run",
        "agentRunId": "agent-run",
        "runtimeId": "runtime-1",
    }
    param_by_kind = {
        "session.terminate": session_params,
        "session.restart_container": session_params,
        "cleanup.request_janitor": {
            "expectedRunId": "target-run",
            "cleanupRef": "cleanup-1",
        },
        "cleanup.verify": {"expectedRunId": "target-run"},
        "target.annotate": {"expectedRunId": "target-run"},
        "target.verify": {"expectedRunId": "target-run"},
    }

    for kind in EXPECTED_UNAVAILABLE_ACTIONS:
        result = await handlers[kind](
            {
                "actionKind": kind,
                "actionId": f"action-{kind}",
                "params": param_by_kind[kind],
            },
            {},
            _target(),
        )
        assert result["status"] not in {"accepted", "applied", "queued", "no_op"}, (
            f"{kind} adapter must not report success while advertised unavailable"
        )
