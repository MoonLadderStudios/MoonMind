"""MoonMind-owned production adapters for remediation execution controls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.remediation_actions import (
    remediation_action_kinds,
)
from moonmind.workflows.temporal.remediation_tools import (
    MoonMindControlPlaneRemediationActionExecutor,
    RemediationTargetHealthSnapshot,
)


class TemporalRemediationControlPlane:
    """Dispatch controls through Temporal, the owner of execution/session state."""

    def __init__(self, client: TemporalClientAdapter | None = None) -> None:
        self._client = client or TemporalClientAdapter()

    async def execute(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        kind = str(action_request.get("actionKind") or "")
        parameters = action_request.get("params")
        if not isinstance(parameters, Mapping):
            parameters = action_request.get("parameters")
        params = dict(parameters) if isinstance(parameters, Mapping) else {}
        before = [f"execution:{target.workflow_id}:run:{target.current_run_id}"]

        if kind == "execution.pause":
            await self._client.signal_workflow(target.workflow_id, "Pause", params)
        elif kind == "execution.resume":
            await self._client.signal_workflow(target.workflow_id, "Resume", params)
        elif kind == "execution.cancel":
            await self._client.cancel_workflow(target.workflow_id)
        elif kind == "execution.force_terminate":
            await self._client.terminate_workflow(
                target.workflow_id,
                reason=str(params.get("reason") or "authorized remediation"),
            )
        elif kind.startswith("session."):
            agent_run_id = str(params.get("agentRunId") or "").strip()
            runtime_id = str(params.get("runtimeId") or "").strip()
            if not agent_run_id or not runtime_id:
                return {
                    "status": "precondition_failed",
                    "reason": "agentRunId and runtimeId are required",
                    "beforeEvidenceRefs": before,
                    "afterEvidenceRefs": [],
                }
            update = {
                "session.interrupt_turn": "InterruptTurn",
                "session.clear": "ClearSession",
                "session.cancel": "CancelSession",
                "session.terminate": "CancelSession",
                "session.restart_container": "ClearSession",
            }[kind]
            await self._client.update_workflow(
                f"{agent_run_id}:session:{runtime_id}",
                update,
                {
                    "requestId": str(action_request.get("actionId") or ""),
                    **({"reason": params["reason"]} if params.get("reason") else {}),
                },
            )
        elif kind in {"target.annotate", "target.verify", "cleanup.verify"}:
            # The surrounding service publishes the durable annotation and
            # verification artifacts. No second mutation is required here.
            return {
                "status": "applied",
                "beforeEvidenceRefs": before,
                "afterEvidenceRefs": before,
                "verification": {"status": "verified", "targetRunChanged": False},
            }
        else:
            return {
                "status": "precondition_failed",
                "reason": "required owning control-plane identifiers are unavailable",
                "beforeEvidenceRefs": before,
                "afterEvidenceRefs": [],
            }
        return {
            "status": "accepted",
            "beforeEvidenceRefs": before,
            "afterEvidenceRefs": [],
            "verification": {"status": "pending"},
        }


def build_remediation_action_executor() -> MoonMindControlPlaneRemediationActionExecutor:
    """Build an explicit adapter entry for every policy-visible action kind."""

    plane = TemporalRemediationControlPlane()
    return MoonMindControlPlaneRemediationActionExecutor(
        {action_kind: plane.execute for action_kind in remediation_action_kinds()}
    )


__all__ = ["TemporalRemediationControlPlane", "build_remediation_action_executor"]
