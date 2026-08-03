"""Typed evidence tools for remediation tasks.

The tools in this module intentionally sit at a service/activity boundary. They
read the bounded remediation context artifact and only expose target evidence
that the context explicitly names.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.workflows.temporal.artifacts import TemporalArtifactService
from moonmind.workflows.temporal.remediation_context import (
    REMEDIATION_CONTEXT_LINK_TYPE,
    RemediationLifecyclePublisher,
    build_remediation_audit_event,
    build_remediation_decision_log,
    build_remediation_final_summary,
    build_remediation_target_annotation,
)
from moonmind.workflows.temporal.remediation_actions import (
    REMEDIATION_BRANCH_SENSITIVE_FIELDS,
    RemediationActionAuthorityService,
    RemediationMutationGuardPolicy,
    RemediationMutationGuardService,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_action_verification_contract,
    remediation_changes_require_checkpoint_branch,
)
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

RemediationLogStream = Literal["stdout", "stderr", "merged", "diagnostics"]

_ALLOWED_ACTION_RESULT_STATUSES = frozenset(
    {
        "accepted",
        "applied",
        "no_op",
        "delivery_unknown",
        "rejected",
        "denied",
        "precondition_failed",
        "approval_required",
        "verification_required",
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


@dataclass(frozen=True, slots=True)
class RemediationEvidencePage:
    """One bounded, redacted page from a typed context evidence class."""

    evidence_class: str
    status: str
    items: tuple[dict[str, Any], ...]
    next_cursor: int | None
    degraded_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RemediationArtifactReadResult:
    """Bounded metadata and optional content for one context-linked artifact."""

    artifact_id: str
    metadata: dict[str, Any]
    size_bytes: int
    content: str | None
    content_truncated: bool

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


class MoonMindControlPlaneRemediationActionExecutor:
    """Allowlisted adapter dispatcher for MoonMind-owned control-plane actions."""

    def __init__(
        self,
        adapters: Mapping[
            str,
            Callable[
                [Mapping[str, Any], Mapping[str, Any], RemediationTargetHealthSnapshot],
                Awaitable[Mapping[str, Any]],
            ],
        ],
    ) -> None:
        self._adapters = dict(adapters)

    async def execute_action(
        self,
        *,
        action_request: Mapping[str, Any],
        guard_result: Mapping[str, Any],
        target_health: RemediationTargetHealthSnapshot,
    ) -> Mapping[str, Any]:
        action_kind = _required_string(action_request.get("actionKind"), "actionKind")
        parameters = action_request.get("params")
        if not isinstance(parameters, Mapping):
            parameters = action_request.get("parameters")
        parameters_mapping = parameters if isinstance(parameters, Mapping) else {}
        original_inputs = guard_result.get("originalInputs")
        proposed_inputs = guard_result.get("proposedInputs")
        if isinstance(original_inputs, Mapping) and isinstance(
            proposed_inputs, Mapping
        ):
            branch_sensitive_changes = list(
                remediation_changes_require_checkpoint_branch(
                    original=original_inputs,
                    proposed=proposed_inputs,
                )
            )
        else:
            input_changes = parameters_mapping.get("inputChanges")
            changed_fields = (
                set(input_changes) if isinstance(input_changes, Mapping) else set()
            )
            branch_sensitive_changes = sorted(
                changed_fields & REMEDIATION_BRANCH_SENSITIVE_FIELDS
            )
        if (
            branch_sensitive_changes
            and action_kind != "checkpoint_branch.create_from_remediation_context"
        ):
            return {
                "status": "denied",
                "reason": "checkpoint_branch_required_for_corrected_input",
                "changedFields": branch_sensitive_changes,
                "beforeEvidenceRefs": [],
                "afterEvidenceRefs": [],
            }
        adapter = self._adapters.get(action_kind)
        if adapter is None:
            return {
                "status": "denied",
                "reason": "control_plane_adapter_unavailable",
                "beforeEvidenceRefs": [],
                "afterEvidenceRefs": [],
            }
        result = await adapter(action_request, guard_result, target_health)
        if not isinstance(result, Mapping):
            raise RemediationEvidenceToolError(
                f"Control-plane adapter for {action_kind} returned an invalid result."
            )
        normalized = dict(result)
        _normalize_action_result_status(normalized.get("status"))
        normalized.setdefault("beforeEvidenceRefs", [])
        normalized.setdefault("afterEvidenceRefs", [])
        return normalized

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
        stabilization_waiter: Callable[[float], Awaitable[None]] | None = None,
        stabilization_delay_seconds: float = 0.25,
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
        self._stabilization_waiter = stabilization_waiter or asyncio.sleep
        self._stabilization_delay_seconds = max(
            0.0, min(float(stabilization_delay_seconds), 5.0)
        )
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

    async def read_target_artifact_bounded(
        self,
        *,
        remediation_workflow_id: str,
        artifact_ref: str | Mapping[str, Any],
        include_content: bool = False,
        max_content_bytes: int = 65_536,
        principal: str = "service:remediation-tools",
    ) -> RemediationArtifactReadResult:
        """Read metadata and bounded redacted content for a linked artifact."""

        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        artifact_id = _artifact_id_from_ref(artifact_ref)
        if not artifact_id:
            raise RemediationEvidenceToolError("artifactRef must include artifact_id.")
        if artifact_id not in _collect_context_artifact_ids(context):
            raise RemediationEvidenceToolError(
                f"Artifact {artifact_id} is not listed in remediation context."
            )
        artifact, payload = await self._artifact_service.read(
            artifact_id=artifact_id,
            principal=principal,
        )
        bound = max(0, min(int(max_content_bytes), 1_048_576))
        bounded = payload[:bound] if include_content else b""
        return RemediationArtifactReadResult(
            artifact_id=artifact_id,
            metadata=_redact_payload_value(
                artifact.metadata_json
                if isinstance(artifact.metadata_json, Mapping)
                else {}
            ),
            size_bytes=len(payload),
            content=(
                _redact_text(bounded.decode("utf-8", errors="replace"))
                if include_content
                else None
            ),
            content_truncated=include_content and len(payload) > len(bounded),
        )

    async def read_evidence_page(
        self,
        *,
        remediation_workflow_id: str,
        evidence_class: str,
        cursor: int = 0,
        limit: int = 20,
        include_content: bool = False,
        max_content_bytes: int = 65_536,
        principal: str = "service:remediation-tools",
    ) -> RemediationEvidencePage:
        """Read a typed Omnigent evidence class without treating refs as grants.

        The context index is the allowlist, while each referenced artifact is
        independently authorized by ``TemporalArtifactService``. Missing
        historical classes return an explicit degraded page.
        """

        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        normalized_class = _required_string(evidence_class, "evidenceClass")
        evidence = context.get("evidence")
        evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
        index = evidence_mapping.get("omnigentIndex")
        entries = _safe_sequence(index)
        selected = next(
            (
                item
                for item in entries
                if isinstance(item, Mapping)
                and item.get("class") == normalized_class
            ),
            None,
        )
        if not isinstance(selected, Mapping):
            raise RemediationEvidenceToolError(
                f"Unknown remediation evidence class: {normalized_class}."
            )
        refs = [
            item
            for item in _safe_sequence(selected.get("refs"))
            if isinstance(item, Mapping)
        ]
        start = max(0, int(cursor))
        page_limit = max(1, min(int(limit), 100))
        page_refs = refs[start : start + page_limit]
        allowed = _collect_context_artifact_ids(context)
        items: list[dict[str, Any]] = []
        content_bound = max(0, min(int(max_content_bytes), 1_048_576))
        for ref in page_refs:
            artifact_id = _artifact_id_from_ref(ref)
            item: dict[str, Any] = {"ref": _redact_payload_value(dict(ref))}
            if not artifact_id or artifact_id not in allowed:
                item.update(
                    {
                        "status": "unavailable",
                        "degradedReason": "reference is not authorized by remediation context",
                    }
                )
                items.append(item)
                continue
            artifact, payload = await self._artifact_service.read(
                artifact_id=artifact_id,
                principal=principal,
            )
            item.update(
                {
                    "status": "available",
                    "artifactId": artifact_id,
                    "metadata": _redact_payload_value(
                        artifact.metadata_json
                        if isinstance(artifact.metadata_json, Mapping)
                        else {}
                    ),
                    "sizeBytes": len(payload),
                }
            )
            if include_content:
                bounded = payload[:content_bound]
                item["content"] = _redact_text(
                    bounded.decode("utf-8", errors="replace")
                )
                item["contentTruncated"] = len(payload) > len(bounded)
            items.append(item)
        next_cursor = start + len(page_refs)
        if next_cursor >= len(refs):
            next_cursor = None
        return RemediationEvidencePage(
            evidence_class=normalized_class,
            status=str(selected.get("status") or ("available" if refs else "missing")),
            items=tuple(items),
            next_cursor=next_cursor,
            degraded_reason=_string_or_none(selected.get("degradedReason")),
        )

    async def read_execution_and_step_details(self, **kwargs: Any) -> RemediationEvidencePage:
        """Read bounded execution/Step Execution evidence."""
        return await self.read_evidence_page(
            evidence_class="execution_and_steps", **kwargs
        )

    async def read_checkpoint_and_recovery_manifests(
        self, **kwargs: Any
    ) -> RemediationEvidencePage:
        """Read bounded checkpoint and recovery manifests."""
        return await self.read_evidence_page(
            evidence_class="checkpoint_and_recovery", **kwargs
        )

    async def read_bridge_event_pages(self, **kwargs: Any) -> RemediationEvidencePage:
        """Read bounded bridge event pages; live cursors use follow_target_logs."""
        return await self.read_evidence_page(evidence_class="bridge_events", **kwargs)

    async def read_capture_and_resource_manifests(
        self, **kwargs: Any
    ) -> RemediationEvidencePage:
        """Read bounded capture and resource manifests."""
        return await self.read_evidence_page(evidence_class="capture", **kwargs)

    async def read_cleanup_and_janitor_evidence(
        self, **kwargs: Any
    ) -> RemediationEvidencePage:
        """Read bounded cleanup, janitor, incident, and publication evidence."""
        return await self.read_evidence_page(evidence_class="lifecycle", **kwargs)

    async def read_branch_and_publication_evidence(
        self, **kwargs: Any
    ) -> RemediationEvidencePage:
        """Read bounded branch, comparison, promotion, and publication evidence."""
        return await self.read_evidence_page(
            evidence_class="checkpoint_branches", **kwargs
        )

    async def read_policy_and_approval_snapshots(
        self, **kwargs: Any
    ) -> RemediationEvidencePage:
        """Read bounded policy, approval, lock, retention, and redaction snapshots."""
        return await self.read_evidence_page(evidence_class="policy", **kwargs)

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
        action_kind: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
        authority_result: Mapping[str, Any] | None = None,
        guard_result: Mapping[str, Any] | None = None,
        principal: str = "service:remediation-tools",
    ) -> dict[str, Any]:
        """Execute an authorized action and publish bounded lifecycle artifacts."""
        link = await self._load_link(remediation_workflow_id)
        if authority_result is None or guard_result is None:
            if link.authority_mode != "admin_auto":
                raise RemediationEvidenceToolError(
                    "Persisted remediation authority does not permit automatic mutation."
                )
            normalized_action_kind = _required_string(action_kind, "actionKind")
            normalized_idempotency_key = _required_string(
                idempotency_key, "idempotencyKey"
            )
            authority = await RemediationActionAuthorityService(
                session=self._session
            ).evaluate_action_request(
            remediation_workflow_id=remediation_workflow_id,
            action_kind=normalized_action_kind,
            parameters=parameters,
            dry_run=dry_run,
            idempotency_key=normalized_idempotency_key,
            requesting_principal=principal,
            permissions=RemediationPermissionSet(
                can_view_target=True,
                can_request_admin_profile=True,
            ),
            security_profile=RemediationSecurityProfile(
                profile_ref="persisted:admin_auto",
                execution_principal=principal,
                allowed_action_kinds=(normalized_action_kind,),
            ),
            )
            authority_result = authority.to_dict()
            guard = await RemediationMutationGuardService(
                session=self._session
            ).evaluate(
            remediation_workflow_id=remediation_workflow_id,
            remediation_run_id=link.remediation_run_id,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            action_kind=normalized_action_kind,
            idempotency_key=normalized_idempotency_key,
            parameters=parameters,
            policy=RemediationMutationGuardPolicy(),
            now=datetime.now(timezone.utc),
            )
            guard_result = guard.to_dict()
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

        before_snapshot, before_state_artifact = await self._publish_target_state_snapshot(
            link=link,
            action_id=str(action_request["actionId"]),
            phase="before_action",
            principal=principal,
        )

        raw_result = await self._action_executor.execute_action(
            action_request=action_request,
            guard_result=guard_result,
            target_health=preparation.target,
        )
        if not isinstance(raw_result, Mapping):
            raise RemediationEvidenceToolError("action executor returned invalid result.")
        immediate_snapshot, immediate_state_artifact = (
            await self._publish_target_state_snapshot(
                link=link,
                action_id=str(action_request["actionId"]),
                phase="immediate_after_action",
                principal=principal,
            )
        )
        if verification_required := _bool_or_default(
            raw_result.get("verificationRequired"),
            default=_normalize_action_result_status(raw_result.get("status")) == "applied",
        ):
            await self._stabilization_waiter(self._stabilization_delay_seconds)
        stabilized_snapshot, stabilized_state_artifact = (
            await self._publish_target_state_snapshot(
                link=link,
                action_id=str(action_request["actionId"]),
                phase="stabilized_after_action",
                principal=principal,
            )
        )
        status = _normalize_action_result_status(raw_result.get("status"))
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
            "beforeStateRef": before_state_artifact.artifact_id,
            "afterStateRef": immediate_state_artifact.artifact_id,
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

        verification_contract = remediation_action_verification_contract(action_kind)
        verification_contract["stabilizationDelaySeconds"] = (
            self._stabilization_delay_seconds
        )
        verification_outcome, verification_checks = _execute_verification_contract(
            contract=verification_contract,
            action_kind=action_kind,
            action_status=status,
            before=before_snapshot,
            immediate_after=immediate_snapshot,
            stabilized=stabilized_snapshot,
        )
        verification_payload = {
            "schemaVersion": "moonmind.remediation-verification.v1",
            "outcome": verification_outcome,
            "phase": "verification_completed",
            "actionKind": action_kind,
            "actionId": action_request["actionId"],
            "actionResultRef": result_artifact.artifact_id,
            "target": {
                "workflowId": link.target_workflow_id,
                "runId": link.target_run_id,
                "sessionIdentity": preparation.context_target.get("sessionIdentity"),
                "stepIdentity": preparation.context_target.get("stepIdentity"),
                "checkpointRef": preparation.context_target.get("checkpointRef"),
            },
            "evidence": {
                "beforeStateRef": before_state_artifact.artifact_id,
                "immediateAfterStateRef": immediate_state_artifact.artifact_id,
                "stabilizedStateRef": stabilized_state_artifact.artifact_id,
            },
            "verificationHint": redacted_verification_hint,
            "verificationContract": verification_contract,
            "checks": verification_checks,
        }
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
        link.outcome = verification_outcome
        operator_state = dict(link.operator_state or {})
        operator_state["latestAction"] = {
            "actionKind": action_kind,
            "risk": action_request.get("riskTier"),
            "policyDecision": authority_result.get("decision"),
            "idempotencyKey": action_request["actionId"],
            "requestRef": request_artifact.artifact_id,
            "resultRef": result_artifact.artifact_id,
            "verificationRef": verification_artifact.artifact_id,
            "verificationOutcome": verification_outcome,
            "beforeStateRef": before_state_artifact.artifact_id,
            "afterStateRef": immediate_state_artifact.artifact_id,
            "stabilizedStateRef": stabilized_state_artifact.artifact_id,
            "lock": guard_result.get("lock"),
            "actor": action_request.get("requester"),
        }
        operator_state["targetAnnotationRef"] = annotation_artifact.artifact_id
        link.operator_state = operator_state
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

    async def _publish_target_state_snapshot(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        action_id: str,
        phase: str,
        principal: str,
    ) -> tuple[dict[str, Any], Any]:
        """Reacquire and persist service-owned evidence for action verification."""

        target = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            link.target_workflow_id,
        )
        if target is None:
            snapshot = {
                "schemaVersion": "moonmind.remediation-target-state.v1",
                "phase": phase,
                "available": False,
                "workflowId": link.target_workflow_id,
                "pinnedRunId": link.target_run_id,
            }
        else:
            await self._session.refresh(target)
            memo = target.memo if isinstance(target.memo, Mapping) else {}
            artifact_refs = sorted(
                str(item)
                for item in _safe_sequence(target.artifact_refs)
                if _string_or_none(item)
            )
            evidence_identity = {
                "runId": target.run_id,
                "state": _enum_value(target.state),
                "closeStatus": _enum_value(target.close_status),
                "checkpointRef": memo.get("checkpointRef")
                or memo.get("checkpoint_ref"),
                "sessionIdentity": memo.get("sessionIdentity")
                or memo.get("session_identity"),
                "workspaceHead": memo.get("workspaceHead")
                or memo.get("workspace_head"),
                "artifactRefs": artifact_refs,
            }
            snapshot = {
                "schemaVersion": "moonmind.remediation-target-state.v1",
                "phase": phase,
                "available": True,
                "workflowId": target.workflow_id,
                "pinnedRunId": link.target_run_id,
                "currentRunId": target.run_id,
                "state": _enum_value(target.state),
                "closeStatus": _enum_value(target.close_status),
                "checkpointRef": memo.get("checkpointRef")
                or memo.get("checkpoint_ref"),
                "sessionIdentity": memo.get("sessionIdentity")
                or memo.get("session_identity"),
                "workspaceHead": memo.get("workspaceHead")
                or memo.get("workspace_head"),
                "evidenceSignature": "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        evidence_identity, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest(),
                "observedAt": datetime.now(timezone.utc).isoformat(),
            }
        artifact = await self._lifecycle_publisher.publish_json_artifact(
            remediation_workflow_id=link.remediation_workflow_id,
            artifact_type="remediation.target_state",
            name=f"evidence/remediation_target_state-{action_id}-{phase}.json",
            payload=snapshot,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            principal=principal,
        )
        return snapshot, artifact

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
        operator_state = dict(link.operator_state or {})
        operator_state.update(
            {
                "phase": "completed",
                "summaryRef": summary_artifact.artifact_id,
                "decisionLogRef": decision_artifact.artifact_id,
                "immediateRepair": final_summary.get("repair"),
                "prevention": final_summary.get("prevention"),
                "cleanup": {"lockRelease": final_summary.get("lockRelease")},
            }
        )
        link.operator_state = operator_state
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

def _execute_verification_contract(
    *,
    contract: Mapping[str, Any],
    action_kind: str,
    action_status: str,
    before: Mapping[str, Any],
    immediate_after: Mapping[str, Any],
    stabilized: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Execute a typed contract against fresh evidence; executor prose is untrusted."""

    strategy = _required_string(contract.get("strategy"), "verification strategy")
    checks: list[dict[str, Any]] = []

    def checked(name: str, passed: bool, **evidence: Any) -> bool:
        checks.append({"name": name, "passed": passed, "evidence": evidence})
        return passed

    if action_status == "approval_required":
        return "approval_required", checks
    if action_status not in {"accepted", "applied", "no_op"}:
        return "verification_failed", checks
    snapshots = (before, immediate_after, stabilized)
    if not checked(
        "fresh_evidence_available",
        all(snapshot.get("available") is True for snapshot in snapshots),
        phases=[snapshot.get("phase") for snapshot in snapshots],
    ):
        return "evidence_unavailable", checks
    before_state = _string_or_none(before.get("state"))
    immediate_state = _string_or_none(immediate_after.get("state"))
    stabilized_state = _string_or_none(stabilized.get("state"))
    if not before_state or not immediate_state or not stabilized_state:
        return "evidence_unavailable", checks
    failed_states = {"failed", "canceled", "terminated"}
    resolved_states = {"completed"}
    if action_kind in {
        "execution.cancel",
        "execution.force_terminate",
        "session.cancel",
        "session.terminate",
    }:
        outcome = (
            "verified_resolved"
            if stabilized_state in {"canceled", "terminated"}
            else "verified_no_change"
            if before_state == stabilized_state
            else "verification_failed"
        )
        checked(
            "target_terminal",
            outcome == "verified_resolved",
            state=stabilized_state,
        )
        return outcome, checks
    if strategy == "target_paused":
        outcome = (
            "verified_resolved"
            if stabilized_state in {"paused", "suspended"}
            else "verified_no_change"
            if before_state == stabilized_state
            else "verification_failed"
        )
        checked(
            "target_paused",
            outcome == "verified_resolved",
            state=stabilized_state,
        )
        return outcome, checks
    if strategy in {"target_active", "run_identity_changed"}:
        run_changed = _string_or_none(before.get("currentRunId")) != _string_or_none(
            stabilized.get("currentRunId")
        )
        resumed = (
            before_state in {"paused", "failed", "canceled"}
            and stabilized_state in {"running", "executing", "completed"}
        )
        passed = run_changed if strategy == "run_identity_changed" else resumed
        checked(strategy, passed, runChanged=run_changed, state=stabilized_state)
        if passed:
            return "verified_resolved", checks
    if strategy == "session_identity_changed":
        changed = _string_or_none(before.get("sessionIdentity")) != _string_or_none(
            stabilized.get("sessionIdentity")
        )
        checked(strategy, changed)
        if changed:
            return "verified_resolved", checks
    if strategy == "checkpoint_changed":
        changed = _string_or_none(before.get("checkpointRef")) != _string_or_none(
            stabilized.get("checkpointRef")
        )
        checked(strategy, changed)
        if changed:
            return "verified_resolved", checks
    if strategy in {"evidence_changed", "evidence_available", "terminal_evidence"}:
        evidence_changed = _string_or_none(
            before.get("evidenceSignature")
        ) != _string_or_none(stabilized.get("evidenceSignature"))
        passed = strategy == "evidence_available" or evidence_changed
        if strategy == "terminal_evidence":
            passed = stabilized_state in resolved_states | failed_states
        checked(
            strategy,
            passed,
            evidenceChanged=evidence_changed,
            state=stabilized_state,
        )
        if passed:
            return "verified_resolved", checks
    if before_state not in failed_states and stabilized_state in failed_states:
        return "regressed", checks
    if stabilized_state in resolved_states and before_state not in resolved_states:
        return "verified_resolved", checks
    if stabilized_state in failed_states:
        return "still_failed", checks
    before_signature = (
        before_state,
        _string_or_none(before.get("currentRunId")),
        _string_or_none(before.get("checkpointRef")),
    )
    stabilized_signature = (
        stabilized_state,
        _string_or_none(stabilized.get("currentRunId")),
        _string_or_none(stabilized.get("checkpointRef")),
    )
    if before_signature == stabilized_signature:
        return "verified_no_change", checks
    return "verification_failed", checks

def _annotation_decision_for_status(status: str) -> str:
    if status in {"accepted", "applied", "failed", "timed_out"}:
        return "attempted"
    if status == "no_op":
        return "skipped"
    if status == "approval_required":
        return "approval_required"
    if status in {"rejected", "denied", "precondition_failed"}:
        return "denied"
    return "escalated"

def _required_string(value: Any, field_name: str) -> str:
    normalized = _string_or_none(value)
    if not normalized:
        raise RemediationEvidenceToolError(f"{field_name} is required.")
    return normalized

def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

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
    "MoonMindControlPlaneRemediationActionExecutor",
    "RemediationActionExecutor",
    "RemediationActionRequestPreparation",
    "RemediationEvidencePage",
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
