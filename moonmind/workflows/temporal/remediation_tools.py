"""Typed evidence tools for remediation tasks.

The tools in this module intentionally sit at a service/activity boundary. They
read the bounded remediation context artifact and only expose target evidence
that the context explicitly names.
"""

from __future__ import annotations

import json
import re
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactService
from moonmind.workflows.temporal.remediation_context import (
    REMEDIATION_CONTEXT_LINK_TYPE,
    RemediationLifecyclePublisher,
    build_corrected_instruction_retry_provenance,
    build_remediation_audit_event,
    build_remediation_decision_log,
    build_remediation_final_summary,
    build_remediation_target_annotation,
)
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

RemediationLogStream = Literal["stdout", "stderr", "merged", "diagnostics"]

_ALLOWED_ACTION_RESULT_STATUSES = frozenset(
    {
        "applied",
        "no_op",
        "rejected",
        "precondition_failed",
        "approval_required",
        "timed_out",
        "failed",
    }
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.-])/(?:[^\s\"']+/)*[^\s\"']+")
_PRESIGNED_URL_PATTERN = re.compile(
    r"https?://[^\s\"']*(?:X-Amz-Signature|X-Amz-Credential|AWSAccessKeyId|Signature|sig=|token=)[^\s\"']*",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:token|password|secret|api[_-]?key|credential)\s*[:=]\s*([^\s,;\"']+)"
)

class RemediationEvidenceToolError(RuntimeError):
    """Raised when a remediation evidence tool request is invalid."""

@dataclass(frozen=True, slots=True)
class RemediationLogReadResult:
    """Bounded historical log read result."""

    agent_run_id: str
    stream: RemediationLogStream
    lines: tuple[str, ...]
    next_cursor: str | None = None

@dataclass(frozen=True, slots=True)
class RemediationLiveFollowEvent:
    """One live-follow event visible to a remediation task."""

    sequence: int
    stream: str
    text: str
    timestamp: str | None = None

@dataclass(frozen=True, slots=True)
class RemediationLiveFollowResult:
    """Live-follow batch plus the cursor the caller should persist."""

    agent_run_id: str
    events: tuple[RemediationLiveFollowEvent, ...]
    resume_cursor: dict[str, Any] | None

@dataclass(frozen=True, slots=True)
class RemediationTargetHealthSnapshot:
    """Fresh bounded target health used to guard side-effecting action requests."""

    workflow_id: str
    pinned_run_id: str
    current_run_id: str
    state: str
    close_status: str | None
    title: str | None
    summary: str | None
    target_run_changed: bool

@dataclass(frozen=True, slots=True)
class RemediationActionRequestPreparation:
    """Side-effect-free pre-action read of current target health."""

    remediation_workflow_id: str
    action_kind: str
    target: RemediationTargetHealthSnapshot
    context_target: dict[str, Any]

class RemediationLogReader(Protocol):
    """Read bounded historical logs for a target agent run."""

    async def read_logs(
        self,
        *,
        agent_run_id: str,
        stream: RemediationLogStream,
        cursor: str | None = None,
        tail_lines: int | None = None,
    ) -> RemediationLogReadResult:
        raise NotImplementedError

class RemediationLiveFollower(Protocol):
    """Follow live target output for a target agent run."""

    async def follow_logs(
        self,
        *,
        agent_run_id: str,
        from_sequence: int | None = None,
    ) -> RemediationLiveFollowResult:
        raise NotImplementedError

class RemediationActionExecutor(Protocol):
    """Execute one authorized remediation action through an owning subsystem."""

    async def execute_action(
        self,
        *,
        action_request: Mapping[str, Any],
        guard_result: Mapping[str, Any],
        target_health: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        raise NotImplementedError

class _UnavailableLogReader:
    async def read_logs(
        self,
        *,
        agent_run_id: str,
        stream: RemediationLogStream,
        cursor: str | None = None,
        tail_lines: int | None = None,
    ) -> RemediationLogReadResult:
        raise RemediationEvidenceToolError(
            "remediation.read_target_logs is not configured in this runtime."
        )

class _UnavailableLiveFollower:
    async def follow_logs(
        self,
        *,
        agent_run_id: str,
        from_sequence: int | None = None,
    ) -> RemediationLiveFollowResult:
        raise RemediationEvidenceToolError(
            "remediation.follow_target_logs is not configured in this runtime."
        )

class _UnavailableActionExecutor:
    async def execute_action(
        self,
        *,
        action_request: Mapping[str, Any],
        guard_result: Mapping[str, Any],
        target_health: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        raise RemediationEvidenceToolError(
            "remediation.execute_action is not configured in this runtime."
        )

class RemediationEvidenceToolService:
    """Typed evidence access surface for one remediation execution."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        artifact_service: TemporalArtifactService,
        log_reader: RemediationLogReader | None = None,
        live_follower: RemediationLiveFollower | None = None,
        action_executor: RemediationActionExecutor | None = None,
        cursor_recorder: Callable[[str, dict[str, Any] | None], Awaitable[None]]
        | None = None,
    ) -> None:
        self._session = session
        self._artifact_service = artifact_service
        self._log_reader = log_reader or _UnavailableLogReader()
        self._live_follower = live_follower or _UnavailableLiveFollower()
        self._action_executor = action_executor or _UnavailableActionExecutor()
        self._lifecycle_publisher = RemediationLifecyclePublisher(
            session=session,
            artifact_service=artifact_service,
        )
        self._cursor_recorder = cursor_recorder
        self._context_payload_cache: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_context(
        self,
        *,
        remediation_workflow_id: str,
        principal: str = "service:remediation-tools",
    ) -> dict[str, Any]:
        """Return the parsed linked remediation context artifact."""

        link = await self._load_link(remediation_workflow_id)
        return await self._read_context_payload(link=link, principal=principal)

    async def read_target_artifact(
        self,
        *,
        remediation_workflow_id: str,
        artifact_ref: str | Mapping[str, Any],
        principal: str = "service:remediation-tools",
    ) -> bytes:
        """Read a target artifact only when declared by the context bundle."""

        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        artifact_id = _artifact_id_from_ref(artifact_ref)
        if not artifact_id:
            raise RemediationEvidenceToolError("artifactRef must include artifact_id.")
        allowed = _collect_context_artifact_ids(context)
        if artifact_id not in allowed:
            raise RemediationEvidenceToolError(
                f"Artifact {artifact_id} is not listed in remediation context."
            )
        _artifact, payload = await self._artifact_service.read(
            artifact_id=artifact_id,
            principal=principal,
        )
        return payload

    async def read_target_logs(
        self,
        *,
        remediation_workflow_id: str,
        agent_run_id: str,
        stream: RemediationLogStream,
        cursor: str | None = None,
        tail_lines: int | None = None,
        principal: str = "service:remediation-tools",
    ) -> RemediationLogReadResult:
        """Read bounded logs for a agentRunId declared by the context bundle."""

        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        normalized_agent_run_id = _required_string(agent_run_id, "agentRunId")
        if normalized_agent_run_id not in _collect_context_agent_run_ids(context):
            raise RemediationEvidenceToolError(
                f"Agent run {normalized_agent_run_id} is not listed in remediation context."
            )
        normalized_stream = _normalize_log_stream(stream)
        bounded_tail_lines = _bounded_tail_lines(context, tail_lines)
        return await self._log_reader.read_logs(
            agent_run_id=normalized_agent_run_id,
            stream=normalized_stream,
            cursor=cursor,
            tail_lines=bounded_tail_lines,
        )

    async def follow_target_logs(
        self,
        *,
        remediation_workflow_id: str,
        agent_run_id: str | None = None,
        from_sequence: int | None = None,
        principal: str = "service:remediation-tools",
    ) -> RemediationLiveFollowResult:
        """Follow live target logs only when context and policy allow it."""

        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        live_follow = context.get("liveFollow")
        live_mapping = live_follow if isinstance(live_follow, Mapping) else {}
        if live_mapping.get("supported") is not True:
            raise RemediationEvidenceToolError(
                "Live follow is not supported for this remediation context."
            )
        mode = str(live_mapping.get("mode") or "").strip()
        if mode not in {"follow", "snapshot_then_follow"}:
            raise RemediationEvidenceToolError(
                "Live follow is not allowed by remediation mode."
            )

        selected_agent_run_id = _required_string(
            agent_run_id or live_mapping.get("agentRunId"), "agentRunId"
        )
        if selected_agent_run_id not in _collect_context_agent_run_ids(context):
            raise RemediationEvidenceToolError(
                f"Agent run {selected_agent_run_id} is not listed in remediation context."
            )
        if live_mapping.get("agentRunId") not in {None, selected_agent_run_id}:
            raise RemediationEvidenceToolError(
                "Requested agentRunId does not match the live-follow target."
            )

        sequence = _normalize_sequence(
            from_sequence,
            default_cursor=live_mapping.get("resumeCursor"),
        )
        result = await self._live_follower.follow_logs(
            agent_run_id=selected_agent_run_id,
            from_sequence=sequence,
        )
        if self._cursor_recorder is not None:
            await self._cursor_recorder(link.remediation_workflow_id, result.resume_cursor)
        return result

    async def prepare_action_request(
        self,
        *,
        remediation_workflow_id: str,
        action_kind: str,
        principal: str = "service:remediation-tools",
    ) -> RemediationActionRequestPreparation:
        """Re-read current target health before a side-effecting action request.

        This method does not execute actions. It provides the typed freshness guard
        that action submission code must consume before invoking any future
        side-effecting remediation action surface.
        """

        normalized_action_kind = _required_string(action_kind, "actionKind")
        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        target = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            link.target_workflow_id,
        )
        if target is None:
            raise RemediationEvidenceToolError(
                f"Target execution {link.target_workflow_id} was not found."
            )
        context_target = context.get("target")
        context_target_mapping = (
            dict(context_target) if isinstance(context_target, Mapping) else {}
        )
        return RemediationActionRequestPreparation(
            remediation_workflow_id=link.remediation_workflow_id,
            action_kind=normalized_action_kind,
            target=RemediationTargetHealthSnapshot(
                workflow_id=target.workflow_id,
                pinned_run_id=link.target_run_id,
                current_run_id=target.run_id,
                state=_enum_value(target.state) or "",
                close_status=_enum_value(target.close_status),
                title=_string_or_none(
                    target.memo.get("title")
                    if isinstance(target.memo, Mapping)
                    else None
                ),
                summary=_string_or_none(
                    target.memo.get("summary")
                    if isinstance(target.memo, Mapping)
                    else None
                ),
                target_run_changed=target.run_id != link.target_run_id,
            ),
            context_target=context_target_mapping,
        )

    async def execute_action(
        self,
        *,
        remediation_workflow_id: str,
        authority_result: Mapping[str, Any],
        guard_result: Mapping[str, Any],
        principal: str = "service:remediation-tools",
    ) -> dict[str, Any]:
        """Execute an authorized action and publish bounded lifecycle artifacts."""

        if not isinstance(authority_result, Mapping):
            raise RemediationEvidenceToolError("authorityResult must be an object.")
        if not isinstance(guard_result, Mapping):
            raise RemediationEvidenceToolError("guardResult must be an object.")
        if authority_result.get("executable") is not True:
            raise RemediationEvidenceToolError(
                "authorityResult must be executable before action execution."
            )
        if guard_result.get("executable") is not True:
            raise RemediationEvidenceToolError(
                "guardResult must be executable before action execution."
            )

        action_request = authority_result.get("request")
        if not isinstance(action_request, Mapping):
            raise RemediationEvidenceToolError("authorityResult.request is required.")
        action_kind = _required_string(action_request.get("actionKind"), "actionKind")
        if action_kind != _required_string(guard_result.get("actionKind"), "actionKind"):
            raise RemediationEvidenceToolError(
                "authorityResult and guardResult action kinds do not match."
            )

        preparation = await self.prepare_action_request(
            remediation_workflow_id=remediation_workflow_id,
            action_kind=action_kind,
            principal=principal,
        )
        link = await self._load_link(remediation_workflow_id)
        self._validate_execution_context(
            link=link,
            remediation_workflow_id=remediation_workflow_id,
            authority_result=authority_result,
            guard_result=guard_result,
            action_request=action_request,
        )
        request_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.action_request",
            name=f"reports/remediation_action_request-{action_request['actionId']}.json",
            payload=_redact_payload_value(
                {
                    **dict(action_request),
                    "authority": dict(authority_result),
                    "guard": dict(guard_result),
                }
            ),
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )

        raw_result = await self._action_executor.execute_action(
            action_request=action_request,
            guard_result=guard_result,
            target_health=preparation.target,
        )
        if not isinstance(raw_result, Mapping):
            raise RemediationEvidenceToolError("action executor returned invalid result.")
        status = _normalize_action_result_status(raw_result.get("status"))
        verification_required = _bool_or_default(
            raw_result.get("verificationRequired"),
            default=status == "applied",
        )
        verification_hint = _string_or_none(raw_result.get("verificationHint"))
        if verification_hint is None and verification_required:
            action_result = authority_result.get("result")
            action_result_mapping = (
                action_result if isinstance(action_result, Mapping) else {}
            )
            verification_hint = _string_or_none(
                action_result_mapping.get("verificationHint")
            )
        if verification_required and verification_hint is None:
            raise RemediationEvidenceToolError(
                "verificationHint is required when verificationRequired is true."
            )
        redacted_verification_hint = _redact_text(verification_hint)
        if verification_required and redacted_verification_hint is None:
            redacted_verification_hint = "Verification hint redacted."
        applied_at = _string_or_none(raw_result.get("appliedAt"))
        if applied_at is None and status == "applied":
            applied_at = datetime.now(timezone.utc).isoformat()
        result_payload = {
            "schemaVersion": "v1",
            "actionKind": action_kind,
            "actionId": action_request["actionId"],
            "status": status,
            "message": _redact_text(raw_result.get("message"))
            or f"Action {action_kind} completed with status {status}.",
            "appliedAt": applied_at,
            "verificationRequired": verification_required,
            "verificationHint": redacted_verification_hint,
            "beforeStateRef": _redact_text(raw_result.get("beforeStateRef")),
            "afterStateRef": _redact_text(raw_result.get("afterStateRef")),
            "sideEffects": _redact_sequence(raw_result.get("sideEffects")),
        }
        result_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.action_result",
            name=f"reports/remediation_action_result-{action_request['actionId']}.json",
            payload=result_payload,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )

        verification = raw_result.get("verification")
        verification_payload = (
            dict(verification)
            if isinstance(verification, Mapping)
            else {"status": "not_verified"}
        )
        verification_payload.setdefault("actionKind", action_kind)
        verification_payload.setdefault("actionId", action_request["actionId"])
        verification_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.verification",
            name=f"reports/remediation_verification-{action_request['actionId']}.json",
            payload=verification_payload,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )

        audit_timestamp = datetime.now(timezone.utc)
        audit_payload = build_remediation_audit_event(
            event_id=f"{link.remediation_workflow_id}:{action_request['actionId']}:action",
            event_type="remediation.action",
            actor_user=_string_or_none(action_request.get("requester")),
            execution_principal=principal,
            remediation_workflow_id=link.remediation_workflow_id,
            remediation_run_id=link.remediation_run_id,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            action_kind=action_kind,
            risk_tier=_string_or_none(
                action_request.get("riskTier") or authority_result.get("risk")
            ),
            approval_decision=_string_or_none(authority_result.get("decision")),
            timestamp=audit_timestamp,
            metadata={
                "status": status,
                "idempotencyKey": action_request["actionId"],
                "verificationRequired": verification_required,
            },
        )
        audit_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.audit_event",
            name=f"events/remediation_action-{action_request['actionId']}.json",
            payload=audit_payload,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )
        annotation_payload = build_remediation_target_annotation(
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            remediation_workflow_id=link.remediation_workflow_id,
            remediation_run_id=link.remediation_run_id,
            action_kind=action_kind,
            decision=_annotation_decision_for_status(status),
            artifact_refs={
                "actionRequest": request_artifact.artifact_id,
                "actionResult": result_artifact.artifact_id,
                "verification": verification_artifact.artifact_id,
                "auditEvent": audit_artifact.artifact_id,
            },
            timestamp=audit_timestamp,
            metadata={
                "status": status,
                "nativeArtifactPolicy": "preserve",
            },
        )
        annotation_artifact = (
            await self._lifecycle_publisher.publish_target_annotation(
                remediation_workflow_id=link.remediation_workflow_id,
                target_workflow_id=link.target_workflow_id,
                target_run_id=link.target_run_id,
                name=(
                    "annotations/remediation_target-"
                    f"{action_request['actionId']}.json"
                ),
                payload=annotation_payload,
                principal=principal,
            )
        )

        link.latest_action_summary = action_kind
        link.outcome = status
        await self._session.commit()
        return {
            "schemaVersion": "v1",
            "actionKind": action_kind,
            "status": status,
            "artifactRefs": {
                "actionRequest": request_artifact.artifact_id,
                "actionResult": result_artifact.artifact_id,
                "verification": verification_artifact.artifact_id,
                "auditEvent": audit_artifact.artifact_id,
                "targetAnnotation": annotation_artifact.artifact_id,
            },
        }

    async def publish_lifecycle_summary(
        self,
        *,
        remediation_workflow_id: str,
        summary: Mapping[str, Any],
        repair: Mapping[str, Any],
        prevention: Mapping[str, Any],
        decision_log_entries: Sequence[Mapping[str, Any]],
        lock_release: str,
        final_audit_ref: str | None = None,
        principal: str = "service:remediation-tools",
    ) -> dict[str, Any]:
        """Publish the v1 remediation decision log and final lifecycle summary."""

        link = await self._load_link(remediation_workflow_id)
        decision_log = build_remediation_decision_log(entries=decision_log_entries)
        decision_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.decision_log",
            name="logs/remediation_decision_log.json",
            payload=decision_log,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )
        final_summary = build_remediation_final_summary(
            summary=summary,
            repair=repair,
            prevention=prevention,
            decision_log_ref=decision_artifact.artifact_id,
            final_audit_ref=final_audit_ref,
            lock_release=lock_release,
        )
        summary_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.summary",
            name="reports/remediation_summary.json",
            payload=final_summary,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )

        link.outcome = str(final_summary.get("resolution") or link.outcome or "")
        await self._session.commit()
        return {
            "schemaVersion": "v1",
            "artifactRefs": {
                "decisionLog": decision_artifact.artifact_id,
                "summary": summary_artifact.artifact_id,
            },
            "repairOutcome": final_summary.get("repair", {}).get("repairOutcome")
            if isinstance(final_summary.get("repair"), Mapping)
            else None,
            "preventionStatus": final_summary.get("prevention", {}).get("status")
            if isinstance(final_summary.get("prevention"), Mapping)
            else None,
            "lockRelease": final_summary.get("lockRelease"),
        }

    async def create_checkpoint_branch_from_remediation_context(
        self,
        *,
        remediation_workflow_id: str,
        request: Mapping[str, Any],
        checkpoint_branch_service: CheckpointBranchService | None = None,
        principal: str = "service:remediation-tools",
    ) -> dict[str, Any]:
        """Create and launch a fresh-session checkpoint branch repair turn."""

        if not isinstance(request, Mapping):
            raise RemediationEvidenceToolError("checkpoint branch request must be an object.")
        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        source = request.get("source")
        source_mapping = source if isinstance(source, Mapping) else {}
        checkpoint_ref = _required_artifact_ref(
            source_mapping.get("checkpointRef") or source_mapping.get("checkpoint_ref"),
            "source.checkpointRef",
        )
        checkpoint_boundary = _required_string(
            source_mapping.get("checkpointBoundary")
            or source_mapping.get("checkpoint_boundary")
            or "after_execution",
            "source.checkpointBoundary",
        )
        if checkpoint_boundary not in {
            "before_execution",
            "after_execution",
            "before_recovery_restoration",
        }:
            raise RemediationEvidenceToolError(
                "source.checkpointBoundary is not supported."
            )
        target = context.get("target") if isinstance(context.get("target"), Mapping) else {}
        target_run_id = _required_string(target.get("runId"), "context.target.runId")
        requested_run_id = str(
            source_mapping.get("runId") or source_mapping.get("run_id") or target_run_id
        ).strip()
        if requested_run_id != target_run_id or target_run_id != link.target_run_id:
            raise RemediationEvidenceToolError(
                "source.runId must match the pinned remediation target run."
            )
        evidence = context.get("evidence")
        evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
        allowed_refs = set(_artifact_ref_list(evidence_mapping.get("targetArtifactRefs")))
        if checkpoint_ref not in allowed_refs:
            raise RemediationEvidenceToolError(
                "source.checkpointRef is not listed in remediation context evidence."
            )

        branch_payload = request.get("branch")
        branch_mapping = branch_payload if isinstance(branch_payload, Mapping) else {}
        workspace_policy = _required_string(
            branch_mapping.get("workspacePolicy")
            or branch_mapping.get("workspace_policy")
            or "apply_previous_execution_diff_to_clean_baseline",
            "branch.workspacePolicy",
        )
        runtime_context_policy = _required_string(
            branch_mapping.get("runtimeContextPolicy")
            or branch_mapping.get("runtime_context_policy")
            or "fresh_agent_run",
            "branch.runtimeContextPolicy",
        )
        if runtime_context_policy != "fresh_agent_run":
            raise RemediationEvidenceToolError(
                "Omnigent v1 remediation checkpoint branches require fresh_agent_run."
            )

        instruction_ref, instruction_digest, instruction_artifact_ref = (
            await self._checkpoint_branch_instruction(
                link=link,
                request=request,
                principal=principal,
            )
        )
        stable_material = json.dumps(
            {
                "remediationWorkflowId": link.remediation_workflow_id,
                "contextArtifactRef": link.context_artifact_ref,
                "checkpointRef": checkpoint_ref,
                "instructionDigest": instruction_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        stable_digest = hashlib.sha256(stable_material).hexdigest()[:20]
        branch_id = str(branch_mapping.get("branchId") or f"cbr-rem-{stable_digest}").strip()
        branch_turn_id = str(
            branch_mapping.get("branchTurnId") or f"{branch_id}:turn:1"
        ).strip()
        idempotency_key = str(
            request.get("idempotencyKey")
            or f"{link.remediation_workflow_id}:checkpoint_branch:{stable_digest}"
        ).strip()

        service = checkpoint_branch_service or CheckpointBranchService(self._session)
        graph = await service.create_branch_graph(
            {
                "branchId": branch_id,
                "branchTurnId": branch_turn_id,
                "source": {
                    "workflowId": link.target_workflow_id,
                    "runId": link.target_run_id,
                    "logicalStepId": _string_or_none(
                        source_mapping.get("logicalStepId")
                        or source_mapping.get("logical_step_id")
                    ),
                    "sourceExecutionOrdinal": _positive_int_or_none(
                        source_mapping.get("executionOrdinal")
                        or source_mapping.get("execution_ordinal")
                    ),
                    "checkpointBoundary": checkpoint_boundary,
                    "checkpointRef": checkpoint_ref,
                    "checkpointDigest": _string_or_none(
                        source_mapping.get("checkpointDigest")
                        or source_mapping.get("checkpoint_digest")
                    ),
                },
                "label": _string_or_none(branch_mapping.get("label"))
                or "Remediation checkpoint repair",
                "workspacePolicy": workspace_policy,
                "runtimeContextPolicy": runtime_context_policy,
                "instructionRef": instruction_ref,
                "instructionDigest": instruction_digest,
                "contextBundleRef": link.context_artifact_ref,
                "idempotencyKey": idempotency_key,
                "gitRepository": _string_or_none(branch_mapping.get("gitRepository")),
                "gitBaseBranch": _string_or_none(branch_mapping.get("gitBaseBranch")),
                "gitBaseCommit": _string_or_none(branch_mapping.get("gitBaseCommit")),
                "gitWorkBranch": _string_or_none(branch_mapping.get("gitWorkBranch")),
                "createdBy": principal,
            }
        )

        launch = request.get("launch")
        launch_mapping = launch if isinstance(launch, Mapping) else {}
        launch_idempotency_key = build_branch_turn_launch_idempotency_key(
            workflow_id=link.target_workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
        )
        turn = await service.launch_turn(
            workflow_id=link.target_workflow_id,
            branch_id=branch_id,
            branch_turn_id=branch_turn_id,
            context_bundle_ref=_required_artifact_ref(
                launch_mapping.get("contextBundleRef") or link.context_artifact_ref,
                "launch.contextBundleRef",
            ),
            step_execution_manifest_ref=_required_artifact_ref(
                launch_mapping.get("stepExecutionManifestRef"),
                "launch.stepExecutionManifestRef",
            ),
            checkpoint_ref=checkpoint_ref,
            diagnostics_ref=_required_artifact_ref(
                launch_mapping.get("diagnosticsRef"),
                "launch.diagnosticsRef",
            ),
            idempotency_key=launch_idempotency_key,
            created_step_execution_id=_required_string(
                launch_mapping.get("createdStepExecutionId"),
                "launch.createdStepExecutionId",
            ),
            runtime_agent_run_id=_string_or_none(
                launch_mapping.get("runtimeAgentRunId")
            ),
            provider_session_id=_string_or_none(
                launch_mapping.get("providerSessionId")
            ),
            agent_request_ref=_string_or_none(launch_mapping.get("runtimeRequestRef")),
            agent_result_ref=_string_or_none(launch_mapping.get("runtimeResultRef")),
        )
        provenance = build_corrected_instruction_retry_provenance(
            original_input_ref=checkpoint_ref,
            remediation_context_ref=link.context_artifact_ref,
            corrected_instructions_ref=instruction_ref,
            retry_action_kind="checkpoint_branch.create_from_remediation_context",
            reason=_string_or_none(request.get("reason"))
            or "Remediation requested a fresh-session checkpoint branch repair.",
            metadata={
                "branchId": branch_id,
                "branchTurnId": branch_turn_id,
                "launchIdempotencyKey": launch_idempotency_key,
                "instructionArtifactRef": instruction_artifact_ref,
            },
        )
        provenance_artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.checkpoint_branch_provenance",
            name=f"reports/remediation_checkpoint_branch-{branch_id}.json",
            payload=provenance,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )
        link.latest_action_summary = "checkpoint_branch.create_from_remediation_context"
        link.outcome = "applied"
        await self._session.commit()
        return {
            "schemaVersion": "v1",
            "actionKind": "checkpoint_branch.create_from_remediation_context",
            "status": "applied",
            "targetWorkflowId": link.target_workflow_id,
            "branchId": branch_id,
            "branchTurnId": turn.branch_turn_id,
            "launchIdempotencyKey": launch_idempotency_key,
            "artifactRefs": {
                "remediationContext": link.context_artifact_ref,
                "correctedInstructions": instruction_ref,
                "instructionArtifact": instruction_artifact_ref,
                "provenance": provenance_artifact.artifact_id,
            },
            "graph": graph.model_dump(by_alias=True),
        }

    async def _checkpoint_branch_instruction(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        request: Mapping[str, Any],
        principal: str,
    ) -> tuple[str, str, str | None]:
        instructions = request.get("instructions")
        instruction_mapping = instructions if isinstance(instructions, Mapping) else {}
        instruction_ref = _string_or_none(
            instruction_mapping.get("instructionRef")
            or instruction_mapping.get("instruction_ref")
        )
        instruction_digest = _string_or_none(
            instruction_mapping.get("instructionDigest")
            or instruction_mapping.get("instruction_digest")
        )
        if instruction_ref:
            _required_artifact_ref(instruction_ref, "instructions.instructionRef")
            if not instruction_digest or not instruction_digest.startswith("sha256:"):
                raise RemediationEvidenceToolError(
                    "instructions.instructionDigest is required with instructionRef."
                )
            return instruction_ref, instruction_digest, None
        text = _required_string(instruction_mapping.get("text"), "instructions.text")
        payload_bytes = text.encode("utf-8")
        digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
        artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.corrected_instructions",
            name="reports/remediation_corrected_instructions.json",
            payload={
                "schemaVersion": "v1",
                "text": text,
                "digest": digest,
                "immutable": True,
            },
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )
        return artifact.artifact_id, digest, artifact.artifact_id

    async def _load_link(
        self, remediation_workflow_id: str
    ) -> db_models.TemporalExecutionRemediationLink:
        workflow_id = _required_string(remediation_workflow_id, "remediationWorkflowId")
        link = await self._session.get(
            db_models.TemporalExecutionRemediationLink, workflow_id
        )
        if link is None:
            raise RemediationEvidenceToolError(
                f"No remediation link found for {workflow_id}."
            )
        if not link.context_artifact_ref:
            raise RemediationEvidenceToolError(
                f"Remediation context artifact is not linked for {workflow_id}."
            )
        return link

    async def _read_context_payload(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        principal: str,
    ) -> dict[str, Any]:
        cache_key = (link.remediation_workflow_id, link.context_artifact_ref)
        cached = self._context_payload_cache.get(cache_key)
        if cached is not None:
            return cached

        artifact, payload = await self._artifact_service.read(
            artifact_id=link.context_artifact_ref,
            principal=principal,
        )
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, Mapping) else {}
        if metadata.get("artifact_type") != REMEDIATION_CONTEXT_LINK_TYPE:
            raise RemediationEvidenceToolError(
                f"Artifact {artifact.artifact_id} is not a remediation context."
            )
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemediationEvidenceToolError(
                f"Remediation context artifact {artifact.artifact_id} is invalid JSON."
            ) from exc
        if not isinstance(decoded, dict):
            raise RemediationEvidenceToolError(
                f"Remediation context artifact {artifact.artifact_id} is not an object."
            )
        target = decoded.get("target")
        target_mapping = target if isinstance(target, Mapping) else {}
        if target_mapping.get("workflowId") != link.target_workflow_id:
            raise RemediationEvidenceToolError(
                "Remediation context target workflow does not match the persisted link."
            )
        self._context_payload_cache[cache_key] = decoded
        return decoded

    def _validate_execution_context(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        remediation_workflow_id: str,
        authority_result: Mapping[str, Any],
        guard_result: Mapping[str, Any],
        action_request: Mapping[str, Any],
    ) -> None:
        expected_workflow_id = link.remediation_workflow_id
        supplied_workflow_id = _required_string(
            remediation_workflow_id,
            "remediationWorkflowId",
        )
        if supplied_workflow_id != expected_workflow_id:
            raise RemediationEvidenceToolError(
                "remediationWorkflowId does not match the persisted remediation link."
            )

        for label, payload in (
            ("authorityResult", authority_result),
            ("guardResult", guard_result),
        ):
            if (
                _required_string(
                    payload.get("remediationWorkflowId"),
                    f"{label}.remediationWorkflowId",
                )
                != expected_workflow_id
            ):
                raise RemediationEvidenceToolError(
                    f"{label}.remediationWorkflowId does not match the action context."
                )
            if (
                _required_string(
                    payload.get("targetWorkflowId"),
                    f"{label}.targetWorkflowId",
                )
                != link.target_workflow_id
            ):
                raise RemediationEvidenceToolError(
                    f"{label}.targetWorkflowId does not match the action context."
                )
            if (
                _required_string(
                    payload.get("idempotencyKey"),
                    f"{label}.idempotencyKey",
                )
                != action_request["actionId"]
            ):
                raise RemediationEvidenceToolError(
                    f"{label}.idempotencyKey does not match the action request."
                )

        request_target = action_request.get("target")
        request_target_mapping = (
            request_target if isinstance(request_target, Mapping) else {}
        )
        if request_target_mapping.get("workflowId") != link.target_workflow_id:
            raise RemediationEvidenceToolError(
                "authorityResult.request.target.workflowId does not match the action context."
            )
        guard_lock = guard_result.get("lock")
        guard_lock_mapping = guard_lock if isinstance(guard_lock, Mapping) else {}
        if guard_lock_mapping.get("targetRunId") != link.target_run_id:
            raise RemediationEvidenceToolError(
                "guardResult.lock.targetRunId does not match the persisted target run."
            )
        if guard_lock_mapping.get("holderWorkflowId") != expected_workflow_id:
            raise RemediationEvidenceToolError(
                "guardResult.lock.holderWorkflowId does not match the action context."
            )

def _collect_context_artifact_ids(context: Mapping[str, Any]) -> set[str]:
    evidence = context.get("evidence")
    evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
    artifact_ids: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            artifact_id = _artifact_id_from_ref(value)
            if artifact_id:
                artifact_ids.add(artifact_id)
            for item in value.values():
                collect(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                collect(item)

    collect(evidence_mapping)
    return artifact_ids

def _collect_context_agent_run_ids(context: Mapping[str, Any]) -> set[str]:
    evidence = context.get("evidence")
    evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
    agent_runs = evidence_mapping.get("agentRuns")
    output: set[str] = set()
    if isinstance(agent_runs, Sequence) and not isinstance(
        agent_runs, (str, bytes, bytearray)
    ):
        for item in agent_runs:
            if isinstance(item, Mapping):
                agent_run_id = _string_or_none(item.get("agentRunId"))
                if agent_run_id:
                    output.add(agent_run_id)
    selected = context.get("selectedSteps")
    if isinstance(selected, Sequence) and not isinstance(
        selected, (str, bytes, bytearray)
    ):
        for item in selected:
            if isinstance(item, Mapping):
                agent_run_id = _string_or_none(item.get("agentRunId"))
                if agent_run_id:
                    output.add(agent_run_id)
    return output

def _artifact_id_from_ref(value: str | Mapping[str, Any] | Any) -> str | None:
    if isinstance(value, Mapping):
        return _string_or_none(value.get("artifact_id") or value.get("artifactId"))
    return _string_or_none(value)

def _bounded_tail_lines(context: Mapping[str, Any], requested: int | None) -> int | None:
    max_tail_lines = 2000
    boundedness = context.get("boundedness")
    if isinstance(boundedness, Mapping):
        try:
            value = boundedness.get("maxTailLines")
            parsed = int(value) if value is not None else max_tail_lines
            if parsed >= 0:
                max_tail_lines = parsed
        except (TypeError, ValueError):
            # Ignore invalid policy metadata and keep the default/current bound.
            pass

    policy_tail_lines: int | None = None
    evidence_policy = (
        context.get("policies", {}).get("evidencePolicy")
        if isinstance(context.get("policies"), Mapping)
        else None
    )
    if isinstance(evidence_policy, Mapping):
        try:
            parsed_policy = int(evidence_policy.get("tailLines"))
            if parsed_policy >= 0:
                policy_tail_lines = parsed_policy
        except (TypeError, ValueError):
            policy_tail_lines = None

    effective_limit = min(
        value for value in (max_tail_lines, policy_tail_lines) if value is not None
    )
    if requested is None:
        requested = policy_tail_lines
        if requested is None:
            requested = max_tail_lines
    return max(0, min(int(requested), effective_limit))

def _normalize_log_stream(value: Any) -> RemediationLogStream:
    normalized = _required_string(value, "stream")
    if normalized not in {"stdout", "stderr", "merged", "diagnostics"}:
        raise RemediationEvidenceToolError(
            "stream must be one of stdout, stderr, merged, or diagnostics."
        )
    return normalized  # type: ignore[return-value]

def _normalize_sequence(value: int | None, *, default_cursor: Any) -> int | None:
    if value is not None:
        return max(0, int(value))
    if isinstance(default_cursor, Mapping):
        try:
            parsed = int(default_cursor.get("sequence"))
        except (TypeError, ValueError):
            return None
        return max(0, parsed)
    return None

def _normalize_action_result_status(value: Any) -> str:
    status = _required_string(value, "status")
    if status not in _ALLOWED_ACTION_RESULT_STATUSES:
        raise RemediationEvidenceToolError(
            f"Unsupported action result status: {status}."
        )
    return status

def _annotation_decision_for_status(status: str) -> str:
    if status in {"applied", "failed", "timed_out"}:
        return "attempted"
    if status == "no_op":
        return "skipped"
    if status == "approval_required":
        return "approval_required"
    if status in {"rejected", "precondition_failed"}:
        return "denied"
    return "escalated"

def _required_string(value: Any, field_name: str) -> str:
    normalized = _string_or_none(value)
    if not normalized:
        raise RemediationEvidenceToolError(f"{field_name} is required.")
    return normalized

def _required_artifact_ref(value: Any, field_name: str) -> str:
    normalized = _required_string(value, field_name)
    if normalized.startswith(("omnigent://", "http://", "https://", "file://", "/")):
        raise RemediationEvidenceToolError(
            f"{field_name} must be a MoonMind artifact ref, not a raw provider resource."
        )
    if not (
        normalized.startswith("art_")
        or normalized.startswith("artifact://")
        or normalized.startswith("artifact:v1:")
    ):
        raise RemediationEvidenceToolError(
            f"{field_name} must be a MoonMind artifact ref."
        )
    return normalized

def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

def _positive_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RemediationEvidenceToolError(
            "source.executionOrdinal must be a positive integer."
        ) from exc
    if parsed < 1:
        raise RemediationEvidenceToolError(
            "source.executionOrdinal must be a positive integer."
        )
    return parsed

def _artifact_ref_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    refs: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            raw = item.get("artifact_id") or item.get("artifactId") or item.get("ref")
        else:
            raw = item
        ref = str(raw or "").strip()
        if ref:
            refs.append(ref)
    return refs

def _safe_sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []

def _bool_or_default(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)

def _redact_sequence(value: Any) -> list[Any]:
    return [_redact_payload_value(item) for item in _safe_sequence(value)]

def _redact_payload_value(value: Any) -> Any:
    def apply_custom_redaction(node: Any) -> Any:
        if isinstance(node, str):
            return _redact_text(node)
        if isinstance(node, Mapping):
            return {
                str(key): apply_custom_redaction(item)
                for key, item in node.items()
            }
        if isinstance(node, (list, tuple)):
            return [apply_custom_redaction(item) for item in node]
        return node

    return apply_custom_redaction(redact_sensitive_payload(value))

def _redact_text(value: Any) -> str | None:
    normalized = _string_or_none(value)
    if normalized is None:
        return None
    if normalized.startswith(("artifact://", "ref://")):
        return normalized
    redacted = redact_sensitive_text(normalized)
    if redacted is None:
        return None
    redacted = _PRESIGNED_URL_PATTERN.sub("[REDACTED_URL]", redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub("[REDACTED_SECRET]", redacted)
    redacted = _ABSOLUTE_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    return redacted

def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", value)
    return _string_or_none(enum_value)

__all__ = [
    "RemediationActionExecutor",
    "RemediationActionRequestPreparation",
    "RemediationEvidenceToolError",
    "RemediationEvidenceToolService",
    "RemediationLiveFollowEvent",
    "RemediationLiveFollowResult",
    "RemediationLiveFollower",
    "RemediationLogReadResult",
    "RemediationLogReader",
    "RemediationLogStream",
    "RemediationTargetHealthSnapshot",
]
