"""Concrete activity-family helpers for the Temporal activity catalog."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from moonmind.config.settings import settings
from moonmind.jules.status import JulesStatusSnapshot, normalize_jules_status
from moonmind.schemas.jules_models import JulesCreateTaskRequest, JulesGetTaskRequest
from moonmind.schemas.manifest_ingest_models import CompiledManifestPlanModel
from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.adapters.jules_client import JulesClient
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.plan_validation import validate_plan_payload
from moonmind.workflows.skills.skill_dispatcher import execute_skill_activity
from moonmind.workflows.skills.skill_plan_contracts import (
    ActivityExecutionContext,
    ActivityInvocationEnvelope,
    CompactActivityResult,
    ObservabilitySummary,
    PlanDefinition,
    SkillResult,
    parse_plan_definition,
)
from moonmind.workflows.skills.skill_registry import (
    SkillRegistrySnapshot,
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog
from moonmind.workflows.temporal.artifacts import (
    ArtifactRef,
    ArtifactUploadDescriptor,
    ExecutionRef,
    TemporalArtifactService,
    build_artifact_ref,
)
from moonmind.workflows.temporal.manifest_ingest import (
    build_manifest_run_index,
    build_manifest_summary,
    compile_manifest_plan,
    plan_nodes_to_runtime_nodes,
)

HeartbeatCallback = Callable[[Mapping[str, Any]], Awaitable[None] | None]
PlanGenerator = Callable[
    [Any, Mapping[str, Any], SkillRegistrySnapshot | None],
    Mapping[str, Any] | PlanDefinition | Awaitable[Mapping[str, Any] | PlanDefinition],
]
JulesClientFactory = Callable[[], JulesClient]
_PLACEHOLDER_DIGEST_FRAGMENT = "sha256:dummy"
_GITHUB_REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class TemporalActivityRuntimeError(RuntimeError):
    """Raised when one of the Temporal activity helpers cannot complete."""


@dataclass(frozen=True, slots=True)
class ArtifactCreateActivityResult:
    """Result from ``artifact.create``."""

    artifact_ref: ArtifactRef
    upload_descriptor: ArtifactUploadDescriptor


@dataclass(frozen=True, slots=True)
class PlanGenerateActivityResult:
    """Result from ``plan.generate``."""

    plan_ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class ManifestCompileActivityResult:
    """Result from manifest compile activity helpers."""

    plan_ref: ArtifactRef
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class SandboxCommandResult:
    """Structured result from ``sandbox.run_command``."""

    exit_code: int
    command: tuple[str, ...]
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    diagnostics_ref: ArtifactRef | None


@dataclass(frozen=True, slots=True)
class IntegrationStartResult:
    """Structured result from ``integration.jules.start``."""

    external_id: str
    status: str
    tracking_ref: ArtifactRef | None
    url: str | None = None
    normalized_status: str = "unknown"
    provider_status: str = "unknown"
    callback_supported: bool = False

    @property
    def external_url(self) -> str | None:
        return self.url


@dataclass(frozen=True, slots=True)
class IntegrationStatusResult:
    """Structured result from ``integration.jules.status``."""

    external_id: str
    status: str
    tracking_ref: ArtifactRef | None
    url: str | None = None
    normalized_status: str = "unknown"
    provider_status: str = "unknown"
    terminal: bool = False

    @property
    def external_url(self) -> str | None:
        return self.url


@dataclass(frozen=True, slots=True)
class TemporalActivityBinding:
    """Resolved runtime binding of one activity type to a handler."""

    activity_type: str
    task_queue: str
    fleet: str
    handler: Callable[..., Any]


_ACTIVITY_HANDLER_ATTRS: dict[str, tuple[str, str]] = {
    "artifact.create": ("artifacts", "artifact_create"),
    "artifact.write_complete": ("artifacts", "artifact_write_complete"),
    "artifact.read": ("artifacts", "artifact_read"),
    "artifact.list_for_execution": ("artifacts", "artifact_list_for_execution"),
    "artifact.compute_preview": ("artifacts", "artifact_compute_preview"),
    "artifact.link": ("artifacts", "artifact_link"),
    "artifact.pin": ("artifacts", "artifact_pin"),
    "artifact.unpin": ("artifacts", "artifact_unpin"),
    "artifact.lifecycle_sweep": ("artifacts", "artifact_lifecycle_sweep"),
    "plan.generate": ("plans", "plan_generate"),
    "plan.validate": ("plans", "plan_validate"),
    "mm.tool.execute": ("skills", "mm_tool_execute"),
    "mm.skill.execute": ("skills", "mm_skill_execute"),
    "sandbox.checkout_repo": ("sandbox", "sandbox_checkout_repo"),
    "sandbox.apply_patch": ("sandbox", "sandbox_apply_patch"),
    "sandbox.run_command": ("sandbox", "sandbox_run_command"),
    "sandbox.run_tests": ("sandbox", "sandbox_run_tests"),
    "integration.jules.start": ("integrations", "integration_jules_start"),
    "integration.jules.status": ("integrations", "integration_jules_status"),
    "integration.jules.fetch_result": (
        "integrations",
        "integration_jules_fetch_result",
    ),
}


def _artifact_id_from_ref(value: ArtifactRef | str) -> str:
    if isinstance(value, ArtifactRef):
        return value.artifact_id
    normalized = str(value or "").strip()
    if not normalized:
        raise TemporalActivityRuntimeError("artifact reference is required")
    return normalized


def _artifact_locator(value: ArtifactRef | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, ArtifactRef):
        return value.artifact_id
    normalized = str(value).strip()
    return normalized or None


def _temporal_snapshot_from_payload(
    payload: Mapping[str, Any],
    *,
    artifact_locator: str,
) -> SkillRegistrySnapshot:
    skills = parse_skill_registry(payload)
    digest_only = create_registry_snapshot(
        skills=skills,
        artifact_store=InMemoryArtifactStore(),
    )
    return SkillRegistrySnapshot(
        digest=digest_only.digest,
        artifact_ref=artifact_locator,
        skills=skills,
    )


async def _read_json_artifact(
    service: TemporalArtifactService,
    *,
    artifact_ref: ArtifactRef | str,
    principal: str,
) -> Any:
    _artifact, payload = await service.read(
        artifact_id=_artifact_id_from_ref(artifact_ref),
        principal=principal,
        allow_restricted_raw=True,
    )
    return json.loads(payload.decode("utf-8"))


def build_activity_invocation_envelope(
    *,
    correlation_id: str,
    idempotency_key: str | None = None,
    input_refs: Sequence[ArtifactRef | str] = (),
    parameters: Mapping[str, Any] | None = None,
    side_effecting: bool = True,
) -> ActivityInvocationEnvelope:
    """Normalize one activity request into the shared compact envelope."""

    return ActivityInvocationEnvelope(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        input_refs=tuple(
            locator
            for locator in (_artifact_locator(item) for item in input_refs)
            if locator is not None
        ),
        parameters=dict(parameters or {}),
        side_effecting=side_effecting,
    )


def build_compact_activity_result(
    *,
    output_refs: Sequence[ArtifactRef | str] = (),
    summary: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    diagnostics_ref: ArtifactRef | str | None = None,
) -> CompactActivityResult:
    """Normalize one activity response into the shared compact envelope."""

    return CompactActivityResult(
        output_refs=tuple(
            locator
            for locator in (_artifact_locator(item) for item in output_refs)
            if locator is not None
        ),
        summary=dict(summary or {}),
        metrics=None if metrics is None else dict(metrics),
        diagnostics_ref=_artifact_locator(diagnostics_ref),
    )


def build_activity_execution_context(
    *,
    workflow_id: str,
    run_id: str,
    activity_id: str,
    attempt: int,
    task_queue: str,
) -> ActivityExecutionContext:
    """Create the runtime-derived context object used by logging and telemetry."""

    return ActivityExecutionContext(
        workflow_id=workflow_id,
        run_id=run_id,
        activity_id=activity_id,
        attempt=attempt,
        task_queue=task_queue,
    )


def build_observability_summary(
    *,
    context: ActivityExecutionContext,
    activity_type: str,
    correlation_id: str,
    idempotency_key: str,
    outcome: str,
    diagnostics_ref: ArtifactRef | str | None = None,
    metrics_dimensions: Mapping[str, Any] | None = None,
) -> ObservabilitySummary:
    """Create a structured summary without leaking the raw idempotency key."""

    return ObservabilitySummary(
        workflow_id=context.workflow_id,
        run_id=context.run_id,
        activity_type=activity_type,
        activity_id=context.activity_id,
        attempt=context.attempt,
        correlation_id=correlation_id,
        idempotency_key_hash=hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest(),
        outcome=outcome,
        diagnostics_ref=_artifact_locator(diagnostics_ref),
        metrics_dimensions=dict(metrics_dimensions or {}),
    )


async def _write_json_artifact(
    service: TemporalArtifactService,
    *,
    principal: str,
    payload: Mapping[str, Any] | list[Any],
    execution_ref: ExecutionRef | dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> ArtifactRef:
    artifact, _upload = await service.create(
        principal=principal,
        content_type="application/json",
        link=execution_ref,
        metadata_json=metadata_json,
    )
    completed = await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=(json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        content_type="application/json",
    )
    return build_artifact_ref(completed)


def _tail_text(payload: bytes, *, max_chars: int = 512) -> str:
    text = payload.decode("utf-8", errors="replace")
    return text[-max_chars:]


def _default_registry_skill_payload(*, name: str, version: str) -> dict[str, Any]:
    description = (
        "Execute generic runtime CLI instructions."
        if name == "auto"
        else f"Execute '{name}' via the generic runtime CLI handler."
    )
    # 3600s gives the sandbox worker enough headroom to exhaust the full
    # Gemini capacity-retry backoff cycle (up to 8 attempts with max 600s
    # delay each) before Temporal cancels the activity.
    start_to_close_seconds = 3600
    schedule_to_close_seconds = 3900
    if name in {
        "pr-resolver",
        "batch-pr-resolver",
        "fix-comments",
        "fix-ci",
        "fix-merge-conflicts",
    }:
        # Resolver/fix skills can run longer due bounded retry loops and CI waits.
        start_to_close_seconds = 7200
        schedule_to_close_seconds = 7500

    return {
        "name": name,
        "version": version,
        "description": description,
        "inputs": {
            "schema": {
                "type": "object",
                "properties": {
                    "instructions": {"type": "string"},
                    "runtime": {"type": "object"},
                },
                "additionalProperties": True,
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "additionalProperties": True,
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["sandbox"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": start_to_close_seconds,
                "schedule_to_close_seconds": schedule_to_close_seconds,
            },
            "retries": {"max_attempts": 1},
        },
    }


def _iter_requested_registry_tools(
    parameters: Mapping[str, Any] | None,
) -> tuple[tuple[str, str], ...]:
    selected: list[tuple[str, str]] = [("auto", "1.0")]
    seen = {("auto", "1.0")}

    if not isinstance(parameters, Mapping):
        return tuple(selected)

    task_payload = parameters.get("task")
    if not isinstance(task_payload, Mapping):
        return tuple(selected)

    candidate_nodes: list[Mapping[str, Any]] = [task_payload]
    steps = task_payload.get("steps")
    if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes, bytearray)):
        for step in steps:
            if isinstance(step, Mapping):
                candidate_nodes.append(step)

    for candidate in candidate_nodes:
        tool_payload = candidate.get("tool")
        skill_payload = candidate.get("skill")
        selected_payload = tool_payload if isinstance(tool_payload, Mapping) else None
        if selected_payload is None and isinstance(skill_payload, Mapping):
            selected_payload = skill_payload
        if selected_payload is None:
            continue

        tool_name = str(
            selected_payload.get("name") or selected_payload.get("id") or ""
        ).strip()
        if not tool_name:
            continue
        tool_version = str(selected_payload.get("version") or "").strip() or "1.0"
        key = (tool_name, tool_version)
        if key in seen:
            continue
        seen.add(key)
        selected.append(key)

    return tuple(selected)


def _default_skill_registry_payload(
    *, parameters: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "skills": [
            _default_registry_skill_payload(name=name, version=version)
            for name, version in _iter_requested_registry_tools(parameters)
        ]
    }


def _contains_placeholder_refs(value: Any) -> bool:
    if isinstance(value, str):
        return _PLACEHOLDER_DIGEST_FRAGMENT in value.lower()
    if isinstance(value, Mapping):
        return any(_contains_placeholder_refs(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_placeholder_refs(item) for item in value)
    return False


def _coerce_activity_request(
    request: Mapping[str, Any] | None,
    *,
    activity_type: str,
) -> dict[str, Any]:
    if request is None:
        return {}
    if not isinstance(request, Mapping):
        raise TemporalActivityRuntimeError(
            f"{activity_type} payload must be a JSON object"
        )
    return dict(request)


async def _maybe_call_heartbeat(
    callback: HeartbeatCallback | None,
    payload: Mapping[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


class TemporalPlanActivities:
    """Implementation helpers for ``plan.*`` activities."""

    def __init__(
        self,
        *,
        artifact_service: TemporalArtifactService,
        planner: PlanGenerator | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._planner = planner

    async def plan_generate(
        self,
        request: Mapping[str, Any] | None = None,
        /,
        *,
        principal: str | None = None,
        inputs_ref: ArtifactRef | str | None = None,
        parameters: Mapping[str, Any] | None = None,
        registry_snapshot_ref: ArtifactRef | str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> PlanGenerateActivityResult:
        request_payload = _coerce_activity_request(
            request, activity_type="plan.generate"
        )
        if request_payload:
            if principal is None:
                principal = request_payload.get("principal")
            if inputs_ref is None:
                inputs_ref = request_payload.get("inputs_ref")
            if parameters is None:
                parameters = request_payload.get("parameters")
            if registry_snapshot_ref is None:
                registry_snapshot_ref = request_payload.get("registry_snapshot_ref")
            if execution_ref is None:
                execution_ref = request_payload.get("execution_ref")

        if not principal or not isinstance(principal, str):
            raise TemporalActivityRuntimeError("plan.generate principal is required")
        if self._planner is None:
            raise TemporalActivityRuntimeError(
                "plan.generate planner is not configured"
            )

        inputs_payload: Any = None
        if inputs_ref is not None:
            inputs_payload = await _read_json_artifact(
                self._artifact_service,
                artifact_ref=inputs_ref,
                principal=principal,
            )

        snapshot: SkillRegistrySnapshot | None = None
        if registry_snapshot_ref is not None:
            registry_payload = await _read_json_artifact(
                self._artifact_service,
                artifact_ref=registry_snapshot_ref,
                principal=principal,
            )
            if not isinstance(registry_payload, Mapping):
                raise TemporalActivityRuntimeError(
                    "registry snapshot artifact payload must be a JSON object"
                )
            snapshot = _temporal_snapshot_from_payload(
                registry_payload,
                artifact_locator=_artifact_id_from_ref(registry_snapshot_ref),
            )
        else:
            registry_payload = _default_skill_registry_payload(parameters=parameters)
            fallback_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload=registry_payload,
                execution_ref=execution_ref,
                metadata_json={
                    "name": "registry_snapshot.json",
                    "producer": "activity:plan.generate",
                    "labels": ["registry", "snapshot"],
                },
            )
            snapshot = _temporal_snapshot_from_payload(
                registry_payload,
                artifact_locator=fallback_ref.artifact_id,
            )

        result = self._planner(inputs_payload, dict(parameters or {}), snapshot)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, PlanDefinition):
            payload = result.to_payload()
        elif isinstance(result, Mapping):
            payload = dict(result)
        else:
            raise TemporalActivityRuntimeError(
                f"plan.generate returned unsupported payload type {type(result)!r}"
            )

        if snapshot is not None:
            metadata = payload.setdefault("metadata", {})
            if not isinstance(metadata, dict):
                raise TemporalActivityRuntimeError(
                    "plan metadata must be a JSON object"
                )
            metadata.setdefault("title", "Generated Plan")
            metadata.setdefault(
                "created_at",
                datetime.now(UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            )
            registry_meta = metadata.setdefault("registry_snapshot", {})
            if not isinstance(registry_meta, dict):
                raise TemporalActivityRuntimeError(
                    "plan metadata.registry_snapshot must be a JSON object"
                )
            registry_meta.setdefault("digest", snapshot.digest)
            registry_meta.setdefault("artifact_ref", snapshot.artifact_ref)

        if _contains_placeholder_refs(payload):
            raise TemporalActivityRuntimeError(
                "plan.generate output contains placeholder ref(s) matching '*:sha256:dummy'"
            )

        parse_plan_definition(payload)
        plan_ref = await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload=payload,
            execution_ref=execution_ref,
            metadata_json={
                "name": "plan.json",
                "producer": "activity:plan.generate",
                "labels": ["plan"],
            },
        )
        return PlanGenerateActivityResult(plan_ref=plan_ref)

    async def plan_validate(
        self,
        *,
        plan_ref: ArtifactRef | str,
        registry_snapshot_ref: ArtifactRef | str,
        principal: str,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> ArtifactRef:
        plan_payload = await _read_json_artifact(
            self._artifact_service,
            artifact_ref=plan_ref,
            principal=principal,
        )
        registry_payload = await _read_json_artifact(
            self._artifact_service,
            artifact_ref=registry_snapshot_ref,
            principal=principal,
        )
        if not isinstance(plan_payload, Mapping):
            raise TemporalActivityRuntimeError(
                "plan artifact payload must be a JSON object"
            )
        if not isinstance(registry_payload, Mapping):
            raise TemporalActivityRuntimeError(
                "registry snapshot artifact payload must be a JSON object"
            )

        snapshot = _temporal_snapshot_from_payload(
            registry_payload,
            artifact_locator=_artifact_id_from_ref(registry_snapshot_ref),
        )
        validated = validate_plan_payload(
            payload=plan_payload, registry_snapshot=snapshot
        )
        return await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload=validated.plan.to_payload(),
            execution_ref=execution_ref,
            metadata_json={
                "name": "validated_plan.json",
                "producer": "activity:plan.validate",
                "labels": ["plan", "validated"],
            },
        )


class TemporalSkillActivities:
    """Implementation helper for ``mm.skill.execute``."""

    def __init__(
        self,
        *,
        dispatcher: Any,
        artifact_service: TemporalArtifactService | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._artifact_service = artifact_service

    async def mm_skill_execute(
        self,
        *,
        invocation_payload: Mapping[str, Any],
        registry_snapshot: SkillRegistrySnapshot | None = None,
        registry_snapshot_ref: ArtifactRef | str | None = None,
        artifact_service: TemporalArtifactService | None = None,
        principal: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> SkillResult:
        return await self._execute_skill_invocation(
            invocation_payload=invocation_payload,
            registry_snapshot=registry_snapshot,
            registry_snapshot_ref=registry_snapshot_ref,
            artifact_service=artifact_service,
            principal=principal,
            context=context,
        )

    async def _execute_skill_invocation(
        self,
        *,
        invocation_payload: Mapping[str, Any],
        registry_snapshot: SkillRegistrySnapshot | None = None,
        registry_snapshot_ref: ArtifactRef | str | None = None,
        artifact_service: TemporalArtifactService | None = None,
        principal: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> SkillResult:
        resolved_snapshot = registry_snapshot
        resolved_artifact_service = artifact_service or self._artifact_service
        if resolved_snapshot is None:
            if (
                resolved_artifact_service is None
                or principal is None
                or registry_snapshot_ref is None
            ):
                raise TemporalActivityRuntimeError(
                    "skill execution requires a registry snapshot or an artifact-backed registry reference"
                )
            registry_payload = await _read_json_artifact(
                resolved_artifact_service,
                artifact_ref=registry_snapshot_ref,
                principal=principal,
            )
            if not isinstance(registry_payload, Mapping):
                raise TemporalActivityRuntimeError(
                    "registry snapshot artifact payload must be a JSON object"
                )
            resolved_snapshot = _temporal_snapshot_from_payload(
                registry_payload,
                artifact_locator=_artifact_id_from_ref(registry_snapshot_ref),
            )

        return await execute_skill_activity(
            invocation_payload=invocation_payload,
            registry_snapshot=resolved_snapshot,
            dispatcher=self._dispatcher,
            context=context,
        )

    async def mm_tool_execute(
        self,
        *,
        invocation_payload: Mapping[str, Any],
        registry_snapshot: SkillRegistrySnapshot | None = None,
        registry_snapshot_ref: ArtifactRef | str | None = None,
        artifact_service: TemporalArtifactService | None = None,
        principal: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> SkillResult:
        """Canonical tool-execution alias for mm.skill.execute."""

        return await self._execute_skill_invocation(
            invocation_payload=invocation_payload,
            registry_snapshot=registry_snapshot,
            registry_snapshot_ref=registry_snapshot_ref,
            artifact_service=artifact_service,
            principal=principal,
            context=context,
        )


class TemporalManifestActivities:
    """Implementation helpers for manifest-ingest activity steps."""

    def __init__(self, *, artifact_service: TemporalArtifactService) -> None:
        self._artifact_service = artifact_service

    async def manifest_read(
        self,
        *,
        principal: str,
        manifest_ref: ArtifactRef | str,
    ) -> str:
        _artifact, payload = await self._artifact_service.read(
            artifact_id=_artifact_id_from_ref(manifest_ref),
            principal=principal,
            allow_restricted_raw=True,
        )
        return payload.decode("utf-8")

    async def manifest_compile(
        self,
        *,
        principal: str,
        manifest_ref: ArtifactRef | str,
        action: str,
        options: Mapping[str, Any] | None,
        requested_by: Mapping[str, Any],
        execution_policy: Mapping[str, Any],
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> ManifestCompileActivityResult:
        _artifact, manifest_payload = await self._artifact_service.read(
            artifact_id=_artifact_id_from_ref(manifest_ref),
            principal=principal,
            allow_restricted_raw=True,
        )
        plan = compile_manifest_plan(
            manifest_ref=_artifact_id_from_ref(manifest_ref),
            manifest_payload=manifest_payload,
            action=action,
            options=options,
            requested_by=requested_by,
            execution_policy=execution_policy,
        )
        plan_ref = await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload=plan.model_dump(by_alias=True),
            execution_ref=execution_ref,
            metadata_json={
                "name": "manifest_plan.json",
                "producer": "activity:manifest.compile",
                "labels": ["manifest", "plan"],
            },
        )
        return ManifestCompileActivityResult(
            plan_ref=plan_ref,
            manifest_digest=plan.manifest_digest,
        )

    async def manifest_write_summary(
        self,
        *,
        principal: str,
        workflow_id: str,
        state: str,
        phase: str,
        manifest_ref: str,
        plan_ref: str | None,
        nodes: Sequence[Mapping[str, Any]] | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> tuple[ArtifactRef, ArtifactRef]:
        resolved_nodes = await self._resolve_manifest_nodes(
            principal=principal,
            plan_ref=plan_ref,
            nodes=nodes,
        )
        summary = build_manifest_summary(
            workflow_id=workflow_id,
            state=state,
            phase=phase,
            manifest_ref=manifest_ref,
            plan_ref=plan_ref,
            nodes=resolved_nodes,
        )
        run_index = build_manifest_run_index(
            workflow_id=workflow_id,
            manifest_ref=manifest_ref,
            nodes=resolved_nodes,
        )
        summary_ref = await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload=summary.model_dump(by_alias=True),
            execution_ref=execution_ref,
            metadata_json={
                "name": "manifest_summary.json",
                "producer": "activity:manifest.summary",
                "labels": ["manifest", "summary"],
            },
        )
        run_index_ref = await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload=run_index.model_dump(by_alias=True),
            execution_ref=execution_ref,
            metadata_json={
                "name": "manifest_run_index.json",
                "producer": "activity:manifest.run_index",
                "labels": ["manifest", "run-index"],
            },
        )
        return summary_ref, run_index_ref

    async def _resolve_manifest_nodes(
        self,
        *,
        principal: str,
        plan_ref: str | None,
        nodes: Sequence[Mapping[str, Any]] | None,
    ) -> list[Mapping[str, Any]]:
        if nodes:
            return list(nodes)
        if not plan_ref:
            return []

        try:
            payload = await _read_json_artifact(
                self._artifact_service,
                artifact_ref=plan_ref,
                principal=principal,
            )
            compiled_plan = CompiledManifestPlanModel.model_validate(payload)
        except Exception as exc:
            raise TemporalActivityRuntimeError(
                "manifest.write_summary could not hydrate plan nodes from plan_ref"
            ) from exc

        runtime_nodes = plan_nodes_to_runtime_nodes(
            compiled_plan,
            requested_by=compiled_plan.requested_by,
        )
        return [node.model_dump(by_alias=True, mode="json") for node in runtime_nodes]


class TemporalSandboxActivities:
    """Implementation helper for ``sandbox.run_command``."""

    def __init__(
        self,
        *,
        artifact_service: TemporalArtifactService | None = None,
        redactor: SecretRedactor | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._redactor = redactor or SecretRedactor.from_environ()
        self._workspace_root = Path(
            workspace_root or settings.spec_workflow.workspace_root
        ).resolve()

    def _resolve_workspace(
        self, workspace_ref: str | Path, *, must_exist: bool
    ) -> Path:
        workspace = Path(workspace_ref).expanduser().resolve()
        sandbox_root = (self._workspace_root / "temporal_sandbox").resolve()
        if not workspace.is_relative_to(sandbox_root):
            raise TemporalActivityRuntimeError(
                f"workspace path escapes sandbox root: {workspace}"
            )
        if must_exist and not workspace.exists():
            raise TemporalActivityRuntimeError(f"workspace does not exist: {workspace}")
        return workspace

    def _resolve_checkout_source(self, repo_ref: str | Path) -> tuple[str, str | Path]:
        normalized = str(repo_ref).strip()
        if not normalized:
            raise TemporalActivityRuntimeError("sandbox.checkout_repo repo_ref is required")

        if normalized.startswith(("http://", "https://", "git@", "file://")):
            return ("remote", normalized)
        if _GITHUB_REPOSITORY_SLUG_PATTERN.fullmatch(normalized):
            return ("remote", f"https://github.com/{normalized}.git")

        source = Path(normalized).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise TemporalActivityRuntimeError(
                f"unsupported sandbox repo_ref '{repo_ref}'"
            )
        if not source.is_relative_to(self._workspace_root):
            raise TemporalActivityRuntimeError(
                "sandbox.checkout_repo local sources must be under workspace_root"
            )
        return ("local", source)

    async def sandbox_checkout_repo(
        self,
        *,
        repo_ref: str | Path,
        idempotency_key: str,
        checkout_revision: str | None = None,
    ) -> str:
        source_kind, source = self._resolve_checkout_source(repo_ref)

        workspace_id = hashlib.sha256(
            f"{source_kind}:{source}:{checkout_revision or ''}:{idempotency_key}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        workspace = (self._workspace_root / "temporal_sandbox" / workspace_id).resolve()
        workspace.parent.mkdir(parents=True, exist_ok=True)
        if workspace.exists():
            return str(workspace)

        if source_kind == "local":
            shutil.copytree(Path(source), workspace)
            return str(workspace)

        clone_result = await self.sandbox_run_command(
            {
                "workspace_ref": str(workspace.parent),
                "cmd": ["git", "clone", str(source), str(workspace)],
                "timeout_seconds": 600,
            }
        )
        if clone_result.exit_code != 0:
            raise TemporalActivityRuntimeError(
                "sandbox.checkout_repo failed to clone repository: "
                f"{clone_result.stderr_tail or clone_result.stdout_tail}"
            )

        if checkout_revision:
            checkout_result = await self.sandbox_run_command(
                {
                    "workspace_ref": str(workspace),
                    "cmd": ["git", "checkout", checkout_revision],
                    "timeout_seconds": 120,
                }
            )
            if checkout_result.exit_code != 0:
                raise TemporalActivityRuntimeError(
                    "sandbox.checkout_repo failed to checkout revision "
                    f"'{checkout_revision}': "
                    f"{checkout_result.stderr_tail or checkout_result.stdout_tail}"
                )

        if not workspace.exists():
            raise TemporalActivityRuntimeError(
                "sandbox.checkout_repo did not create a workspace directory"
            )
        return str(workspace)

    async def sandbox_apply_patch(
        self,
        *,
        workspace_ref: str | Path,
        patch_ref: ArtifactRef | str,
        principal: str,
        strip: int = 0,
        timeout_seconds: float | None = None,
        heartbeat: HeartbeatCallback | None = None,
    ) -> str:
        if self._artifact_service is None:
            raise TemporalActivityRuntimeError(
                "sandbox.apply_patch requires artifact storage"
            )

        cwd = self._resolve_workspace(workspace_ref, must_exist=True)

        _artifact, patch_payload = await self._artifact_service.read(
            artifact_id=_artifact_id_from_ref(patch_ref),
            principal=principal,
            allow_restricted_raw=True,
        )
        await _maybe_call_heartbeat(
            heartbeat, {"phase": "patch.read", "bytes": len(patch_payload)}
        )

        with tempfile.NamedTemporaryFile(delete=False) as patch_file:
            patch_file.write(patch_payload)
            patch_path = Path(patch_file.name)

        command: tuple[str, ...]
        if shutil.which("patch"):
            command = ("patch", f"-p{int(strip)}", "-i", str(patch_path))
        elif shutil.which("git"):
            command = ("git", "apply", str(patch_path))
        else:
            patch_path.unlink(missing_ok=True)
            raise TemporalActivityRuntimeError(
                "sandbox.apply_patch requires either 'patch' or 'git' in PATH"
            )

        try:
            result = await self.sandbox_run_command(
                {
                    "workspace_ref": str(cwd),
                    "cmd": list(command),
                    "principal": principal,
                    "timeout_seconds": timeout_seconds,
                },
                heartbeat=heartbeat,
            )
        finally:
            patch_path.unlink(missing_ok=True)

        if result.exit_code != 0:
            raise TemporalActivityRuntimeError(
                f"sandbox.apply_patch failed with exit code {result.exit_code}"
            )
        return str(cwd)

    async def sandbox_run_command(
        self,
        request: Mapping[str, Any] | None = None,
        /,
        *,
        workspace_ref: str | Path | None = None,
        cmd: str | Sequence[str] | None = None,
        principal: str | None = None,
        env: Mapping[str, str | None] | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        heartbeat: HeartbeatCallback | None = None,
    ) -> SandboxCommandResult:
        request_payload = _coerce_activity_request(
            request, activity_type="sandbox.run_command"
        )
        if request_payload:
            if workspace_ref is None:
                workspace_ref = request_payload.get("workspace_ref")
            if cmd is None:
                cmd = request_payload.get("cmd") or request_payload.get("command")
            if principal is None:
                principal = request_payload.get("principal")
            if env is None:
                env = request_payload.get("env")
            if execution_ref is None:
                execution_ref = request_payload.get("execution_ref")
            if timeout_seconds is None:
                timeout_seconds = request_payload.get("timeout_seconds")

        if workspace_ref is None:
            sandbox_root = (self._workspace_root / "temporal_sandbox").resolve()
            sandbox_root.mkdir(parents=True, exist_ok=True)
            workspace_ref = tempfile.mkdtemp(prefix="run-command-", dir=sandbox_root)

        cwd = self._resolve_workspace(workspace_ref, must_exist=True)
        if cmd is None:
            raise TemporalActivityRuntimeError("sandbox command must not be empty")

        if isinstance(cmd, str):
            command = tuple(shlex.split(cmd))
        else:
            command = tuple(str(part) for part in cmd)
        if not command:
            raise TemporalActivityRuntimeError("sandbox command must not be empty")

        merged_env = os.environ.copy()
        if env:
            for key, value in env.items():
                env_key = str(key)
                if value is None:
                    merged_env.pop(env_key, None)
                    continue
                merged_env[env_key] = str(value)

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        started = time.monotonic()
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()

        async def _drain(
            stream: asyncio.StreamReader | None,
            *,
            target: bytearray,
            stream_name: str,
        ) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                target.extend(chunk)
                await _maybe_call_heartbeat(
                    heartbeat,
                    {
                        "phase": "running",
                        "stream": stream_name,
                        "bytes": len(target),
                    },
                )

        stdout_task = asyncio.create_task(
            _drain(process.stdout, target=stdout_buffer, stream_name="stdout")
        )
        stderr_task = asyncio.create_task(
            _drain(process.stderr, target=stderr_buffer, stream_name="stderr")
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, process.wait()),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TemporalActivityRuntimeError(
                f"sandbox command timed out after {timeout_seconds} seconds"
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        combined = (
            f"$ {self._redactor.scrub(shlex.join(command))}\n"
            + self._redactor.scrub(stdout_buffer.decode("utf-8", errors="replace"))
            + self._redactor.scrub(stderr_buffer.decode("utf-8", errors="replace"))
        ).encode("utf-8")

        diagnostics_ref: ArtifactRef | None = None
        if self._artifact_service is not None and principal is not None:
            artifact, _upload = await self._artifact_service.create(
                principal=principal,
                content_type="text/plain",
                link=execution_ref,
                metadata_json={
                    "name": "sandbox_command.log",
                    "producer": "activity:sandbox.run_command",
                    "labels": ["sandbox", "logs"],
                },
            )
            completed = await self._artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal=principal,
                payload=combined,
                content_type="text/plain",
            )
            diagnostics_ref = build_artifact_ref(completed)

        return SandboxCommandResult(
            exit_code=int(process.returncode or 0),
            command=command,
            duration_ms=duration_ms,
            stdout_tail=_tail_text(stdout_buffer),
            stderr_tail=_tail_text(stderr_buffer),
            diagnostics_ref=diagnostics_ref,
        )

    async def sandbox_run_tests(
        self,
        *,
        workspace_ref: str | Path,
        parameters: Mapping[str, Any] | None = None,
        principal: str,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        heartbeat: HeartbeatCallback | None = None,
    ) -> ArtifactRef:
        if self._artifact_service is None:
            raise TemporalActivityRuntimeError(
                "sandbox.run_tests requires artifact storage"
            )

        params = dict(parameters or {})
        command = params.get("cmd") or params.get("command")
        if command is None:
            workspace = Path(workspace_ref).resolve()
            default_script = workspace / "tools" / "test_unit.sh"
            if default_script.exists():
                command = ("bash", str(default_script))
            else:
                raise TemporalActivityRuntimeError(
                    "sandbox.run_tests requires parameters.cmd or tools/test_unit.sh"
                )

        result = await self.sandbox_run_command(
            {
                "workspace_ref": str(workspace_ref),
                "cmd": command,
                "principal": principal,
                "execution_ref": execution_ref,
                "timeout_seconds": timeout_seconds,
            },
            heartbeat=heartbeat,
        )
        return await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload={
                "exit_code": result.exit_code,
                "command": list(result.command),
                "duration_ms": result.duration_ms,
                "stdout_tail": result.stdout_tail,
                "stderr_tail": result.stderr_tail,
                "diagnostics_ref": _artifact_locator(result.diagnostics_ref),
            },
            execution_ref=execution_ref,
            metadata_json={
                "name": "sandbox_test_report.json",
                "producer": "activity:sandbox.run_tests",
                "labels": ["sandbox", "tests", "report"],
            },
        )


class TemporalJulesActivities:
    """Implementation helpers for ``integration.jules.*``."""

    def __init__(
        self,
        *,
        artifact_service: TemporalArtifactService | None = None,
        client_factory: JulesClientFactory | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._client_factory = client_factory or self._build_default_client
        self._starts_by_idempotency: dict[str, IntegrationStartResult] = {}

    @staticmethod
    def _build_default_client() -> JulesClient:
        gate = settings.jules_runtime_gate
        if not gate.enabled:
            raise TemporalActivityRuntimeError(gate.error_message)
        return JulesClient(
            base_url=settings.jules.jules_api_url,
            api_key=settings.jules.jules_api_key,
            timeout=settings.jules.jules_timeout_seconds,
            retry_attempts=settings.jules.jules_retry_attempts,
            retry_delay_seconds=settings.jules.jules_retry_delay_seconds,
        )

    @staticmethod
    def _status_snapshot(raw_status: str | None) -> JulesStatusSnapshot:
        return normalize_jules_status(raw_status)

    async def _write_failure_summary_artifact(
        self,
        *,
        principal: str,
        execution_ref: ExecutionRef | dict[str, Any] | None,
        external_id: str,
        status: IntegrationStatusResult,
    ) -> ArtifactRef:
        summary_message = (
            f"Jules task '{external_id}' reached terminal status "
            f"'{status.provider_status}' ({status.normalized_status})."
        )
        return await _write_json_artifact(
            self._artifact_service,
            principal=principal,
            payload={
                "externalId": external_id,
                "providerStatus": status.provider_status,
                "normalizedStatus": status.normalized_status,
                "terminal": status.terminal,
                "externalUrl": status.external_url,
                "trackingRef": _artifact_locator(status.tracking_ref),
                "summary": summary_message,
            },
            execution_ref=execution_ref,
            metadata_json={
                "name": "jules_failure_summary.json",
                "producer": "activity:integration.jules.fetch_result",
                "labels": ["integration", "jules", "failure", "summary"],
            },
        )

    async def integration_jules_start(
        self,
        request: Mapping[str, Any] | None = None,
        /,
        *,
        principal: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
        inputs_ref: ArtifactRef | str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> IntegrationStartResult:
        request_payload = _coerce_activity_request(
            request, activity_type="integration.jules.start"
        )
        if request_payload:
            if principal is None:
                principal = request_payload.get("principal")
            if parameters is None:
                parameters = request_payload.get("parameters")
            if correlation_id is None:
                correlation_id = request_payload.get("correlation_id")
            if inputs_ref is None:
                inputs_ref = request_payload.get("inputs_ref")
            if execution_ref is None:
                execution_ref = request_payload.get("execution_ref")
            if idempotency_key is None:
                idempotency_key = request_payload.get("idempotency_key")

        if idempotency_key is not None:
            existing = self._starts_by_idempotency.get(idempotency_key)
            if existing is not None:
                return existing

        parameters = dict(parameters or {})
        title = str(parameters.get("title") or "MoonMind Integration Task").strip()
        description = str(parameters.get("description") or "").strip()

        if not description and inputs_ref is not None:
            if self._artifact_service is None or principal is None:
                raise TemporalActivityRuntimeError(
                    "integration.jules.start requires artifact_service and principal when inputs_ref is supplied"
                )
            _artifact, payload = await self._artifact_service.read(
                artifact_id=_artifact_id_from_ref(inputs_ref),
                principal=principal,
                allow_restricted_raw=True,
            )
            description = payload.decode("utf-8", errors="replace")
        if not description:
            raise TemporalActivityRuntimeError(
                "integration.jules.start requires parameters.description or inputs_ref"
            )

        metadata = parameters.get("metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TemporalActivityRuntimeError("integration metadata must be an object")

        metadata_payload = dict(metadata or {})
        if correlation_id is not None:
            normalized_correlation_id = str(correlation_id).strip()
            if not normalized_correlation_id:
                raise TemporalActivityRuntimeError(
                    "integration.jules.start correlation_id must not be blank"
                )
            metadata_payload.setdefault("correlationId", normalized_correlation_id)
        if idempotency_key is not None:
            metadata_payload.setdefault("idempotencyKey", idempotency_key)
            metadata_payload.setdefault("requestId", idempotency_key)

        client = self._client_factory()
        try:
            response = await client.create_task(
                JulesCreateTaskRequest(
                    title=title,
                    description=description,
                    metadata=metadata_payload,
                )
            )
        finally:
            await client.aclose()

        status_snapshot = self._status_snapshot(response.status)

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload=response.model_dump(by_alias=True, mode="json"),
                execution_ref=execution_ref,
                metadata_json={
                    "name": "jules_start.json",
                    "producer": "activity:integration.jules.start",
                    "labels": ["integration", "jules", "tracking"],
                },
            )

        result = IntegrationStartResult(
            external_id=response.task_id,
            status=status_snapshot.provider_status,
            tracking_ref=tracking_ref,
            url=response.url,
            normalized_status=status_snapshot.normalized_status,
            provider_status=status_snapshot.provider_status,
            callback_supported=False,
        )
        if idempotency_key is not None:
            self._starts_by_idempotency[idempotency_key] = result
        return result

    async def integration_jules_status(
        self,
        *,
        external_id: str,
        principal: str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> IntegrationStatusResult:
        client = self._client_factory()
        try:
            response = await client.get_task(JulesGetTaskRequest(task_id=external_id))
        finally:
            await client.aclose()

        status_snapshot = self._status_snapshot(response.status)

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload=response.model_dump(by_alias=True, mode="json"),
                execution_ref=execution_ref,
                metadata_json={
                    "name": "jules_status.json",
                    "producer": "activity:integration.jules.status",
                    "labels": ["integration", "jules", "tracking"],
                },
            )

        return IntegrationStatusResult(
            external_id=response.task_id,
            status=status_snapshot.provider_status,
            tracking_ref=tracking_ref,
            url=response.url,
            normalized_status=status_snapshot.normalized_status,
            provider_status=status_snapshot.provider_status,
            terminal=status_snapshot.terminal,
        )

    async def integration_jules_fetch_result(
        self,
        *,
        external_id: str,
        principal: str,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> tuple[ArtifactRef, ...]:
        if self._artifact_service is None:
            raise TemporalActivityRuntimeError(
                "integration.jules.fetch_result requires artifact storage"
            )

        status = await self.integration_jules_status(
            external_id=external_id,
            principal=principal,
            execution_ref=execution_ref,
        )
        output_refs: list[ArtifactRef] = []
        if status.tracking_ref is not None:
            output_refs.append(status.tracking_ref)
        if status.normalized_status in {"failed", "canceled"}:
            output_refs.append(
                await self._write_failure_summary_artifact(
                    principal=principal,
                    execution_ref=execution_ref,
                    external_id=external_id,
                    status=status,
                )
            )
        return tuple(output_refs)


def _build_activity_wrapper(
    func: Callable[..., Any],
) -> Callable[[Any, Any], Awaitable[Any]]:
    params = list(inspect.signature(func).parameters.values())
    non_self_params = params[1:] if params else []
    accepts_positional_request = bool(non_self_params) and non_self_params[0].kind in {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }
    accepts_request_keyword = any(param.name == "request" for param in non_self_params)
    accepts_var_kwargs = any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in non_self_params
    )

    async def _wrapper(self, request=None):
        if accepts_positional_request:
            return await func(self, request)
        if isinstance(request, Mapping):
            return await func(self, **request)
        if request is None:
            return await func(self)
        if accepts_request_keyword or accepts_var_kwargs:
            return await func(self, request=request)
        return await func(self, request)

    return _wrapper


def _bind_activity_handler(
    implementation: Any,
    *,
    func: Callable[..., Any],
    activity_type: str,
) -> Any:
    from temporalio import activity

    definition = getattr(func, "__temporal_activity_definition", None)
    if definition is not None:
        definition_name = str(getattr(definition, "name", "") or "")
        if definition_name == activity_type:
            return func.__get__(implementation, type(implementation))

    _wrapper = _build_activity_wrapper(func)
    _wrapper.__name__ = func.__name__
    _wrapper.__qualname__ = func.__qualname__
    _wrapper.__doc__ = func.__doc__
    decorated_func = activity.defn(name=activity_type)(_wrapper)
    return decorated_func.__get__(implementation, type(implementation))


def build_activity_bindings(
    catalog: TemporalActivityCatalog,
    *,
    artifact_activities: Any | None = None,
    plan_activities: Any | None = None,
    skill_activities: Any | None = None,
    sandbox_activities: Any | None = None,
    integration_activities: Any | None = None,
    fleets: Sequence[str] | None = None,
) -> tuple[TemporalActivityBinding, ...]:
    """Bind catalog activity types to concrete runtime handlers."""

    requested_fleets = set(fleets or ())
    implementations = {
        "artifacts": artifact_activities,
        "plans": plan_activities,
        "skills": skill_activities,
        "sandbox": sandbox_activities,
        "integrations": integration_activities,
    }
    bindings: list[TemporalActivityBinding] = []
    bound_keys: set[tuple[str, str]] = set()
    for definition in catalog.activities:
        if requested_fleets and definition.fleet not in requested_fleets:
            continue

        try:
            implementation_key, attr_name = _ACTIVITY_HANDLER_ATTRS[
                definition.activity_type
            ]
        except KeyError as exc:
            raise TemporalActivityRuntimeError(
                f"Activity '{definition.activity_type}' has no runtime binding metadata"
            ) from exc

        implementation = implementations[implementation_key]
        if implementation is None:
            raise TemporalActivityRuntimeError(
                f"Activity '{definition.activity_type}' requires a "
                f"{implementation_key.rstrip('s')} implementation"
            )

        func = getattr(type(implementation), attr_name, None)
        if func is None:
            raise TemporalActivityRuntimeError(
                f"Activity '{definition.activity_type}' requires handler "
                f"'{attr_name}' on {type(implementation).__name__}"
            )
        handler = _bind_activity_handler(
            implementation,
            func=func,
            activity_type=definition.activity_type,
        )

        binding = TemporalActivityBinding(
            activity_type=definition.activity_type,
            task_queue=definition.task_queue,
            fleet=definition.fleet,
            handler=handler,
        )
        bindings.append(binding)
        bound_keys.add((binding.activity_type, binding.fleet))

    if skill_activities is not None:
        activity_aliases = (
            ("mm.tool.execute", "mm_tool_execute"),
            ("mm.skill.execute", "mm_skill_execute"),
        )
        for fleet in catalog.fleets:
            if fleet.fleet == "workflow":
                continue
            if requested_fleets and fleet.fleet not in requested_fleets:
                continue
            for activity_type, attr_name in activity_aliases:
                binding_key = (activity_type, fleet.fleet)
                if binding_key in bound_keys:
                    continue
                resolved_attr_name = attr_name
                func = getattr(type(skill_activities), resolved_attr_name, None)
                if func is None and attr_name == "mm_tool_execute":
                    # Compatibility: if only mm_skill_execute exists, bind it as
                    # mm.tool.execute without requiring custom class changes.
                    resolved_attr_name = "mm_skill_execute"
                    func = getattr(type(skill_activities), resolved_attr_name, None)
                if func is None:
                    raise TemporalActivityRuntimeError(
                        f"Activity '{activity_type}' requires handler "
                        f"'{resolved_attr_name}' on {type(skill_activities).__name__}"
                    )

                handler = _bind_activity_handler(
                    skill_activities,
                    func=func,
                    activity_type=activity_type,
                )
                bindings.append(
                    TemporalActivityBinding(
                        activity_type=activity_type,
                        task_queue=fleet.task_queues[0],
                        fleet=fleet.fleet,
                        handler=handler,
                    )
                )

    return tuple(bindings)


__all__ = [
    "ArtifactCreateActivityResult",
    "HeartbeatCallback",
    "IntegrationStartResult",
    "IntegrationStatusResult",
    "PlanGenerateActivityResult",
    "SandboxCommandResult",
    "build_activity_execution_context",
    "build_activity_invocation_envelope",
    "build_compact_activity_result",
    "build_observability_summary",
    "TemporalActivityBinding",
    "TemporalActivityRuntimeError",
    "TemporalJulesActivities",
    "TemporalPlanActivities",
    "TemporalSkillActivities",
    "TemporalSandboxActivities",
    "build_activity_bindings",
]
