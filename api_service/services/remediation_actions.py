"""MoonMind-owned production adapters for remediation execution controls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.services.checkpoint_branch_service import CheckpointBranchService
from moonmind.omnigent.policies import (
    bind_approval_request,
    policy_authority_evidence,
    resolve_action,
    validate_approval_binding,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.remediation_tools import (
    MoonMindControlPlaneRemediationActionExecutor,
    RemediationTargetHealthSnapshot,
)
from moonmind.workflows.temporal.service import TemporalExecutionService

ActionHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any], RemediationTargetHealthSnapshot],
    Awaitable[Mapping[str, Any]],
]


class TemporalRemediationControlPlane:
    """Route each action to the durable service that owns the mutated state."""

    def __init__(
        self,
        *,
        client: TemporalClientAdapter | None = None,
        execution_service: TemporalExecutionService | None = None,
        checkpoint_branch_service: CheckpointBranchService | None = None,
    ) -> None:
        self._client = client or TemporalClientAdapter()
        self._execution_service = execution_service
        self._checkpoint_branch_service = checkpoint_branch_service

    @staticmethod
    def _parts(
        action_request: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> tuple[str, dict[str, Any], list[str]]:
        action_id = str(action_request.get("actionId") or "").strip()
        if not action_id:
            raise ValueError("actionId is required for stable idempotency")
        raw = action_request.get("params")
        if not isinstance(raw, Mapping):
            raw = action_request.get("parameters")
        params = dict(raw) if isinstance(raw, Mapping) else {}
        return action_id, params, [
            f"execution:{target.workflow_id}:run:{target.current_run_id}"
        ]

    @staticmethod
    def _accepted(
        before: list[str], *, after: list[str], message: str
    ) -> Mapping[str, Any]:
        return {
            "status": "accepted",
            "message": message,
            "beforeEvidenceRefs": before,
            "afterEvidenceRefs": after,
            "verification": {"status": "pending"},
            "verificationRequired": True,
            "verificationHint": message,
        }

    @staticmethod
    def _required(params: Mapping[str, Any], name: str) -> str:
        value = str(params.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name} is required")
        return value

    @staticmethod
    def _ensure_expected_run(
        params: Mapping[str, Any], target: RemediationTargetHealthSnapshot
    ) -> None:
        expected = str(params.get("expectedRunId") or target.pinned_run_id).strip()
        if expected != target.current_run_id:
            raise ValueError("expectedRunId does not match the current target run")

    async def execution_control(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_id, params, before = self._parts(action_request, target)
        self._ensure_expected_run(params, target)
        kind = str(action_request["actionKind"])
        if kind == "execution.pause":
            await self._client.update_workflow(
                target.workflow_id, "Pause", {"idempotency_key": action_id}
            )
        elif kind == "execution.resume":
            await self._client.update_workflow(
                target.workflow_id, "Resume", {"idempotency_key": action_id}
            )
        elif kind == "execution.cancel":
            await self._client.cancel_workflow(target.workflow_id)
        else:
            await self._client.terminate_workflow(
                target.workflow_id,
                reason=str(params.get("reason") or "authorized remediation"),
            )
        return self._accepted(
            before,
            after=[f"execution:{target.workflow_id}:action:{action_id}"],
            message=f"{kind} was delivered to the execution control plane.",
        )

    async def rerun(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_id, params, before = self._parts(action_request, target)
        self._ensure_expected_run(params, target)
        if self._execution_service is None:
            raise RuntimeError("execution service is unavailable")
        if action_request["actionKind"] == "execution.start_fresh_rerun":
            response = await self._execution_service.create_fresh_rerun_execution(
                workflow_id=target.workflow_id,
                idempotency_key=action_id,
            )
        else:
            response = await self._execution_service.update_execution(
                workflow_id=target.workflow_id,
                update_name="RequestRerun",
                idempotency_key=action_id,
            )
        accepted = response.get("accepted") is not False
        status = "accepted" if accepted else "no_op"
        resulting_workflow_id = str(
            response.get("workflow_id") or target.workflow_id
        ).strip()
        return {
            "status": status,
            "message": str(response.get("message") or "Rerun request processed."),
            "beforeEvidenceRefs": before,
            "afterEvidenceRefs": [
                f"execution:{resulting_workflow_id}:rerun-request:{action_id}"
            ],
            "verification": {"status": "pending" if accepted else "verified"},
            "verificationRequired": accepted,
            "verificationHint": (
                "Verify the resulting workflow reaches an authoritative terminal state."
                if accepted else None
            ),
        }

    async def checkpoint_branch(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_id, params, before = self._parts(action_request, target)
        self._ensure_expected_run(params, target)
        context_ref = self._required(params, "remediationContextRef")
        checkpoint_ref = self._required(params, "checkpointRef")
        remediation_workflow_id = self._required(params, "remediationWorkflowId")
        instruction_ref = self._required(params, "instructionRef")
        instruction_digest = self._required(params, "instructionDigest")
        if not instruction_digest.startswith("sha256:"):
            raise ValueError("instructionDigest must be a sha256 digest")
        if self._checkpoint_branch_service is None:
            raise RuntimeError("checkpoint branch service is unavailable")
        branch_id = f"remediation-{action_id}"
        turn_id = f"{branch_id}-turn-1"
        graph = await self._checkpoint_branch_service.create_branch_graph(
            {
                "branchId": branch_id,
                "branchTurnId": turn_id,
                "source": {
                    "workflowId": target.workflow_id,
                    "rootWorkflowId": target.workflow_id,
                    "runId": target.current_run_id,
                    "checkpointBoundary": str(
                        params.get("checkpointBoundary") or "after_execution"
                    ),
                    "checkpointRef": checkpoint_ref,
                    "checkpointDigest": params.get("checkpointDigest"),
                },
                "label": str(
                    params.get("label") or "Remediation checkpoint branch"
                ),
                "branchKind": "root",
                "workspacePolicy": str(
                    params.get("workspacePolicy")
                    or "apply_previous_execution_diff_to_clean_baseline"
                ),
                "runtimeContextPolicy": "fresh_agent_run",
                "createdBy": f"remediation:{remediation_workflow_id}",
                "instructionRef": instruction_ref,
                "instructionDigest": instruction_digest,
                "contextBundleRef": context_ref,
                "idempotencyKey": action_id,
            }
        )
        # Graph creation is a distinct capability from a verified repair. The
        # durable owner has persisted the branch graph and its first turn, but
        # no branch turn has launched, reached a terminal result, or been
        # verified as a repair. Report acceptance with verification pending so
        # the catalog never describes branch-graph persistence as verified
        # repair (coordinates with the branch execution/verification owners).
        return {
            "status": "accepted",
            "message": (
                "Checkpoint Branch graph was created by its durable owner; the "
                "branch turn has not yet launched, reached a terminal result, "
                "or been verified."
            ),
            "beforeEvidenceRefs": before,
            "afterEvidenceRefs": [
                f"checkpoint-branch:{graph.branch.branch_id}",
                f"checkpoint-branch-turn:{turn_id}",
                context_ref,
            ],
            "verification": {"status": "pending"},
            "verificationRequired": True,
            "verificationHint": (
                "Verify the checkpoint branch turn launches, reaches an "
                "authoritative terminal result, and passes post-action "
                "verification before treating the repair as complete."
            ),
        }

    async def session_control(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_id, params, before = self._parts(action_request, target)
        agent_run_id = self._required(params, "agentRunId")
        runtime_id = self._required(params, "runtimeId")
        kind = str(action_request["actionKind"])
        update = {
            "session.interrupt_turn": "InterruptTurn",
            "session.clear": "ClearSession",
            "session.cancel": "CancelSession",
            "session.terminate": None,
            "session.restart_container": None,
        }[kind]
        if update is None:
            raise ValueError(f"{kind} is unsupported by the owning session control plane")
        await self._client.update_workflow(
            f"{agent_run_id}:session:{runtime_id}",
            update,
            {"requestId": action_id, "reason": params.get("reason")},
        )
        return self._accepted(
            before,
            after=[f"managed-session:{agent_run_id}:{runtime_id}:action:{action_id}"],
            message=f"{kind} was delivered to the managed-session control plane.",
        )

    async def omnigent_reconcile(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_id, params, before = self._parts(action_request, target)
        kind = str(action_request["actionKind"])
        profile_id = self._required(params, "providerProfileId")
        host_lease_ref = str(params.get("hostLeaseRef") or "").strip() or None
        if (
            kind.startswith("host.")
            or kind == "host_lease.reconcile_stale"
            or kind == "provider_profile.evict_stale_lease"
        ):
            if host_lease_ref is None:
                raise ValueError("hostLeaseRef is required")
        result = await self._client.start_workflow(
            workflow_type="MoonMind.OmnigentOAuthHostJanitor",
            workflow_id=f"remediation-omnigent:{action_id}",
            input_args={
                "profile_id": profile_id,
                "force": True,
                "actionKind": kind,
                "hostLeaseRef": host_lease_ref,
                "expectedHostState": params.get("expectedHostState"),
                "requestId": action_id,
            },
        )
        return self._accepted(
            before,
            after=[f"workflow:{result.workflow_id}:run:{result.run_id}"],
            message=f"{kind} was queued on the Omnigent host/lease control plane.",
        )

    async def managed_runtime_reconcile(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_id, params, before = self._parts(action_request, target)
        kind = str(action_request["actionKind"])
        container_ref = self._required(params, "containerRef")
        result = await self._client.start_workflow(
            workflow_type="MoonMind.ManagedSessionReconcile",
            workflow_id=f"remediation-managed-runtime:{action_id}",
            input_args={
                "actionKind": kind,
                "containerRef": container_ref,
                "expectedState": params.get("expectedState"),
                "targetWorkflowId": target.workflow_id,
                "requestId": action_id,
            },
        )
        return self._accepted(
            before,
            after=[f"workflow:{result.workflow_id}:run:{result.run_id}"],
            message=f"{kind} was queued on the managed-runtime control plane.",
        )

    async def janitor(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        raise ValueError(
            "targeted cleanup is unsupported until cleanupRef can be resolved "
            "by its owning control plane"
        )

    async def evidence_only(
        self,
        action_request: Mapping[str, Any],
        _guard_result: Mapping[str, Any],
        target: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        raise ValueError(
            "verification actions require an owning evidence adapter and cannot "
            "synthesize success"
        )

    @staticmethod
    def _typed(handler: ActionHandler) -> ActionHandler:
        async def execute(
            action_request: Mapping[str, Any],
            guard_result: Mapping[str, Any],
            target: RemediationTargetHealthSnapshot,
        ) -> Mapping[str, Any]:
            before = [f"execution:{target.workflow_id}:run:{target.current_run_id}"]
            try:
                return await handler(action_request, guard_result, target)
            except ValueError as exc:
                return {
                    "status": "precondition_failed",
                    "reason": str(exc),
                    "beforeEvidenceRefs": before,
                    "afterEvidenceRefs": [],
                }
            except asyncio.TimeoutError:
                return {
                    "status": "timed_out",
                    "reason": "owning control plane timed out",
                    "beforeEvidenceRefs": before,
                    "afterEvidenceRefs": [],
                }
            except Exception as exc:
                return {
                    "status": "delivery_unknown",
                    "reason": (
                        "owning control plane did not return authoritative "
                        f"terminal evidence ({type(exc).__name__})"
                    ),
                    "beforeEvidenceRefs": before,
                    "afterEvidenceRefs": [],
                }

        return execute

    @staticmethod
    def _policy_bound(handler: ActionHandler) -> ActionHandler:
        """Fail closed on the exact run-bound policy before any side effect."""

        async def execute(
            action_request: Mapping[str, Any],
            guard_result: Mapping[str, Any],
            target: RemediationTargetHealthSnapshot,
        ) -> Mapping[str, Any]:
            snapshot = action_request.get("policySnapshot")
            if not isinstance(snapshot, Mapping):
                # A verified non-Omnigent target carries no Omnigent policy
                # authority, so Omnigent policy binding does not apply and its
                # owning adapter enforces its own runtime. The runtime marker is
                # stamped by execute_action from the persisted target execution
                # record, never from caller tool arguments. Any other case
                # (Omnigent target, or an unverifiable runtime) fails closed.
                runtime = (
                    str(action_request.get("targetRuntime") or "").strip().casefold()
                )
                if runtime and runtime != "omnigent":
                    return await handler(action_request, guard_result, target)
                return {
                    "status": "denied",
                    "reason": "omnigent_policy_snapshot_required",
                    "beforeEvidenceRefs": [],
                    "afterEvidenceRefs": [],
                }

            try:
                authority = policy_authority_evidence(snapshot)
            except ValueError as exc:
                return {
                    "status": "denied",
                    "reason": "omnigent_policy_snapshot_invalid",
                    "detail": str(exc),
                    "beforeEvidenceRefs": [],
                    "afterEvidenceRefs": [],
                }

            action = str(action_request.get("actionKind") or "").strip()
            decision = resolve_action(snapshot, action)
            if decision["decision"] == "deny":
                return {
                    "status": "denied",
                    "reason": "omnigent_policy_action_denied",
                    "policyDecision": decision,
                    "policyAuthority": authority,
                    "beforeEvidenceRefs": [],
                    "afterEvidenceRefs": [],
                }

            # The run id is the optimistic target identity used by the mutation
            # guard. A newly requested approval is durably returned with the
            # action result; an approved retry must present that exact binding.
            current_target_state = target.current_run_id
            if decision["decision"] == "approval_required":
                binding = action_request.get("approvalBinding")
                if not isinstance(binding, Mapping):
                    try:
                        binding = bind_approval_request(
                            snapshot,
                            action,
                            target_expected_state=current_target_state,
                        )
                    except ValueError as exc:
                        return {
                            "status": "denied",
                            "reason": "omnigent_approval_binding_invalid",
                            "detail": str(exc),
                            "policyAuthority": authority,
                            "beforeEvidenceRefs": [],
                            "afterEvidenceRefs": [],
                        }
                    return {
                        "status": "approval_required",
                        "reason": "omnigent_policy_approval_required",
                        "approvalBinding": binding,
                        "policyDecision": decision,
                        "policyAuthority": authority,
                        "beforeEvidenceRefs": [],
                        "afterEvidenceRefs": [],
                    }
                if not str(action_request.get("approvalRef") or "").strip():
                    return {
                        "status": "denied",
                        "reason": "omnigent_approval_reference_required",
                        "policyAuthority": authority,
                        "beforeEvidenceRefs": [],
                        "afterEvidenceRefs": [],
                    }
                try:
                    validate_approval_binding(
                        binding,
                        snapshot,
                        target_current_state=current_target_state,
                    )
                except ValueError as exc:
                    return {
                        "status": "denied",
                        "reason": "omnigent_approval_binding_stale",
                        "detail": str(exc),
                        "policyAuthority": authority,
                        "beforeEvidenceRefs": [],
                        "afterEvidenceRefs": [],
                    }

            result = dict(await handler(action_request, guard_result, target))
            result["policyAuthority"] = authority
            if decision["decision"] == "approval_required":
                result["approvalBinding"] = dict(action_request["approvalBinding"])
            return result

        return execute

    def handlers(self, *, enforce_policy: bool = False) -> dict[str, ActionHandler]:
        handlers = {
            "execution.pause": self.execution_control,
            "execution.resume": self.execution_control,
            "execution.cancel": self.execution_control,
            "execution.force_terminate": self.execution_control,
            "execution.request_rerun_same_workflow": self.rerun,
            "execution.start_fresh_rerun": self.rerun,
            "checkpoint_branch.create_from_remediation_context": self.checkpoint_branch,
            "session.interrupt_turn": self.session_control,
            "session.clear": self.session_control,
            "session.cancel": self.session_control,
            "session.terminate": self.session_control,
            "session.restart_container": self.session_control,
            "provider_profile.evict_stale_lease": self.omnigent_reconcile,
            "host.drain": self.omnigent_reconcile,
            "host.stop": self.omnigent_reconcile,
            "host.restart": self.omnigent_reconcile,
            "host.remove": self.omnigent_reconcile,
            "host_lease.reconcile_stale": self.omnigent_reconcile,
            "workload.restart_helper_container": self.managed_runtime_reconcile,
            "workload.reap_orphan_container": self.managed_runtime_reconcile,
            "cleanup.request_janitor": self.janitor,
            "cleanup.verify": self.evidence_only,
            "target.annotate": self.evidence_only,
            "target.verify": self.evidence_only,
        }
        typed = {kind: self._typed(handler) for kind, handler in handlers.items()}
        if not enforce_policy:
            return typed
        return {kind: self._policy_bound(handler) for kind, handler in typed.items()}


def build_remediation_action_executor(
    *, session: AsyncSession | None = None
) -> MoonMindControlPlaneRemediationActionExecutor:
    """Build explicit owning adapters for every policy-visible action."""

    client = TemporalClientAdapter()
    execution_service = (
        TemporalExecutionService(session, client_adapter=client)
        if session is not None
        else None
    )
    plane = TemporalRemediationControlPlane(
        client=client,
        execution_service=execution_service,
        checkpoint_branch_service=(
            CheckpointBranchService(session) if session is not None else None
        ),
    )
    return MoonMindControlPlaneRemediationActionExecutor(
        plane.handlers(enforce_policy=True)
    )


__all__ = ["TemporalRemediationControlPlane", "build_remediation_action_executor"]
