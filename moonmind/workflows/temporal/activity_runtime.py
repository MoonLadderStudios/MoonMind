"""Concrete activity-family helpers for the Temporal activity catalog."""

from __future__ import annotations

import asyncio
import contextlib
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
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence, TypeVar, get_type_hints

from pydantic import BaseModel, ValidationError

from moonmind.config.settings import settings
from moonmind.jules.status import JulesStatusSnapshot, normalize_jules_status
from moonmind.schemas.manifest_ingest_models import CompiledManifestPlanModel
from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeCancelInput,
    AgentRuntimeFetchResultInput,
    AgentRuntimeStatusInput,
    ExternalAgentRunInput,
    PlanGenerateInput,
)
from moonmind.workflows.tasks.routing import _coerce_bool
from moonmind.auth.env_shaping import _should_filter_base_env_var
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ManagedProfileLaunchContext,
    build_managed_profile_launch_context,
)
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
from moonmind.schemas.workload_models import WorkloadRequest, WorkloadResult
from moonmind.workloads.tool_bridge import (
    CONTAINER_START_HELPER_TOOL,
    CONTAINER_STOP_HELPER_TOOL,
    build_dood_tool_definition_payload,
    is_dood_tool,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionRecord,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
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
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_managed_api_key_reference,
    shape_launch_github_auth_environment,
)
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    append_managed_codex_runtime_note,
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
_NON_SECRET_MANAGED_SESSION_ENV_KEYS: tuple[str, ...] = ("MOONMIND_URL",)
_MANAGED_SESSION_TELEMETRY_KEYS: tuple[str, ...] = (
    "activityType",
    "taskRunId",
    "runtimeId",
    "sessionId",
    "sessionEpoch",
    "sessionStatus",
    "isDegraded",
    "containerId",
    "threadId",
    "turnId",
)

HeartbeatCallback = Callable[[Mapping[str, Any]], Awaitable[None] | None]
PlanGenerator = Callable[
    [Any, Mapping[str, Any], SkillRegistrySnapshot | None],
    Mapping[str, Any] | PlanDefinition | Awaitable[Mapping[str, Any] | PlanDefinition],
]
JulesClientFactory = Callable[[], JulesClient]
JulesAgentAdapterFactory = Callable[[], JulesAgentAdapter]
CodexCloudClientFactory = Callable[[], CodexCloudHttpClient]
CodexCloudAdapterFactory = Callable[[], CodexCloudAgentAdapter]
SessionContractT = TypeVar("SessionContractT", bound=BaseModel)
_PLACEHOLDER_DIGEST_FRAGMENT = "sha256:dummy"
_GITHUB_REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_PULL_REQUEST_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+"
)
_GEMINI_ERROR_REPORT_DIR = Path("/tmp")
_GEMINI_ERROR_REPORT_GLOB = "gemini-client-error-*.json"


def _managed_session_telemetry_context(
    payload: Mapping[str, Any] | BaseModel | None,
    *,
    activity_type: str | None = None,
) -> dict[str, str | int | bool]:
    if isinstance(payload, BaseModel):
        raw_payload: Mapping[str, Any] = payload.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
    elif isinstance(payload, Mapping):
        raw_payload = payload
    else:
        raw_payload = {}
    context: dict[str, str | int | bool] = {}
    if activity_type:
        context["activityType"] = activity_type
    for key in _MANAGED_SESSION_TELEMETRY_KEYS:
        if key == "activityType":
            continue
        value = raw_payload.get(key)
        if isinstance(value, bool):
            context[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            context[key] = value
        elif isinstance(value, str) and value.strip():
            context[key] = value.strip()
    return context


def _log_managed_session_activity(
    activity_type: str,
    payload: Mapping[str, Any] | BaseModel | None,
) -> None:
    context = _managed_session_telemetry_context(
        payload,
        activity_type=activity_type,
    )
    if not context:
        return
    logger.info(
        "managed session activity",
        extra={"managed_session": context},
    )
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
_PUBLISH_GIT_EXCLUDED_PATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "live_streams.spool",
    ".agents/skills/active",
)
_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS = 10.0


class ManagedSessionController(Protocol):
    """Remote control surface for managed session containers."""

    async def launch_session(
        self, request: LaunchCodexManagedSessionRequest, /
    ) -> CodexManagedSessionHandle | Mapping[str, Any]:
        pass

    async def session_status(
        self, request: CodexManagedSessionLocator, /
    ) -> CodexManagedSessionHandle | Mapping[str, Any]:
        pass

    async def send_turn(
        self, request: SendCodexManagedSessionTurnRequest, /
    ) -> CodexManagedSessionTurnResponse | Mapping[str, Any]:
        pass

    async def steer_turn(
        self, request: SteerCodexManagedSessionTurnRequest, /
    ) -> CodexManagedSessionTurnResponse | Mapping[str, Any]:
        pass

    async def interrupt_turn(
        self, request: InterruptCodexManagedSessionTurnRequest, /
    ) -> CodexManagedSessionTurnResponse | Mapping[str, Any]:
        pass

    async def clear_session(
        self, request: CodexManagedSessionClearRequest, /
    ) -> CodexManagedSessionHandle | Mapping[str, Any]:
        pass

    async def terminate_session(
        self, request: TerminateCodexManagedSessionRequest, /
    ) -> CodexManagedSessionHandle | Mapping[str, Any]:
        pass

    async def fetch_session_summary(
        self, request: FetchCodexManagedSessionSummaryRequest, /
    ) -> CodexManagedSessionSummary | Mapping[str, Any]:
        pass

    async def publish_session_artifacts(
        self, request: PublishCodexManagedSessionArtifactsRequest, /
    ) -> CodexManagedSessionArtifactsPublication | Mapping[str, Any]:
        pass

    async def reconcile(self) -> Sequence[CodexManagedSessionRecord | Mapping[str, Any]]:
        pass


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
    "provider_profile.manager_state": ("artifacts", "provider_profile_manager_state"),
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
    "merge_gate.evaluate_readiness": ("integrations", "merge_gate_evaluate_readiness"),
    "merge_gate.create_resolver_run": ("integrations", "merge_gate_create_resolver_run"),
    "agent_runtime.build_launch_context": (
        "agent_runtime",
        "agent_runtime_build_launch_context",
    ),
    "agent_runtime.launch": ("agent_runtime", "agent_runtime_launch"),
    "agent_runtime.launch_session": ("agent_runtime", "agent_runtime_launch_session"),
    "agent_runtime.load_session_snapshot": (
        "agent_runtime",
        "agent_runtime_load_session_snapshot",
    ),
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
    "agent_runtime.session_status": (
        "agent_runtime",
        "agent_runtime_session_status",
    ),
    "agent_runtime.prepare_turn_instructions": (
        "agent_runtime",
        "agent_runtime_prepare_turn_instructions",
    ),
    "agent_runtime.send_turn": ("agent_runtime", "agent_runtime_send_turn"),
    "agent_runtime.steer_turn": ("agent_runtime", "agent_runtime_steer_turn"),
    "agent_runtime.interrupt_turn": (
        "agent_runtime",
        "agent_runtime_interrupt_turn",
    ),
    "agent_runtime.clear_session": (
        "agent_runtime",
        "agent_runtime_clear_session",
    ),
    "agent_runtime.terminate_session": (
        "agent_runtime",
        "agent_runtime_terminate_session",
    ),
    "agent_runtime.fetch_session_summary": (
        "agent_runtime",
        "agent_runtime_fetch_session_summary",
    ),
    "agent_runtime.publish_session_artifacts": (
        "agent_runtime",
        "agent_runtime_publish_session_artifacts",
    ),
    "agent_runtime.reconcile_managed_sessions": (
        "agent_runtime",
        "agent_runtime_reconcile_managed_sessions",
    ),
    "agent_runtime.status": ("agent_runtime", "agent_runtime_status"),
    "agent_runtime.fetch_result": ("agent_runtime", "agent_runtime_fetch_result"),
    "agent_runtime.cancel": ("agent_runtime", "agent_runtime_cancel"),
    "workload.run": ("agent_runtime", "workload_run"),
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
    if is_dood_tool(name):
        return build_dood_tool_definition_payload(name=name, version=version)

    if name == "story.create_jira_issues":
        return {
            "name": name,
            "version": version,
            "description": "Create Jira issues from Moon Spec story breakdown output.",
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "stories": {"type": "array"},
                        "storyOutput": {"type": "object"},
                        "storyBreakdownPath": {"type": "string"},
                        "storyBreakdownJson": {"type": "string"},
                        "repository": {"type": "string"},
                        "targetBranch": {"type": "string"},
                        "branch": {"type": "string"},
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
            "requirements": {"capabilities": ["integration:jira"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 300,
                    "schedule_to_close_seconds": 600,
                },
                "retries": {"max_attempts": 1},
            },
        }

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
        if tool_name.lower() == "jira-issue-creator":
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


def _validate_external_agent_run_input(payload: Any) -> ExternalAgentRunInput:
    """Validate external activity input, including scalar legacy histories."""

    if isinstance(payload, str):
        payload = {"runId": payload}
    try:
        return ExternalAgentRunInput.model_validate(payload)
    except ValidationError as exc:
        raise TemporalActivityRuntimeError(
            f"external agent run payload is invalid: {exc}"
        ) from exc


def _validate_agent_runtime_status_input(payload: Any) -> AgentRuntimeStatusInput:
    if isinstance(payload, AgentRuntimeStatusInput):
        return payload
    try:
        return AgentRuntimeStatusInput.model_validate(payload)
    except ValidationError as exc:
        raise TemporalActivityRuntimeError(
            f"agent_runtime.status payload is invalid: {exc}"
        ) from exc


def _validate_agent_runtime_fetch_result_input(
    payload: Any,
) -> AgentRuntimeFetchResultInput:
    if isinstance(payload, AgentRuntimeFetchResultInput):
        return payload
    if isinstance(payload, AgentRuntimeStatusInput):
        return AgentRuntimeFetchResultInput(
            runId=payload.run_id,
            agentId=payload.agent_id,
        )
    try:
        return AgentRuntimeFetchResultInput.model_validate(payload)
    except ValidationError as exc:
        raise TemporalActivityRuntimeError(
            f"agent_runtime.fetch_result payload is invalid: {exc}"
        ) from exc


def _validate_agent_runtime_cancel_input(payload: Any) -> AgentRuntimeCancelInput:
    if isinstance(payload, AgentRuntimeCancelInput):
        return payload
    try:
        return AgentRuntimeCancelInput.model_validate(payload)
    except ValidationError as exc:
        raise TemporalActivityRuntimeError(
            f"agent_runtime.cancel payload is invalid: {exc}"
        ) from exc


async def _maybe_call_heartbeat(
    callback: HeartbeatCallback | None,
    payload: Mapping[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


async def _await_with_activity_heartbeats(
    awaitable: Awaitable[Any],
    *,
    heartbeat_payload: Mapping[str, Any],
    interval_seconds: float | None = None,
) -> Any:
    from temporalio import activity

    task = asyncio.ensure_future(awaitable)
    try:
        if not activity.in_activity():
            return await task

        heartbeat_interval = (
            interval_seconds
            if interval_seconds is not None
            else _SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS
        )
        while True:
            done, _ = await asyncio.wait({task}, timeout=heartbeat_interval)
            if task in done:
                return await task
            activity.heartbeat(dict(heartbeat_payload))
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


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
            idempotency_key=idempotency_key,
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
        idempotency_key: str | None = None,
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

        execution_context = dict(context or {})
        if idempotency_key is not None:
            execution_context["idempotency_key"] = idempotency_key

        return await execute_skill_activity(
            invocation_payload=invocation_payload,
            registry_snapshot=resolved_snapshot,
            dispatcher=self._dispatcher,
            context=execution_context,
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
        idempotency_key: str | None = None,
    ) -> SkillResult:
        """Canonical tool-execution alias for mm.skill.execute."""

        return await self._execute_skill_invocation(
            invocation_payload=invocation_payload,
            registry_snapshot=registry_snapshot,
            registry_snapshot_ref=registry_snapshot_ref,
            artifact_service=artifact_service,
            principal=principal,
            context=context,
            idempotency_key=idempotency_key,
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
        request = _validate_external_agent_run_input(payload)
        return await jules_status_activity(request.run_id)

    async def integration_jules_fetch_result(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_fetch_result_activity
        request = _validate_external_agent_run_input(payload)
        return await jules_fetch_result_activity(request.run_id)

    async def integration_jules_cancel(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import jules_cancel_activity
        request = _validate_external_agent_run_input(payload)
        return await jules_cancel_activity(request.run_id)

    async def repo_create_pr(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import repo_create_pr_activity
        return await repo_create_pr_activity(payload)

    async def repo_merge_pr(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.jules_activities import repo_merge_pr_activity
        return await repo_merge_pr_activity(payload)

    async def merge_gate_evaluate_readiness(self, payload, /, **kwargs):
        from moonmind.workflows.adapters.github_service import GitHubService

        if not isinstance(payload, Mapping):
            return {
                "headSha": "",
                "ready": False,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": "Merge-gate readiness payload is invalid.",
                        "retryable": False,
                        "source": "policy",
                    }
                ],
                "policyAllowed": False,
            }

        pull_request = payload.get("pullRequest") or {}
        policy = payload.get("policy") or {}
        if not isinstance(pull_request, Mapping):
            pull_request = {}
        if not isinstance(policy, Mapping):
            policy = {}

        readiness = await GitHubService().evaluate_pull_request_readiness(
            repo=str(pull_request.get("repo") or ""),
            pr_number=int(pull_request.get("number") or 0),
            head_sha=str(pull_request.get("headSha") or ""),
            policy=dict(policy),
            github_token=payload.get("githubToken"),
        )
        evidence = readiness.model_dump(by_alias=True)

        jira_issue_key = str(payload.get("jiraIssueKey") or "").strip()
        if policy.get("jiraStatus") == "required":
            jira_allowed, jira_blocker = await self._merge_gate_jira_status_allowed(
                jira_issue_key
            )
            evidence["jiraStatusAllowed"] = jira_allowed
            if jira_blocker is not None:
                evidence.setdefault("blockers", []).append(jira_blocker)
                evidence["ready"] = False
        else:
            evidence["jiraStatusAllowed"] = True

        return evidence

    async def _merge_gate_jira_status_allowed(
        self,
        issue_key: str,
    ) -> tuple[bool, dict[str, Any] | None]:
        if not issue_key:
            return (
                False,
                {
                    "kind": "jira_status_pending",
                    "summary": "Required Jira issue key is missing.",
                    "retryable": False,
                    "source": "jira",
                },
            )
        try:
            from moonmind.integrations.jira.models import GetIssueRequest
            from moonmind.integrations.jira.tool import JiraToolService

            issue = await JiraToolService().get_issue(
                GetIssueRequest(issueKey=issue_key, fields=["status"])
            )
        except Exception:
            return (
                False,
                {
                    "kind": "external_state_unavailable",
                    "summary": "Jira status could not be fetched.",
                    "retryable": True,
                    "source": "jira",
                },
            )
        status = ((issue or {}).get("fields") or {}).get("status") or {}
        category = status.get("statusCategory") or {}
        category_key = str(category.get("key") or "").strip().lower()
        if category_key == "done":
            return True, None
        return (
            False,
            {
                "kind": "jira_status_pending",
                "summary": "Jira status is not yet allowed for merge automation.",
                "retryable": True,
                "source": "jira",
            },
        )

    async def merge_gate_create_resolver_run(self, payload, /, **kwargs):
        import hashlib

        key = ""
        run_input: Mapping[str, Any] | None = None
        if isinstance(payload, Mapping):
            key = str(payload.get("idempotencyKey") or "").strip()
            run_payload = payload.get("runInput") or payload.get("run_input")
            if isinstance(run_payload, Mapping):
                run_input = run_payload
        suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else "resolver"
        workflow_id = f"merge-gate-resolver-{suffix}"
        if run_input is not None:
            try:
                from moonmind.config.settings import settings
                from moonmind.workflows.temporal.activity_catalog import WORKFLOW_TASK_QUEUE
                from moonmind.workflows.temporal.client import get_temporal_client

                client = await get_temporal_client(
                    settings.temporal.address,
                    settings.temporal.namespace,
                )
                handle = await client.start_workflow(
                    "MoonMind.Run",
                    dict(run_input),
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                return {
                    "workflowId": workflow_id,
                    "runId": getattr(handle, "first_execution_run_id", None),
                    "created": True,
                }
            except Exception as exc:
                message = str(exc).lower()
                if "already" not in message or "start" not in message:
                    raise
                return {
                    "workflowId": workflow_id,
                    "runId": None,
                    "created": False,
                }
        return {
            "workflowId": workflow_id,
            "runId": None,
            "created": True,
        }

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
        request = _validate_external_agent_run_input(payload)
        return await codex_cloud_status_activity(request.run_id)

    async def integration_codex_cloud_fetch_result(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import codex_cloud_fetch_result_activity
        request = _validate_external_agent_run_input(payload)
        return await codex_cloud_fetch_result_activity(request.run_id)

    async def integration_codex_cloud_cancel(self, payload, /, **kwargs):
        from moonmind.workflows.temporal.activities.codex_cloud_activities import codex_cloud_cancel_activity
        request = _validate_external_agent_run_input(payload)
        return await codex_cloud_cancel_activity(request.run_id)

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
        session_controller: ManagedSessionController | None = None,
        workload_launcher: Any | None = None,
        workload_registry: Any | None = None,
        client_adapter: Any = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._run_store = run_store
        self._run_supervisor = run_supervisor
        self._run_launcher = run_launcher
        self._session_controller = session_controller
        self._workload_launcher = workload_launcher
        self._workload_registry = workload_registry
        if client_adapter is None:
            from moonmind.workflows.temporal import client as temporal_client_module

            client_adapter = temporal_client_module.TemporalClientAdapter()
        self._client_adapter = client_adapter
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

    async def agent_runtime_build_launch_context(
        self,
        payload: Mapping[str, Any],
        /,
    ) -> dict[str, Any]:
        """Build managed launch context on the activity side."""

        profile_raw = payload.get("profile")
        if not isinstance(profile_raw, Mapping):
            raise TemporalActivityRuntimeError(
                "payload.profile is required for agent_runtime.build_launch_context"
            )
        runtime_for_profile = str(payload.get("runtime_for_profile") or "").strip()
        workflow_id = str(payload.get("workflow_id") or "").strip()
        default_credential_source = str(
            payload.get("default_credential_source")
            or payload.get("defaultCredentialSource")
            or ""
        ).strip()
        if not runtime_for_profile or not workflow_id or not default_credential_source:
            raise TemporalActivityRuntimeError(
                "payload must include runtime_for_profile, workflow_id, and default_credential_source"
            )

        profile = dict(profile_raw)
        context = build_managed_profile_launch_context(
            profile=profile,
            runtime_for_profile=runtime_for_profile,
            workflow_id=workflow_id,
            default_credential_source=default_credential_source,
        )
        delta_env_overrides = dict(context.delta_env_overrides)
        tags = profile.get("tags") or []
        if "proxy-first" in tags:
            from cryptography.fernet import Fernet

            from api_service.core.encryption import get_encryption_key

            provider = str(profile.get("provider_id") or "anthropic").strip().lower()
            payload_bytes = json.dumps(
                {
                    "provider": provider,
                    "workflow_id": workflow_id,
                    "secret_refs": profile.get("secret_refs", {}),
                    "exp": time.time() + 3600,
                }
            ).encode("utf-8")
            fernet = Fernet(get_encryption_key().encode("utf-8"))
            proxy_token = "mm-proxy-token:" + fernet.encrypt(payload_bytes).decode(
                "utf-8"
            )
            api_url = os.environ.get(
                "MOONMIND_PROXY_URL",
                "http://moonmind-api:8000/api/v1/proxy",
            )
            delta_env_overrides["MOONMIND_PROXY_TOKEN"] = proxy_token
            if provider in {"anthropic", "minimax"}:
                delta_env_overrides["ANTHROPIC_BASE_URL"] = f"{api_url}/{provider}"
                delta_env_overrides["ANTHROPIC_API_KEY"] = proxy_token
                delta_env_overrides["ANTHROPIC_AUTH_TOKEN"] = proxy_token
            elif provider == "openai":
                delta_env_overrides["OPENAI_BASE_URL"] = f"{api_url}/openai/v1"
                delta_env_overrides["OPENAI_API_KEY"] = proxy_token

        passthrough_env_keys = [
            key
            for key in context.passthrough_env_keys
            if str(os.environ.get(key, "")).strip()
        ]
        combined_env_keys = {
            key for key in os.environ if not _should_filter_base_env_var(key)
        }
        combined_env_keys.update(delta_env_overrides)
        result = ManagedProfileLaunchContext(
            profile_id=context.profile_id,
            credential_source=context.credential_source,
            delta_env_overrides=delta_env_overrides,
            passthrough_env_keys=passthrough_env_keys,
            env_keys_count=len(combined_env_keys),
        )
        return {
            "profile_id": result.profile_id,
            "credential_source": result.credential_source,
            "delta_env_overrides": result.delta_env_overrides,
            "passthrough_env_keys": result.passthrough_env_keys,
            "env_keys_count": result.env_keys_count,
        }

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

    async def workload_run(
        self,
        payload: Mapping[str, Any],
        /,
    ) -> dict[str, Any]:
        """Run one validated Docker workload on the agent_runtime fleet."""

        if self._workload_registry is None or self._workload_launcher is None:
            raise TemporalActivityRuntimeError(
                "workload registry and launcher are required for workload.run"
            )
        request_payload = dict(payload.get("request", payload))
        reason = str(request_payload.pop("reason", "") or "bounded_window_complete")
        if request_payload.get("toolName") == CONTAINER_STOP_HELPER_TOOL:
            request_payload.setdefault("command", ["stop"])
        request = WorkloadRequest.model_validate(request_payload)
        validated = self._workload_registry.validate_request(request)
        if request.tool_name == CONTAINER_START_HELPER_TOOL:
            result = await self._workload_launcher.start_helper(validated)
        elif request.tool_name == CONTAINER_STOP_HELPER_TOOL:
            result = await self._workload_launcher.stop_helper(
                validated,
                reason=reason,
            )
        else:
            result = await self._workload_launcher.run(validated)
        if not isinstance(result, WorkloadResult):
            result = WorkloadResult.model_validate(result)
        return result.model_dump(mode="json", by_alias=True)

    async def agent_runtime_publish_artifacts(
        self,
        result: AgentRunResult | None = None,
        /,
    ) -> AgentRunResult | None:
        """Publish agent-run outputs back to artifact storage.

        Best-effort publication writes ``output.summary`` and
        ``output.agent_result`` JSON artifacts plus managed-session
        ``input.*`` reference artifacts when those refs are present.
        Returns the result enriched with a ``diagnostics_ref`` pointing to
        the persisted ``output.agent_result`` artifact.
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

        from temporalio import activity

        try:
            info = activity.info()
        except RuntimeError:
            info = None

        def _execution_ref(link_type: str) -> ExecutionRef | None:
            if info is None:
                return None
            return ExecutionRef(
                namespace=info.namespace,
                workflow_id=info.workflow_id,
                run_id=info.workflow_run_id,
                link_type=link_type,
            )

        metadata = (
            result_dict.get("metadata")
            if isinstance(result_dict.get("metadata"), Mapping)
            else {}
        )
        moonmind_metadata = (
            metadata.get("moonmind")
            if isinstance(metadata.get("moonmind"), Mapping)
            else {}
        )
        step_ledger_metadata = (
            moonmind_metadata.get("stepLedger")
            if isinstance(moonmind_metadata.get("stepLedger"), Mapping)
            else {}
        )
        step_artifact_metadata: dict[str, Any] = {}
        logical_step_id = str(step_ledger_metadata.get("logicalStepId") or "").strip()
        if logical_step_id:
            step_artifact_metadata["step_id"] = logical_step_id
        attempt = step_ledger_metadata.get("attempt")
        if isinstance(attempt, (int, float)) and not isinstance(attempt, bool):
            step_artifact_metadata["attempt"] = int(attempt)
        scope = str(step_ledger_metadata.get("scope") or "").strip()
        if scope:
            step_artifact_metadata["scope"] = scope

        async def _write_reference_artifact(
            *,
            link_type: str,
            artifact_ref_value: str,
            field_name: str,
        ) -> str:
            ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload={
                    field_name: artifact_ref_value,
                },
                execution_ref=_execution_ref(link_type),
                metadata_json={
                    "name": link_type.replace(".", "_") + ".json",
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": ["agent_runtime", link_type],
                    **step_artifact_metadata,
                },
            )
            return ref.artifact_id

        instruction_ref = str(metadata.get("instructionRef") or "").strip()
        resolved_skillset_ref = str(metadata.get("resolvedSkillsetRef") or "").strip()

        # Build summary payload for the artifact
        summary_payload: dict[str, Any] = {
            "summary": result_dict.get("summary") or result_dict.get("raw", ""),
            "output_refs": result_dict.get("output_refs") or result_dict.get("outputRefs") or [],
            "failure_class": result_dict.get("failure_class") or result_dict.get("failureClass"),
            "provider_error_code": result_dict.get("provider_error_code") or result_dict.get("providerErrorCode"),
            "metrics": result_dict.get("metrics") or {},
        }

        try:
            published_refs: dict[str, str] = {}
            if instruction_ref:
                published_refs["inputInstructionsRef"] = await _write_reference_artifact(
                    link_type="input.instructions",
                    artifact_ref_value=instruction_ref,
                    field_name="instructionRef",
                )
            if resolved_skillset_ref:
                published_refs["inputSkillSnapshotRef"] = await _write_reference_artifact(
                    link_type="input.skill_snapshot",
                    artifact_ref_value=resolved_skillset_ref,
                    field_name="resolvedSkillsetRef",
                )
            summary_ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=summary_payload,
                execution_ref=_execution_ref("output.summary"),
                metadata_json={
                    "name": "agent_run_summary.json",
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": ["agent_runtime", "output.summary"],
                    **step_artifact_metadata,
                },
            )
            agent_result_ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=result_dict,
                execution_ref=_execution_ref("output.agent_result"),
                metadata_json={
                    "name": "agent_run_result.json",
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": ["agent_runtime", "output.agent_result"],
                    **step_artifact_metadata,
                },
            )
            # Enrich result with the diagnostics ref
            if isinstance(result, Mapping):
                enriched = dict(result)
                if "diagnosticsRef" in enriched:
                    enriched["diagnosticsRef"] = agent_result_ref.artifact_id
                else:
                    enriched["diagnostics_ref"] = agent_result_ref.artifact_id
                enriched_metadata = (
                    dict(enriched.get("metadata") or {})
                    if isinstance(enriched.get("metadata"), Mapping)
                    else {}
                )
                enriched_metadata.update(
                    {
                        **published_refs,
                        "outputSummaryRef": summary_ref.artifact_id,
                        "outputAgentResultRef": agent_result_ref.artifact_id,
                    }
                )
                enriched["metadata"] = enriched_metadata
                # Remove snake_case if alias is present to avoid Pydantic validation errors
                if "diagnosticsRef" in enriched and "diagnostics_ref" in enriched:
                    del enriched["diagnostics_ref"]
                return enriched
            if hasattr(result, "diagnostics_ref"):
                result.diagnostics_ref = agent_result_ref.artifact_id
            if hasattr(result, "metadata"):
                metadata_obj = getattr(result, "metadata", None)
                enriched_metadata = (
                    dict(metadata_obj) if isinstance(metadata_obj, Mapping) else {}
                )
                enriched_metadata.update(
                    {
                        **published_refs,
                        "outputSummaryRef": summary_ref.artifact_id,
                        "outputAgentResultRef": agent_result_ref.artifact_id,
                    }
                )
                result.metadata = enriched_metadata
            return result
        except Exception as exc:
            logger.warning(
                "agent_runtime.publish_artifacts failed to publish managed-session artifacts",
                exc_info=True,
            )
            return result

    def _require_session_controller(
        self, *, activity_type: str
    ) -> ManagedSessionController:
        if self._session_controller is None:
            raise TemporalActivityRuntimeError(
                f"session_controller is required for {activity_type}"
            )
        return self._session_controller

    @staticmethod
    def _validate_session_request(
        request: Mapping[str, Any] | BaseModel | None,
        *,
        activity_type: str,
        model_type: type[SessionContractT],
    ) -> SessionContractT:
        if isinstance(request, model_type):
            _log_managed_session_activity(activity_type, request)
            return request
        raw_payload: Mapping[str, Any] | None
        if isinstance(request, BaseModel):
            raw_payload = request.model_dump(mode="json", by_alias=True)
        else:
            raw_payload = request
        payload = _coerce_activity_request(raw_payload, activity_type=activity_type)
        validated = model_type.model_validate(payload)
        _log_managed_session_activity(activity_type, validated)
        return validated

    @staticmethod
    def _validate_session_response(
        response: Any,
        *,
        activity_type: str,
        model_type: type[SessionContractT],
    ) -> SessionContractT:
        try:
            return model_type.model_validate(response)
        except Exception as exc:
            raise TemporalActivityRuntimeError(
                f"{activity_type} returned an invalid session contract payload"
            ) from exc

    @staticmethod
    def _coerce_launch_session_request_payload(
        request: Mapping[str, Any] | LaunchCodexManagedSessionRequest | None,
    ) -> tuple[LaunchCodexManagedSessionRequest, ManagedRuntimeProfile | None]:
        if isinstance(request, LaunchCodexManagedSessionRequest):
            return request, None

        payload = _coerce_activity_request(
            request,
            activity_type="agent_runtime.launch_session",
        )
        request_payload = payload.get("request")
        if isinstance(request_payload, Mapping):
            profile_payload = payload.get("profile")
            profile = (
                ManagedRuntimeProfile.model_validate(profile_payload)
                if profile_payload is not None
                else None
            )
            return (
                LaunchCodexManagedSessionRequest.model_validate(request_payload),
                profile,
            )
        return LaunchCodexManagedSessionRequest.model_validate(payload), None

    @staticmethod
    async def _materialize_launch_session_environment(
        *,
        request: LaunchCodexManagedSessionRequest,
        profile: ManagedRuntimeProfile,
    ) -> dict[str, str]:
        from moonmind.workflows.adapters.materializer import (
            ProviderProfileMaterializer,
        )
        from moonmind.workflows.adapters.secret_boundary import (
            SecretResolverBoundary,
        )

        class _ActivitySecretResolver(SecretResolverBoundary):
            async def resolve_secrets(
                self,
                secret_refs: dict[str, str],
            ) -> dict[str, str]:
                resolved: dict[str, str] = {}
                for key, ref in secret_refs.items():
                    resolved[key] = await resolve_managed_api_key_reference(
                        ref,
                        field_name=f"profile.secretRefs.{key}",
                    )
                return resolved

        materializer = ProviderProfileMaterializer(
            base_env=dict(request.environment),
            secret_resolver=_ActivitySecretResolver(),
        )
        runtime_support_dir = str(
            Path(request.codex_home_path).expanduser().resolve().parent
        )
        materialized_environment, _command = await materializer.materialize(
            profile,
            workspace_path=request.workspace_path,
            runtime_support_dir=runtime_support_dir,
        )
        return materialized_environment

    @staticmethod
    async def _shape_launch_session_request(
        request: LaunchCodexManagedSessionRequest,
        *,
        profile: ManagedRuntimeProfile | None = None,
    ) -> LaunchCodexManagedSessionRequest:
        """Resolve runtime-only auth immediately before remote session launch."""

        environment = dict(request.environment)
        if profile is not None:
            environment = (
                await TemporalAgentRuntimeActivities._materialize_launch_session_environment(
                    request=request,
                    profile=profile,
                )
            )
        for key in _NON_SECRET_MANAGED_SESSION_ENV_KEYS:
            value = os.environ.get(key)
            if value is not None and value.strip() and key not in environment:
                environment[key] = value
        environment = await shape_launch_github_auth_environment(
            environment,
            ambient_github_token=os.environ.get("GITHUB_TOKEN"),
        )
        return request.model_copy(update={"environment": environment})

    async def agent_runtime_launch_session(
        self,
        request: Mapping[str, Any] | LaunchCodexManagedSessionRequest | None = None,
        /,
    ) -> CodexManagedSessionHandle:
        controller = self._require_session_controller(
            activity_type="agent_runtime.launch_session"
        )
        validated, profile = self._coerce_launch_session_request_payload(
            request
        )
        validated = await self._shape_launch_session_request(
            validated,
            profile=profile,
        )
        try:
            response = await controller.launch_session(validated)
        except Exception as exc:
            detail = self._sanitize_operator_summary(str(exc)) or "managed session launch failed"
            raise TemporalActivityRuntimeError(
                f"agent_runtime.launch_session failed: {detail}"
            ) from exc
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.launch_session",
            model_type=CodexManagedSessionHandle,
        )

    async def agent_runtime_load_session_snapshot(
        self,
        request: Mapping[str, Any] | CodexManagedSessionBinding | None = None,
        /,
    ) -> CodexManagedSessionSnapshot:
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.load_session_snapshot",
            model_type=CodexManagedSessionBinding,
        )
        handle = await self._client_adapter.get_workflow_handle(validated.workflow_id)
        response = await handle.query("get_status")
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.load_session_snapshot",
            model_type=CodexManagedSessionSnapshot,
        )

    async def agent_runtime_session_status(
        self,
        request: Mapping[str, Any] | CodexManagedSessionLocator | None = None,
        /,
    ) -> CodexManagedSessionHandle:
        controller = self._require_session_controller(
            activity_type="agent_runtime.session_status"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.session_status",
            model_type=CodexManagedSessionLocator,
        )
        response = await controller.session_status(validated)
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.session_status",
            model_type=CodexManagedSessionHandle,
        )

    async def agent_runtime_prepare_turn_instructions(
        self,
        payload: Mapping[str, Any],
        /,
    ) -> str:
        request_raw = payload.get("request")
        if not isinstance(request_raw, Mapping):
            raise TemporalActivityRuntimeError(
                "payload.request is required for agent_runtime.prepare_turn_instructions"
            )
        request = AgentExecutionRequest.model_validate(dict(request_raw))
        workspace_path_raw = str(
            payload.get("workspace_path") or payload.get("workspacePath") or ""
        ).strip()
        instruction_ref = str(request.instruction_ref or "").strip()
        if instruction_ref:
            if not workspace_path_raw:
                raise TemporalActivityRuntimeError(
                    "payload.workspace_path or payload.workspacePath is required when request.instructionRef is set"
                )
            from moonmind.rag.context_injection import ContextInjectionService

            service = ContextInjectionService()
            await service.inject_context(
                request=request,
                workspace_path=Path(workspace_path_raw),
            )
            instruction_ref = str(request.instruction_ref or "").strip()
            if instruction_ref:
                return self._prepare_managed_codex_turn_text(
                    instruction_ref,
                    parameters=request.parameters,
                )
        parameters = request.parameters if isinstance(request.parameters, dict) else {}
        instructions = str(parameters.get("instructions") or "").strip()
        if instructions:
            return self._prepare_managed_codex_turn_text(
                instructions,
                parameters=parameters,
            )
        raise TemporalActivityRuntimeError(
            "request.instructionRef or request.parameters.instructions is required"
        )

    @classmethod
    def _prepare_managed_codex_turn_text(
        cls,
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
    ) -> str:
        prepared = cls._append_jira_issue_creator_tool_hint(
            instructions,
            parameters=parameters,
        )
        return append_managed_codex_runtime_note(prepared)

    @staticmethod
    def _append_jira_issue_creator_tool_hint(
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
    ) -> str:
        params = parameters if isinstance(parameters, Mapping) else {}
        selected_skill = str(params.get("selectedSkill") or "").strip().lower()
        if selected_skill != "jira-issue-creator":
            return instructions
        if "MoonMind trusted Jira tools" in instructions:
            return instructions
        story_breakdown_path = str(params.get("storyBreakdownPath") or "").strip()
        story_breakdown_hint = (
            f"- Read MoonSpec story candidates from `{story_breakdown_path}`.\n"
            if story_breakdown_path
            else ""
        )
        return (
            instructions.rstrip()
            + "\n\nMoonMind trusted Jira tools:\n"
            + story_breakdown_hint
            + "- Use the internal MoonMind API from the managed session via "
            + "`$MOONMIND_URL` for Jira operations; do not look for raw Jira "
            + "credentials in the shell.\n"
            + "- List available tools with `GET $MOONMIND_URL/mcp/tools`.\n"
            + "- Invoke Jira tools with `POST $MOONMIND_URL/mcp/tools/call` and "
            + "JSON like `{\"tool\":\"jira.list_create_issue_types\","
            + "\"arguments\":{\"projectKey\":\"<PROJECT_KEY>\"}}`.\n"
            + "- Resolve the Story issue type through `jira.list_create_issue_types` "
            + "and create issues through `jira.create_issue`.\n"
            + "- Treat the task as blocked if Jira tool calls are unavailable or no "
            + "Jira issue key is returned."
        )

    async def agent_runtime_send_turn(
        self,
        request: Mapping[str, Any] | SendCodexManagedSessionTurnRequest | None = None,
        /,
    ) -> CodexManagedSessionTurnResponse:
        controller = self._require_session_controller(
            activity_type="agent_runtime.send_turn"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.send_turn",
            model_type=SendCodexManagedSessionTurnRequest,
        )
        response = await _await_with_activity_heartbeats(
            controller.send_turn(validated),
            heartbeat_payload={
                "activityType": "agent_runtime.send_turn",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
                "threadId": validated.thread_id,
            },
        )
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.send_turn",
            model_type=CodexManagedSessionTurnResponse,
        )

    async def agent_runtime_steer_turn(
        self,
        request: Mapping[str, Any] | SteerCodexManagedSessionTurnRequest | None = None,
        /,
    ) -> CodexManagedSessionTurnResponse:
        controller = self._require_session_controller(
            activity_type="agent_runtime.steer_turn"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.steer_turn",
            model_type=SteerCodexManagedSessionTurnRequest,
        )
        response = await _await_with_activity_heartbeats(
            controller.steer_turn(validated),
            heartbeat_payload={
                "activityType": "agent_runtime.steer_turn",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
                "threadId": validated.thread_id,
                "turnId": validated.turn_id,
            },
        )
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.steer_turn",
            model_type=CodexManagedSessionTurnResponse,
        )

    async def agent_runtime_interrupt_turn(
        self,
        request: Mapping[str, Any]
        | InterruptCodexManagedSessionTurnRequest
        | None = None,
        /,
    ) -> CodexManagedSessionTurnResponse:
        controller = self._require_session_controller(
            activity_type="agent_runtime.interrupt_turn"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.interrupt_turn",
            model_type=InterruptCodexManagedSessionTurnRequest,
        )
        response = await _await_with_activity_heartbeats(
            controller.interrupt_turn(validated),
            heartbeat_payload={
                "activityType": "agent_runtime.interrupt_turn",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
                "threadId": validated.thread_id,
                "turnId": validated.turn_id,
            },
        )
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.interrupt_turn",
            model_type=CodexManagedSessionTurnResponse,
        )

    async def agent_runtime_clear_session(
        self,
        request: Mapping[str, Any] | CodexManagedSessionClearRequest | None = None,
        /,
    ) -> CodexManagedSessionHandle:
        controller = self._require_session_controller(
            activity_type="agent_runtime.clear_session"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.clear_session",
            model_type=CodexManagedSessionClearRequest,
        )
        response = await _await_with_activity_heartbeats(
            controller.clear_session(validated),
            heartbeat_payload={
                "activityType": "agent_runtime.clear_session",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
                "threadId": validated.thread_id,
            },
        )
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.clear_session",
            model_type=CodexManagedSessionHandle,
        )

    async def agent_runtime_terminate_session(
        self,
        request: Mapping[str, Any] | TerminateCodexManagedSessionRequest | None = None,
        /,
    ) -> CodexManagedSessionHandle:
        controller = self._require_session_controller(
            activity_type="agent_runtime.terminate_session"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.terminate_session",
            model_type=TerminateCodexManagedSessionRequest,
        )
        response = await _await_with_activity_heartbeats(
            controller.terminate_session(validated),
            heartbeat_payload={
                "activityType": "agent_runtime.terminate_session",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
                "threadId": validated.thread_id,
            },
        )
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.terminate_session",
            model_type=CodexManagedSessionHandle,
        )

    async def agent_runtime_fetch_session_summary(
        self,
        request: Mapping[str, Any]
        | FetchCodexManagedSessionSummaryRequest
        | None = None,
        /,
    ) -> CodexManagedSessionSummary:
        controller = self._require_session_controller(
            activity_type="agent_runtime.fetch_session_summary"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.fetch_session_summary",
            model_type=FetchCodexManagedSessionSummaryRequest,
        )
        response = await controller.fetch_session_summary(validated)
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.fetch_session_summary",
            model_type=CodexManagedSessionSummary,
        )

    async def agent_runtime_publish_session_artifacts(
        self,
        request: Mapping[str, Any]
        | PublishCodexManagedSessionArtifactsRequest
        | None = None,
        /,
    ) -> CodexManagedSessionArtifactsPublication:
        controller = self._require_session_controller(
            activity_type="agent_runtime.publish_session_artifacts"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.publish_session_artifacts",
            model_type=PublishCodexManagedSessionArtifactsRequest,
        )
        response = await controller.publish_session_artifacts(validated)
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.publish_session_artifacts",
            model_type=CodexManagedSessionArtifactsPublication,
        )

    async def agent_runtime_reconcile_managed_sessions(
        self,
        payload: Mapping[str, Any] | None = None,
        /,
    ) -> dict[str, Any]:
        controller = self._require_session_controller(
            activity_type="agent_runtime.reconcile_managed_sessions"
        )
        del payload
        records = await _await_with_activity_heartbeats(
            controller.reconcile(),
            heartbeat_payload={
                "activityType": "agent_runtime.reconcile_managed_sessions",
            },
        )
        session_id_limit = 50
        session_ids: list[str] = []
        degraded_count = 0
        reconciled_count = 0
        for raw_record in records:
            reconciled_count += 1
            record = CodexManagedSessionRecord.model_validate(raw_record)
            if len(session_ids) < session_id_limit:
                session_ids.append(record.session_id)
            if str(record.status).lower() == "degraded":
                degraded_count += 1
        return {
            "managedSessionRecordsReconciled": reconciled_count,
            "degradedSessionRecords": degraded_count,
            "sessionIds": session_ids,
            "truncated": reconciled_count > session_id_limit,
        }

    @staticmethod
    def _agent_runtime_request_identifiers(
        request: Any,
    ) -> tuple[str, str]:
        """Extract ``run_id`` and ``agent_id`` from a flexible request shape."""
        if isinstance(request, (AgentRuntimeStatusInput, AgentRuntimeFetchResultInput)):
            return request.run_id, request.agent_id
        if isinstance(request, Mapping):
            validated = _validate_agent_runtime_status_input(request)
            return validated.run_id, validated.agent_id
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
        if isinstance(request, Mapping):
            request = _validate_agent_runtime_status_input(request)
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
        if isinstance(request, Mapping):
            request = _validate_agent_runtime_fetch_result_input(request)
        elif isinstance(request, AgentRuntimeStatusInput) and not isinstance(
            request, AgentRuntimeFetchResultInput
        ):
            request = AgentRuntimeFetchResultInput(
                runId=request.run_id,
                agentId=request.agent_id,
            )
        run_id, _agent_id = self._agent_runtime_request_identifiers(request)

        publish_mode = (
            request.publish_mode
            if isinstance(request, AgentRuntimeFetchResultInput)
            else "none"
        )

        target_branch = (
            request.target_branch
            if isinstance(request, AgentRuntimeFetchResultInput)
            else None
        )
        head_branch = (
            request.head_branch
            if isinstance(request, AgentRuntimeFetchResultInput)
            else None
        )

        async def _unused_profile_fetcher(**_kwargs: Any) -> dict[str, Any]:
            return {"profiles": []}

        async def _unused_slot_signal(**_kwargs: Any) -> None:
            return None

        pr_resolver_expected = (
            request.pr_resolver_expected
            if isinstance(request, AgentRuntimeFetchResultInput)
            else False
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
                meta.setdefault("taskRunId", record.run_id)
                if record.stdout_artifact_ref:
                    meta.setdefault("stdoutArtifactRef", record.stdout_artifact_ref)
                if record.stderr_artifact_ref:
                    meta.setdefault("stderrArtifactRef", record.stderr_artifact_ref)
                if record.merged_log_artifact_ref:
                    meta.setdefault(
                        "mergedLogArtifactRef",
                        record.merged_log_artifact_ref,
                    )
                if record.diagnostics_ref:
                    meta.setdefault("diagnosticsRef", record.diagnostics_ref)
                operator_summary = await self._collect_operator_summary(
                    record=record,
                    result=result,
                )
                if operator_summary:
                    meta["operator_summary"] = operator_summary

            # Push the agent's work branch if publish_mode requires it and the
            # agent completed without failure.
            if result.failure_class is None and publish_mode != "none":
                raw_commit_message = (
                    request.commit_message
                    if isinstance(request, AgentRuntimeFetchResultInput)
                    else None
                )
                push_kwargs: dict[str, Any] = {
                    "target_branch": target_branch,
                }
                if publish_mode == "branch":
                    push_kwargs["allow_target_branch_push"] = True
                if isinstance(head_branch, str) and head_branch.strip():
                    push_kwargs["head_branch"] = head_branch.strip()
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
    def _parse_git_status_paths(status_output: bytes) -> tuple[str, ...]:
        """Extract changed paths from `git status --porcelain=v1 -z` output."""

        def _decode_path(path_bytes: bytes) -> str:
            return os.fsdecode(path_bytes)

        raw_output = bytes(status_output or b"")
        if not raw_output:
            return ()

        entries = raw_output.split(b"\0")
        paths: list[str] = []
        index = 0
        while index < len(entries):
            record = entries[index]
            if not record:
                index += 1
                continue
            if len(record) < 4 or record[2:3] != b" ":
                raise ValueError(f"unexpected git status record: {record!r}")

            status = record[:2].decode("ascii", errors="strict")
            path_bytes = record[3:]
            if not path_bytes:
                raise ValueError(f"missing path in git status record: {record!r}")
            paths.append(_decode_path(path_bytes))

            if "R" in status or "C" in status:
                index += 1
                if index >= len(entries):
                    raise ValueError(
                        f"missing original path for git rename/copy record: {record!r}"
                    )
                original_path_bytes = entries[index]
                if not original_path_bytes:
                    raise ValueError(
                        f"missing original path for git rename/copy record: {record!r}"
                    )
                paths.append(_decode_path(original_path_bytes))

            index += 1

        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _should_exclude_publish_path(path_text: str) -> bool:
        """Skip runtime scaffolding paths that should never be published."""
        normalized = str(path_text or "").strip().rstrip("/")
        if not normalized:
            return True
        for excluded in _PUBLISH_GIT_EXCLUDED_PATHS:
            if normalized == excluded or normalized.startswith(f"{excluded}/"):
                return True
        return False

    @staticmethod
    def _workspace_command_env(workspace: str) -> dict[str, str]:
        """Build a subprocess env that exposes workspace-local command shims."""
        env = dict(os.environ)
        support_root = Path(workspace).resolve().parent / ".moonmind"
        support_bin = support_root / "bin"
        support_gitconfig = support_root / "gitconfig"
        github_token = str(env.get("GITHUB_TOKEN", "")).strip()
        git_helper_path = support_bin / "git-credential-moonmind"
        helper_command: str | None = None

        try:
            support_root.mkdir(parents=True, exist_ok=True)
            support_bin.mkdir(parents=True, exist_ok=True)
            if github_token:
                helper_script = (
                    "#!/usr/bin/env python3\n"
                    "import os\n"
                    "import sys\n"
                    "\n"
                    "operation = str(sys.argv[1] if len(sys.argv) > 1 else '').strip().lower()\n"
                    "if operation not in {'get', 'fill'}:\n"
                    "    raise SystemExit(0)\n"
                    "\n"
                    "request = {}\n"
                    "for raw_line in sys.stdin:\n"
                    "    line = raw_line.rstrip('\\n')\n"
                    "    if not line:\n"
                    "        break\n"
                    "    key, _, value = line.partition('=')\n"
                    "    request[key] = value\n"
                    "\n"
                    "host = str(request.get('host') or '').strip().lower()\n"
                    "protocol = str(request.get('protocol') or '').strip().lower()\n"
                    "if host != 'github.com' or (protocol and protocol != 'https'):\n"
                    "    raise SystemExit(0)\n"
                    "\n"
                    "token = str(os.environ.get('GITHUB_TOKEN', '')).strip()\n"
                    "if not token:\n"
                    "    raise SystemExit(0)\n"
                    "\n"
                    "sys.stdout.write('username=x-access-token\\n')\n"
                    "sys.stdout.write(f'password={token}\\n\\n')\n"
                    "sys.stdout.flush()\n"
                )
                git_helper_path.write_text(helper_script, encoding="utf-8")
                git_helper_path.chmod(0o700)
                helper_command = shlex.quote(str(git_helper_path))

            git_config_lines = [
                "# moonmind-runtime-git-config\n",
                "[safe]\n",
                f'\tdirectory = "{Path(workspace).resolve()}"\n',
            ]
            if helper_command is not None:
                git_config_lines.extend(
                    [
                        "[credential]\n",
                        f"\thelper = !{helper_command}\n",
                    ]
                )
            support_gitconfig.write_text("".join(git_config_lines), encoding="utf-8")
            support_gitconfig.chmod(0o600)
        except OSError:
            logger.warning(
                "Failed to bootstrap workspace-local git support files for workspace %s "
                "(support_root=%s, gitconfig=%s)",
                workspace,
                support_root,
                support_gitconfig,
                exc_info=True,
            )

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
                workspace,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
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

        if not status_stdout:
            return {}

        try:
            changed_paths = tuple(
                path
                for path in self._parse_git_status_paths(status_stdout)
                if not self._should_exclude_publish_path(path)
            )
        except ValueError as exc:
            return {
                "push_status": "failed",
                "push_error": f"could not parse workspace changes: {exc}",
            }
        if not changed_paths:
            return {}

        add_proc = await asyncio.create_subprocess_exec(
            *self._workspace_git_command(
                workspace, "add", "-A", "--", *changed_paths,
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
        head_branch: str | None = None,
        commit_message: str | None = None,
        allow_target_branch_push: bool = False,
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
            target_branch_name = (
                target_branch.strip() if isinstance(target_branch, str) else ""
            )
            head_branch_name = (
                head_branch.strip() if isinstance(head_branch, str) else ""
            )

            target_branch_push_allowed = (
                allow_target_branch_push
                and bool(target_branch_name)
                and (not head_branch_name or head_branch_name == target_branch_name)
            )

            protected = {"main", "master", "HEAD"}
            if target_branch_name and not target_branch_push_allowed:
                protected.add(target_branch_name)
            if (
                current_branch == "HEAD"
                and head_branch_name
                and head_branch_name not in protected
            ):
                repair_proc = await asyncio.create_subprocess_exec(
                    *self._workspace_git_command(
                        workspace, "checkout", "-B", head_branch_name,
                    ),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=command_env,
                )
                try:
                    repair_stdout, repair_stderr = await asyncio.wait_for(
                        repair_proc.communicate(), timeout=30,
                    )
                except asyncio.TimeoutError:
                    repair_proc.kill()
                    await repair_proc.wait()
                    return {
                        "push_status": "failed",
                        "push_error": "detached HEAD recovery timed out after 30s",
                        "push_branch": "HEAD",
                    }
                if repair_proc.returncode != 0:
                    repair_detail = (
                        repair_stderr.decode("utf-8", errors="replace").strip()
                        or repair_stdout.decode("utf-8", errors="replace").strip()
                    )
                    return {
                        "push_status": "failed",
                        "push_error": (
                            "could not recover detached HEAD onto "
                            f"'{head_branch_name}': {repair_detail}"
                        ),
                        "push_branch": "HEAD",
                    }
                logger.info(
                    "Recovered detached HEAD for run %s onto branch '%s' before push",
                    run_id,
                    head_branch_name,
                )
                current_branch = head_branch_name
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

            same_branch_publish = (
                target_branch_push_allowed
                and bool(target_branch_name)
                and current_branch == target_branch_name
            )
            base_ref = f"origin/{target_branch_name or 'main'}"
            commit_count: int | None = None
            if same_branch_publish:
                commit_count = await self._count_branch_commits_ahead(
                    workspace=workspace,
                    base_ref=base_ref,
                    branch=current_branch,
                    run_id=run_id,
                    env=command_env,
                )

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

            # Verify the branch actually has commits over the publish base.
            # git push succeeds as a no-op when the branch is already
            # up-to-date, which would cause repo.create_pr to fail
            # with HTTP 422 ("No commits between main and <branch>").
            if commit_count is None:
                commit_count = await self._count_branch_commits_ahead(
                    workspace=workspace,
                    base_ref=base_ref,
                    branch=current_branch,
                    run_id=run_id,
                    env=command_env,
                )

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

    async def _count_branch_commits_ahead(
        self,
        *,
        workspace: str,
        base_ref: str,
        branch: str,
        run_id: str,
        env: Mapping[str, str],
    ) -> int:
        try:
            count_proc = await asyncio.create_subprocess_exec(
                *self._workspace_git_command(
                    workspace,
                    "rev-list",
                    "--count",
                    f"{base_ref}..{branch}",
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            count_stdout, _ = await asyncio.wait_for(
                count_proc.communicate(), timeout=10,
            )
            if count_proc.returncode != 0:
                raise RuntimeError(
                    f"git rev-list failed with return code {count_proc.returncode}"
                )
            return int(
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
            # If rev-list fails, assume commits exist and let PR creation or
            # branch publication handle any remote-side rejection.
            return -1

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
        if isinstance(request, AgentRuntimeCancelInput):
            cancel_input = request
        elif isinstance(request, Mapping):
            cancel_input = _validate_agent_runtime_cancel_input(request)
        elif isinstance(request, (list, tuple)) and len(request) >= 2:
            cancel_input = AgentRuntimeCancelInput(
                agentKind=str(request[0] or "unknown"),
                runId=str(request[1] or ""),
            )
        else:
            cancel_input = AgentRuntimeCancelInput(
                agentKind="unknown",
                runId=str(request or "unknown"),
            )

        agent_kind = cancel_input.agent_kind
        run_id_str = cancel_input.run_id

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
