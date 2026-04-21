"""Typed evidence tools for remediation tasks.

The tools in this module intentionally sit at a service/activity boundary. They
read the bounded remediation context artifact and only expose target evidence
that the context explicitly names.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.workflows.temporal.artifacts import TemporalArtifactService
from moonmind.workflows.temporal.remediation_context import (
    REMEDIATION_CONTEXT_LINK_TYPE,
)

RemediationLogStream = Literal["stdout", "stderr", "merged", "diagnostics"]


class RemediationEvidenceToolError(RuntimeError):
    """Raised when a remediation evidence tool request is invalid."""


@dataclass(frozen=True, slots=True)
class RemediationLogReadResult:
    """Bounded historical log read result."""

    task_run_id: str
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

    task_run_id: str
    events: tuple[RemediationLiveFollowEvent, ...]
    resume_cursor: dict[str, Any] | None


class RemediationLogReader(Protocol):
    """Read bounded historical logs for a target task run."""

    async def read_logs(
        self,
        *,
        task_run_id: str,
        stream: RemediationLogStream,
        cursor: str | None = None,
        tail_lines: int | None = None,
    ) -> RemediationLogReadResult:
        raise NotImplementedError


class RemediationLiveFollower(Protocol):
    """Follow live target output for a target task run."""

    async def follow_logs(
        self,
        *,
        task_run_id: str,
        from_sequence: int | None = None,
    ) -> RemediationLiveFollowResult:
        raise NotImplementedError


class _UnavailableLogReader:
    async def read_logs(
        self,
        *,
        task_run_id: str,
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
        task_run_id: str,
        from_sequence: int | None = None,
    ) -> RemediationLiveFollowResult:
        raise RemediationEvidenceToolError(
            "remediation.follow_target_logs is not configured in this runtime."
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
        cursor_recorder: Callable[[str, dict[str, Any] | None], Awaitable[None]]
        | None = None,
    ) -> None:
        self._session = session
        self._artifact_service = artifact_service
        self._log_reader = log_reader or _UnavailableLogReader()
        self._live_follower = live_follower or _UnavailableLiveFollower()
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
        task_run_id: str,
        stream: RemediationLogStream,
        cursor: str | None = None,
        tail_lines: int | None = None,
        principal: str = "service:remediation-tools",
    ) -> RemediationLogReadResult:
        """Read bounded logs for a taskRunId declared by the context bundle."""

        link = await self._load_link(remediation_workflow_id)
        context = await self._read_context_payload(link=link, principal=principal)
        normalized_task_run_id = _required_string(task_run_id, "taskRunId")
        if normalized_task_run_id not in _collect_context_task_run_ids(context):
            raise RemediationEvidenceToolError(
                f"Task run {normalized_task_run_id} is not listed in remediation context."
            )
        normalized_stream = _normalize_log_stream(stream)
        bounded_tail_lines = _bounded_tail_lines(context, tail_lines)
        return await self._log_reader.read_logs(
            task_run_id=normalized_task_run_id,
            stream=normalized_stream,
            cursor=cursor,
            tail_lines=bounded_tail_lines,
        )

    async def follow_target_logs(
        self,
        *,
        remediation_workflow_id: str,
        task_run_id: str | None = None,
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

        selected_task_run_id = _required_string(
            task_run_id or live_mapping.get("taskRunId"), "taskRunId"
        )
        if selected_task_run_id not in _collect_context_task_run_ids(context):
            raise RemediationEvidenceToolError(
                f"Task run {selected_task_run_id} is not listed in remediation context."
            )
        if live_mapping.get("taskRunId") not in {None, selected_task_run_id}:
            raise RemediationEvidenceToolError(
                "Requested taskRunId does not match the live-follow target."
            )

        sequence = _normalize_sequence(
            from_sequence,
            default_cursor=live_mapping.get("resumeCursor"),
        )
        result = await self._live_follower.follow_logs(
            task_run_id=selected_task_run_id,
            from_sequence=sequence,
        )
        if self._cursor_recorder is not None:
            await self._cursor_recorder(link.remediation_workflow_id, result.resume_cursor)
        return result

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


def _collect_context_task_run_ids(context: Mapping[str, Any]) -> set[str]:
    evidence = context.get("evidence")
    evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
    task_runs = evidence_mapping.get("taskRuns")
    output: set[str] = set()
    if isinstance(task_runs, Sequence) and not isinstance(
        task_runs, (str, bytes, bytearray)
    ):
        for item in task_runs:
            if isinstance(item, Mapping):
                task_run_id = _string_or_none(item.get("taskRunId"))
                if task_run_id:
                    output.add(task_run_id)
    selected = context.get("selectedSteps")
    if isinstance(selected, Sequence) and not isinstance(
        selected, (str, bytes, bytearray)
    ):
        for item in selected:
            if isinstance(item, Mapping):
                task_run_id = _string_or_none(item.get("taskRunId"))
                if task_run_id:
                    output.add(task_run_id)
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


__all__ = [
    "RemediationEvidenceToolError",
    "RemediationEvidenceToolService",
    "RemediationLiveFollowEvent",
    "RemediationLiveFollowResult",
    "RemediationLiveFollower",
    "RemediationLogReadResult",
    "RemediationLogReader",
    "RemediationLogStream",
]
