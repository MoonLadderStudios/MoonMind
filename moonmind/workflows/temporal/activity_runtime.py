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
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence, get_type_hints

from moonmind.config.settings import settings
from moonmind.jules.status import JulesStatusSnapshot, normalize_jules_status
from moonmind.schemas.manifest_ingest_models import CompiledManifestPlanModel
from moonmind.schemas.temporal_activity_models import (
    PlanGenerateInput,
)
from moonmind.workflows.tasks.routing import _coerce_bool
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.adapters.managed_agent_adapter import ManagedAgentAdapter
from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.codex_cloud_agent_adapter import CodexCloudAgentAdapter
from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClient as CodexCloudHttpClient
from moonmind.codex_cloud.settings import build_codex_cloud_gate, CODEX_CLOUD_DISABLED_MESSAGE
from moonmind.workflows.adapters.jules_client import JulesClient


from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunStatus,
    AgentRunResult,
    ManagedRunRecord,
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
    compute_registry_digest,
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



class CmdRes:
    def __init__(self, stdout_bytes: bytes):
        self._stdout_bytes = stdout_bytes

    @property
    def stdout(self) -> str:
        return self._stdout_bytes.decode('utf-8', errors='replace')

async def _run_command(cmd, **kwargs):
    check = kwargs.pop("check", True)
    kwargs["stdout"] = asyncio.subprocess.PIPE
    kwargs["stderr"] = asyncio.subprocess.PIPE
    proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    stdout, stderr = await proc.communicate()
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd} {stderr.decode('utf-8', errors='replace')}")
    return CmdRes(stdout)

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
_GEMINI_ERROR_REPORT_DIR = Path("/tmp")
_GEMINI_ERROR_REPORT_GLOB = "gemini-client-error-*.json"
_GEMINI_ERROR_REPORT_TIME_PADDING_SECONDS = 45
_GEMINI_QUOTA_MARKERS: tuple[str, ...] = (
    "terminalquotaerror",
    "quota_exhausted",
    "exhausted your capacity",
    "quota will reset after",
)
_GEMINI_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "rate limit",
    "too many requests",
    " 429",
    "code: 429",
)
_OPERATOR_SUMMARY_TAIL_BYTES = 64 * 1024
_PUBLISH_GIT_ADD_EXCLUDES: tuple[str, ...] = (
    ":(exclude)CLAUDE.md",
    ":(exclude)live_streams.spool",
    ":(exclude).agents/skills/active",
)


def _managed_runtime_artifact_root() -> Path:
    return managed_runtime_artifact_root()


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
    "execution.dependency_status_snapshot": (
        "artifacts",
        "execution_dependency_status_snapshot",
    ),
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
    "provider_profile.list": ("artifacts", "provider_profile_list"),
    "provider_profile.ensure_manager": ("artifacts", "provider_profile_ensure_manager"),
    "provider_profile.reset_manager": ("artifacts", "provider_profile_reset_manager"),
    "provider_profile.verify_lease_holders": (
        "artifacts",
        "provider_profile_verify_lease_holders",
    ),
    "provider_profile.sync_slot_leases": (
        "artifacts",
        "provider_profile_sync_slot_leases",
    ),
    "oauth_session.ensure_volume": ("artifacts", "oauth_session_ensure_volume"),
    "oauth_session.start_auth_runner": ("artifacts", "oauth_session_start_auth_runner"),
    "oauth_session.update_terminal_session": ("artifacts", "oauth_session_update_terminal_session"),
    "oauth_session.stop_auth_runner": ("artifacts", "oauth_session_stop_auth_runner"),
    "oauth_session.update_status": ("artifacts", "oauth_session_update_status"),
    "oauth_session.mark_failed": ("artifacts", "oauth_session_mark_failed"),
    "oauth_session.cleanup_stale": ("artifacts", "oauth_session_cleanup_stale"),
    "oauth_session.verify_volume": ("artifacts", "oauth_session_verify_volume"),
    "oauth_session.verify_cli_fingerprint": ("artifacts", "oauth_session_verify_cli_fingerprint"),
    "oauth_session.register_profile": ("artifacts", "oauth_session_register_profile"),
    "integration.jules.start": ("integrations", "integration_jules_start"),
    "integration.jules.status": ("integrations", "integration_jules_status"),
    "integration.jules.fetch_result": (
        "integrations",
        "integration_jules_fetch_result",
    ),
    "integration.jules.cancel": ("integrations", "integration_jules_cancel"),
    "integration.jules.send_message": ("integrations", "integration_jules_send_message"),
    "integration.jules.list_activities": ("integrations", "integration_jules_list_activities"),
    "integration.jules.answer_question": ("integrations", "integration_jules_answer_question"),
    "integration.jules.get_auto_answer_config": ("integrations", "integration_jules_get_auto_answer_config"),
    # General-purpose repo operations (provider-agnostic)
    "repo.create_pr": ("integrations", "repo_create_pr"),
    "repo.merge_pr": ("integrations", "repo_merge_pr"),
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
    "agent_skill.resolve": ("agent_skills", "resolve_skills"),
    "agent_skill.build_prompt_index": ("agent_skills", "build_prompt_index"),
    "agent_skill.materialize": ("agent_skills", "materialize"),
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
    raw_skills: Any
    if "tools" in payload:
        raw_skills = payload.get("tools")
    elif "skills" in payload:
        raw_skills = payload.get("skills")
    else:
        raw_skills = payload

    if isinstance(raw_skills, list) and not raw_skills:
        skills = ()
        digest = compute_registry_digest(skills=skills)
    else:
        skills = parse_skill_registry(payload)
        digest_only = create_registry_snapshot(
            skills=skills,
            artifact_store=InMemoryArtifactStore(),
        )
        digest = digest_only.digest

    return SkillRegistrySnapshot(
        digest=digest,
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
    selected: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

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

    # 'auto' is a placeholder meaning "no explicit skill selected". It should
    # not be included in the registry as a dispatchable skill — when only 'auto'
    # is present, the runtime should be used directly without skill dispatch.
    if selected and all(name == "auto" for name, _ in selected):
        selected = []

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
        request: Mapping[str, Any] | PlanGenerateInput | None = None,
        /,
        *,
        principal: str | None = None,
        inputs_ref: ArtifactRef | str | None = None,
        parameters: Mapping[str, Any] | None = None,
        registry_snapshot_ref: ArtifactRef | str | None = None,
        execution_ref: ExecutionRef | dict[str, Any] | None = None,
    ) -> PlanGenerateActivityResult:
        if isinstance(request, PlanGenerateInput):
            # Model fast path
            if principal is None:
                principal = request.principal
            if inputs_ref is None and request.inputs_ref is not None:
                inputs_ref = getattr(request.inputs_ref, "artifact_id", request.inputs_ref)
            if parameters is None:
                parameters = request.parameters
            if registry_snapshot_ref is None and request.registry_snapshot_ref is not None:
                registry_snapshot_ref = getattr(request.registry_snapshot_ref, "artifact_id", request.registry_snapshot_ref)
            if execution_ref is None:
                execution_ref = request.execution_ref
        else:
            request_payload = _coerce_activity_request(
                request, activity_type="plan.generate"
            )
            if request_payload:
                try:
                    # Validate legacy dictionary through the new model
                    model = PlanGenerateInput.model_validate(request_payload)
                    if principal is None:
                        principal = model.principal
                    if inputs_ref is None and model.inputs_ref is not None:
                        inputs_ref = getattr(model.inputs_ref, "artifact_id", model.inputs_ref)
                    if parameters is None:
                        parameters = model.parameters
                    if registry_snapshot_ref is None and model.registry_snapshot_ref is not None:
                        registry_snapshot_ref = getattr(model.registry_snapshot_ref, "artifact_id", model.registry_snapshot_ref)
                    if execution_ref is None:
                        execution_ref = model.execution_ref
                except Exception as e:
                    logger.warning("Failed to parse plan.generate legacy payload as PlanGenerateInput: %s", e)
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
        idempotency_key: str | None = None,
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
        if token not in {"queued", "running", "completed", "failed", "canceled", "unknown"}:
            return snapshot

        terminal = token in {"completed", "failed", "canceled"}
        return JulesStatusSnapshot(
            provider_status=snapshot.provider_status,
            provider_status_token=snapshot.provider_status_token,
            normalized_status=token,
            terminal=terminal,
            succeeded=token == "completed",
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

    async def integration_jules_start(self, request, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_start_activity
        return await jules_start_activity(request)

    async def integration_jules_status(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_status_activity
        run_id = payload
        if isinstance(payload, Mapping):
            run_id = payload.get("external_id") or payload.get("externalId") or payload.get("run_id") or payload.get("runId")
        if not run_id or not isinstance(run_id, str):
            raise TemporalActivityRuntimeError("integration.jules.status requires a non-empty run_id string")
        return await jules_status_activity(run_id.strip())

    async def integration_jules_fetch_result(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_fetch_result_activity
        run_id = payload
        if isinstance(payload, Mapping):
            run_id = payload.get("external_id") or payload.get("externalId") or payload.get("run_id") or payload.get("runId")
        if not run_id or not isinstance(run_id, str):
            raise TemporalActivityRuntimeError("integration.jules.fetch_result requires a non-empty run_id string")
        return await jules_fetch_result_activity(run_id.strip())

    async def integration_jules_cancel(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_cancel_activity
        run_id = payload
        if isinstance(payload, Mapping):
            run_id = payload.get("external_id") or payload.get("externalId") or payload.get("run_id") or payload.get("runId")
        if not run_id or not isinstance(run_id, str):
            raise TemporalActivityRuntimeError("integration.jules.cancel requires a non-empty run_id string")
        return await jules_cancel_activity(run_id.strip())

    async def repo_create_pr(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import repo_create_pr_activity
        return await repo_create_pr_activity(payload)

    async def repo_merge_pr(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import repo_merge_pr_activity
        return await repo_merge_pr_activity(payload)

    async def integration_jules_send_message(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_send_message_activity
        return await jules_send_message_activity(payload)

    async def integration_jules_list_activities(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_list_activities_activity
        session_id = payload
        if isinstance(payload, Mapping):
            session_id = payload.get("session_id") or payload.get("sessionId")
        if not session_id or not isinstance(session_id, str):
            raise TemporalActivityRuntimeError("integration.jules.list_activities requires a non-empty session_id string")
        return await jules_list_activities_activity(session_id.strip())

    async def integration_jules_answer_question(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_answer_question_activity
        return await jules_answer_question_activity(payload)

    async def integration_jules_get_auto_answer_config(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_get_auto_answer_config_activity
        return await jules_get_auto_answer_config_activity(payload)

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

    async def integration_codex_cloud_start(self, request, /, **kwargs):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import codex_cloud_start_activity
        return await codex_cloud_start_activity(request)

    async def integration_codex_cloud_status(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import codex_cloud_status_activity
        run_id = payload
        if isinstance(payload, Mapping):
            run_id = payload.get("external_id") or payload.get("externalId") or payload.get("run_id") or payload.get("runId")
        if not run_id or not isinstance(run_id, str):
            raise TemporalActivityRuntimeError("integration.codex_cloud.status requires a non-empty run_id string")
        return await codex_cloud_status_activity(run_id.strip())

    async def integration_codex_cloud_fetch_result(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import codex_cloud_fetch_result_activity
        run_id = payload
        if isinstance(payload, Mapping):
            run_id = payload.get("external_id") or payload.get("externalId") or payload.get("run_id") or payload.get("runId")
        if not run_id or not isinstance(run_id, str):
            raise TemporalActivityRuntimeError("integration.codex_cloud.fetch_result requires a non-empty run_id string")
        return await codex_cloud_fetch_result_activity(run_id.strip())

    async def integration_codex_cloud_cancel(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import codex_cloud_cancel_activity
        run_id = payload
        if isinstance(payload, Mapping):
            run_id = payload.get("external_id") or payload.get("externalId") or payload.get("run_id") or payload.get("runId")
        if not run_id or not isinstance(run_id, str):
            raise TemporalActivityRuntimeError("integration.codex_cloud.cancel requires a non-empty run_id string")
        return await codex_cloud_cancel_activity(run_id.strip())

    async def integration_openclaw_execute(self, request, /, **kwargs):
        from moonmind.workflows.temporal.activities.openclaw_activities import openclaw_execute_activity
        from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

        if isinstance(request, Mapping):
            request_payload = _coerce_activity_request(request, activity_type="integration.openclaw.execute")
            if not request_payload:
                raise TemporalActivityRuntimeError("integration.openclaw.execute requires AgentExecutionRequest payload")
            req = AgentExecutionRequest.model_validate(request_payload)
        elif isinstance(request, AgentExecutionRequest):
            req = request
        else:
            raise TemporalActivityRuntimeError("integration.openclaw.execute requires AgentExecutionRequest payload")
            
        return await openclaw_execute_activity(req)

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

    @staticmethod
    def _resolve_task_instructions(parameters: Mapping[str, Any]) -> str:
        task_node = parameters.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}

        instructions = str(
            task.get("instructions") or parameters.get("instructions") or ""
        ).strip()
        if instructions:
            return instructions

        steps = task.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                step_instructions = str(step.get("instructions") or "").strip()
                if step_instructions:
                    return step_instructions
        return ""

    @staticmethod
    def _normalize_proposal_text(value: object) -> str:
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            text = str(value).strip()
        else:
            text = ""
        text = re.sub(r"\s+", " ", text)
        return text

    @classmethod
    def _comparison_key(cls, value: object) -> str:
        normalized = cls._normalize_proposal_text(value).lower()
        return re.sub(r"[^a-z0-9]+", " ", normalized).strip()

    @classmethod
    def _resolve_proposal_idea(
        cls,
        *,
        payload: Mapping[str, Any],
        parameters: Mapping[str, Any],
        task: Mapping[str, Any],
        instructions: str,
    ) -> str:
        result_node = payload.get("result")
        result = result_node if isinstance(result_node, Mapping) else {}
        keys = (
            "proposalTitle",
            "proposalIdea",
            "suggestedTitle",
            "titleSuggestion",
            "recommendedNextAction",
            "nextAction",
            "nextStep",
            "next_step",
        )
        candidate_sources = (payload, result, parameters, task)
        workflow_texts = {
            cls._comparison_key(task.get("title")),
            cls._comparison_key(parameters.get("title")),
            cls._comparison_key(instructions.splitlines()[0] if instructions else ""),
            cls._comparison_key(instructions),
        }
        workflow_texts.discard("")

        for source in candidate_sources:
            for key in keys:
                idea = cls._normalize_proposal_text(source.get(key))
                if not idea:
                    continue
                if cls._comparison_key(idea) in workflow_texts:
                    continue
                return idea
        return ""

    @classmethod
    def _build_follow_up_instructions(cls, proposal_idea: str, instructions: str) -> str:
        normalized_idea = cls._normalize_proposal_text(proposal_idea)
        normalized_instructions = cls._normalize_proposal_text(instructions)
        if not normalized_instructions:
            return normalized_idea
        if cls._comparison_key(normalized_idea) == cls._comparison_key(
            normalized_instructions
        ):
            return normalized_idea
        return (
            f"{normalized_idea}\n\n"
            "Context from the completed task:\n"
            f"{normalized_instructions}"
        )

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

        task_node = parameters.get("task")
        task = dict(task_node) if isinstance(task_node, Mapping) else {}
        instructions = self._resolve_task_instructions(parameters)

        proposal_idea = self._resolve_proposal_idea(
            payload=payload,
            parameters=parameters,
            task=task,
            instructions=instructions,
        )

        if not proposal_idea:
            # Do not create generic proposals whose title simply repeats the
            # completed workflow. The fallback path only emits a proposal when
            # it receives an explicit next-step idea from upstream context.
            return []

        normalized_title = proposal_idea
        if not normalized_title.lower().startswith("[run_quality]"):
            normalized_title = f"[run_quality] {normalized_title}"
        if len(normalized_title) > 194:
            normalized_title = normalized_title[:194].rstrip()

        summary = (
            f"Automatic follow-up proposal generated from workflow {workflow_id}. "
            f"Proposed next step: {proposal_idea[:500]}"
        )
        follow_up_instructions = self._build_follow_up_instructions(
            proposal_idea, instructions
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
                    "instructions": follow_up_instructions,
                    "runtime": runtime,
                    "git": git,
                    "publish": publish,
                },
            },
        }

        candidate: dict[str, Any] = {
            "title": normalized_title,
            "summary": summary,
            "category": "run_quality",
            "tags": ["artifact_gap", "auto-generated", "follow_up"],
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

        from moonmind.workflows.tasks.task_contract import (
            TaskProposalPolicy,
            build_effective_proposal_policy,
        )

        parsed_policy: TaskProposalPolicy | None = None
        if isinstance(policy, Mapping) and policy:
            try:
                parsed_policy = TaskProposalPolicy.model_validate(policy)
            except Exception as exc:
                logger.warning("proposal.submit: invalid proposal policy: %s", exc)

        effective_policy = build_effective_proposal_policy(
            policy=parsed_policy,
            default_targets=getattr(
                settings.task_proposals,
                "proposal_targets_default",
                "project",
            ),
            default_max_items_project=getattr(
                settings.task_proposals,
                "max_items_project_default",
                3,
            ),
            default_max_items_moonmind=getattr(
                settings.task_proposals,
                "max_items_moonmind_default",
                2,
            ),
            default_moonmind_severity_floor=getattr(
                settings.task_proposals,
                "moonmind_severity_floor_default",
                "high",
            ),
            severity_vocabulary=getattr(
                settings.task_proposals,
                "severity_vocabulary",
                None,
            ),
        )
        default_runtime = parsed_policy.default_runtime if parsed_policy else None
        moonmind_repo = str(
            getattr(settings.task_proposals, "moonmind_ci_repository", "") or ""
        ).strip()
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
            for candidate in candidates:
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

                payload_node = stamped_request.get("payload")
                target_repo = ""
                if isinstance(payload_node, Mapping):
                    target_repo = str(payload_node.get("repository") or "").strip()
                
                should_submit = False
                if effective_policy.consume_project_slot():
                    should_submit = True
                else:
                    severity = str(candidate.get("severity") or "medium")
                    is_moonmind_repo = (
                        bool(moonmind_repo)
                        and target_repo.lower() == moonmind_repo.lower()
                    )
                    if (
                        is_moonmind_repo
                        and effective_policy.severity_meets_floor(severity)
                        and effective_policy.consume_moonmind_slot()
                    ):
                        should_submit = True

                if not should_submit:
                    continue

                try:
                    if service is not None:
                        from moonmind.workflows.task_proposals.models import (
                            TaskProposalOriginSource,
                        )

                        origin_source = TaskProposalOriginSource.WORKFLOW
                        origin_metadata = {
                            "workflow_id": workflow_id,
                            "temporal_run_id": run_id,
                            "triggerRepo": trigger_repo,
                            "triggerJobId": run_id,
                            "signal": {"severity": "normal", "type": "follow_up"}
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

    async def _report_task_run_binding(self, workflow_id: str, run_id: str) -> None:
        """Persist the managed task-run UUID onto the execution record.

        Temporal execution detail uses ``workflow_id`` as the durable task
        handle, while managed-run observability artifacts are keyed by the
        runtime run UUID. Store that UUID on the execution record so the UI can
        resolve the corresponding observability APIs without guessing.
        """
        workflow_id = str(workflow_id or "").strip()
        run_id = str(run_id or "").strip()
        if not workflow_id or not run_id:
            return

        import uuid
        try:
            uuid.UUID(run_id)
        except ValueError:
            logger.warning(
                "run_id %r is not a valid UUID; skipping task run binding for workflow %s",
                run_id,
                workflow_id,
            )
            return

        from api_service.db.base import get_async_session_context
        from api_service.db.models import TemporalExecutionCanonicalRecord

        try:
            async with get_async_session_context() as db:
                record = await db.get(TemporalExecutionCanonicalRecord, workflow_id)
                if record is None:
                    logger.warning(
                        "workflow_id %s was not found; cannot persist task run binding",
                        workflow_id,
                    )
                    return
                memo = dict(record.memo or {})
                if memo.get("taskRunId") == run_id:
                    return
                memo["taskRunId"] = run_id
                record.memo = memo
                await db.commit()
        except Exception:
            logger.warning(
                "Failed to persist task run binding for workflow %s run %s",
                workflow_id,
                run_id,
                exc_info=True,
            )

    async def agent_runtime_launch(
        self,
        payload: dict[str, Any],
        /,
    ) -> dict[str, Any]:
        """Launch a managed agent and start background supervision.
        
        Payload must contain:
        - run_id: str
        - workflow_id: str | None
        - request: dict (AgentExecutionRequest dump)
        - profile: dict (ManagedRuntimeProfile dump)
        - workspace_path: str | None
        """
        if self._run_launcher is None or self._run_supervisor is None:
            raise TemporalActivityRuntimeError("launcher and supervisor are required for agent_runtime_launch")

        run_id = payload.get("run_id")
        workflow_id = str(payload.get("workflow_id") or "").strip()
        request_data = payload.get("request")
        profile_data = payload.get("profile")
        if not run_id or request_data is None or profile_data is None:
            raise TemporalActivityRuntimeError("Payload must contain 'run_id', 'request', and 'profile'")

        request = AgentExecutionRequest(**request_data)
        profile = ManagedRuntimeProfile(**profile_data)
        workspace_path = payload.get("workspace_path")

        env_overrides = dict(profile.env_overrides) if profile.env_overrides else {}
        ref = env_overrides.pop("MANAGED_API_KEY_REF", None)
        target_raw = env_overrides.pop("MANAGED_API_KEY_TARGET_ENV", None)
        # We no longer process MANAGED_API_KEY_REF here. It's handled by secret_refs in the launcher.

        profile = profile.model_copy(update={"env_overrides": env_overrides})

        # Idempotency check handled in launcher
        record, process, cleanup_paths, deferred_cleanup_paths = await self._run_launcher.launch(
            run_id=run_id,
            workflow_id=workflow_id or None,
            request=request,
            profile=profile,
            workspace_path=workspace_path,
        )

        if workflow_id:
            record_run_id = str(getattr(record, "run_id", "") or run_id).strip()
            await self._report_task_run_binding(workflow_id, record_run_id)

        if process is None:
            # Idempotent path: run is already active, skip secondary supervision
            return record.model_dump(mode="json")

        # Start background supervision — hold a strong reference so the task
        # is not garbage-collected before it completes.
        timeout_policy = getattr(request, "timeout_policy", None) or {}
        timeout_seconds = (
            timeout_policy.get("timeout_seconds", 3600)
            if isinstance(timeout_policy, dict)
            else getattr(timeout_policy, "timeout_seconds", 3600)
        )

        async def _supervise_and_publish():
            try:
                record = await self._run_supervisor.supervise(
                    run_id=run_id,
                    process=process,
                    timeout_seconds=timeout_seconds,
                    cleanup_paths=cleanup_paths or None,
                    deferred_cleanup_paths=deferred_cleanup_paths or None,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Supervisor failed for run %s", run_id, exc_info=True)
                await self._cleanup_managed_run_publish_support_best_effort(run_id)
                return



        task = asyncio.create_task(_supervise_and_publish())
        self._supervision_tasks.add(task)
        task.add_done_callback(self._supervision_tasks.discard)

        return record.model_dump(mode="json")

    async def agent_runtime_publish_artifacts(
        self,
        result: AgentRunResult | None = None,
        /,
    ) -> AgentRunResult | None:
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
        except Exception as exc:
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

    async def _cleanup_run_support_best_effort(self, run_id: str) -> None:
        if self._run_launcher is None:
            return
        try:
            await self._run_launcher.cleanup_run_support(run_id)
        except Exception:
            logger.warning(
                "Failed to cleanup run launcher support for run_id %s",
                run_id,
                exc_info=True,
            )

    def _cleanup_deferred_run_files_best_effort(self, run_id: str) -> None:
        if self._run_supervisor is None:
            return
        try:
            self._run_supervisor.cleanup_deferred_run_files(run_id)
        except Exception:
            logger.warning(
                "Failed to cleanup deferred run files for run_id %s",
                run_id,
                exc_info=True,
            )

    async def _cleanup_managed_run_publish_support_best_effort(self, run_id: str) -> None:
        await self._cleanup_run_support_best_effort(run_id)
        self._cleanup_deferred_run_files_best_effort(run_id)

    async def agent_runtime_status(
        self,
        request: Any = None,
        /,
    ) -> AgentRunStatus:
        """Read the latest managed run status via activity execution.

        Returns a canonical typed ``AgentRunStatus`` directly so that workflow
        code and the ``_coerce_managed_status_payload`` helper both receive a
        well-typed Pydantic model rather than a plain dict.
        """
        if self._run_store is None:
            raise TemporalActivityRuntimeError(
                "run_store is required for agent_runtime.status"
            )
        run_id, agent_id = self._agent_runtime_request_identifiers(request)

        from temporalio import activity
        if activity.in_activity():
            activity.heartbeat(f"Checking status for run_id {run_id}")

        record = self._run_store.load(run_id)
        if record is None:
            status = AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId=agent_id,
                status="running",
            )
            return status

        status = AgentRunStatus(
            runId=record.run_id,
            agentKind="managed",
            agentId=record.agent_id or agent_id,
            status=record.status,
            metadata={"runtimeId": record.runtime_id},
        )
        return status

    async def agent_runtime_fetch_result(
        self,
        request: Any = None,
        /,
    ) -> AgentRunResult:
        """Read one managed run result via activity execution.

        When *publish_mode* is not ``"none"``, this activity also pushes the
        agent's work branch to the remote **before** returning the result.
        This is deterministic (the workflow awaits this activity) instead of
        the old fire-and-forget pattern that could silently lose pushes.
        """
        if self._run_store is None:
            raise TemporalActivityRuntimeError(
                "run_store is required for agent_runtime.fetch_result"
            )
        run_id, _agent_id = self._agent_runtime_request_identifiers(request)
        if isinstance(request, Mapping):
            raw_publish_mode = (
                request.get("publish_mode")
                or request.get("publishMode")
                or "none"
            )
        else:
            raw_publish_mode = "none"

        publish_mode = str(raw_publish_mode).strip().lower()
        _ALLOWED_PUBLISH_MODES = {"none", "pr", "branch"}
        if publish_mode not in _ALLOWED_PUBLISH_MODES:
            logger.warning(
                "Received invalid publish_mode %r for run_id %s; defaulting to 'none'",
                raw_publish_mode,
                run_id,
            )
            publish_mode = "none"

        target_branch = (
            request.get("target_branch")
            or request.get("targetBranch")
            or request.get("publish_base_branch")
            or request.get("publishBaseBranch")
        ) if isinstance(request, Mapping) else None

        async def _unused_profile_fetcher(**_kwargs: Any) -> dict[str, Any]:
            return {"profiles": []}

        async def _unused_slot_signal(**_kwargs: Any) -> None:
            return None

        if isinstance(request, Mapping):
            raw_pr_resolver_expected = (
                request.get("pr_resolver_expected")
                or request.get("prResolverExpected")
            )
        else:
            raw_pr_resolver_expected = None
        pr_resolver_expected = _coerce_bool(
            raw_pr_resolver_expected, default=False
        )

        adapter = ManagedAgentAdapter(
            profile_fetcher=_unused_profile_fetcher,
            slot_requester=_unused_slot_signal,
            slot_releaser=_unused_slot_signal,
            cooldown_reporter=_unused_slot_signal,
            workflow_id=f"agent_runtime_activity:{run_id}",
            run_store=self._run_store,
        )
        try:
            result = await adapter.fetch_result(
                run_id, pr_resolver_expected=pr_resolver_expected
            )
            record = self._run_store.load(run_id)
            if record is not None:
                result = self._maybe_enrich_gemini_failure_result(
                    result=result,
                    record=record,
                )

            # Build merged metadata from the typed result, then enrich with
            # push/PR URL info using model_copy to preserve the typed contract.
            meta = dict(result.metadata or {})
            if record is not None:
                operator_summary = await self._collect_operator_summary(
                    record=record,
                    result=result,
                )
                if operator_summary:
                    meta["operator_summary"] = operator_summary

            # Push the agent's work branch if publish_mode requires it and the
            # agent completed without failure.
            if result.failure_class is None and publish_mode != "none":
                raw_commit_message = None
                if isinstance(request, Mapping):
                    raw_commit_message = (
                        request.get("commit_message")
                        or request.get("commitMessage")
                    )
                push_kwargs: dict[str, Any] = {
                    "target_branch": target_branch,
                }
                if (
                    isinstance(raw_commit_message, str)
                    and raw_commit_message.strip()
                ):
                    push_kwargs["commit_message"] = raw_commit_message.strip()
                push_info = await self._push_workspace_branch(
                    run_id,
                    **push_kwargs,
                )
                meta.update(push_info)

            # Enrich result with pull_request_url detected from workspace git
            # state (CLI stdout may not always surface PR URLs reliably).
            if result.failure_class is None:
                pr_url = self._detect_pr_url_from_workspace(run_id)
                if pr_url:
                    meta["pull_request_url"] = pr_url

            if meta:
                result = result.model_copy(update={"metadata": meta})

            return result
        finally:
            await self._cleanup_managed_run_publish_support_best_effort(run_id)

    async def _collect_operator_summary(
        self,
        *,
        record: ManagedRunRecord,
        result: AgentRunResult,
    ) -> str | None:
        stdout_summary = await self._extract_stdout_operator_summary(record)
        if stdout_summary:
            return stdout_summary

        summary = self._sanitize_operator_summary(result.summary)
        if summary and summary != "Completed with status completed":
            return summary
        return None

    async def _extract_stdout_operator_summary(
        self,
        record: ManagedRunRecord,
    ) -> str | None:
        if self._artifact_service is None or not record.stdout_artifact_ref:
            return None

        text = await self._read_stdout_artifact_tail(
            artifact_id=record.stdout_artifact_ref,
            run_id=record.run_id,
            max_bytes=_OPERATOR_SUMMARY_TAIL_BYTES,
        )
        if text is None:
            return None

        extracted = self._extract_operator_summary_from_text(text)
        return self._sanitize_operator_summary(extracted)

    async def _read_stdout_artifact_tail(
        self,
        *,
        artifact_id: str,
        run_id: str,
        max_bytes: int,
    ) -> str | None:
        if self._artifact_service is None:
            return None

        try:
            _, path = await self._artifact_service.read_path(
                artifact_id=artifact_id,
                principal="system:agent_runtime",
            )
        except Exception:
            logger.debug(
                "Failed to read stdout artifact path for managed run %s",
                run_id,
                exc_info=True,
            )
        else:
            try:
                return self._read_path_tail_text(path, max_bytes=max_bytes)
            except Exception:
                logger.debug(
                    "Failed to read stdout artifact tail from path for managed run %s",
                    run_id,
                    exc_info=True,
                )

        try:
            _, chunks = await self._artifact_service.read_chunks(
                artifact_id=artifact_id,
                principal="system:agent_runtime",
                chunk_size=max_bytes,
            )
        except Exception:
            logger.debug(
                "Failed to stream stdout artifact for managed run %s",
                run_id,
                exc_info=True,
            )
        else:
            payload = self._tail_bytes_from_chunks(chunks, max_bytes=max_bytes)
            return payload.decode("utf-8", errors="replace")

        try:
            _, payload = await self._artifact_service.read_bytes(
                artifact_id=artifact_id,
                principal="system:agent_runtime",
            )
        except Exception:
            logger.debug(
                "Failed to read stdout artifact for managed run %s",
                run_id,
                exc_info=True,
            )
            return None

        return payload[-max_bytes:].decode("utf-8", errors="replace")

    @staticmethod
    def _read_path_tail_text(path: Path, *, max_bytes: int) -> str:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            tail_start = max(handle.tell() - max_bytes, 0)
            handle.seek(tail_start)
            payload = handle.read()
        return payload.decode("utf-8", errors="replace")

    @staticmethod
    def _tail_bytes_from_chunks(chunks: Iterable[bytes], *, max_bytes: int) -> bytes:
        tail = bytearray()
        for chunk in chunks:
            if not chunk:
                continue
            tail.extend(chunk)
            if len(tail) > max_bytes:
                del tail[:-max_bytes]
        return bytes(tail)

    @staticmethod
    def _extract_operator_summary_from_text(text: str) -> str | None:
        normalized = str(text or "").replace("\r\n", "\n").strip()
        if not normalized:
            return None

        report_start = normalized.rfind("**Final Report**")
        if report_start >= 0:
            normalized = normalized[report_start + len("**Final Report**") :].strip()
        else:
            report_start = normalized.rfind("Final Report")
            if report_start >= 0:
                normalized = normalized[report_start + len("Final Report") :].strip()

        stop_markers = (
            "\ncodex\n",
            "\nexec\n",
            "\ntokens used",
            "\nCommand:",
            "\n/bin/bash -lc",
        )
        for marker in stop_markers:
            marker_index = normalized.find(marker)
            if marker_index >= 0:
                normalized = normalized[:marker_index].strip()

        filtered_lines: list[str] = []
        for raw_line in normalized.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if filtered_lines and filtered_lines[-1] != "":
                    filtered_lines.append("")
                continue
            if stripped in {"**Final Report**", "Final Report", "codex"}:
                continue
            if stripped.startswith("exec") or stripped.startswith("/bin/bash -lc"):
                continue
            filtered_lines.append(line)
            if len(filtered_lines) >= 18:
                break

        while filtered_lines and filtered_lines[0] == "":
            filtered_lines.pop(0)
        while filtered_lines and filtered_lines[-1] == "":
            filtered_lines.pop()

        summary = "\n".join(filtered_lines).strip()
        return summary or None

    @staticmethod
    def _sanitize_operator_summary(summary: str | None) -> str | None:
        if not summary:
            return None

        from moonmind.utils.logging import SecretRedactor, scrub_github_tokens

        redactor = SecretRedactor.from_environ(placeholder="[REDACTED]")
        scrubbed = scrub_github_tokens(redactor.scrub(summary)).strip()
        if not scrubbed:
            return None
        if len(scrubbed) <= 1400:
            return scrubbed
        return f"{scrubbed[:1397].rstrip()}..."

    @staticmethod
    def _is_generic_process_exit_summary(summary: str | None) -> bool:
        text = str(summary or "").strip().lower()
        if not text:
            return True
        return text.startswith("process exited with code")

    @classmethod
    def _maybe_enrich_gemini_failure_result(
        cls,
        *,
        result: AgentRunResult,
        record: ManagedRunRecord,
    ) -> AgentRunResult:
        if record.runtime_id != "gemini_cli":
            return result
        if record.status != "failed":
            return result
        if result.provider_error_code:
            return result
        if result.failure_class not in {None, "execution_error", "integration_error"}:
            return result
        if not cls._is_generic_process_exit_summary(result.summary):
            return result

        report = cls._load_gemini_error_report(record)
        update_payload = cls._gemini_failure_update_payload(report=report, record=record)

        if update_payload is not None:
            return result.model_copy(update=update_payload)
        return result

    @classmethod
    def _gemini_failure_update_payload(
        cls,
        *,
        report: dict[str, str] | None,
        record: ManagedRunRecord,
    ) -> dict[str, Any] | None:
        message = ""
        stack = ""
        if report is not None:
            message = str(report.get("message") or "").strip()
            stack = str(report.get("stack") or "").strip()

        if not message and not stack:
            diagnostics = cls._load_managed_runtime_diagnostics(record)
            parsed_output = diagnostics.get("parsed_output") if isinstance(diagnostics, dict) else None
            if isinstance(parsed_output, dict):
                error_messages = parsed_output.get("error_messages") or []
                if isinstance(error_messages, list):
                    joined = [str(item).strip() for item in error_messages if str(item).strip()]
                    if joined:
                        message = joined[0]
                        stack = "\n".join(joined[1:])
                if not message and parsed_output.get("rate_limited") is True:
                    message = "Gemini API rate limit exceeded"

        merged = "\n".join(part for part in (message, stack) if part).lower()
        if not merged:
            return None

        if any(marker in merged for marker in _GEMINI_QUOTA_MARKERS):
            return {
                "summary": message or "Gemini API quota exhausted",
                "failure_class": "integration_error",
                "provider_error_code": "quota_exhausted",
            }
        if any(marker in merged for marker in _GEMINI_RATE_LIMIT_MARKERS):
            return {
                "summary": message or "Gemini API rate limit exceeded",
                "failure_class": "integration_error",
                "provider_error_code": "429",
            }
        return None

    @classmethod
    def _load_gemini_error_report(
        cls,
        record: ManagedRunRecord,
    ) -> dict[str, str] | None:
        reports_dir = _GEMINI_ERROR_REPORT_DIR
        if not reports_dir.exists() or not reports_dir.is_dir():
            return None

        started_at = record.started_at.timestamp() - _GEMINI_ERROR_REPORT_TIME_PADDING_SECONDS
        finished_dt = record.finished_at or record.started_at
        finished_at = finished_dt.timestamp() + _GEMINI_ERROR_REPORT_TIME_PADDING_SECONDS

        candidates: list[tuple[int, float, Path]] = []
        try:
            report_paths = list(reports_dir.glob(_GEMINI_ERROR_REPORT_GLOB))
        except OSError:
            return None

        for path in report_paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            modified_at = stat.st_mtime
            if modified_at < started_at or modified_at > finished_at:
                continue
            distance = abs(modified_at - finished_dt.timestamp())
            # Prefer reports that clearly reference this run/workspace.
            discriminator = 0 if cls._report_matches_record(path, record) else 1
            candidates.append((discriminator, distance, path))

        if not candidates:
            return None

        for _, _, path in sorted(candidates, key=lambda item: (item[0], item[1])):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            error_obj = payload.get("error")
            if not isinstance(error_obj, dict):
                continue
            message = str(error_obj.get("message") or "").strip()
            stack = str(error_obj.get("stack") or "").strip()
            if not message and not stack:
                continue
            return {"message": message, "stack": stack}

        return None

    @classmethod
    def _load_managed_runtime_diagnostics(
        cls,
        record: ManagedRunRecord,
    ) -> dict[str, Any] | None:
        diagnostics_ref = str(record.diagnostics_ref or "").strip()
        if not diagnostics_ref:
            return None
        path = (_managed_runtime_artifact_root() / diagnostics_ref).resolve()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _workspace_git_command(workspace: str, *args: str) -> list[str]:
        """Build a git command that trusts the run workspace explicitly."""
        resolved_workspace = str(Path(workspace).resolve())
        return [
            "git",
            "-c",
            f"safe.directory={resolved_workspace}",
            "-C",
            resolved_workspace,
            *args,
        ]

    @staticmethod
    def _workspace_command_env(workspace: str) -> dict[str, str]:
        """Build a subprocess env that exposes workspace-local command shims."""
        env = dict(os.environ)
        support_root = Path(workspace).resolve().parent / ".moonmind"
        support_bin = support_root / "bin"
        support_gitconfig = support_root / "gitconfig"
        if support_bin.exists():
            existing_path = str(env.get("PATH") or "").strip()
            env["PATH"] = (
                f"{support_bin}{os.pathsep}{existing_path}"
                if existing_path
                else str(support_bin)
            )
        if support_gitconfig.exists():
            env["GIT_CONFIG_GLOBAL"] = str(support_gitconfig)
        git_name = str(settings.workflow.git_user_name or "").strip()
        git_email = str(settings.workflow.git_user_email or "").strip()
        if git_name:
            env["GIT_AUTHOR_NAME"] = git_name
            env["GIT_COMMITTER_NAME"] = git_name
        if git_email:
            env["GIT_AUTHOR_EMAIL"] = git_email
            env["GIT_COMMITTER_EMAIL"] = git_email
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    async def _commit_workspace_changes_if_needed(
        self,
        workspace: str,
        *,
        run_id: str,
        commit_message: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create one deterministic commit when the workspace is dirty."""
        command_env = dict(env) if env is not None else self._workspace_command_env(workspace)

        status_proc = await asyncio.create_subprocess_exec(
            *self._workspace_git_command(
                workspace, "status", "--porcelain", "--untracked-files=all",
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=command_env,
        )
        status_stdout, status_stderr = await asyncio.wait_for(
            status_proc.communicate(), timeout=15,
        )
        if status_proc.returncode != 0:
            detail = status_stderr.decode("utf-8", errors="replace").strip() or (
                status_stdout.decode("utf-8", errors="replace").strip() or "(no stderr)"
            )
            return {
                "push_status": "failed",
                "push_error": f"could not inspect workspace changes: {detail}",
            }

        if not status_stdout.decode("utf-8", errors="replace").strip():
            return {}

        add_proc = await asyncio.create_subprocess_exec(
            *self._workspace_git_command(
                workspace, "add", "-A", "--", ".", *_PUBLISH_GIT_ADD_EXCLUDES,
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=command_env,
        )
        add_stdout, add_stderr = await asyncio.wait_for(
            add_proc.communicate(), timeout=30,
        )
        if add_proc.returncode != 0:
            detail = add_stderr.decode("utf-8", errors="replace").strip() or (
                add_stdout.decode("utf-8", errors="replace").strip() or "(no stderr)"
            )
            return {
                "push_status": "failed",
                "push_error": f"could not stage workspace changes: {detail}",
            }

        staged_proc = await asyncio.create_subprocess_exec(
            *self._workspace_git_command(
                workspace, "diff", "--cached", "--name-only",
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=command_env,
        )
        staged_stdout, staged_stderr = await asyncio.wait_for(
            staged_proc.communicate(), timeout=15,
        )
        if staged_proc.returncode != 0:
            detail = staged_stderr.decode("utf-8", errors="replace").strip() or (
                staged_stdout.decode("utf-8", errors="replace").strip() or "(no stderr)"
            )
            return {
                "push_status": "failed",
                "push_error": f"could not inspect staged workspace changes: {detail}",
            }

        if not staged_stdout.decode("utf-8", errors="replace").strip():
            return {}

        normalized_message = (
            str(commit_message).strip()
            if isinstance(commit_message, str) and commit_message.strip()
            else f"MoonMind task result for run {run_id}"
        )
        commit_proc = await asyncio.create_subprocess_exec(
            *self._workspace_git_command(
                workspace, "commit", "-m", normalized_message,
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=command_env,
        )
        commit_stdout, commit_stderr = await asyncio.wait_for(
            commit_proc.communicate(), timeout=60,
        )
        if commit_proc.returncode != 0:
            detail = commit_stderr.decode("utf-8", errors="replace").strip() or (
                commit_stdout.decode("utf-8", errors="replace").strip() or "(no stderr)"
            )
            lowered_detail = detail.lower()
            if "nothing to commit" in lowered_detail:
                return {}
            return {
                "push_status": "failed",
                "push_error": f"could not commit workspace changes: {detail}",
            }

        logger.info(
            "Created deterministic publish commit for run %s before push.",
            run_id,
        )
        return {"push_commit_message": normalized_message}

    @staticmethod
    def _report_matches_record(path: Path, record: ManagedRunRecord) -> bool:
        """Best-effort run discriminator for Gemini error reports."""
        try:
            payload_text = path.read_text(encoding="utf-8")
        except OSError:
            return False

        run_id = str(record.run_id or "").strip()
        if run_id and run_id in payload_text:
            return True

        workspace_path = str(record.workspace_path or "").strip()
        if workspace_path and workspace_path in payload_text:
            return True

        if run_id:
            run_workspace_marker = f"/work/agent_jobs/workspaces/{run_id}/repo"
            if run_workspace_marker in payload_text:
                return True

        return False

    async def _push_workspace_branch(
        self,
        run_id: str,
        *,
        target_branch: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Push the workspace branch to origin.

        Returns a dict with ``push_status``, ``push_branch``, and optionally
        ``push_error``, ``push_base_ref``, and ``push_commit_count`` that the
        caller merges into result metadata.

        Uses ``asyncio.create_subprocess_exec`` to avoid blocking the event
        loop.
        """
        if self._run_store is None:
            return {"push_status": "skipped", "push_error": "no run store"}

        record = self._run_store.load(run_id)
        if record is None or not record.workspace_path:
            return {"push_status": "skipped", "push_error": "no workspace"}

        workspace = record.workspace_path
        try:
            command_env = self._workspace_command_env(workspace)
            branch_proc = await asyncio.create_subprocess_exec(
                *self._workspace_git_command(
                    workspace, "rev-parse", "--abbrev-ref", "HEAD",
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=command_env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                branch_proc.communicate(), timeout=10,
            )
            if branch_proc.returncode != 0:
                return {
                    "push_status": "failed",
                    "push_error": f"could not determine branch: {stderr_bytes.decode('utf-8', errors='replace').strip()}",
                }
            current_branch = stdout_bytes.decode("utf-8", errors="replace").strip()

            protected = {"main", "master", "HEAD"}
            if target_branch:
                protected.add(target_branch)
            if not current_branch or current_branch in protected:
                logger.warning(
                    "Post-agent git push skipped for run %s: "
                    "HEAD is on protected branch '%s'",
                    run_id,
                    current_branch or "(detached/unknown)",
                )
                return {
                    "push_status": "protected_branch",
                    "push_branch": current_branch or "(unknown)",
                }

            commit_info = await self._commit_workspace_changes_if_needed(
                workspace,
                run_id=run_id,
                commit_message=commit_message,
                env=command_env,
            )
            if commit_info.get("push_status") == "failed":
                commit_info.setdefault("push_branch", current_branch)
                return commit_info

            push_proc = await asyncio.create_subprocess_exec(
                *self._workspace_git_command(
                    workspace, "push", "-u", "origin", current_branch,
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=command_env,
            )
            push_stdout, push_stderr = await asyncio.wait_for(
                push_proc.communicate(), timeout=120,
            )
            if push_proc.returncode != 0:
                error_detail = push_stderr.decode("utf-8", errors="replace").strip() or "(no stderr)"
                logger.error(
                    "Post-agent git push FAILED for run %s "
                    "(branch=%s, rc=%d): %s",
                    run_id,
                    current_branch,
                    push_proc.returncode,
                    error_detail,
                )
                return {
                    "push_status": "failed",
                    "push_branch": current_branch,
                    "push_error": error_detail,
                }

            # Verify the branch actually has commits over origin/main.
            # git push succeeds as a no-op when the branch is already
            # up-to-date, which would cause repo.create_pr to fail
            # with HTTP 422 ("No commits between main and <branch>").
            base_ref = f"origin/{target_branch or 'main'}"
            try:
                count_proc = await asyncio.create_subprocess_exec(
                    *self._workspace_git_command(
                        workspace,
                        "rev-list",
                        "--count",
                        f"{base_ref}..{current_branch}",
                    ),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=command_env,
                )
                count_stdout, _ = await asyncio.wait_for(
                    count_proc.communicate(), timeout=10,
                )
                if count_proc.returncode != 0:
                    raise RuntimeError(
                        f"git rev-list failed with return code {count_proc.returncode}"
                    )
                commit_count = int(
                    count_stdout.decode("utf-8", errors="replace").strip() or "0"
                )
            except Exception as exc:
                logger.warning(
                    "Failed to count commits for run %s, falling back to "
                    "assuming commits exist: %s",
                    run_id,
                    exc,
                    exc_info=True,
                )
                # If rev-list fails, assume commits exist and let PR
                # creation handle it.
                commit_count = -1

            if commit_count == 0:
                logger.warning(
                    "Post-agent git push completed for run %s but branch "
                    "'%s' has no commits over %s",
                    run_id,
                    current_branch,
                    base_ref,
                )
                return {
                    "push_status": "no_commits",
                    "push_branch": current_branch,
                    "push_base_ref": base_ref,
                    "push_commit_count": 0,
                }

            logger.info(
                "Post-agent git push completed for run %s (branch=%s)",
                run_id,
                current_branch,
            )
            result: dict[str, Any] = {
                "push_status": "pushed",
                "push_branch": current_branch,
                "push_base_ref": base_ref,
            }
            result.update(commit_info)
            if commit_count >= 0:
                result["push_commit_count"] = commit_count
            return result
        except Exception as exc:
            logger.warning(
                "Post-agent git push failed for run %s",
                run_id,
                exc_info=True,
            )
            return {
                "push_status": "failed",
                "push_error": str(exc),
            }

    def _detect_pr_url_from_workspace(self, run_id: str) -> str | None:
        """Best-effort detection of a PR URL from the workspace git state."""
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
                self._workspace_git_command(
                    workspace, "rev-parse", "--abbrev-ref", "HEAD",
                ),
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
                env=self._workspace_command_env(workspace),
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
            TemporalAgentRuntimeActivities._workspace_git_command(
                workspace, "remote", "get-url", "origin",
            ),
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
    ) -> AgentRunStatus:
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

        run_id_str = str(run_id)

        if agent_kind == "managed":
            try:
                if self._run_supervisor is not None:
                    try:
                        await self._run_supervisor.cancel(run_id_str)
                        logger.info(
                            "agent_runtime.cancel completed for managed run %s",
                            run_id_str,
                        )
                    except Exception as exc:
                        import asyncio as _asyncio
                        if isinstance(exc, _asyncio.CancelledError):
                            raise
                        logger.warning(
                            "agent_runtime.cancel supervisor failed for managed run %s",
                            run_id_str,
                            exc_info=True,
                        )
                else:
                    logger.warning(
                        "agent_runtime.cancel called for managed run %s but no supervisor configured",
                        run_id_str,
                    )
                    if self._run_store is not None:
                        self._run_store.update_status(
                            run_id_str,
                            "canceled",
                            finished_at=datetime.now(tz=UTC),
                            error_message="Canceled via activity (no supervisor)",
                        )
                        logger.info(
                            "agent_runtime.cancel marked run %s as cancelled in store",
                            run_id_str,
                        )
            except Exception:
                logger.warning(
                    "agent_runtime.cancel store update failed for managed run %s",
                    run_id_str,
                    exc_info=True,
                )
            finally:
                await self._cleanup_run_support_best_effort(run_id_str)

            return AgentRunStatus(
                runId=run_id_str,
                agentKind="managed",
                agentId="managed",
                status="canceled",
            )

        # External or unknown agent kind
        logger.warning(
            "agent_runtime.cancel called for %s/%s — external cancel requires provider adapter",
            agent_kind,
            run_id_str,
        )
        return AgentRunStatus(
            runId=run_id_str,
            agentKind="external",
            agentId=str(agent_kind) if str(agent_kind) else "external",
            status="canceled",
        )


def _build_activity_wrapper(
    func: Callable[..., Any],
) -> Callable[[Any, Any], Awaitable[Any]]:
    original_signature = inspect.signature(func)
    params = list(original_signature.parameters.values())
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

    if all(
        param.kind is not inspect.Parameter.KEYWORD_ONLY for param in non_self_params
    ):
        annotation_globals = dict(func.__globals__)
        try:
            from moonmind.schemas.temporal_activity_models import (
                ArtifactReadInput,
                ArtifactWriteCompleteInput,
            )

            annotation_globals.setdefault("ArtifactReadInput", ArtifactReadInput)
            annotation_globals.setdefault(
                "ArtifactWriteCompleteInput", ArtifactWriteCompleteInput
            )
        except ImportError:
            # These imports are optional; failures are expected in some environments.
            logger.debug(
                "Failed to import ArtifactReadInput/ArtifactWriteCompleteInput for type hint resolution"
            )

        try:
            resolved_hints = get_type_hints(
                func,
                globalns=annotation_globals,
            )
        except (NameError, TypeError):
            # Fall back to the original annotations if type hint resolution fails,
            # rather than failing worker startup.
            resolved_hints = dict(getattr(func, "__annotations__", {}) or {})

        _wrapper.__signature__ = original_signature  # type: ignore[attr-defined]
        _wrapper.__annotations__ = resolved_hints

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
    agent_skills_activities: Any | None = None,
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
        "agent_skills": agent_skills_activities,
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
