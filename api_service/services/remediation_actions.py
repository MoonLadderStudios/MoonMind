"""MoonMind-owned production adapters for remediation execution controls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from copy import deepcopy
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
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

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _validate_remediation_branch_authority(
    *, source: Any, target: RemediationTargetHealthSnapshot, params: Mapping[str, Any]
) -> tuple[str, int]:
    """Validate immutable source lineage before persisting or dispatching a branch."""

    if str(getattr(source, "workflow_id", "") or "") != target.workflow_id:
        raise ValueError("source_workflow_mismatch")
    if str(getattr(source, "run_id", "") or "") != target.current_run_id:
        raise ValueError("source_run_mismatch")
    logical_step_id = str(params.get("logicalStepId") or "").strip()
    if not logical_step_id:
        raise ValueError("source_logical_step_missing")
    try:
        execution_ordinal = int(params.get("executionOrdinal"))
    except (TypeError, ValueError) as exc:
        raise ValueError("source_execution_ordinal_missing") from exc
    if execution_ordinal < 1:
        raise ValueError("source_execution_ordinal_invalid")

    checkpoint_ref = str(params.get("checkpointRef") or "").strip()
    checkpoint_digest = str(params.get("checkpointDigest") or "").strip()
    if not checkpoint_digest or not _SHA256_RE.fullmatch(checkpoint_digest):
        raise ValueError("checkpoint_digest_missing_or_invalid")
    instruction_ref = str(params.get("instructionRef") or "").strip()
    instruction_digest = str(params.get("instructionDigest") or "").strip()
    if not instruction_ref.startswith("artifact://"):
        raise ValueError("instruction_ref_invalid")
    if not _SHA256_RE.fullmatch(instruction_digest):
        raise ValueError("instruction_digest_invalid")

    candidates: list[Mapping[str, Any]] = []
    memo = getattr(source, "memo", None)
    if isinstance(memo, Mapping):
        candidates.append(memo)
    finish = getattr(source, "finish_summary_json", None)
    if isinstance(finish, Mapping):
        candidates.append(finish)

    def matches(value: object) -> bool:
        if isinstance(value, Mapping):
            ref = value.get("checkpointRef") or value.get("stepCheckpointRef")
            digest = value.get("checkpointDigest")
            step = value.get("logicalStepId") or value.get("stepId")
            ordinal = value.get("executionOrdinal") or value.get("attempt")
            if ref == checkpoint_ref:
                return (
                    digest == checkpoint_digest
                    and str(step or "") == logical_step_id
                    and int(ordinal or 0) == execution_ordinal
                )
            return any(matches(child) for child in value.values())
        if isinstance(value, list):
            return any(matches(child) for child in value)
        return False

    if not any(matches(candidate) for candidate in candidates):
        raise ValueError("checkpoint_authority_mismatch")
    return logical_step_id, execution_ordinal


def _validate_checkpoint_payload_lineage(
    payload: bytes,
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    execution_ordinal: int,
    checkpoint_ref: str,
) -> Mapping[str, Any]:
    """Require the resolved checkpoint itself to attest the exact source turn."""

    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("checkpoint_manifest_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("checkpoint_manifest_invalid")

    def candidates(value: object):
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from candidates(child)
        elif isinstance(value, list):
            for child in value:
                yield from candidates(child)

    for candidate in candidates(decoded):
        candidate_ref = candidate.get("checkpointRef") or candidate.get(
            "stepCheckpointRef"
        )
        if candidate_ref != checkpoint_ref:
            continue
        candidate_workflow = candidate.get("workflowId") or candidate.get(
            "sourceWorkflowId"
        )
        candidate_run = candidate.get("runId") or candidate.get("sourceRunId")
        candidate_step = candidate.get("logicalStepId") or candidate.get("stepId")
        candidate_step_execution = candidate.get("stepExecutionId")
        candidate_ordinal = (
            candidate.get("executionOrdinal")
            or candidate.get("sourceExecutionOrdinal")
            or candidate.get("attemptOrdinal")
            or candidate.get("attempt")
        )
        try:
            ordinal_matches = int(candidate_ordinal) == execution_ordinal
        except (TypeError, ValueError):
            ordinal_matches = False
        if (
            candidate_workflow == workflow_id
            and candidate_run == run_id
            and str(candidate_step or "") == logical_step_id
            and ordinal_matches
            and candidate_step_execution
            == f"{workflow_id}:{run_id}:{logical_step_id}:execution:{execution_ordinal}"
        ):
            return decoded
    raise ValueError("checkpoint_lineage_mismatch")


class TemporalRemediationControlPlane:
    """Route each action to the durable service that owns the mutated state."""

    def __init__(
        self,
        *,
        client: TemporalClientAdapter | None = None,
        execution_service: TemporalExecutionService | None = None,
        checkpoint_branch_service: CheckpointBranchService | None = None,
        artifact_service: Any | None = None,
    ) -> None:
        self._client = client or TemporalClientAdapter()
        self._execution_service = execution_service
        self._checkpoint_branch_service = checkpoint_branch_service
        self._artifact_service = artifact_service

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
        # Adapters report *delivery* only. Repair verification is decided by the
        # trusted post-action verification phase (issue #3622) from fresh durable
        # evidence, never from an adapter-returned verification mapping.
        return {
            "status": "accepted",
            "message": message,
            "beforeEvidenceRefs": before,
            "afterEvidenceRefs": after,
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
        if self._checkpoint_branch_service is None:
            raise RuntimeError("checkpoint branch service is unavailable")
        if self._execution_service is None:
            raise RuntimeError("execution service is unavailable")
        source = await self._execution_service.describe_execution(target.workflow_id)
        logical_step_id, execution_ordinal = _validate_remediation_branch_authority(
            source=source,
            target=target,
            params=params,
        )
        if self._artifact_service is None:
            raise RuntimeError("artifact service is unavailable")
        try:
            _artifact, checkpoint_payload = await self._artifact_service.read(
                artifact_id=checkpoint_ref.removeprefix("artifact://"),
                principal=str(source.owner_id),
            )
            (
                _instruction_artifact,
                instruction_payload,
            ) = await self._artifact_service.read(
                artifact_id=instruction_ref.removeprefix("artifact://"),
                principal=str(source.owner_id),
            )
        except Exception as exc:
            raise ValueError("branch_source_artifact_unresolvable") from exc
        expected_checkpoint_digest = str(params["checkpointDigest"])
        actual_checkpoint_digest = (
            f"sha256:{hashlib.sha256(checkpoint_payload).hexdigest()}"
        )
        if expected_checkpoint_digest != actual_checkpoint_digest:
            raise ValueError("checkpoint_digest_mismatch")
        actual_instruction_digest = (
            f"sha256:{hashlib.sha256(instruction_payload).hexdigest()}"
        )
        if instruction_digest != actual_instruction_digest:
            raise ValueError("instruction_digest_mismatch")
        _validate_checkpoint_payload_lineage(
            checkpoint_payload,
            workflow_id=target.workflow_id,
            run_id=target.current_run_id,
            logical_step_id=logical_step_id,
            execution_ordinal=execution_ordinal,
            checkpoint_ref=checkpoint_ref,
        )
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
                    "logicalStepId": logical_step_id,
                    "executionOrdinal": execution_ordinal,
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
        launch_key = build_branch_turn_launch_idempotency_key(
            workflow_id=target.workflow_id,
            branch_id=branch_id,
            branch_turn_id=turn_id,
        )
        launch_namespace = uuid5(NAMESPACE_URL, launch_key)
        runtime_workflow_id = f"mm:{uuid5(launch_namespace, 'workflow')}"
        runtime_run_id = str(uuid5(launch_namespace, "run"))
        step_execution_id = (
            f"{runtime_workflow_id}:{runtime_run_id}:{logical_step_id}:execution:1"
        )
        branch_turn = {
            "branchId": branch_id,
            "branchTurnId": turn_id,
            "sourceCheckpoint": {
                "workflowId": target.workflow_id,
                "runId": target.current_run_id,
                "logicalStepId": logical_step_id,
                "sourceExecutionOrdinal": execution_ordinal,
                "checkpointBoundary": str(params.get("checkpointBoundary") or "after_execution"),
                "checkpointRef": checkpoint_ref,
                "checkpointDigest": params.get("checkpointDigest"),
            },
            "instructionRef": instruction_ref,
            "instructionDigest": instruction_digest,
            "workspacePolicy": str(
                params.get("workspacePolicy")
                or "apply_previous_execution_diff_to_clean_baseline"
            ),
            "runtimeContextPolicy": "fresh_agent_run",
        }
        runtime = {
            "mode": "omnigent",
            "kind": "external",
            "providerProfileRef": params.get("providerProfileRef"),
            "executionProfileRef": params.get("executionProfileRef"),
            "model": params.get("model"),
            "effort": params.get("effort"),
        }
        runtime_parameters = {
            "repository": deepcopy((source.parameters or {}).get("repository") or {}),
            "targetRuntime": "omnigent",
            "workflow": {
                "runtime": runtime,
                "steps": [{
                    "id": logical_step_id,
                    "tool": {"type": "agent_runtime", "name": "omnigent"},
                    "inputs": {"checkpointBranchTurn": branch_turn},
                }],
            },
            "checkpointBranchTurn": branch_turn,
        }
        await self._execution_service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=source.owner_id,
            owner_type=(source.owner_type.value if source.owner_type else "user"),
            title=f"Checkpoint Branch {branch_id}",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=runtime_parameters,
            idempotency_key=launch_key,
            repository=None,
            integration=None,
            summary=f"Server-owned remediation branch for {target.workflow_id}.",
            _workflow_id=runtime_workflow_id,
            _run_id=runtime_run_id,
        )
        await self._checkpoint_branch_service.launch_turn(
            workflow_id=target.workflow_id,
            branch_id=branch_id,
            branch_turn_id=turn_id,
            context_bundle_ref=context_ref,
            step_execution_manifest_ref=(
                f"artifact://checkpoint-branches/{turn_id}/step-execution-manifest"
            ),
            checkpoint_ref=None,
            diagnostics_ref=f"artifact://checkpoint-branches/{turn_id}/diagnostics",
            idempotency_key=launch_key,
            created_step_execution_id=step_execution_id,
            runtime_agent_run_id=runtime_workflow_id,
        )
        return {
            "status": "accepted",
            "message": "Checkpoint Branch execution was accepted by the Omnigent coordinator.",
            "beforeEvidenceRefs": before,
            "afterEvidenceRefs": [
                f"checkpoint-branch:{graph.branch.branch_id}",
                f"checkpoint-branch-turn:{turn_id}",
                f"workflow:{runtime_workflow_id}:run:{runtime_run_id}",
                context_ref,
            ],
            "verificationRequired": True,
            "verificationHint": (
                "Re-verify the target objective from fresh evidence; a persisted "
                "branch is a remediation candidate, not a confirmed repair."
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
    if session is not None:
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        artifact_service = TemporalArtifactService(TemporalArtifactRepository(session))
    else:
        artifact_service = None
    plane = TemporalRemediationControlPlane(
        client=client,
        execution_service=execution_service,
        checkpoint_branch_service=(
            CheckpointBranchService(session) if session is not None else None
        ),
        artifact_service=artifact_service,
    )
    return MoonMindControlPlaneRemediationActionExecutor(
        plane.handlers(enforce_policy=True)
    )


__all__ = ["TemporalRemediationControlPlane", "build_remediation_action_executor"]
