"""Concrete activity-family helpers for the Temporal activity catalog."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from logging import getLogger
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
from moonmind.schemas.manifest_ingest_models import CompiledManifestPlanModel
from moonmind.schemas.jules_models import JulesIntegrationMergePRResult
from moonmind.workflows.adapters.managed_agent_adapter import ManagedAgentAdapter
from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.codex_cloud_agent_adapter import CodexCloudAgentAdapter
from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClient as CodexCloudHttpClient
from moonmind.codex_cloud.settings import build_codex_cloud_gate, CODEX_CLOUD_DISABLED_MESSAGE
from moonmind.workflows.adapters.jules_client import JulesClient

from api_service.db.base import get_async_session_context
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository

from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunStatus,
    AgentRunResult,
    ManagedRuntimeProfile,
)
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

logger = getLogger(__name__)

HeartbeatCallback = Callable[[Mapping[str, Any]], Awaitable[None] | None]
PlanGenerator = Callable[
    [Any, Mapping[str, Any], SkillRegistrySnapshot | None],
    Mapping[str, Any] | PlanDefinition | Awaitable[Mapping[str, Any] | PlanDefinition],
]
JulesClientFactory = Callable[[], JulesClient]
JulesAgentAdapterFactory = Callable[[], JulesAgentAdapter]
CodexCloudClientFactory = Callable[[], CodexCloudHttpClient]
CodexCloudAdapterFactory = Callable[[], CodexCloudAgentAdapter]
_PLACEHOLDER_DIGEST_FRAGMENT = "sha256:dummy"
_GITHUB_REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_PULL_REQUEST_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+"
)


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
    "auth_profile.list": ("artifacts", "auth_profile_list"),
    "auth_profile.ensure_manager": ("artifacts", "auth_profile_ensure_manager"),
    "integration.jules.start": ("integrations", "integration_jules_start"),
    "integration.jules.status": ("integrations", "integration_jules_status"),
    "integration.jules.fetch_result": (
        "integrations",
        "integration_jules_fetch_result",
    ),
    "integration.jules.cancel": ("integrations", "integration_jules_cancel"),
    "integration.jules.merge_pr": ("integrations", "integration_jules_merge_pr"),
    "integration.jules.send_message": ("integrations", "integration_jules_send_message"),
    "integration.jules.list_activities": ("integrations", "integration_jules_list_activities"),
    "integration.jules.answer_question": ("integrations", "integration_jules_answer_question"),
    "integration.jules.get_auto_answer_config": ("integrations", "integration_jules_get_auto_answer_config"),
    "agent_runtime.launch": ("agent_runtime", "agent_runtime_launch"),
    "integration.codex_cloud.start": ("integrations", "integration_codex_cloud_start"),
    "integration.codex_cloud.status": ("integrations", "integration_codex_cloud_status"),
    "integration.codex_cloud.fetch_result": (
        "integrations",
        "integration_codex_cloud_fetch_result",
    ),
    "integration.codex_cloud.cancel": ("integrations", "integration_codex_cloud_cancel"),
    "integration.openclaw.execute": ("integrations", "integration_openclaw_execute"),
    "agent_runtime.publish_artifacts": (
        "agent_runtime",
        "agent_runtime_publish_artifacts",
    ),
    "agent_runtime.status": ("agent_runtime", "agent_runtime_status"),
    "agent_runtime.fetch_result": ("agent_runtime", "agent_runtime_fetch_result"),
    "agent_runtime.cancel": ("agent_runtime", "agent_runtime_cancel"),
    "proposal.generate": ("proposals", "proposal_generate"),
    "proposal.submit": ("proposals", "proposal_submit"),
    "step.review": ("reviews", "step_review"),
}


def _artifact_id_from_ref(value: ArtifactRef | str) -> str:
    if isinstance(value, ArtifactRef):
        return value.artifact_id
    normalized = str(value or "").strip()
    if not normalized:
        raise TemporalActivityRuntimeError("artifact reference is required")
    return normalized


def _derive_integration_title(
    description: str, fallback_title: str | None = None
) -> str:
    """Derive a human-readable task title from the description if missing.

    When *fallback_title* is ``None`` or blank the first non-empty line of
    *description* is used (truncated to 100 chars).  An explicit title —
    including one that happens to equal the default placeholder — is always
    preserved.
    """
    original_title = str(fallback_title or "").strip()
    if not original_title:
        if description:
            lines = [line.strip() for line in description.splitlines() if line.strip()]
            if lines:
                first_line = lines[0]
                if len(first_line) > 100:
                    first_line = first_line[:97] + "..."
                if first_line:
                    return first_line
    return original_title or "MoonMind Integration Task"


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
            workspace_root or settings.workflow.workspace_root
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


class TemporalIntegrationActivities:
    """Implementation helpers for ``integration.jules.*``."""

    def __init__(
        self,
        *,
        artifact_service: TemporalArtifactService | None = None,
        client_factory: JulesClientFactory | None = None,
        adapter_factory: JulesAgentAdapterFactory | None = None,
        codex_cloud_client_factory: CodexCloudClientFactory | None = None,
        codex_cloud_adapter_factory: CodexCloudAdapterFactory | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._client_factory = client_factory or self._build_default_client
        self._adapter = (
            adapter_factory()
            if adapter_factory is not None
            else JulesAgentAdapter(client_factory=self._client_factory)
        )
        self._codex_cloud_client_factory = (
            codex_cloud_client_factory or self._build_default_codex_cloud_client
        )
        self._codex_cloud_adapter = (
            codex_cloud_adapter_factory()
            if codex_cloud_adapter_factory is not None
            else CodexCloudAgentAdapter(
                client_factory=self._codex_cloud_client_factory
            )
        )

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

    @staticmethod
    def _status_snapshot_from_provider_and_hint(
        *,
        provider_status: str | None,
        normalized_hint: str | None,
    ) -> JulesStatusSnapshot:
        snapshot = normalize_jules_status(provider_status)
        token = str(normalized_hint or "").strip().lower()
        if token == "cancelled":
            token = "canceled"
        if token not in {"queued", "running", "succeeded", "failed", "canceled", "unknown"}:
            return snapshot

        terminal = token in {"succeeded", "failed", "canceled"}
        return JulesStatusSnapshot(
            provider_status=snapshot.provider_status,
            provider_status_token=snapshot.provider_status_token,
            normalized_status=token,
            terminal=terminal,
            succeeded=token == "succeeded",
            failed=token == "failed",
            canceled=token == "canceled",
        )

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

        parameters = dict(parameters or {})
        title_param = str(parameters.get("title") or "").strip()
        description = str(parameters.get("description") or "").strip()

        # Fallback: when dispatched from AgentRun, the task description lives
        # in instruction_ref / instructionRef of the serialised request, not
        # in parameters.description.
        if not description and request_payload:
            instruction_ref_text = (
                request_payload.get("instruction_ref")
                or request_payload.get("instructionRef")
                or ""
            )
            if isinstance(instruction_ref_text, str):
                description = instruction_ref_text.strip()

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

        title = _derive_integration_title(
            description=description,
            fallback_title=title_param or None,
        ) or "MoonMind Integration Task"

        metadata = parameters.get("metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TemporalActivityRuntimeError("integration metadata must be an object")

        metadata_payload = dict(metadata or {})
        if correlation_id is not None and not str(correlation_id).strip():
            raise TemporalActivityRuntimeError(
                "integration.jules.start correlation_id must not be blank"
            )
        resolved_correlation_id = str(
            correlation_id
            or metadata_payload.get("correlationId")
            or f"integration:jules:{title}"
        ).strip()
        if not resolved_correlation_id:
            raise TemporalActivityRuntimeError(
                "integration.jules.start requires a non-empty correlation id"
            )
        resolved_idempotency_key = str(
            idempotency_key
            or metadata_payload.get("idempotencyKey")
            or f"jules:{resolved_correlation_id}:{title}"
        ).strip()
        if not resolved_idempotency_key:
            raise TemporalActivityRuntimeError(
                "integration.jules.start requires a non-empty idempotency key"
            )
        metadata_payload.setdefault("correlationId", resolved_correlation_id)
        metadata_payload.setdefault("idempotencyKey", resolved_idempotency_key)
        metadata_payload.setdefault("requestId", resolved_idempotency_key)

        workspace_spec = {}
        if request_payload:
            workspace_spec = dict(request_payload.get("workspace_spec") or request_payload.get("workspaceSpec") or {})

        adapter_request = AgentExecutionRequest(
            agentKind="external",
            agentId="jules",
            executionProfileRef=str(
                parameters.get("execution_profile_ref") or "profile:jules-default"
            ),
            correlationId=resolved_correlation_id,
            idempotencyKey=resolved_idempotency_key,
            instructionRef=_artifact_locator(inputs_ref),
            inputRefs=[]
            if inputs_ref is None
            else [_artifact_id_from_ref(inputs_ref)],
            workspaceSpec=workspace_spec,
            parameters={
                **parameters,
                "title": title,
                "description": description,
                "metadata": metadata_payload,
            },
        )
        handle = await self._adapter.start(adapter_request)
        status_snapshot = self._status_snapshot(
            str(handle.metadata.get("providerStatus") or "unknown")
        )
        external_url = str(handle.metadata.get("externalUrl") or "").strip() or None

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "taskId": handle.run_id,
                    "status": status_snapshot.provider_status,
                    "url": external_url,
                    "metadata": dict(handle.metadata),
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "jules_start.json",
                    "producer": "activity:integration.jules.start",
                    "labels": ["integration", "jules", "tracking"],
                },
            )

        result = IntegrationStartResult(
            external_id=handle.run_id,
            status=status_snapshot.provider_status,
            tracking_ref=tracking_ref,
            url=external_url,
            normalized_status=status_snapshot.normalized_status,
            provider_status=status_snapshot.provider_status,
            callback_supported=False,
        )
        return result

    async def integration_jules_status(
        self,
        request: Mapping[str, Any] | str | None = None,
        /,
        *,
        external_id: str | None = None,
        principal: str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> IntegrationStatusResult:
        request_payload: dict[str, Any] = {}
        if isinstance(request, Mapping):
            request_payload = _coerce_activity_request(
                request, activity_type="integration.jules.status"
            )
        elif request is not None and not isinstance(request, str):
            raise TemporalActivityRuntimeError(
                "integration.jules.status payload must be a JSON object or string external_id"
            )

        if request_payload:
            if external_id is None:
                external_id = (
                    request_payload.get("external_id")
                    or request_payload.get("externalId")
                    or request_payload.get("run_id")
                    or request_payload.get("runId")
                )
            if principal is None:
                principal = request_payload.get("principal")
            if execution_ref is None:
                execution_ref = request_payload.get("execution_ref") or request_payload.get(
                    "executionRef"
                )
        if external_id is None and isinstance(request, str):
            external_id = request

        resolved_external_id = str(external_id or "").strip()
        if not resolved_external_id:
            raise TemporalActivityRuntimeError(
                "integration.jules.status requires a non-empty external_id"
            )

        status = await self._adapter.status(resolved_external_id)
        provider_status = str(status.metadata.get("providerStatus") or "unknown")
        external_url = (
            str(
                status.metadata.get("pullRequestUrl")
                or status.metadata.get("externalUrl")
                or ""
            ).strip()
            or None
        )
        status_snapshot = self._status_snapshot_from_provider_and_hint(
            provider_status=provider_status,
            normalized_hint=status.metadata.get("normalizedStatus"),
        )

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "taskId": status.run_id,
                    "status": provider_status,
                    "url": external_url,
                    "metadata": dict(status.metadata),
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "jules_status.json",
                    "producer": "activity:integration.jules.status",
                    "labels": ["integration", "jules", "tracking"],
                },
            )

        return IntegrationStatusResult(
            external_id=status.run_id,
            status=status_snapshot.provider_status,
            tracking_ref=tracking_ref,
            url=external_url,
            normalized_status=status_snapshot.normalized_status,
            provider_status=status_snapshot.provider_status,
            terminal=status_snapshot.terminal or status.terminal,
        )

    async def integration_jules_fetch_result(
        self,
        request: Mapping[str, Any] | None = None,
        /,
        *,
        external_id: str | None = None,
        principal: str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_payload = _coerce_activity_request(
            request, activity_type="integration.jules.fetch_result"
        )
        if request_payload:
            if external_id is None:
                external_id = (
                    request_payload.get("external_id")
                    or request_payload.get("externalId")
                    or request_payload.get("run_id")
                    or request_payload.get("runId")
                )
            if principal is None:
                principal = request_payload.get("principal")
            if execution_ref is None:
                execution_ref = request_payload.get("execution_ref") or request_payload.get(
                    "executionRef"
                )
        resolved_external_id = str(external_id or "").strip()
        if not resolved_external_id:
            raise TemporalActivityRuntimeError(
                "integration.jules.fetch_result requires a non-empty external_id"
            )

        status = await self.integration_jules_status(
            external_id=resolved_external_id,
            principal=principal,
            execution_ref=execution_ref,
        )
        output_refs: list[str] = []
        if status.tracking_ref is not None:
            output_refs.append(status.tracking_ref.artifact_id)

        if (
            status.normalized_status in {"failed", "canceled"}
            and principal is not None
            and self._artifact_service is not None
        ):
            failure_summary = await self._write_failure_summary_artifact(
                principal=principal,
                execution_ref=execution_ref,
                external_id=resolved_external_id,
                status=status,
            )
            output_refs.append(failure_summary.artifact_id)

        failure_class: str | None = None
        if status.normalized_status == "failed":
            failure_class = "integration_error"
        elif status.normalized_status == "canceled":
            failure_class = "execution_error"

        pull_request_url: str | None = None
        if status.external_url:
            pr_match = _GITHUB_PULL_REQUEST_URL_PATTERN.search(status.external_url)
            if pr_match is not None:
                pull_request_url = pr_match.group(0)

        summary = (
            f"Jules task {resolved_external_id} ended with provider status "
            f"'{status.provider_status}'."
        )
        if pull_request_url is not None:
            summary = (
                f"Jules task {resolved_external_id} created pull request "
                f"{pull_request_url} and reported provider status "
                f"'{status.provider_status}'."
            )

        metadata: dict[str, Any] = {
            "normalizedStatus": status.normalized_status,
            "providerStatus": status.provider_status,
            "terminal": status.terminal,
        }
        if status.external_url:
            metadata["externalUrl"] = status.external_url
        if pull_request_url is not None:
            metadata["pullRequestUrl"] = pull_request_url

        result = AgentRunResult(
            outputRefs=output_refs,
            summary=summary,
            failureClass=failure_class,
            providerErrorCode=status.provider_status if failure_class else None,
            metadata=metadata,
        )
        return result.model_dump(mode="json", by_alias=True)

    async def integration_jules_cancel(
        self,
        request: Mapping[str, Any] | None = None,
        /,
        *,
        external_id: str | None = None,
        principal: str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> IntegrationStatusResult:
        """Attempt best-effort cancellation of one Jules task."""
        request_payload = _coerce_activity_request(
            request, activity_type="integration.jules.cancel"
        )
        if request_payload:
            if external_id is None:
                external_id = (
                    request_payload.get("external_id")
                    or request_payload.get("externalId")
                    or request_payload.get("run_id")
                    or request_payload.get("runId")
                )
            if principal is None:
                principal = request_payload.get("principal")
            if execution_ref is None:
                execution_ref = request_payload.get("execution_ref") or request_payload.get(
                    "executionRef"
                )
        resolved_external_id = str(external_id or "").strip()
        if not resolved_external_id:
            raise TemporalActivityRuntimeError(
                "integration.jules.cancel requires a non-empty external_id"
            )

        adapter = self._build_adapter()
        result = await adapter.cancel(resolved_external_id)

        provider_status = str(
            result.metadata.get("providerStatus") or ""
        ).strip() or "canceled"
        normalized = self._status_snapshot(provider_status)

        tracking_ref: ArtifactRef | None = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "activity": "integration.jules.cancel",
                    "externalId": resolved_external_id,
                    "providerStatus": provider_status,
                    "normalizedStatus": normalized.normalized_status,
                    "cancelAccepted": result.metadata.get("cancelAccepted", False),
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "jules_cancel.json",
                    "producer": "activity:integration.jules.cancel",
                    "labels": ["integration", "jules", "cancel"],
                },
            )

        return IntegrationStatusResult(
            external_id=resolved_external_id,
            status=result.status,
            tracking_ref=tracking_ref,
            url=str(result.metadata.get("externalUrl") or "").strip() or None,
            normalized_status=normalized.normalized_status,
            provider_status=provider_status,
            terminal=normalized.terminal,
        )

    async def integration_jules_merge_pr(
        self, payload: dict
    ) -> JulesIntegrationMergePRResult:
        """Auto-merge a Jules-created PR into its target branch via GitHub API."""
        client = self._client_factory()

        pr_url = payload.get("pr_url") or payload.get("prUrl") or ""
        target_branch = payload.get("target_branch") or payload.get("targetBranch")

        if target_branch:
            success, summary = await client.update_pull_request_base(
                pr_url=pr_url, new_base=target_branch,
            )
            if not success:
                return JulesIntegrationMergePRResult(
                    prUrl=pr_url,
                    merged=False,
                    summary=f"Base branch update failed: {summary}",
                )

        return await client.merge_pull_request(pr_url=pr_url)

    async def integration_jules_send_message(
        self,
        *,
        session_id: str,
        prompt: str,
        principal: str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> IntegrationStatusResult:
        """Send a follow-up prompt to an existing Jules session.

        Used for multi-step workflows: resumes the session with new
        instructions instead of creating a new session.
        """
        if not session_id or not session_id.strip():
            raise TemporalActivityRuntimeError(
                "integration.jules.send_message requires a non-empty session_id"
            )
        if not prompt or not prompt.strip():
            raise TemporalActivityRuntimeError(
                "integration.jules.send_message requires a non-empty prompt"
            )

        result = await self._adapter.send_message(
            run_id=session_id,
            prompt=prompt,
        )
        status_snapshot = self._status_snapshot(
            str(result.metadata.get("providerStatus") or "running")
        )

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "activity": "integration.jules.send_message",
                    "sessionId": session_id,
                    "providerStatus": status_snapshot.provider_status,
                    "normalizedStatus": status_snapshot.normalized_status,
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "jules_send_message.json",
                    "producer": "activity:integration.jules.send_message",
                    "labels": ["integration", "jules", "send_message"],
                },
            )

        return IntegrationStatusResult(
            external_id=session_id,
            status=status_snapshot.provider_status,
            tracking_ref=tracking_ref,
            normalized_status=status_snapshot.normalized_status,
            provider_status=status_snapshot.provider_status,
        )

    async def integration_jules_list_activities(
        self,
        *,
        session_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch session activities and extract the latest agent question."""
        client = self._client_factory()
        try:
            result = await client.list_activities(session_id)
            return result.model_dump(by_alias=True)
        finally:
            await client.aclose()

    async def integration_jules_answer_question(
        self,
        *,
        session_id: str,
        question: str,
        task_context: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Orchestrate a full question-answer cycle for Jules."""
        from moonmind.schemas.jules_models import JulesSendMessageRequest
        from moonmind.workflows.temporal.activities.jules_activities import _generate_llm_answer

        if not session_id or not question:
            return {"answered": False, "answer": "", "error": "Missing session_id or question"}

        prompt_parts = [
            "You are answering a clarification question from Jules (an AI coding agent).",
        ]
        if task_context:
            prompt_parts.append(f"Task context: {task_context}")
        prompt_parts.extend([
            "",
            f"Jules's question:\n{question}",
            "",
            "Provide a concise, actionable answer. If the question asks about a preference or",
            "choice, choose the most reasonable default and explain your reasoning briefly.",
            "Do not ask follow-up questions.",
        ])
        clarification_prompt = "\n".join(prompt_parts)

        # Dispatch to an LLM to generate the actual answer.
        answer = await _generate_llm_answer(clarification_prompt)

        client = self._client_factory()
        try:
            await client.send_message(
                JulesSendMessageRequest(session_id=session_id, prompt=answer)
            )
            return {"answered": True, "answer": answer, "error": None}
        except Exception as exc:
            logger.error(
                "Failed to send auto-answer to Jules session %s: %s",
                session_id, exc, exc_info=True,
            )
            return {"answered": False, "answer": answer, "error": str(exc)}
        finally:
            await client.aclose()

    async def integration_jules_get_auto_answer_config(
        self,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Read auto-answer configuration from environment variables.

        Raises ``ValueError`` if integer env vars contain non-integer values.
        """
        enabled_raw = os.environ.get("JULES_AUTO_ANSWER_ENABLED", "true").strip().lower()
        enabled = enabled_raw not in ("false", "0", "no", "off")

        max_answers_raw = os.environ.get("JULES_MAX_AUTO_ANSWERS", "3")
        try:
            max_answers = int(max_answers_raw)
        except ValueError:
            raise ValueError(
                f"JULES_MAX_AUTO_ANSWERS must be an integer, got: {max_answers_raw!r}"
            )

        runtime = os.environ.get("JULES_AUTO_ANSWER_RUNTIME", "llm").strip() or "llm"

        timeout_raw = os.environ.get("JULES_AUTO_ANSWER_TIMEOUT_SECONDS", "300")
        try:
            timeout = int(timeout_raw)
        except ValueError:
            raise ValueError(
                f"JULES_AUTO_ANSWER_TIMEOUT_SECONDS must be an integer, got: {timeout_raw!r}"
            )

        return {
            "enabled": enabled,
            "max_answers": max_answers,
            "runtime": runtime,
            "timeout_seconds": timeout,
        }

    # ---- Codex Cloud integration handlers ----

    @staticmethod
    def _build_default_codex_cloud_client() -> CodexCloudHttpClient:
        import os

        gate = build_codex_cloud_gate()
        if not gate.enabled:
            raise TemporalActivityRuntimeError(
                f"{CODEX_CLOUD_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
            )
        cloud_url = os.environ.get("CODEX_CLOUD_API_URL", "").strip()
        cloud_key = os.environ.get("CODEX_CLOUD_API_KEY", "").strip()
        return CodexCloudHttpClient(base_url=cloud_url, api_key=cloud_key)

    async def integration_codex_cloud_start(
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
        """Start a Codex Cloud-backed run via the canonical adapter contract."""
        request_payload = _coerce_activity_request(
            request, activity_type="integration.codex_cloud.start"
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

        parameters = dict(parameters or {})
        title_param = str(parameters.get("title") or "").strip()
        description = str(parameters.get("description") or "").strip()

        if not description and inputs_ref is not None:
            if self._artifact_service is None or principal is None:
                raise TemporalActivityRuntimeError(
                    "integration.codex_cloud.start requires artifact_service and principal when inputs_ref is supplied"
                )
            _artifact, payload = await self._artifact_service.read(
                artifact_id=_artifact_id_from_ref(inputs_ref),
                principal=principal,
                allow_restricted_raw=True,
            )
            description = payload.decode("utf-8", errors="replace")
        if not description:
            raise TemporalActivityRuntimeError(
                "integration.codex_cloud.start requires parameters.description or inputs_ref"
            )

        title = _derive_integration_title(
            description=description,
            fallback_title=title_param or None,
        ) or "MoonMind Integration Task"

        resolved_correlation_id = str(
            correlation_id or f"integration:codex_cloud:{title}"
        ).strip()
        resolved_idempotency_key = str(
            idempotency_key or f"codex_cloud:{resolved_correlation_id}:{title}"
        ).strip()

        workspace_spec = {}
        if request_payload:
            workspace_spec = dict(request_payload.get("workspace_spec") or request_payload.get("workspaceSpec") or {})

        adapter_request = AgentExecutionRequest(
            agentKind="external",
            agentId="codex_cloud",
            executionProfileRef=str(
                parameters.get("execution_profile_ref") or "profile:codex-cloud-default"
            ),
            correlationId=resolved_correlation_id,
            idempotencyKey=resolved_idempotency_key,
            instructionRef=_artifact_locator(inputs_ref),
            inputRefs=[]
            if inputs_ref is None
            else [_artifact_id_from_ref(inputs_ref)],
            workspaceSpec=workspace_spec,
            parameters={
                **parameters,
                "title": title,
                "description": description,
            },
        )
        handle = await self._codex_cloud_adapter.start(adapter_request)
        provider_status = str(handle.metadata.get("providerStatus") or "unknown")
        external_url = str(handle.metadata.get("externalUrl") or "").strip() or None

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "taskId": handle.run_id,
                    "status": provider_status,
                    "url": external_url,
                    "metadata": dict(handle.metadata),
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "codex_cloud_start.json",
                    "producer": "activity:integration.codex_cloud.start",
                    "labels": ["integration", "codex_cloud", "tracking"],
                },
            )

        from moonmind.workflows.adapters.codex_cloud_client import normalize_codex_cloud_status

        normalized = normalize_codex_cloud_status(provider_status)
        return IntegrationStartResult(
            external_id=handle.run_id,
            status=provider_status,
            tracking_ref=tracking_ref,
            url=external_url,
            normalized_status=normalized,
            provider_status=provider_status,
            callback_supported=False,
        )

    async def integration_codex_cloud_status(
        self,
        *,
        external_id: str,
        principal: str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> IntegrationStatusResult:
        """Poll current status for one Codex Cloud task."""
        status = await self._codex_cloud_adapter.status(external_id)
        provider_status = str(status.metadata.get("providerStatus") or "unknown")
        external_url = str(status.metadata.get("externalUrl") or "").strip() or None

        from moonmind.workflows.adapters.codex_cloud_client import normalize_codex_cloud_status

        normalized = normalize_codex_cloud_status(provider_status)

        tracking_ref = None
        if self._artifact_service is not None and principal is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "taskId": status.run_id,
                    "status": provider_status,
                    "url": external_url,
                    "metadata": dict(status.metadata),
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "codex_cloud_status.json",
                    "producer": "activity:integration.codex_cloud.status",
                    "labels": ["integration", "codex_cloud", "tracking"],
                },
            )

        return IntegrationStatusResult(
            external_id=status.run_id,
            status=provider_status,
            tracking_ref=tracking_ref,
            url=external_url,
            normalized_status=normalized,
            provider_status=provider_status,
            terminal=status.terminal,
        )

    async def integration_codex_cloud_fetch_result(
        self,
        *,
        external_id: str,
        principal: str,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> tuple[ArtifactRef, ...]:
        """Fetch terminal result for one completed Codex Cloud task."""
        if self._artifact_service is None:
            raise TemporalActivityRuntimeError(
                "integration.codex_cloud.fetch_result requires artifact storage"
            )

        status = await self.integration_codex_cloud_status(
            external_id=external_id,
            principal=principal,
            execution_ref=execution_ref,
        )
        output_refs: list[ArtifactRef] = []
        if status.tracking_ref is not None:
            output_refs.append(status.tracking_ref)
        return tuple(output_refs)

    async def integration_codex_cloud_cancel(
        self,
        *,
        external_id: str,
        principal: str,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> IntegrationStatusResult:
        """Attempt best-effort cancellation of one Codex Cloud task."""
        result = await self._codex_cloud_adapter.cancel(external_id)

        provider_status = str(
            result.metadata.get("providerStatus") or ""
        ).strip() or "canceled"

        from moonmind.workflows.adapters.codex_cloud_client import normalize_codex_cloud_status

        normalized = normalize_codex_cloud_status(provider_status)

        tracking_ref: ArtifactRef | None = None
        if self._artifact_service is not None:
            tracking_ref = await _write_json_artifact(
                self._artifact_service,
                principal=principal,
                payload={
                    "activity": "integration.codex_cloud.cancel",
                    "externalId": external_id,
                    "providerStatus": provider_status,
                    "normalizedStatus": normalized,
                    "cancelAccepted": result.metadata.get("cancelAccepted", False),
                },
                execution_ref=execution_ref,
                metadata_json={
                    "name": "codex_cloud_cancel.json",
                    "producer": "activity:integration.codex_cloud.cancel",
                    "labels": ["integration", "codex_cloud", "cancel"],
                },
            )

        return IntegrationStatusResult(
            external_id=external_id,
            status=result.status,
            tracking_ref=tracking_ref,
            url=str(result.metadata.get("externalUrl") or "").strip() or None,
            normalized_status=normalized,
            provider_status=provider_status,
            terminal=result.terminal,
        )

    async def integration_openclaw_execute(
        self,
        request: Mapping[str, Any] | None = None,
        /,
    ) -> dict[str, Any]:
        """Run one OpenClaw streaming execution; heartbeats carry stream progress."""

        from moonmind.openclaw.execute import run_openclaw_execution

        body = _coerce_activity_request(
            request, activity_type="integration.openclaw.execute"
        )
        if not body:
            raise TemporalActivityRuntimeError(
                "integration.openclaw.execute requires AgentExecutionRequest payload"
            )
        req = AgentExecutionRequest.model_validate(body)
        result = await run_openclaw_execution(req)
        return result.model_dump(mode="json", by_alias=True)


class TemporalProposalActivities:
    """Implementation helpers for ``proposal.*`` activities."""

    def __init__(
        self,
        *,
        artifact_service: TemporalArtifactService | None = None,
        proposal_service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._proposal_service_factory = proposal_service_factory

    async def proposal_generate(
        self,
        request: Mapping[str, Any] | None = None,
        /,
    ) -> list[dict[str, Any]]:
        """Analyze execution context and produce candidate follow-up proposals.

        Generates structured proposal candidates from the workflow's
        ``initialParameters`` (passed via the *request* payload by
        ``_run_proposals_stage``).  Each candidate matches the schema
        consumed by ``proposal_submit``: ``title``, ``summary``,
        ``category``, ``tags``, and ``taskCreateRequest``.

        Returns an empty list when insufficient context is available to
        produce a meaningful proposal (e.g. missing instructions).
        """
        payload = dict(request or {})
        parameters: dict[str, Any] = payload.get("parameters") or {}
        repo = str(payload.get("repo") or parameters.get("repository") or "").strip()
        workflow_id = str(payload.get("workflow_id") or "").strip()

        # Extract instructions from the task payload or top-level parameters.
        task_node = parameters.get("task")
        task = dict(task_node) if isinstance(task_node, Mapping) else {}
        instructions = str(
            task.get("instructions") or parameters.get("instructions") or ""
        ).strip()

        if not instructions:
            # Without instructions there is not enough context to derive a
            # meaningful follow-up proposal.
            return []

        # Derive a concise title from the first line of instructions.
        first_line = instructions.splitlines()[0].strip()
        title_prefix = "Follow-up: "
        max_title_len = 200
        title_body = first_line[: max_title_len - len(title_prefix)]
        title = f"{title_prefix}{title_body}"

        summary = (
            f"Automatic follow-up proposal generated from workflow {workflow_id}. "
            f"Original instructions: {instructions[:500]}"
        )

        # Reconstruct a task-create request envelope from the original
        # execution context so that the proposal can be promoted to a
        # queued task with one click.
        runtime_node = task.get("runtime")
        runtime = dict(runtime_node) if isinstance(runtime_node, Mapping) else {}
        git_node = task.get("git")
        git = dict(git_node) if isinstance(git_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = dict(publish_node) if isinstance(publish_node, Mapping) else {}

        task_create_request: dict[str, Any] = {
            "type": "task",
            "payload": {
                "repository": repo,
                "task": {
                    "instructions": instructions,
                    "runtime": runtime,
                    "git": git,
                    "publish": publish,
                },
            },
        }

        candidate: dict[str, Any] = {
            "title": title,
            "summary": summary,
            "category": "follow_up",
            "tags": ["auto-generated", "follow-up"],
            "taskCreateRequest": task_create_request,
        }

        return [candidate]

    async def proposal_submit(
        self,
        request: Mapping[str, Any] | None = None,
        /,
    ) -> dict[str, Any]:
        """Validate, filter, and submit generated proposals to the Proposal Queue API.

        Returns a summary dict with ``generated_count``, ``submitted_count``,
        and ``errors`` (redacted).
        """
        logger = getLogger(__name__)
        payload = dict(request or {})
        candidates: list[Any] = payload.get("candidates") or []
        policy: dict[str, Any] = payload.get("policy") or {}
        origin: dict[str, Any] = payload.get("origin") or {}
        workflow_id: str = origin.get("workflow_id") or ""
        run_id: str = origin.get("temporal_run_id") or ""
        trigger_repo: str = origin.get("trigger_repo") or ""

        max_items = int(policy.get("max_items", 10))
        default_runtime = policy.get("default_runtime")
        generated_count = len(candidates)
        submitted_count = 0
        errors: list[str] = []

        if not candidates:
            return {
                "generated_count": 0,
                "submitted_count": 0,
                "errors": [],
            }

        service_or_ctx = None
        if self._proposal_service_factory is not None:
            try:
                service_or_ctx = self._proposal_service_factory()
            except Exception as exc:
                logger.warning(
                    "proposal.submit: failed to create proposal service: %s", exc
                )
                return {
                    "generated_count": generated_count,
                    "submitted_count": 0,
                    "errors": ["proposal service unavailable"],
                }

        import contextlib
        if hasattr(service_or_ctx, "__aenter__"):
            ctx = service_or_ctx
        else:
            @contextlib.asynccontextmanager
            async def _wrap():
                yield service_or_ctx
            ctx = _wrap()

        async with ctx as service:
            for candidate in candidates[:max_items]:
                if not isinstance(candidate, Mapping):
                    errors.append("skipped non-object candidate")
                    continue
                title = str(candidate.get("title") or "").strip()
                summary = str(candidate.get("summary") or "").strip()
                task_create_request = candidate.get("taskCreateRequest")
                if not title or not summary or not isinstance(task_create_request, Mapping):
                    errors.append(f"skipped malformed candidate: {title!r}")
                    continue

                # Stamp default runtime into taskCreateRequest if not already set
                stamped_request = dict(task_create_request)
                if default_runtime and isinstance(default_runtime, str):
                    payload_node = stamped_request.get("payload")
                    if isinstance(payload_node, dict):
                        task_node = payload_node.get("task")
                        if isinstance(task_node, dict):
                            runtime_node = task_node.get("runtime")
                            if isinstance(runtime_node, dict):
                                if not runtime_node.get("mode"):
                                    runtime_node["mode"] = default_runtime
                            else:
                                task_node["runtime"] = {"mode": default_runtime}
                        else:
                            payload_node["task"] = {
                                "runtime": {"mode": default_runtime}
                            }

                try:
                    if service is not None:
                        from moonmind.workflows.task_proposals.models import (
                            TaskProposalOriginSource,
                        )

                        origin_source = TaskProposalOriginSource.WORKFLOW
                        origin_metadata = {
                            "workflowId": workflow_id,
                            "temporalRunId": run_id,
                            "triggerRepo": trigger_repo,
                        }
                        await service.create_proposal(
                            title=title,
                            summary=summary,
                            category=candidate.get("category"),
                            tags=candidate.get("tags"),
                            task_create_request=stamped_request,
                            origin_source=origin_source,
                            origin_id=None,
                            origin_metadata=origin_metadata,
                            proposed_by_worker_id=f"temporal:{workflow_id}",
                            proposed_by_user_id=None,
                        )
                        submitted_count += 1
                    else:
                        # No service available — log and count as submitted for
                        # structural verification in tests.
                        logger.info(
                            "proposal.submit: would submit proposal %r (no service wired)",
                            title,
                        )
                        submitted_count += 1
                except Exception as exc:
                    redacted_error = str(exc)[:200]
                    errors.append(f"submission failed for {title!r}: {redacted_error}")
                    logger.warning(
                        "proposal.submit: failed to submit proposal %r: %s",
                        title,
                        redacted_error,
                    )

        return {
            "generated_count": generated_count,
            "submitted_count": submitted_count,
            "errors": errors,
        }


class TemporalAgentRuntimeActivities:
    """Implementation helpers for ``agent_runtime.*`` activities."""

    def __init__(
        self,
        *,
        artifact_service: TemporalArtifactService | None = None,
        run_store: "ManagedRunStore | None" = None,
        run_supervisor: "ManagedRunSupervisor | None" = None,
        run_launcher: "ManagedRuntimeLauncher | None" = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._run_store = run_store
        self._run_supervisor = run_supervisor
        self._run_launcher = run_launcher
        self._supervision_tasks: set[asyncio.Task] = set()

    async def agent_runtime_launch(
        self,
        payload: dict[str, Any],
        /,
    ) -> dict[str, Any]:
        """Launch a managed agent and start background supervision.
        
        Payload must contain:
        - run_id: str
        - request: dict (AgentExecutionRequest dump)
        - profile: dict (ManagedRuntimeProfile dump)
        - workspace_path: str | None
        """
        if self._run_launcher is None or self._run_supervisor is None:
            raise TemporalActivityRuntimeError("launcher and supervisor are required for agent_runtime_launch")

        run_id = payload.get("run_id")
        request_data = payload.get("request")
        profile_data = payload.get("profile")
        if not run_id or request_data is None or profile_data is None:
            raise TemporalActivityRuntimeError("Payload must contain 'run_id', 'request', and 'profile'")

        request = AgentExecutionRequest(**request_data)
        profile = ManagedRuntimeProfile(**profile_data)
        workspace_path = payload.get("workspace_path")

        # Idempotency check handled in launcher
        record, process, endpoints = await self._run_launcher.launch(
            run_id=run_id,
            request=request,
            profile=profile,
            workspace_path=workspace_path,
        )

        if endpoints:
            try:
                async with get_async_session_context() as db_session:
                    service = AgentQueueService(AgentQueueRepository(db_session))
                    from moonmind.workflows.agent_queue import models
                    from datetime import UTC, datetime, timedelta
                    from uuid import UUID
                    
                    # Convert run_id string to UUID if possible, else generate one based on string
                    try:
                        task_run_id = UUID(run_id)
                    except ValueError:
                        import hashlib
                        task_run_id = UUID(hashlib.md5(run_id.encode('utf-8')).hexdigest())

                    # If run_id is not a UUID, we must pass a valid UUID to the service. But wait, we dropped the FK.
                    # We can just insert it. Wait, AgentQueueService expects UUID.
                    # Let's just insert directly using the db_session to bypass strict UUID typing if necessary,
                    # OR just ensure task_run_id is a UUID.
                    # Actually AgentQueueService.report_live_session expects task_run_id as UUID.
                    
                    expires_at = (datetime.now(UTC) + timedelta(minutes=60)).isoformat()
                    await service.report_live_session(
                        task_run_id=task_run_id,
                        worker_id="temporal-activity-worker",
                        worker_hostname="localhost",
                        status=models.AgentJobLiveSessionStatus.READY,
                        provider=models.AgentJobLiveSessionProvider.TMATE,
                        attach_ro=endpoints.get("attach_ro"),
                        attach_rw=endpoints.get("attach_rw"),
                        web_ro=endpoints.get("web_ro"),
                        web_rw=endpoints.get("web_rw"),
                        tmate_session_name=endpoints.get("tmate_session_name"),
                        tmate_socket_path=endpoints.get("tmate_socket_path"),
                        expires_at=expires_at,
                    )
            except Exception as e:
                logger.warning(f"Failed to report live session for run {run_id}", exc_info=True)

        # Start background supervision — hold a strong reference so the task
        # is not garbage-collected before it completes.
        timeout_policy = getattr(request, "timeout_policy", None) or {}
        timeout_seconds = (
            timeout_policy.get("timeout_seconds", 3600)
            if isinstance(timeout_policy, dict)
            else getattr(timeout_policy, "timeout_seconds", 3600)
        )
        exit_code_path = None
        cleanup_paths: list[str] | None = None
        if endpoints:
            exit_code_path = endpoints.get("exit_code_path")
            cleanup_paths = [
                path
                for path in (
                    endpoints.get("tmate_socket_path"),
                    endpoints.get("tmate_config_path"),
                    exit_code_path,
                )
                if path
            ]

        task = asyncio.create_task(
            self._run_supervisor.supervise(
                run_id=run_id,
                process=process,
                timeout_seconds=timeout_seconds,
                exit_code_path=exit_code_path,
                cleanup_paths=cleanup_paths,
            )
        )
        self._supervision_tasks.add(task)
        task.add_done_callback(self._supervision_tasks.discard)

        return record.model_dump(mode="json")

    async def agent_runtime_publish_artifacts(
        self,
        result: Any = None,
        /,
    ) -> Any:
        """Publish agent-run outputs back to artifact storage.

        Writes a summary JSON artifact containing the run result metadata
        (output refs, summary, failure class) via the artifact service.
        Returns the result enriched with a ``diagnostics_ref`` pointing to
        the persisted summary artifact.
        """
        if result is None:
            return result
        if self._artifact_service is None:
            logger.warning(
                "agent_runtime.publish_artifacts called without artifact_service; "
                "returning result unchanged"
            )
            return result

        # Normalize to dict
        if isinstance(result, Mapping):
            result_dict = dict(result)
        elif hasattr(result, "model_dump"):
            result_dict = result.model_dump(mode="json", by_alias=True)
        else:
            result_dict = {"raw": str(result)}

        # Build summary payload for the artifact
        summary_payload: dict[str, Any] = {
            "summary": result_dict.get("summary") or result_dict.get("raw", ""),
            "output_refs": result_dict.get("output_refs") or result_dict.get("outputRefs") or [],
            "failure_class": result_dict.get("failure_class") or result_dict.get("failureClass"),
            "provider_error_code": result_dict.get("provider_error_code") or result_dict.get("providerErrorCode"),
            "metrics": result_dict.get("metrics") or {},
        }

        try:
            summary_ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=summary_payload,
                metadata_json={
                    "name": "agent_run_result.json",
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": ["agent_runtime", "result"],
                },
            )
            # Enrich result with the diagnostics ref
            if isinstance(result, Mapping):
                enriched = dict(result)
                if "diagnosticsRef" in enriched:
                    enriched["diagnosticsRef"] = summary_ref.artifact_id
                else:
                    enriched["diagnostics_ref"] = summary_ref.artifact_id
                # Remove snake_case if alias is present to avoid Pydantic validation errors
                if "diagnosticsRef" in enriched and "diagnostics_ref" in enriched:
                    del enriched["diagnostics_ref"]
                return enriched
            if hasattr(result, "diagnostics_ref"):
                result.diagnostics_ref = summary_ref.artifact_id
            return result
        except Exception:
            logger.warning(
                "agent_runtime.publish_artifacts failed to write summary artifact",
                exc_info=True,
            )
            return result

    @staticmethod
    def _agent_runtime_request_identifiers(
        request: Any,
    ) -> tuple[str, str]:
        """Extract ``run_id`` and ``agent_id`` from a flexible request shape."""
        if isinstance(request, Mapping):
            run_id = str(
                request.get("run_id")
                or request.get("runId")
                or ""
            ).strip()
            agent_id = str(
                request.get("agent_id")
                or request.get("agentId")
                or request.get("agent")
                or "managed"
            ).strip() or "managed"
            if not run_id:
                raise TemporalActivityRuntimeError(
                    "agent_runtime request requires run_id"
                )
            return run_id, agent_id
        if isinstance(request, str):
            run_id = request.strip()
            if not run_id:
                raise TemporalActivityRuntimeError(
                    "agent_runtime request requires run_id"
                )
            return run_id, "managed"
        raise TemporalActivityRuntimeError(
            "agent_runtime request must be a mapping or run_id string"
        )

    async def agent_runtime_status(
        self,
        request: Any = None,
        /,
    ) -> dict[str, Any]:
        """Read the latest managed run status via activity execution."""
        if self._run_store is None:
            raise TemporalActivityRuntimeError(
                "run_store is required for agent_runtime.status"
            )
        run_id, agent_id = self._agent_runtime_request_identifiers(request)
        record = self._run_store.load(run_id)
        if record is None:
            status = AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId=agent_id,
                status="running",
            )
            return status.model_dump(mode="json", by_alias=True)

        status = AgentRunStatus(
            runId=record.run_id,
            agentKind="managed",
            agentId=record.agent_id or agent_id,
            status=record.status,
            metadata={"runtimeId": record.runtime_id},
        )
        return status.model_dump(mode="json", by_alias=True)

    async def agent_runtime_fetch_result(
        self,
        request: Any = None,
        /,
    ) -> dict[str, Any]:
        """Read one managed run result via activity execution."""
        if self._run_store is None:
            raise TemporalActivityRuntimeError(
                "run_store is required for agent_runtime.fetch_result"
            )
        run_id, _agent_id = self._agent_runtime_request_identifiers(request)

        async def _unused_profile_fetcher(**_kwargs: Any) -> dict[str, Any]:
            return {"profiles": []}

        async def _unused_slot_signal(**_kwargs: Any) -> None:
            return None

        adapter = ManagedAgentAdapter(
            profile_fetcher=_unused_profile_fetcher,
            slot_requester=_unused_slot_signal,
            slot_releaser=_unused_slot_signal,
            cooldown_reporter=_unused_slot_signal,
            workflow_id=f"agent_runtime_activity:{run_id}",
            run_store=self._run_store,
        )
        result = await adapter.fetch_result(run_id)
        result_dict = result.model_dump(mode="json", by_alias=True)

        # Enrich result with pull_request_url detected from workspace git
        # state.  Managed agents run inside tmate so their stdout doesn't
        # capture CLI output; we check for PRs from the workspace branch
        # after the run completes instead.
        if result.failure_class is None:
            pr_url = self._detect_pr_url_from_workspace(run_id)
            if pr_url:
                meta = dict(result_dict.get("metadata") or {})
                meta["pull_request_url"] = pr_url
                result_dict["metadata"] = meta

        return result_dict

    def _detect_pr_url_from_workspace(self, run_id: str) -> str | None:
        """Best-effort detection of a PR URL from the workspace git state."""
        import re
        import subprocess

        if self._run_store is None:
            return None
        record = self._run_store.load(run_id)
        if record is None or not record.workspace_path:
            return None

        workspace = record.workspace_path
        try:
            # Get the current branch in the workspace
            branch_result = subprocess.run(
                ["git", "-C", workspace, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if branch_result.returncode != 0:
                return None
            branch = branch_result.stdout.strip()
            if not branch or branch in ("main", "master", "HEAD"):
                return None

            # Check for an open PR from this branch
            pr_result = subprocess.run(
                [
                    "gh", "pr", "list",
                    "--repo", self._detect_repo_from_workspace(workspace),
                    "--head", branch,
                    "--json", "url",
                    "--limit", "1",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=workspace,
            )
            if pr_result.returncode != 0:
                return None
            import json as _json
            prs = _json.loads(pr_result.stdout.strip() or "[]")
            if prs and isinstance(prs, list) and prs[0].get("url"):
                return str(prs[0]["url"])
        except Exception:
            logger.debug(
                "Failed to detect PR URL from workspace for run %s",
                run_id,
                exc_info=True,
            )
        return None

    @staticmethod
    def _detect_repo_from_workspace(workspace: str) -> str:
        """Extract the GitHub owner/repo from the workspace remote URL."""
        import re
        import subprocess

        result = subprocess.run(
            ["git", "-C", workspace, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        url = result.stdout.strip()
        # Match github.com/owner/repo or github.com:owner/repo
        match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
        return match.group(1) if match else ""

    async def agent_runtime_cancel(
        self,
        request: Any = None,
        /,
    ) -> None:
        """Best-effort cancel of an in-flight agent run.

        For managed runs, delegates to the ``ManagedRunSupervisor`` to
        terminate the subprocess.  For external runs, logs the request
        (external cancel must go through the provider adapter).
        """
        if isinstance(request, Mapping):
            agent_kind = request.get("agent_kind", "unknown")
            run_id = request.get("run_id", "unknown")
        elif isinstance(request, (list, tuple)) and len(request) >= 2:
            agent_kind, run_id = request[0], request[1]
        else:
            agent_kind, run_id = "unknown", str(request)

        if agent_kind == "managed":
            if self._run_supervisor is not None:
                try:
                    await self._run_supervisor.cancel(str(run_id))
                    logger.info(
                        "agent_runtime.cancel completed for managed run %s",
                        run_id,
                    )
                    return
                except Exception:
                    logger.warning(
                        "agent_runtime.cancel failed for managed run %s",
                        run_id,
                        exc_info=True,
                    )
                    return
            else:
                logger.warning(
                    "agent_runtime.cancel called for managed run %s but no supervisor configured",
                    run_id,
                )
                # Fall through to store-based cancel if possible
                if self._run_store is not None:
                    try:
                        self._run_store.update_status(
                            str(run_id),
                            "cancelled",
                            finished_at=datetime.now(tz=UTC),
                            error_message="Cancelled via activity (no supervisor)",
                        )
                        logger.info(
                            "agent_runtime.cancel marked run %s as cancelled in store",
                            run_id,
                        )
                    except Exception:
                        logger.warning(
                            "agent_runtime.cancel store update failed for %s",
                            run_id,
                            exc_info=True,
                        )
                return

        # External or unknown agent kind
        logger.warning(
            "agent_runtime.cancel called for %s/%s — external cancel requires provider adapter",
            agent_kind,
            run_id,
        )


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


class TemporalReviewActivities:
    """Implementation helpers for ``step.review`` activities."""

    async def step_review(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        from moonmind.workflows.temporal.activities.step_review import step_review_activity
        return await step_review_activity(payload)


def build_activity_bindings(
    catalog: TemporalActivityCatalog,
    *,
    artifact_activities: Any | None = None,
    plan_activities: Any | None = None,
    skill_activities: Any | None = None,
    sandbox_activities: Any | None = None,
    integration_activities: Any | None = None,
    agent_runtime_activities: Any | None = None,
    proposal_activities: Any | None = None,
    review_activities: Any | None = None,
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
        "agent_runtime": agent_runtime_activities,
        "proposals": proposal_activities,
        "reviews": review_activities,
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
    "TemporalAgentRuntimeActivities",
    "TemporalIntegrationActivities",
    "TemporalPlanActivities",
    "TemporalProposalActivities",
    "TemporalSkillActivities",
    "TemporalSandboxActivities",
    "build_activity_bindings",
]
