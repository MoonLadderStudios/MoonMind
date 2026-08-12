"""Durable owner for one server-authored Checkpoint Branch turn."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select
    from api_service.db.base import async_session_maker
    from api_service.db.models import (
        OmnigentBridgeSessionEvent,
        TemporalArtifactRetentionClass,
    )
    from api_service.services.checkpoint_branch_service import CheckpointBranchService
    from api_service.services.checkpoint_branch_turn_execution import (
        get_checkpoint_branch_artifact_service,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
    from moonmind.workflows.temporal.activity_catalog import (
        ARTIFACTS_TASK_QUEUE,
        SANDBOX_TASK_QUEUE,
    )

WORKFLOW_NAME = "MoonMind.CheckpointBranchTurn"
SCHEMA_VERSION = "checkpoint-branch-turn-execution/v1"

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def checkpoint_branch_turn_terminal_disposition(
    *,
    result: AgentRunResult,
    checkpoint_ref: str | None,
    authority_chain: Mapping[str, Any] | None,
) -> str:
    """Classify terminal delivery without confusing it with repair success."""

    chain = _mapping(authority_chain)
    terminal = _mapping(chain.get("terminal"))
    reason_codes = {
        str(item.get("code") or "").strip().lower()
        for item in chain.get("reasons", [])
        if isinstance(item, Mapping)
    }
    provider_code = str(result.provider_error_code or "").strip().lower()
    codes = {provider_code, *reason_codes}
    if result.failure_class == "canceled":
        return "canceled"
    if any(
        "delivery_unknown" in code or "ambiguous_terminal" in code
        for code in codes
    ):
        return "delivery_unknown"
    if any("resume_unavailable" in code for code in codes):
        return "resume_unavailable"
    if chain and (
        terminal.get("cleanupCompleted") is False
        or terminal.get("leaseReleased") is False
        or terminal.get("janitorRequired") is True
    ):
        return "cleanup_failure"
    if result.failure_class or result.provider_error_code:
        return "provider_failure"
    if not checkpoint_ref:
        return "terminal_checkpoint_missing"
    return "verification_pending"


async def _load_authority_chain(bridge_session_id: str | None) -> dict[str, Any]:
    if not bridge_session_id:
        return {}
    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(OmnigentBridgeSessionEvent)
                .where(
                    OmnigentBridgeSessionEvent.bridge_session_id
                    == bridge_session_id,
                    OmnigentBridgeSessionEvent.event_type
                    == "lifecycle.authority_chain",
                )
                .order_by(OmnigentBridgeSessionEvent.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if event is None:
        return {}
    metadata = _mapping(event.metadata_)
    inner = _mapping(metadata.get("metadata"))
    return _mapping(inner.get("authorityChain"))


async def _write_result_artifact(
    *,
    principal: str,
    payload: Mapping[str, Any],
    kind: str,
    branch_turn_id: str,
    source_namespace: str,
    source_workflow_id: str,
    source_run_id: str,
) -> str:
    data = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":")
    ).encode()
    async with async_session_maker() as session:
        artifacts = get_checkpoint_branch_artifact_service(session)
        artifact, _upload = await artifacts.create(
            principal=principal,
            content_type="application/json",
            size_bytes=len(data),
            sha256=_sha256(data).removeprefix("sha256:"),
            retention_class=TemporalArtifactRetentionClass.LONG,
            link={
                "namespace": source_namespace,
                "workflow_id": source_workflow_id,
                "run_id": source_run_id,
                "link_type": "checkpoint_branch.turn",
                "label": f"Checkpoint Branch turn {branch_turn_id}",
            },
            metadata_json={
                "kind": kind,
                "branchTurnId": branch_turn_id,
                "issue": "MoonLadderStudios/MoonMind#3621",
            },
        )
        await artifacts.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=data,
            content_type="application/json",
        )
        return f"artifact://{artifact.artifact_id}"


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def mark_checkpoint_branch_turn_running(payload: Mapping[str, Any]) -> None:
    """Persist dispatch state immediately before the AgentRun child starts."""

    async with async_session_maker() as session:
        await CheckpointBranchService(session).mark_turn_running(
            workflow_id=str(payload["workflowId"]),
            branch_id=str(payload["branchId"]),
            branch_turn_id=str(payload["branchTurnId"]),
            runtime_agent_run_id=str(payload["agentRunWorkflowId"]),
        )
        await session.commit()


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def persist_checkpoint_branch_turn_terminal(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist terminal delivery evidence and hand off to verification."""

    workflow_id = str(payload["workflowId"])
    branch_id = str(payload["branchId"])
    branch_turn_id = str(payload["branchTurnId"])
    principal = str(payload["principal"])
    raw_result = _mapping(payload.get("agentResult"))
    result = AgentRunResult.model_validate(raw_result)
    checkpoint = _mapping(payload.get("checkpoint"))
    checkpoint_ref = str(checkpoint.get("checkpointRef") or "").strip() or None
    if checkpoint_ref and not checkpoint_ref.startswith("artifact://"):
        checkpoint_ref = f"artifact://{checkpoint_ref}"
    checkpoint_digest: str | None = None
    if checkpoint_ref:
        try:
            async with async_session_maker() as checkpoint_session:
                _artifact, checkpoint_bytes = await get_checkpoint_branch_artifact_service(
                    checkpoint_session
                ).read(
                    artifact_id=checkpoint_ref.removeprefix("artifact://"),
                    principal="service:checkpoint-branch-turn",
                    allow_restricted_raw=True,
                )
            checkpoint_digest = _sha256(checkpoint_bytes)
        except Exception:
            checkpoint_ref = None
    capture = _mapping(result.metadata.get("omnigentCheckpointCapture"))
    terminal_ref = str(capture.get("terminalRef") or "").strip() or None
    provider_session_id = (
        str(capture.get("omnigentSessionId") or "").strip() or None
    )
    authority_chain = _mapping(result.metadata.get("authorityChain"))
    if not authority_chain:
        authority_chain = await _load_authority_chain(
            str(capture.get("bridgeSessionId") or "").strip() or None
        )
    disposition = checkpoint_branch_turn_terminal_disposition(
        result=result,
        checkpoint_ref=checkpoint_ref,
        authority_chain=authority_chain,
    )
    outcome = str(payload.get("outcome") or "failed").strip().lower()
    if disposition in {"delivery_unknown", "resume_unavailable", "cleanup_failure"}:
        outcome = "blocked"
    elif result.failure_class or result.provider_error_code or not checkpoint_ref:
        outcome = "canceled" if result.failure_class == "canceled" else "failed"
    result_payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    agent_result_ref = await _write_result_artifact(
        principal=principal,
        payload=result_payload,
        kind="runtime.branch_turn.agent_result.json",
        branch_turn_id=branch_turn_id,
        source_namespace=str(payload["sourceNamespace"]),
        source_workflow_id=workflow_id,
        source_run_id=str(payload["sourceRunId"]),
    )
    diagnostics_payload = {
        "schemaVersion": "checkpoint-branch-terminal-diagnostics/v1",
        "deliveryOutcome": outcome,
        "terminalDisposition": disposition,
        "checkpointCaptured": checkpoint_ref is not None,
        "authorityChain": authority_chain,
        "verificationPending": outcome == "succeeded",
        "failureClass": result.failure_class,
        "providerErrorCode": result.provider_error_code,
        "runtimeDiagnosticsRef": result.diagnostics_ref,
    }
    diagnostics_ref = await _write_result_artifact(
        principal=principal,
        payload=diagnostics_payload,
        kind="output.branch_turn.diagnostics.json",
        branch_turn_id=branch_turn_id,
        source_namespace=str(payload["sourceNamespace"]),
        source_workflow_id=workflow_id,
        source_run_id=str(payload["sourceRunId"]),
    )
    async with async_session_maker() as session:
        turn = await CheckpointBranchService(session).finalize_turn_execution(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            outcome=outcome,
            agent_result_ref=agent_result_ref,
            diagnostics_ref=diagnostics_ref,
            checkpoint_ref=checkpoint_ref,
            checkpoint_digest=checkpoint_digest,
            provider_session_id=provider_session_id,
            terminal_ref=terminal_ref,
            output_refs=result.output_refs,
            terminal_disposition=disposition,
        )
        await session.commit()
        return {
            "branchId": branch_id,
            "branchTurnId": branch_turn_id,
            "status": turn.status,
            "deliveryOutcome": outcome,
            "terminalDisposition": disposition,
            "verificationPending": outcome == "succeeded",
            "agentResultRef": agent_result_ref,
            "diagnosticsRef": diagnostics_ref,
            "checkpointRef": checkpoint_ref,
            "terminalRef": terminal_ref,
        }


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindCheckpointBranchTurnWorkflow:
    """Execute, checkpoint, and retain one fresh Omnigent branch turn."""

    def __init__(self) -> None:
        self._phase = "created"
        self._result: dict[str, Any] | None = None

    @workflow.query(name="checkpoint_branch.turn.state")
    def state(self) -> dict[str, Any]:
        return {"phase": self._phase, "result": self._result}

    async def _persist_terminal(
        self,
        payload: Mapping[str, Any],
        *,
        result: AgentRunResult,
        outcome: str,
        checkpoint: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _mapping(
            await workflow.execute_activity(
                "checkpoint_branch.turn.persist_terminal",
                {
                    "workflowId": payload["workflowId"],
                    "branchId": payload["branchId"],
                    "branchTurnId": payload["branchTurnId"],
                    "principal": payload["principal"],
                    "sourceNamespace": payload["sourceNamespace"],
                    "sourceRunId": payload["sourceRunId"],
                    "outcome": outcome,
                    "agentResult": result.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    "checkpoint": dict(checkpoint or {}),
                },
                start_to_close_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
        )

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("unsupported Checkpoint Branch turn schema")
        agent_request = AgentExecutionRequest.model_validate(payload["agentRequest"])
        self._phase = "dispatching"
        await workflow.execute_activity(
            "checkpoint_branch.turn.mark_running",
            {
                "workflowId": payload["workflowId"],
                "branchId": payload["branchId"],
                "branchTurnId": payload["branchTurnId"],
                "agentRunWorkflowId": payload["agentRunWorkflowId"],
            },
            start_to_close_timeout=timedelta(minutes=1),
            schedule_to_close_timeout=timedelta(minutes=3),
            retry_policy=_RETRY,
        )
        try:
            self._phase = "running"
            raw_result = await workflow.execute_child_workflow(
                "MoonMind.AgentRun",
                agent_request,
                id=str(payload["agentRunWorkflowId"]),
                task_queue=workflow.info().task_queue,
            )
            result = (
                raw_result
                if isinstance(raw_result, AgentRunResult)
                else AgentRunResult.model_validate(raw_result)
            )
            if result.failure_class or result.provider_error_code:
                self._phase = "failed"
                self._result = await self._persist_terminal(
                    payload, result=result, outcome="failed"
                )
                return self._result

            self._phase = "capturing_workspace"
            step = agent_request.step_execution
            assert step is not None
            identity = {
                "workflowId": step.workflow_id,
                "runId": step.run_id,
                "logicalStepId": step.logical_step_id,
                "executionOrdinal": step.execution_ordinal,
            }
            capture = _mapping(
                await workflow.execute_activity(
                    "workspace.capture_checkpoint",
                    {
                        "identity": identity,
                        "boundary": "after_execution",
                        "kind": "worktree_archive",
                        "workspaceLocator": payload["workspaceLocator"],
                        "artifactNamespace": (
                            f"checkpoint-branches/{payload['branchId']}/"
                            f"{payload['branchTurnId']}"
                        ),
                        "idempotencyKey": (
                            f"{step.step_execution_id}:capture:after_execution"
                        ),
                        "baseCommit": payload.get("baseCommit"),
                        "includeUntracked": True,
                        "includeIgnoredFiles": False,
                    },
                    task_queue=SANDBOX_TASK_QUEUE,
                    start_to_close_timeout=timedelta(minutes=5),
                    schedule_to_close_timeout=timedelta(minutes=10),
                    retry_policy=_RETRY,
                )
            )
            if capture.get("status") != "captured" or not isinstance(
                capture.get("workspace"), Mapping
            ):
                raise ValueError("branch workspace checkpoint capture failed")
            self._phase = "persisting_checkpoint"
            omnigent_capture = _mapping(
                result.metadata.get("omnigentCheckpointCapture")
            )
            omnigent_capture["workspaceLocator"] = payload["workspaceLocator"]
            omnigent_capture["instructionRefs"] = [payload["instructionRef"]]
            checkpoint = _mapping(
                await workflow.execute_activity(
                    "step_checkpoint.create_v2",
                    {
                        "identity": identity,
                        "boundary": "after_execution",
                        "taskInputSnapshotRef": payload["instructionRef"],
                        "workspace": capture["workspace"],
                        "omnigentCheckpointCapture": omnigent_capture,
                        "createdAt": workflow.now().astimezone(UTC).isoformat(),
                        "planDigest": _sha256(
                            str(agent_request.step_execution.context_bundle_digest).encode()
                        ),
                        "preparedInputRefs": agent_request.input_refs,
                        "stepOutputs": {
                            "outputRefs": result.output_refs,
                            "terminalRef": omnigent_capture.get("terminalRef"),
                        },
                        "diagnosticRefs": [
                            ref
                            for ref in [
                                result.diagnostics_ref,
                                *capture.get("diagnosticRefs", []),
                            ]
                            if ref
                        ],
                        "idempotencyKey": (
                            f"{step.step_execution_id}:checkpoint:after_execution"
                        ),
                    },
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    start_to_close_timeout=timedelta(minutes=2),
                    schedule_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_RETRY,
                )
            )
            self._phase = "verification_handoff"
            self._result = await self._persist_terminal(
                payload,
                result=result,
                outcome="succeeded",
                checkpoint=checkpoint,
            )
            self._phase = "completed"
            return self._result
        except CancelledError:
            self._phase = "canceled"
            self._result = await self._persist_terminal(
                payload,
                result=AgentRunResult(
                    summary="Checkpoint Branch turn was canceled.",
                    failureClass="canceled",
                ),
                outcome="canceled",
            )
            raise
        except Exception as exc:
            self._phase = "failed"
            self._result = await self._persist_terminal(
                payload,
                result=AgentRunResult(
                    summary=str(exc)[:1000] or "Checkpoint Branch turn failed.",
                    failureClass="system_error",
                    providerErrorCode=type(exc).__name__,
                ),
                outcome="failed",
            )
            return self._result


__all__ = [
    "MoonMindCheckpointBranchTurnWorkflow",
    "WORKFLOW_NAME",
    "mark_checkpoint_branch_turn_running",
    "persist_checkpoint_branch_turn_terminal",
]
