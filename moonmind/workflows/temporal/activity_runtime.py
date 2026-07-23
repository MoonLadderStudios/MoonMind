"""Concrete activity-family helpers for the Temporal activity catalog."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import gzip
from email.message import EmailMessage
import hashlib
import httpx
import inspect
import json
from copy import deepcopy
from logging import getLogger
import os
import re
import shlex
import shutil
import smtplib
import stat
import tempfile
import time
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable, Iterable, Mapping, Protocol, Sequence, TypeVar, get_type_hints

from pydantic import BaseModel, ValidationError
from temporalio import activity as temporal_activity
from temporalio import exceptions as temporal_exceptions

from moonmind.config.settings import settings
from moonmind.services.skills_on_demand import skills_on_demand_runtime_instruction
from moonmind.security.outbound_scan import (
    OutboundBundleItem,
    resolve_high_security_mode,
    scan_outbound_bundle,
    scan_outbound_text,
)
from moonmind.integrations.pentest.models import (
    PENTEST_HEARTBEAT_PHASES,
    PENTEST_RUNTIME_ID,
    PentestApprovedScope,
    PentestExecutionPolicy,
    PentestLaunchPolicyError,
    PentestProviderMaterializationError,
    PentestScopeValidationError,
    PentestWorkloadRequest,
    PentestWorkloadResult,
    build_pentest_execution_materialization,
    build_pentest_launch_plan,
    build_pentest_provider_cooldown_diagnostic,
    build_pentest_progress_annotation,
    build_pentest_publication_result,
    build_pentest_terminal_cleanup_result,
    classify_pentest_failure,
    materialize_pentest_provider_profile,
    pentest_cleanup_selector,
    pentest_provider_lease_metadata,
    redact_pentest_diagnostic_value,
    redact_pentest_human_text,
    resolve_pentest_provider_profile,
    strictly_normalize_pentest_finding_set,
)
from moonmind.jules.status import JulesStatusSnapshot, normalize_jules_status
from moonmind.workflows.temporal.runtime.workspace_locators import (
    resolve_managed_workspace_locator,
)
from moonmind.schemas.manifest_ingest_models import CompiledManifestPlanModel
from moonmind.schemas.managed_checkpoint_models import (
    ManagedCheckpointEntry,
    ManagedWorkspaceCheckpointCaptureInput,
    ManagedWorkspaceCheckpointCaptureResult,
)
from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeCancelInput,
    AgentRuntimeFetchResultInput,
    AgentRuntimeStatusInput,
    AgentRuntimeTerminalCheckpointInput,
    AgentRuntimeTerminalCheckpointResult,
    ExternalAgentRunInput,
    PlanGenerateInput,
)
from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepCheckpointCreateInput,
    StepCheckpointValidateInput,
    StepCheckpointValidateResult,
    WorkspaceCheckpointCaptureInput,
    WorkspaceCheckpointCaptureResult,
    WorkspaceCheckpointEvidenceModel,
    WorkspacePolicyApplyInput,
    WorkspacePolicyApplyResult,
)
from moonmind.schemas.workspace_locator_models import (
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_IDENTITY_MISMATCH,
    WORKSPACE_LOCATOR_UNSUPPORTED,
    WORKSPACE_LOCATOR_ADAPTER,
    WorkspaceLocatorResolutionError,
)
from moonmind.workflows.report_output import report_output_display_name
from moonmind.workflows.checkpoint_branches import generate_checkpoint_branch_name
from moonmind.workflows.executions.routing import _coerce_bool
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.executions.prepared_context import (
    PreparedContextFailure,
    build_prepared_input_manifest,
    select_step_prepared_context,
)
from moonmind.workflows.temporal.completion_summary import (
    is_generic_completion_summary,
)
from moonmind.workflows.temporal.jira_tool_hints import (
    append_selected_jira_tool_hint,
)
from moonmind.auth.env_shaping import _should_filter_base_env_var
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ManagedProfileLaunchContext,
    build_managed_profile_launch_context,
    managed_run_status_metadata,
)
from moonmind.utils.logging import SecretRedactor, redact_sensitive_payload, redact_sensitive_text
from moonmind.utils.metrics import get_metrics_emitter
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.codex_cloud_agent_adapter import CodexCloudAgentAdapter
from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClient as CodexCloudHttpClient
from moonmind.codex_cloud.settings import build_codex_cloud_gate, CODEX_CLOUD_DISABLED_MESSAGE
from moonmind.workflows.adapters.jules_client import JulesClient
from moonmind.workflows.agent_skills.selection import selected_agent_skill
from moonmind.schemas.agent_skill_models import (
    AgentSkillSourceKind,
    ResolvedSkillSet,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer
from moonmind.workflows.temporal.jira_agent_skills import JIRA_AGENT_SKILLS
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    OPS_DIAGNOSE_STACK_TOOL_NAME,
    build_deployment_update_tool_definition_payload,
    build_ops_diagnose_stack_tool_definition_payload,
)

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunStatus,
    AgentRunResult,
    ManagedRunRecord,
    ManagedRuntimeProfile,
    extract_durable_retrieval_metadata,
)
from moonmind.schemas.workload_models import WorkloadResult, parse_workload_request
from moonmind.workloads.tool_bridge import (
    build_container_job_tool_definition_payload,
    is_container_job_tool,
)
from moonmind.workflow_docker_mode import normalize_workflow_docker_mode

# Replay-only vocabulary for the retained ``workload.run`` Activity. These
# names are absent from new executable-tool discovery and dispatch.
_LEGACY_CONTAINER_START_HELPER_TOOL = "container.start_helper"
_LEGACY_CONTAINER_STOP_HELPER_TOOL = "container.stop_helper"


def _legacy_workload_tool_allowed(tool_name: str, workflow_docker_mode: str) -> bool:
    mode = normalize_workflow_docker_mode(workflow_docker_mode)
    curated = {
        "container.run_workload",
        _LEGACY_CONTAINER_START_HELPER_TOOL,
        _LEGACY_CONTAINER_STOP_HELPER_TOOL,
        "moonmind.integration_ci",
        "unreal.run_tests",
    }
    return mode != "disabled" and (
        tool_name in curated
        or (mode == "unrestricted" and tool_name == "container.run_container")
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
    ManagedSessionEnsureDockerSidecarRequest,
    ManagedSessionEnsureDockerSidecarResponse,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.skills.artifact_store import (
    ArtifactStoreError,
    InMemoryArtifactStore,
)
from moonmind.workflows.skills.workspace_links import cleanup_moonmind_skill_projections
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
from moonmind.workflows.skills.approval_policy import (
    recommended_next_action_for_verdict,
    recommended_next_actions,
    step_gate_contract_violations,
)
from moonmind.workflows.skills.tool_plan_contracts import (
    REVIEW_VERDICTS,
    ToolFailure,
)
from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog
from moonmind.workflows.temporal.artifacts import (
    ArtifactRef,
    ArtifactUploadDescriptor,
    ExecutionRef,
    TemporalArtifactError,
    TemporalArtifactService,
    TemporalArtifactValidationError,
    build_artifact_ref,
)
from moonmind.workflows.temporal.report_artifacts import validate_report_bundle_result
from moonmind.workflows.temporal.manifest_ingest import (
    build_manifest_run_index,
    build_manifest_summary,
    compile_manifest_plan,
    plan_nodes_to_runtime_nodes,
)
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    build_github_credential_descriptor_for_launch,
    resolve_managed_api_key_reference,
)
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    append_managed_codex_runtime_note,
)
from moonmind.workflows.temporal.story_output_tools import (
    JIRA_CHECK_BLOCKERS_TOOL_NAME,
    JIRA_LOAD_PRESET_BRIEF_TOOL_NAME,
    JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME,
)
from moonmind.workflows.temporal.step_checkpoints import (
    build_step_checkpoint_create_result,
    build_step_checkpoint_payload,
    checkpoint_kinds_for_workspace_policy,
    validate_step_checkpoint_payload,
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

_PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS = 60.0
_PROFILE_MANAGER_READY_POLL_ATTEMPTS = 60
_PROFILE_MANAGER_READY_POLL_SECONDS = 1.0
_MANAGED_AGENT_UID = 1000
_MANAGED_AGENT_GID = 1000


def _managed_agent_subprocess_kwargs() -> dict[str, int]:
    geteuid = getattr(os, "geteuid", None)
    if os.name != "posix" or not callable(geteuid) or geteuid() != 0:
        return {}
    return {"user": _MANAGED_AGENT_UID, "group": _MANAGED_AGENT_GID}


async def _create_managed_agent_subprocess(
    *command: str,
    **kwargs: Any,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *command,
        **kwargs,
        **_managed_agent_subprocess_kwargs(),
    )


def _normalize_managed_git_ownership(workspace: str) -> None:
    """Repair legacy root-owned Git directories before managed-user Git commands."""

    if not _managed_agent_subprocess_kwargs():
        return
    git_dir = Path(workspace).expanduser().resolve() / ".git"
    try:
        git_stat = os.lstat(git_dir)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(git_stat.st_mode):
        return

    directory_flag = getattr(os, "O_DIRECTORY", None)
    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if directory_flag is None or no_follow_flag is None:
        raise RuntimeError(
            "managed Git ownership repair requires O_DIRECTORY and O_NOFOLLOW support"
        )
    git_fd = os.open(git_dir, os.O_RDONLY | directory_flag | no_follow_flag)
    try:
        os.fchown(git_fd, _MANAGED_AGENT_UID, _MANAGED_AGENT_GID)
        for _root, dirnames, filenames, directory_fd in os.fwalk(
            ".",
            topdown=True,
            follow_symlinks=False,
            dir_fd=git_fd,
        ):
            for name in (*dirnames, *filenames):
                os.chown(
                    name,
                    _MANAGED_AGENT_UID,
                    _MANAGED_AGENT_GID,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
    finally:
        os.close(git_fd)


def _normalize_managed_path_owners(paths: Sequence[Path]) -> None:
    if not _managed_agent_subprocess_kwargs():
        return
    for path in paths:
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        os.chown(
            path,
            _MANAGED_AGENT_UID,
            _MANAGED_AGENT_GID,
            follow_symlinks=False,
        )


class _HashingArchiveReader:
    """Hash file blocks while ``tarfile`` streams them into an archive."""

    def __init__(self, file_handle: BinaryIO) -> None:
        self._file_handle = file_handle
        self._hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self._file_handle.read(size)
        if chunk:
            self._hash.update(chunk)
        return chunk

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


class PentestWorkloadHandle(Protocol):
    async def poll(self) -> Any | None:
        """Return a workload result once complete, otherwise None."""

    async def stop(self, *, grace_seconds: int) -> Mapping[str, Any] | None:
        """Attempt graceful workload termination."""

    async def remove(self) -> Mapping[str, Any] | None:
        """Remove workload runtime resources."""


class PentestProviderLeaseManager(Protocol):
    async def acquire(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        metadata: Mapping[str, Any],
    ) -> str:
        """Acquire provider capacity for one PentestGPT attempt."""

    async def release(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        lease_id: str,
    ) -> None:
        """Release provider capacity for one PentestGPT attempt."""

    async def record_cooldown(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        cooldown_seconds: int,
        reason: str,
    ) -> None:
        """Record provider cooldown for a quota or rate-limit failure."""


class TemporalPentestProviderLeaseManager:
    def __init__(self, client_adapter: Any) -> None:
        self._client_adapter = client_adapter

    async def _ensure_manager_started(self, runtime_id: str) -> str:
        from temporalio.exceptions import WorkflowAlreadyStartedError

        from moonmind.workflows.temporal.activity_catalog import get_workflow_task_queue
        from moonmind.workflows.temporal.workflows.provider_profile_manager import (
            WORKFLOW_NAME as PROVIDER_PROFILE_MANAGER_WF,
            workflow_id_for_runtime,
        )

        workflow_id = workflow_id_for_runtime(runtime_id)
        get_client = getattr(self._client_adapter, "get_client", None)
        if get_client is None:
            return workflow_id
        client = await get_client()
        try:
            await client.start_workflow(
                PROVIDER_PROFILE_MANAGER_WF,
                {"runtime_id": runtime_id},
                id=workflow_id,
                task_queue=get_workflow_task_queue(),
            )
        except WorkflowAlreadyStartedError:
            logger.debug(
                "Provider profile manager %s is already running", workflow_id
            )
        return workflow_id

    async def _assert_profile_known(
        self,
        *,
        workflow_id: str,
        profile_id: str,
    ) -> None:
        get_client = getattr(self._client_adapter, "get_client", None)
        if get_client is None:
            return
        client = await get_client()
        handle = client.get_workflow_handle(workflow_id)
        last_error: Exception | None = None
        for _attempt in range(_PROFILE_MANAGER_READY_POLL_ATTEMPTS):
            try:
                state = await handle.query("get_state")
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(_PROFILE_MANAGER_READY_POLL_SECONDS)
                continue
            last_error = None
            profiles = state.get("profiles") if isinstance(state, Mapping) else None
            if isinstance(profiles, Mapping) and profile_id in profiles:
                return
            await asyncio.sleep(_PROFILE_MANAGER_READY_POLL_SECONDS)
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            f"Provider profile {profile_id!r} is not launch-ready in {workflow_id}"
        )

    async def acquire(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        metadata: Mapping[str, Any],
    ) -> str:
        update_workflow = getattr(self._client_adapter, "update_workflow", None)
        if update_workflow is None:
            raise RuntimeError("Temporal client adapter does not support workflow updates")
        workflow_id = await self._ensure_manager_started(runtime_id)
        await self._assert_profile_known(
            workflow_id=workflow_id,
            profile_id=profile_id,
        )
        await update_workflow(
            workflow_id,
            "AcquireSlot",
            {
                "requester_workflow_id": owner,
                "runtime_id": runtime_id,
                "execution_profile_ref": profile_id,
                "metadata": dict(metadata),
            },
        )
        return owner

    async def release(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        lease_id: str,
    ) -> None:
        await self._client_adapter.signal_workflow(
            await self._ensure_manager_started(runtime_id),
            "release_slot",
            {
                "requester_workflow_id": owner,
                "runtime_id": runtime_id,
                "profile_id": profile_id,
                "lease_id": lease_id,
            },
        )

    async def record_cooldown(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        cooldown_seconds: int,
        reason: str,
    ) -> None:
        await self._client_adapter.signal_workflow(
            await self._ensure_manager_started(runtime_id),
            "report_cooldown",
            {
                "runtime_id": runtime_id,
                "profile_id": profile_id,
                "requester_workflow_id": owner,
                "cooldown_seconds": cooldown_seconds,
                "reason": reason,
            },
        )


def _pentest_provider_lease_owner(
    *,
    agent_run_id: str,
    step_id: str,
    attempt: int,
) -> str:
    return f"pentest:{agent_run_id}:{step_id}:{attempt}"


def _pentest_target_hash(target: str) -> str:
    return hashlib.sha256(target.encode("utf-8")).hexdigest()


def _pentest_provider_lease_safe_metadata(
    request: PentestWorkloadRequest,
    *,
    runtime_id: str,
    profile_id: str,
) -> dict[str, Any]:
    return {
        "tool": "security.pentest.run",
        "runtime_id": runtime_id,
        "profile_id": profile_id,
        "agent_run_id": request.agent_run_id,
        "step_id": request.step_id,
        "attempt": request.attempt,
        "target_hash": _pentest_target_hash(request.target),
        "mode": request.operation_mode,
        "runner_profile": request.runner_profile_id,
    }

def emit_pentest_activity_heartbeat(
    *,
    phase: str,
    agent_run_id: str | None = None,
    step_id: str | None = None,
    attempt: int | None = None,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    """Emit and return a compact redacted Pentest activity heartbeat payload."""

    dropped_metadata_keys = {
        "stdout",
        "stderr",
        "logs",
        "raw_logs",
        "raw_evidence",
        "evidence",
        "diagnostics_body",
        "env",
        "env_overrides",
        "command",
        "credentials",
        "secrets",
    }
    compact_metadata = {
        str(key): redact_pentest_diagnostic_value(value)
        for key, value in dict(metadata or {}).items()
        if value is not None
        and str(key) not in dropped_metadata_keys
        and not re.search(r"(?i)(api[_-]?key|token|password|secret)", str(key))
    }
    payload: dict[str, Any] = {
        "phase": build_pentest_progress_annotation(
            phase=phase,
            message=message or f"Pentest phase {phase}.",
        ).phase,
    }
    if agent_run_id:
        payload["agent_run_id"] = str(agent_run_id)
    if step_id:
        payload["step_id"] = str(step_id)
    if attempt is not None:
        payload["attempt"] = int(attempt)
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = max(0.0, round(float(elapsed_seconds), 3))
    if message:
        payload["message"] = redact_pentest_human_text(str(message))
    if compact_metadata:
        payload["metadata"] = compact_metadata
    try:
        temporal_activity.heartbeat(payload)
    except RuntimeError:
        # Unit tests and trusted internal callers may exercise the helper
        # outside a live Temporal activity context.
        pass
    return payload

async def _await_pentest_workload_with_activity_heartbeats(
    workload_awaitable: Awaitable[Any],
    *,
    request: PentestWorkloadRequest,
    heartbeat_interval_seconds: float = _PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS,
) -> Any:
    """Await a launched Pentest workload while emitting bounded running heartbeats."""

    started_at = time.monotonic()
    task = asyncio.ensure_future(workload_awaitable)
    emit_pentest_activity_heartbeat(
        phase="running",
        agent_run_id=request.agent_run_id,
        step_id=request.step_id,
        attempt=request.attempt,
        message="Pentest workload is running.",
        elapsed_seconds=0.0,
    )
    interval = max(0.001, float(heartbeat_interval_seconds))
    try:
        while not task.done():
            done, _pending = await asyncio.wait({task}, timeout=interval)
            if done:
                break
            emit_pentest_activity_heartbeat(
                phase="running",
                agent_run_id=request.agent_run_id,
                step_id=request.step_id,
                attempt=request.attempt,
                message="Pentest workload is still running.",
                elapsed_seconds=time.monotonic() - started_at,
            )
        return await task
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise

async def _supervise_pentest_workload_with_activity_heartbeats(
    launcher: Any,
    validated_workload_request: Any,
    *,
    request: PentestWorkloadRequest,
    timeout_seconds: float,
    heartbeat_interval_seconds: float = _PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS,
) -> WorkloadResult:
    """Run a Pentest workload with activity-visible heartbeats and cleanup."""

    if not callable(getattr(launcher, "start", None)):
        raw_result = await _await_pentest_workload_with_activity_heartbeats(
            launcher.run(validated_workload_request),
            request=request,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        if isinstance(raw_result, WorkloadResult):
            return raw_result
        return WorkloadResult.model_validate(raw_result)

    started_at = datetime.now(UTC)
    monotonic_started_at = time.monotonic()
    interval = max(0.001, float(heartbeat_interval_seconds))
    timeout = max(0.001, float(timeout_seconds))
    cleanup_metadata: dict[str, Any] = {
        "gracefulTerminationAttempted": False,
        "killEscalated": False,
        "containerRemoved": False,
        "cleanupErrors": [],
    }

    def _cleanup_metadata() -> dict[str, Any]:
        return redact_sensitive_payload(dict(cleanup_metadata))

    def _kill_grace_seconds() -> int:
        profile = getattr(validated_workload_request, "profile", None)
        cleanup = getattr(profile, "cleanup", None)
        return int(getattr(cleanup, "kill_grace_seconds", 30) or 30)

    async def _stop_and_remove(*, terminal_reason: str) -> None:
        cleanup_metadata["terminalReason"] = terminal_reason
        cleanup_metadata["gracefulTerminationAttempted"] = True
        try:
            stop_result = await handle.stop(grace_seconds=_kill_grace_seconds())
            if isinstance(stop_result, Mapping):
                cleanup_metadata.update(dict(stop_result))
        except Exception as exc:
            cleanup_metadata["killEscalated"] = True
            cleanup_metadata["cleanupErrors"].append(
                redact_pentest_human_text(str(exc))
            )
        try:
            remove_result = await handle.remove()
            cleanup_metadata["containerRemoved"] = True
            if isinstance(remove_result, Mapping):
                cleanup_metadata.update(dict(remove_result))
        except Exception as exc:
            cleanup_metadata["containerRemoved"] = False
            cleanup_metadata["cleanupErrors"].append(
                redact_pentest_human_text(str(exc))
            )

    handle: PentestWorkloadHandle = await launcher.start(validated_workload_request)
    emit_pentest_activity_heartbeat(
        phase="running",
        agent_run_id=request.agent_run_id,
        step_id=request.step_id,
        attempt=request.attempt,
        message="Pentest workload is running.",
        elapsed_seconds=0.0,
    )
    try:
        while True:
            raw_result = await handle.poll()
            if raw_result is not None:
                result = (
                    raw_result
                    if isinstance(raw_result, WorkloadResult)
                    else WorkloadResult.model_validate(raw_result)
                )
                result.metadata.setdefault("cleanup", _cleanup_metadata())
                return result
            elapsed = time.monotonic() - monotonic_started_at
            if elapsed >= timeout:
                await _stop_and_remove(terminal_reason="timeout")
                completed_at = datetime.now(UTC)
                return WorkloadResult(
                    requestId=getattr(
                        validated_workload_request,
                        "container_name",
                        f"pentest-{request.agent_run_id}-{request.step_id}-{request.attempt}",
                    ),
                    profileId=request.runner_profile_id,
                    status="timed_out",
                    exitCode=None,
                    startedAt=started_at,
                    completedAt=completed_at,
                    durationSeconds=(completed_at - started_at).total_seconds(),
                    timeoutReason="workload exceeded timeoutSeconds",
                    metadata={"cleanup": _cleanup_metadata()},
                )
            await asyncio.sleep(min(interval, max(0.001, timeout - elapsed)))
            emit_pentest_activity_heartbeat(
                phase="running",
                agent_run_id=request.agent_run_id,
                step_id=request.step_id,
                attempt=request.attempt,
                message="Pentest workload is still running.",
                elapsed_seconds=time.monotonic() - monotonic_started_at,
            )
    except asyncio.CancelledError:
        await _stop_and_remove(terminal_reason="cancellation")
        raise
    except Exception:
        await _stop_and_remove(terminal_reason="failure")
        raise

async def cleanup_pentest_orphan_containers(
    janitor: Any,
    *,
    agent_run_id: str | None = None,
    step_id: str | None = None,
    runner_profile_id: str | None = None,
) -> dict[str, Any]:
    """Remove orphaned Pentest containers selected by deterministic labels."""

    selector = pentest_cleanup_selector(
        agent_run_id=agent_run_id,
        step_id=step_id,
        runner_profile_id=runner_profile_id,
    )
    container_ids = await janitor.find_by_labels(selector)
    removed: list[str] = []
    errors: list[str] = []
    for container_id in container_ids:
        try:
            await janitor.remove(container_id)
            removed.append(str(container_id))
        except Exception as exc:
            errors.append(redact_pentest_human_text(str(exc)))
    return {
        "selector": selector,
        "selected_count": len(container_ids),
        "removed_count": len(removed),
        "removed_container_ids": removed,
        "cleanup_errors": errors,
    }

_GIT_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS = 100_000
_GIT_PUSH_SCAN_MAX_FILE_DIFF_CHARS = 200_000
_GIT_PUSH_SCAN_MAX_CHANGED_FILES = 200

_PROPOSAL_TELEMETRY_SIGNAL_TAGS = {
    "retry",
    "duplicate_output",
    "missing_ref",
    "conflicting_instructions",
    "flaky_test",
    "loop_detected",
    "artifact_gap",
}
_PROPOSAL_TELEMETRY_TAG_ALIASES = {
    "artifact": "artifact_gap",
    "artifact_missing": "artifact_gap",
    "diagnostic_gap": "artifact_gap",
    "duplicate": "duplicate_output",
    "duplicate_outputs": "duplicate_output",
    "flaky": "flaky_test",
    "flaky_tests": "flaky_test",
    "loop": "loop_detected",
    "missing_file": "missing_ref",
    "missing_files": "missing_ref",
    "missing_reference": "missing_ref",
    "missing_refs": "missing_ref",
    "repeated_retry": "retry",
    "retry_exhausted": "retry",
}
_PROPOSAL_TELEMETRY_TAG_LABELS = {
    "artifact_gap": "artifact gap",
    "conflicting_instructions": "conflicting instructions",
    "duplicate_output": "duplicate output",
    "flaky_test": "flaky test",
    "loop_detected": "loop detection",
    "missing_ref": "missing reference",
    "retry": "retry",
}
_AUTO_SKILL_SENTINEL = "auto"
_NON_SECRET_MANAGED_SESSION_ENV_KEYS: tuple[str, ...] = (
    "MOONMIND_URL",
    # Non-secret Unreal toolchain image refs. Threaded into the managed-session
    # launch environment so the docker-sidecar manifest preflight and the agent's
    # build/test skill see the operator-configured image instead of falling back
    # to a gated, late-failing pull. GHCR pull *credentials* are intentionally not
    # here: they are secrets resolved separately by
    # resolve_ghcr_pull_credentials_for_launch.
    "MOONMIND_UNREAL_ENGINE_IMAGE",
    "MOONMIND_DOCKER_PREFLIGHT_IMAGE_REF",
)
_MANAGED_SESSION_TELEMETRY_KEYS: tuple[str, ...] = (
    "activityType",
    "agentRunId",
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
def build_git_push_with_lease_args(
    *,
    branch: str,
    recorded_remote_sha: str | None = None,
) -> list[str]:
    """Build the MM-680 branch publish command with lease semantics."""

    branch_name = str(branch or "").strip()
    if not branch_name:
        raise ValueError("branch is required")
    remote_sha = str(recorded_remote_sha or "").strip()
    lease = (
        f"--force-with-lease=refs/heads/{branch_name}:{remote_sha}"
        if remote_sha
        else f"--force-with-lease=refs/heads/{branch_name}:"
    )
    return ["push", "-u", lease, "origin", branch_name]


def classify_git_push_failure(
    *,
    stderr: str,
    branch: str,
    base_branch: str | None = None,
) -> dict[str, Any]:
    """Classify git push failures that indicate a retryable lease conflict."""

    detail = str(stderr or "").strip() or "(no stderr)"
    lowered = detail.casefold()
    lease_markers = (
        "stale info",
        "fetch first",
        "non-fast-forward",
        "would clobber",
        "force-with-lease",
    )
    if any(marker in lowered for marker in lease_markers):
        result: dict[str, Any] = {
            "push_status": "lease_conflict",
            "push_branch": branch,
            "push_error": detail,
            "retryable": True,
            "diagnostic_kind": "publish_lease_conflict",
            "summary": (
                "Remote branch changed before publish; fetch/rebase or retry "
                "with updated lease."
            ),
        }
        if base_branch:
            result["push_base_branch"] = base_branch
        return result
    return {
        "push_status": "failed",
        "push_branch": branch,
        "push_error": detail,
    }


def build_target_aware_prepared_context_payload(
    task_payload: Mapping[str, Any],
    *,
    logical_step_id: str,
) -> dict[str, Any]:
    """Return compact prepared context payload or bounded prepare failure."""

    try:
        manifest = build_prepared_input_manifest(task_payload)
        context = select_step_prepared_context(
            manifest,
            logical_step_id=logical_step_id,
        )
        return {
            "ok": True,
            "manifestRef": manifest.manifest_ref,
            "context": context.to_metadata(),
        }
    except Exception as exc:
        failure = PreparedContextFailure.from_exception(
            exc,
            logical_step_id=logical_step_id,
        )
        return {"ok": False, "failure": failure.model_dump(by_alias=True)}


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

def _artifact_ref_for_pentest_name(
    publication: Mapping[str, Any],
    name: str,
) -> str | None:
    """Return the compact artifact ref for a named Pentest publication artifact."""

    artifact_publication = publication.get("artifact_publication")
    if not isinstance(artifact_publication, Mapping):
        return None
    artifacts = artifact_publication.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        if artifact.get("name") == name:
            ref = artifact.get("artifact_ref")
            return str(ref).strip() if ref else None
    return None
_OPERATOR_SUMMARY_TAIL_BYTES = 64 * 1024
_PUBLISH_GIT_EXCLUDED_PATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "live_streams.spool",
)
_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS = 10.0

class ManagedSessionController(Protocol):
    """Remote control surface for managed session containers."""

    async def ensure_repo_artifacts_writable_by_runtime_user(
        self,
        workspace_path: str,
        /,
    ) -> None:
        pass

    async def launch_session(
        self, request: LaunchCodexManagedSessionRequest, /
    ) -> CodexManagedSessionHandle | Mapping[str, Any]:
        pass

    async def session_status(
        self, request: CodexManagedSessionLocator, /
    ) -> CodexManagedSessionHandle | Mapping[str, Any]:
        pass

    async def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
        /,
        *,
        observation_sink: Callable[
            [list[Any], str, CodexManagedSessionLocator], Awaitable[None]
        ] | None = None,
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

    async def ensure_docker_sidecar(
        self, request: ManagedSessionEnsureDockerSidecarRequest, /
    ) -> ManagedSessionEnsureDockerSidecarResponse | Mapping[str, Any]:
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

    async def reap_orphan_session_containers(self) -> Any:
        pass

    async def collect_managed_runtime_cleanup_docker_references(self) -> Any:
        pass

def _managed_runtime_artifact_root() -> Path:
    return managed_runtime_artifact_root()

class TemporalActivityRuntimeError(RuntimeError):
    """Raised when one of the Temporal activity helpers cannot complete."""

def _docker_workflows_disabled_failure() -> temporal_exceptions.ApplicationError:
    return temporal_exceptions.ApplicationError(
        "policy_denied: docker_workflows_disabled",
        type="docker_workflows_disabled",
        non_retryable=True,
    )

def _docker_workflow_mode_forbidden_failure(*, workflow_docker_mode: str, tool_name: str) -> temporal_exceptions.ApplicationError:
    return temporal_exceptions.ApplicationError(
        f"policy_denied: docker_workflow_mode_forbidden ({tool_name} requires unrestricted; current mode={workflow_docker_mode})",
        type="docker_workflow_mode_forbidden",
        non_retryable=True,
    )

CODEX_TRANSIENT_TURN_ERROR_TYPE = "CodexTransientTurnError"
CODEX_PERMANENT_TURN_ERROR_TYPE = "CodexPermanentTurnError"
CODEX_EMPTY_ASSISTANT_FAILURE_CAUSE = "app_server_protocol_empty_turn"
_PROVIDER_NATIVE_PR_AGENT_IDS = frozenset({"jules", "jules_api"})


def _normalize_provider_native_pr_agent_id(agent_id: str | None) -> str:
    if not isinstance(agent_id, str):
        return ""
    return agent_id.strip().lower().replace("-", "_")


def _is_empty_assistant_recovery_failure(
    metadata: Mapping[str, Any] | None,
) -> bool:
    if not metadata:
        return False
    failure_cause = str(metadata.get("failureCause") or "").strip()
    if failure_cause == CODEX_EMPTY_ASSISTANT_FAILURE_CAUSE:
        return True
    retry_action = str(metadata.get("retryRecommendedAction") or "").strip()
    reason = str(metadata.get("reason") or "").strip()
    return (
        retry_action == "clear_session"
        and "produced no assistant output" in reason
    )

def _codex_transient_turn_failure(
    reason: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> temporal_exceptions.ApplicationError:
    metadata_payload = dict(metadata or {})
    return temporal_exceptions.ApplicationError(
        reason or "codex turn produced no assistant output",
        metadata_payload,
        type=CODEX_TRANSIENT_TURN_ERROR_TYPE,
        non_retryable=_is_empty_assistant_recovery_failure(metadata_payload),
    )

def _codex_permanent_turn_failure(
    reason: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> temporal_exceptions.ApplicationError:
    return temporal_exceptions.ApplicationError(
        reason or "codex turn failed",
        dict(metadata or {}),
        type=CODEX_PERMANENT_TURN_ERROR_TYPE,
        non_retryable=True,
    )

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
class _SandboxFileSnapshotEntry:
    """Compact file state used to detect sandbox write-policy violations."""

    mode: int
    size: int
    mtime_ns: int
    digest: str
    backup_path: Path | None = None

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
    "artifact.publish_report_bundle": ("artifacts", "artifact_publish_report_bundle"),
    "artifact.read": ("artifacts", "artifact_read"),
    "execution.dependency_status_snapshot": (
        "artifacts",
        "execution_dependency_status_snapshot",
    ),
    "execution.record_terminal_state": (
        "artifacts",
        "execution_record_terminal_state",
    ),
    "resilience.compile_policy": ("artifacts", "resilience_compile_policy"),
    "execution.notify_completion": ("agent_runtime", "execution_notify_completion"),
    "artifact.list_for_execution": ("artifacts", "artifact_list_for_execution"),
    "artifact.compute_preview": ("artifacts", "artifact_compute_preview"),
    "artifact.link": ("artifacts", "artifact_link"),
    "artifact.pin": ("artifacts", "artifact_pin"),
    "artifact.unpin": ("artifacts", "artifact_unpin"),
    "artifact.lifecycle_sweep": ("artifacts", "artifact_lifecycle_sweep"),
    "step_checkpoint.create": ("artifacts", "step_checkpoint_create"),
    "step_checkpoint.validate": ("artifacts", "step_checkpoint_validate"),
    "manifest.compile": ("manifest", "manifest_compile"),
    "manifest.write_summary": ("manifest", "manifest_write_summary"),
    "plan.generate": ("plans", "plan_generate"),
    "plan.validate": ("plans", "plan_validate"),
    "mm.tool.execute": ("skills", "mm_tool_execute"),
    "mm.skill.execute": ("skills", "mm_skill_execute"),
    "sandbox.checkout_repo": ("sandbox", "sandbox_checkout_repo"),
    "sandbox.apply_patch": ("sandbox", "sandbox_apply_patch"),
    "sandbox.run_command": ("sandbox", "sandbox_run_command"),
    "sandbox.run_tests": ("sandbox", "sandbox_run_tests"),
    "workspace.capture_checkpoint": ("sandbox", "workspace_capture_checkpoint"),
    "workspace.apply_checkpoint": ("sandbox", "workspace_apply_policy"),
    "agent_runtime.capture_workspace_checkpoint": (
        "agent_runtime",
        "agent_runtime_capture_workspace_checkpoint",
    ),
    "workspace.apply_policy": ("sandbox", "workspace_apply_policy"),
    "workspace.classify_git_effect": ("sandbox", "workspace_classify_git_effect"),
    "provider_profile.list": ("artifacts", "provider_profile_list"),
    "provider_profile.ensure_manager": ("artifacts", "provider_profile_ensure_manager"),
    "provider_profile.acquire_credential_maintenance_lease": (
        "artifacts",
        "provider_profile_acquire_credential_maintenance_lease",
    ),
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
    "provider_profile.pending_request_order": (
        "artifacts",
        "provider_profile_pending_request_order",
    ),
    "oauth_session.prepare_credential_maintenance": ("agent_runtime", "oauth_session_prepare_credential_maintenance"),
    "oauth_session.revalidate_bound_host": ("agent_runtime", "oauth_session_revalidate_bound_host"),
    "oauth_session.ensure_volume": ("agent_runtime", "oauth_session_ensure_volume"),
    "oauth_session.start_auth_runner": ("agent_runtime", "oauth_session_start_auth_runner"),
    "oauth_session.update_terminal_session": ("artifacts", "oauth_session_update_terminal_session"),
    "oauth_session.stop_auth_runner": ("agent_runtime", "oauth_session_stop_auth_runner"),
    "oauth_session.update_status": ("artifacts", "oauth_session_update_status"),
    "oauth_session.mark_failed": ("artifacts", "oauth_session_mark_failed"),
    "oauth_session.cleanup_stale": ("artifacts", "oauth_session_cleanup_stale"),
    "oauth_session.verify_volume": ("agent_runtime", "oauth_session_verify_volume"),
    "oauth_session.verify_cli_fingerprint": ("agent_runtime", "oauth_session_verify_cli_fingerprint"),
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
    "publication_recovery.observe": ("integrations", "publication_recovery_observe"),
    "publication_recovery.publish": ("integrations", "publication_recovery_publish"),
    "publication_recovery.verify": ("integrations", "publication_recovery_verify"),
    "publication_recovery.restore_candidate": (
        "agent_runtime",
        "publication_recovery_restore_candidate",
    ),
    "publication_recovery.cleanup": (
        "agent_runtime",
        "publication_recovery_cleanup",
    ),
    "publication_recovery.persist_result": (
        "artifacts",
        "publication_recovery_persist_result",
    ),
    "merge_automation.evaluate_readiness": (
        "integrations",
        "merge_automation_evaluate_readiness",
    ),
    "merge_automation.complete_post_merge_jira": (
        "integrations",
        "merge_automation_complete_post_merge_jira",
    ),
    "merge_automation.complete_post_merge_github": (
        "integrations",
        "merge_automation_complete_post_merge_github",
    ),
    "pr_resolver.resolve_selector": (
        "integrations",
        "pr_resolver_resolve_selector",
    ),
    "pr_resolver.read_snapshot": ("integrations", "pr_resolver_read_snapshot"),
    "pr_resolver.classify_gate": ("integrations", "pr_resolver_classify_gate"),
    "pr_resolver.finalize_merge": ("integrations", "pr_resolver_finalize_merge"),
    "pr_resolver.verify_remote_head": (
        "integrations",
        "pr_resolver_verify_remote_head",
    ),
    "pr_resolver.verify_merged": ("integrations", "pr_resolver_verify_merged"),
    "worker.verify_workflow_capability": (
        "integrations",
        "worker_verify_workflow_capability",
    ),
    "pr_resolver.write_terminal_result": (
        "artifacts",
        "pr_resolver_write_terminal_result",
    ),
    "memory.evaluate_proposals": ("integrations", "memory_evaluate_proposals"),
    "memory.apply_policy": ("integrations", "memory_apply_policy"),
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
    "integration.omnigent.execute": ("integrations", "integration_omnigent_execute"),
    "integration.omnigent.profile_bound_execute": ("agent_runtime", "integration_omnigent_profile_bound_execute"),
    "integration.omnigent.oauth_host_janitor": ("agent_runtime", "integration_omnigent_oauth_host_janitor"),
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
    "agent_runtime.ensure_docker_sidecar": (
        "agent_runtime",
        "agent_runtime_ensure_docker_sidecar",
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
    "agent_runtime.publish_bridge_events": (
        "agent_runtime",
        "agent_runtime_publish_bridge_events",
    ),
    "agent_runtime.reconcile_managed_sessions": (
        "agent_runtime",
        "agent_runtime_reconcile_managed_sessions",
    ),
    "agent_runtime.cleanup_managed_runtime_files": (
        "agent_runtime",
        "agent_runtime_cleanup_managed_runtime_files",
    ),
    "agent_runtime.restore_workspace_checkpoint": (
        "agent_runtime",
        "agent_runtime_restore_workspace_checkpoint",
    ),
    "agent_runtime.status": ("agent_runtime", "agent_runtime_status"),
    "agent_runtime.fetch_result": ("agent_runtime", "agent_runtime_fetch_result"),
    "agent_runtime.publish_terminal_checkpoint": (
        "agent_runtime",
        "agent_runtime_publish_terminal_checkpoint",
    ),
    "agent_runtime.evaluate_terminal_evidence": (
        "agent_runtime",
        "agent_runtime_evaluate_terminal_evidence",
    ),
    "agent_runtime.cancel": ("agent_runtime", "agent_runtime_cancel"),
    "workload.run": ("agent_runtime", "workload_run"),
    **{
        f"container_job.{name}": ("agent_runtime", f"container_job_{name}")
        for name in (
            "submit",
            "status",
            "cancel",
            "resolve_workspace",
            "acquire_image",
            "create_container",
            "start_container",
            "observe_container",
            "reconcile_container",
            "stop_container",
            "remove_container",
            "publish_evidence",
            "project_status",
            "repair_projection",
            "cleanup",
        )
    },
    "security.pentest.execute": ("agent_runtime", "security_pentest_execute"),
    "proposal.generate": ("proposals", "proposal_generate"),
    "proposal.submit": ("proposals", "proposal_submit"),
    "step.review": ("reviews", "step_review"),
    "agent_skill.resolve": ("agent_skills", "resolve_skills"),
    "agent_skill.build_prompt_index": ("agent_skills", "build_prompt_index"),
    "agent_skill.materialize": ("agent_skills", "materialize"),
    "agent_skill.query_on_demand": ("agent_skills", "query_on_demand"),
    "agent_skill.request_on_demand": ("agent_skills", "request_on_demand"),
}

# ``mm.tool.execute`` is registered as a capability-routed alias on multiple
# fleets rather than as one catalog route. The workflow-fleet helper is bound
# by ``workflow_registry`` because workflow workers do not use this module's
# side-effecting activity implementations.
_CAPABILITY_ROUTED_ACTIVITY_ALIASES = frozenset({"mm.tool.execute"})
_EXTERNALLY_BOUND_CATALOG_ACTIVITIES = frozenset(
    {"integration.resolve_adapter_metadata"}
)


def validate_activity_catalog_runtime_bindings(
    catalog: TemporalActivityCatalog,
) -> None:
    """Fail startup when canonical routes and concrete handlers drift apart."""
    catalog_types = {definition.activity_type for definition in catalog.activities}
    runtime_types = set(_ACTIVITY_HANDLER_ATTRS)
    missing_catalog_routes = sorted(
        runtime_types - catalog_types - _CAPABILITY_ROUTED_ACTIVITY_ALIASES
    )
    missing_runtime_handlers = sorted(
        catalog_types - runtime_types - _EXTERNALLY_BOUND_CATALOG_ACTIVITIES
    )
    if not missing_catalog_routes and not missing_runtime_handlers:
        return

    details: list[str] = []
    if missing_catalog_routes:
        details.append(
            "handlers without catalog routes: " + ", ".join(missing_catalog_routes)
        )
    if missing_runtime_handlers:
        details.append(
            "catalog routes without handlers: " + ", ".join(missing_runtime_handlers)
        )
    raise TemporalActivityRuntimeError(
        "Temporal activity catalog/runtime binding mismatch; " + "; ".join(details)
    )


def _artifact_id_from_ref(value: ArtifactRef | str) -> str:
    if isinstance(value, ArtifactRef):
        return value.artifact_id
    normalized = str(value or "").strip()
    if not normalized:
        raise TemporalActivityRuntimeError("artifact reference is required")
    return normalized


def _string_or_none_for_activity(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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

def _default_registry_skill_payload(*, name: str) -> dict[str, Any]:
    if is_container_job_tool(name):
        return build_container_job_tool_definition_payload(name=name)

    if name == DEPLOYMENT_UPDATE_TOOL_NAME:
        return build_deployment_update_tool_definition_payload()

    if name == OPS_DIAGNOSE_STACK_TOOL_NAME:
        return build_ops_diagnose_stack_tool_definition_payload()

    if name == "security.pentest.run":
        return {
            "name": name,
            "type": "skill",
            "description": (
                "Run an authorized PentestGPT workload against an approved "
                "target scope and publish normalized findings plus evidence "
                "artifacts."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "required": ["target"],
                    "properties": {
                        "target": {"type": "string"},
                        "scope_artifact_ref": {
                            "type": "string",
                            "description": (
                                "Advanced: ArtifactRef for the approved pentest "
                                "scope document. Execution still fails closed "
                                "until MoonMind has an approved scope for the "
                                "target."
                            ),
                        },
                        "objective": {"type": "string"},
                        "operation_mode": {
                            "type": "string",
                            "enum": [
                                "recon_only",
                                "validate_hypothesis",
                                "full_authorized",
                            ],
                            "default": "recon_only",
                        },
                        "runner_profile_id": {
                            "type": "string",
                            "default": "pentestgpt-claude-oauth",
                        },
                        "execution_profile_ref": {
                            "type": "string",
                            "description": (
                                "Exact Provider Profile to use for PentestGPT."
                            ),
                        },
                        "provider_selector": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "provider_id": {"type": "string"},
                                "tags_any": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "tags_all": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        "provider_runtime_state": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "profile_id": {"type": "string"},
                                    "current_leases": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "available_slots": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "cooldown_until": {"type": "string"},
                                },
                            },
                        },
                        "time_budget_minutes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 480,
                            "default": 60,
                        },
                        "repo_dir": {"type": "string"},
                        "artifacts_dir": {
                            "type": "string",
                            "description": (
                                "Task artifact directory for PentestGPT evidence "
                                "outputs. Supplied by the runtime when not "
                                "provided by a trusted caller."
                            ),
                        },
                        "evidence_level": {
                            "type": "string",
                            "enum": ["minimal", "standard", "full"],
                            "default": "standard",
                        },
                        "network_attachment_ref": {
                            "type": "string",
                            "description": (
                                "Optional artifact or ref reserved for future "
                                "elevated-network runner profiles."
                            ),
                        },
                    },
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": [
                        "status",
                        "target",
                        "runner_profile_id",
                        "launch_plan",
                    ],
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["launch_plan_ready", "provider_cooldown"],
                        },
                        "target": {"type": "string"},
                        "runner_profile_id": {"type": "string"},
                        "provider_profile": {"type": "object"},
                        "provider_lease": {"type": "object"},
                        "provider_cooldown": {
                            "type": "object",
                            "properties": {
                                "profile_id": {"type": "string"},
                                "cooldown_seconds": {
                                    "type": "integer",
                                    "minimum": 0,
                                },
                                "failure_category": {"type": "string"},
                                "retry_allowed": {"type": "boolean"},
                            },
                        },
                        "instruction_bundle": {"type": "object"},
                        "runtime_paths": {"type": "object"},
                        "wrapper_invocation": {"type": "object"},
                        "launch_plan": {
                            "type": "object",
                            "required": [
                                "profile_id",
                                "container_name",
                                "image",
                                "entrypoint",
                                "workdir",
                                "network_policy",
                                "linux_capabilities",
                                "devices",
                                "labels",
                                "cleanup_selector",
                            ],
                            "properties": {
                                "profile_id": {"type": "string"},
                                "container_name": {"type": "string"},
                                "image": {"type": "string"},
                                "entrypoint": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "workdir": {"type": "string"},
                                "mounts": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                },
                                "env_keys": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "network_policy": {"type": "string"},
                                "linux_capabilities": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "devices": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "resources": {"type": "object"},
                                "timeout_seconds": {"type": "integer"},
                                "cleanup": {"type": "object"},
                                "labels": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "cleanup_selector": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                        },
                    },
                }
            },
            "executor": {
                "activity_type": "security.pentest.execute",
                "selector": {"mode": "by_capability"},
                "binding_reason": "stronger_isolation",
            },
            "requirements": {"capabilities": ["agent_runtime"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 28800,
                    "schedule_to_close_seconds": 32400,
                },
                "retries": {
                    "max_attempts": 1,
                    "backoff": "none",
                    "non_retryable_error_codes": [
                        "INVALID_SCOPE",
                        "PERMISSION_DENIED",
                        "UNAPPROVED_TARGET",
                        "UNSUPPORTED_PROFILE",
                        "NON_IDEMPOTENT_OPERATION",
                    ],
                },
            },
            "security": {"allowed_roles": ["admin", "security_operator"]},
        }

    if name == JIRA_CHECK_BLOCKERS_TOOL_NAME:
        return {
            "name": name,
            "description": (
                "Check whether a Jira issue is blocked by unresolved inbound "
                "Blocks links using trusted Jira data."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "targetIssueKey": {"type": "string"},
                        "issueKey": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "blockerPreflight": {"type": "object"},
                        "assessmentArtifactPath": {"type": "string"},
                        "assessment_artifact_path": {"type": "string"},
                        "assessmentVerdict": {"type": "string"},
                        "assessment_verdict": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": ["targetIssueKey", "decision", "summary"],
                    "properties": {
                        "targetIssueKey": {"type": "string"},
                        "decision": {"type": "string", "enum": ["continue", "blocked"]},
                        "blockingIssues": {"type": "array"},
                        "resolvedBlockingIssues": {"type": "array"},
                        "assessmentVerdict": {"type": "string"},
                        "summary": {"type": "string"},
                    },
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
                    "start_to_close_seconds": 60,
                    "schedule_to_close_seconds": 120,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == JIRA_LOAD_PRESET_BRIEF_TOOL_NAME:
        return {
            "name": name,
            "description": (
                "Load a compact Jira preset brief through MoonMind's trusted "
                "Jira service."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "issueKey": {"type": "string"},
                        "issue_key": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "jira_issue_key": {"type": "string"},
                        "artifactPath": {"type": "string"},
                        "artifact_path": {"type": "string"},
                        "briefArtifactPath": {"type": "string"},
                        "brief_artifact_path": {"type": "string"},
                        "jira": {"type": "object"},
                        "issue": {"type": "object"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": [
                        "trustedSource",
                        "jiraIssueKey",
                        "jiraPresetBrief",
                        "summary",
                    ],
                    "properties": {
                        "trustedSource": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "jiraPresetBrief": {"type": "string"},
                        "presetBrief": {"type": "string"},
                        "jiraStepInstructions": {"type": "string"},
                        "artifactPath": {"type": "string"},
                        "resolvedSourceDesignPath": {"type": "string"},
                        "sourceResolution": {"type": "object"},
                        "jiraIssue": {"type": "object"},
                        "summary": {"type": "string"},
                    },
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
                    "start_to_close_seconds": 60,
                    "schedule_to_close_seconds": 120,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME:
        return {
            "name": name,
            "description": (
                "Move a Jira issue to a named status through MoonMind's "
                "trusted Jira transition path."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "issueKey": {"type": "string"},
                        "issue_key": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "jira_issue_key": {"type": "string"},
                        "targetStatus": {"type": "string"},
                        "target_status": {"type": "string"},
                        "statusName": {"type": "string"},
                        "status_name": {"type": "string"},
                        "mode": {"type": "string"},
                        "assessmentArtifactPath": {"type": "string"},
                        "assessment_artifact_path": {"type": "string"},
                        "assessmentVerdict": {"type": "string"},
                        "assessment_verdict": {"type": "string"},
                        "fields": {"type": "object"},
                        "update": {"type": "object"},
                        "jira": {"type": "object"},
                        "issue": {"type": "object"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": ["issueKey", "targetStatus", "decision", "summary"],
                    "properties": {
                        "issueKey": {"type": "string"},
                        "targetStatus": {"type": "string"},
                        "decision": {"type": "string"},
                        "transitioned": {"type": "boolean"},
                        "transitionId": {"type": "string"},
                        "currentStatus": {"type": "object"},
                        "confirmedStatus": {"type": "object"},
                        "summary": {"type": "string"},
                    },
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
                    "start_to_close_seconds": 60,
                    "schedule_to_close_seconds": 120,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == "story.create_jira_issues":
        return {
            "name": name,
            "description": "Create Jira issues from MoonSpec story breakdown output.",
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

    if name == "story.create_github_issues":
        return {
            "name": name,
            "description": "Create GitHub issues from MoonSpec story breakdown output.",
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
            "requirements": {"capabilities": ["integration:github"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 300,
                    "schedule_to_close_seconds": 600,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name in {
        "story.create_github_issue_implement_workflows",
        "story.create_github_issue_orchestrate_workflows",
    }:
        return {
            "name": name,
            "description": (
                "Create downstream MoonMind workflows from GitHub issue mappings."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "github": {"type": "object"},
                        "issueMappings": {"type": "array"},
                        "githubOrchestration": {"type": "object"},
                        "traceability": {"type": "object"},
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
            "requirements": {"capabilities": ["integration:github"]},
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
        if name == _AUTO_SKILL_SENTINEL
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
) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()

    if not isinstance(parameters, Mapping):
        return tuple(selected)

    task_payload = parameters.get("workflow")
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
        if tool_name.lower() in JIRA_AGENT_SKILLS:
            continue
        if tool_name in seen:
            continue
        seen.add(tool_name)
        selected.append(tool_name)

    # 'auto' is a placeholder meaning "no explicit skill selected". It should
    # not be included in the registry as a dispatchable skill — when only 'auto'
    # is present, the runtime should be used directly without skill dispatch.
    if selected and all(name == _AUTO_SKILL_SENTINEL for name in selected):
        selected = []

    return tuple(selected)

def _default_skill_registry_payload(
    *,
    parameters: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    requested: list[str] = []
    seen: set[str] = set()
    for payload in (parameters, inputs):
        for name in _iter_requested_registry_tools(payload):
            if name in seen:
                continue
            seen.add(name)
            requested.append(name)

    return {
        "skills": [
            _default_registry_skill_payload(name=name)
            for name in requested
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

def _coerce_activity_payload_input(
    request: Any,
    *,
    activity_type: str,
    kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(request, BaseModel):
        request = request.model_dump(mode="json", by_alias=True)
    elif request is None:
        request = kwargs
    return _coerce_activity_request(request, activity_type=activity_type)

def _provider_profile_prefers_proxy_first(profile: Mapping[str, Any]) -> bool:
    tags = {str(tag).strip().lower() for tag in profile.get("tags") or []}
    if "proxy-first" in tags:
        return True
    if "direct-credentials-required" in tags:
        return False

    command_behavior = profile.get("command_behavior")
    if not isinstance(command_behavior, Mapping):
        command_behavior = profile.get("commandBehavior")
    if isinstance(command_behavior, Mapping) and bool(
        command_behavior.get("requires_direct_credentials")
        or command_behavior.get("requiresDirectCredentials")
    ):
        return False

    provider = (
        str(profile.get("provider_id") or profile.get("providerId") or "")
        .strip()
        .lower()
    )
    credential_source_raw = (
        profile.get("credential_source") or profile.get("credentialSource") or ""
    )
    credential_source = str(
        getattr(credential_source_raw, "value", credential_source_raw)
    ).strip().lower()
    materialization_mode_raw = (
        profile.get("runtime_materialization_mode")
        or profile.get("runtimeMaterializationMode")
        or ""
    )
    materialization_mode = str(
        getattr(materialization_mode_raw, "value", materialization_mode_raw)
    ).strip().lower()
    if provider == "minimax":
        return False
    return (
        provider in {"anthropic", "openai"}
        and credential_source == "secret_ref"
        and materialization_mode in {"api_key_env", "env_bundle"}
    )

def _redacted_webhook_target(webhook_url: str) -> str:
    if not webhook_url:
        return ""
    return webhook_url.split("?", 1)[0]


def _coerce_notification_recipients(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = value.split(",")
    else:
        raw_values = [str(item) for item in value]
    return [item.strip() for item in raw_values if item.strip()]


def _redacted_email_target(recipients: Sequence[str]) -> str:
    count = len([recipient for recipient in recipients if str(recipient).strip()])
    if count == 1:
        return "email:1 recipient"
    return f"email:{count} recipients"


def _build_execution_notification_payload(
    payload: Mapping[str, Any],
    *,
    redact: bool = True,
) -> dict[str, Any]:
    result_payload = payload.get("result")
    if isinstance(result_payload, BaseModel):
        result_payload = result_payload.model_dump(mode="json", by_alias=True)
    result = result_payload if isinstance(result_payload, Mapping) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    event: dict[str, Any] = {
        "event": "moonmind.execution.completed",
        "workflowId": str(payload.get("workflowId") or ""),
        "runId": str(payload.get("runId") or ""),
        "agentId": str(payload.get("agentId") or ""),
        "agentKind": str(payload.get("agentKind") or ""),
        "status": str(payload.get("status") or ""),
        "failureClass": result.get("failureClass"),
        "providerErrorCode": result.get("providerErrorCode"),
        "retryRecommendation": result.get("retryRecommendation"),
        "summary": result.get("summary"),
        "diagnosticsRef": result.get("diagnosticsRef"),
        "outputRefs": list(result.get("outputRefs") or []),
    }
    for key in (
        "agentRunId",
        "childWorkflowId",
        "childRunId",
        "pullRequestUrl",
        "publishOutcome",
    ):
        value = metadata.get(key)
        if value is not None:
            event[key] = value
    if redact:
        return redact_sensitive_payload(event)
    return event


def _build_execution_notification_email(
    event: Mapping[str, Any],
    *,
    sender: str,
    recipients: Sequence[str],
) -> EmailMessage:
    status = str(event.get("status") or "completed")
    workflow_id = str(event.get("workflowId") or "unknown-workflow")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = f"MoonMind execution {status}: {workflow_id}"
    message.set_content(
        "MoonMind execution completed.\n\n"
        + json.dumps(dict(event), default=str, indent=2, sort_keys=True)
        + "\n"
    )
    return message


def _scan_execution_notification_before_send(
    event: Mapping[str, Any],
    *,
    surface: str,
) -> str | None:
    result = scan_outbound_text(
        json.dumps(dict(event), sort_keys=True, default=str),
        location=surface,
        settings=settings,
    )
    if result.allowed:
        return None
    diagnostics = "; ".join(result.sanitized_diagnostics) or (
        f"Blocked outbound content at {surface}"
    )
    logger.warning(
        "Blocked execution notification send at %s: %s",
        surface,
        diagnostics,
    )
    return diagnostics


def _send_execution_notification_email(
    event: Mapping[str, Any],
    *,
    sender: str,
    recipients: Sequence[str],
    smtp_host: str,
    smtp_port: int,
    smtp_username: str | None,
    smtp_password: str | None,
    smtp_use_tls: bool,
    smtp_use_ssl: bool,
    timeout_seconds: int,
) -> None:
    scan_result = scan_outbound_text(
        json.dumps(dict(event), default=str, sort_keys=True),
        location="execution.notification.email.body",
    )
    if not scan_result.allowed:
        diagnostics = "; ".join(scan_result.sanitized_diagnostics) or (
            "Blocked outbound content at execution.notification.email.body"
        )
        raise TemporalActivityRuntimeError(
            f"Outbound notification blocked by high security scan: {diagnostics}"
        )
    message = _build_execution_notification_email(
        event,
        sender=sender,
        recipients=recipients,
    )
    smtp_cls = smtplib.SMTP_SSL if smtp_use_ssl else smtplib.SMTP
    with smtp_cls(smtp_host, smtp_port, timeout=timeout_seconds) as client:
        if smtp_use_tls and not smtp_use_ssl:
            client.starttls()
        if smtp_username:
            client.login(smtp_username, smtp_password or "")
        client.send_message(message)

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

def _validate_agent_runtime_terminal_checkpoint_input(
    payload: Any,
) -> AgentRuntimeTerminalCheckpointInput:
    if isinstance(payload, AgentRuntimeTerminalCheckpointInput):
        return payload
    try:
        return AgentRuntimeTerminalCheckpointInput.model_validate(payload)
    except ValidationError as exc:
        raise TemporalActivityRuntimeError(
            f"agent_runtime.publish_terminal_checkpoint payload is invalid: {exc}"
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
            try:
                activity.heartbeat(dict(heartbeat_payload))
            except asyncio.QueueFull:
                # The Temporal SDK coalesces activity heartbeats through a
                # bounded local queue.  Queue saturation means an earlier
                # heartbeat is already pending; it is backpressure, not an
                # activity failure.
                logger.debug("activity_heartbeat_coalesced_queue_full")
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
            registry_payload = _default_skill_registry_payload(
                parameters=parameters,
                inputs=inputs_payload if isinstance(inputs_payload, Mapping) else None,
            )
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
        if resolved_artifact_service is not None:
            execution_context["temporal_artifact_service"] = resolved_artifact_service
            execution_context.setdefault(
                "deployment_evidence_principal",
                principal or "system:deployment",
            )

        try:
            return await execute_skill_activity(
                invocation_payload=invocation_payload,
                registry_snapshot=resolved_snapshot,
                dispatcher=self._dispatcher,
                context=execution_context,
            )
        except ToolFailure as exc:
            raise _tool_failure_application_error(exc) from exc

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


def _tool_failure_application_error(
    exc: ToolFailure,
) -> temporal_exceptions.ApplicationError:
    redactor = SecretRedactor.from_environ(placeholder="[REDACTED]")
    payload = _redacted_tool_failure_payload(exc, redactor=redactor)
    return temporal_exceptions.ApplicationError(
        _scrub_temporal_failure_text(
            f"{exc.error_code}: {exc.message}",
            redactor=redactor,
        ),
        payload,
        type=exc.error_code,
        non_retryable=not exc.retryable,
    )


def _redacted_tool_failure_payload(
    exc: ToolFailure,
    *,
    redactor: SecretRedactor,
) -> dict[str, Any]:
    try:
        encoded = json.dumps(exc.to_payload(), default=str, sort_keys=True)
        scrubbed = _scrub_temporal_failure_text(encoded, redactor=redactor)
        decoded = json.loads(scrubbed)
        return decoded if isinstance(decoded, dict) else {"message": str(decoded)}
    except Exception:
        return {
            "error_code": exc.error_code,
            "message": _scrub_temporal_failure_text(
                exc.message,
                redactor=redactor,
            ),
        }


def _scrub_temporal_failure_text(
    text: str,
    *,
    redactor: SecretRedactor,
) -> str:
    return redact_sensitive_text(redactor.scrub(text))


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
        artifact_store: Any | None = None,
        redactor: SecretRedactor | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._artifact_store = artifact_store or InMemoryArtifactStore()
        self._redactor = redactor or SecretRedactor.from_environ()
        self._workspace_root = Path(
            workspace_root or settings.workflow.workspace_root
        ).resolve()

    async def _put_checkpoint_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if self._artifact_service is not None:
            artifact, _upload = await self._artifact_service.create(
                principal="system",
                content_type=content_type,
                metadata_json=dict(metadata or {}),
            )
            completed = await self._artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="system",
                payload=payload,
                content_type=content_type,
            )
            return _compact_artifact_ref_text(build_artifact_ref(completed))
        artifact = self._artifact_store.put_bytes(
            payload,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        return _compact_artifact_ref_text(artifact)

    async def _read_checkpoint_bytes(self, artifact_ref: str) -> bytes:
        if self._artifact_service is not None:
            _artifact, payload = await self._artifact_service.read(
                artifact_id=artifact_ref,
                principal="system",
                allow_restricted_raw=True,
            )
            return payload
        return self._artifact_store.get_bytes(artifact_ref)

    def _pull_auth_diagnostics(self, context_ref: str | None) -> dict[str, Any]:
        mode = "authenticated" if context_ref else "unavailable"
        if os.environ.get("GHCR_PULL_USER") or os.environ.get("GHCR_PULL_TOKEN"):
            mode = "authenticated"
        return {
            "mode": mode,
            "diagnosticRefs": [context_ref] if context_ref else [],
        }

    def _provider_lease_refs(self, context_ref: str | None) -> list[str]:
        return [context_ref] if context_ref else []

    def _workspace_has_unsafe_skill_projection(self, workspace: Path) -> bool:
        for relative in (".agents/skills", ".gemini/skills"):
            candidate = workspace / relative
            if not candidate.exists():
                continue
            try:
                info = candidate.lstat()
            except OSError:
                continue
            if info.st_uid == 0 or candidate.is_symlink():
                return True
        return False

    def _workspace_has_traversal(self, workspace: Path) -> bool:
        for path in workspace.rglob("*"):
            try:
                if path.is_symlink():
                    target = path.resolve()
                    if not target.is_relative_to(workspace):
                        return True
            except OSError:
                return True
        return False

    async def workspace_capture_checkpoint(
        self,
        request: Mapping[str, Any] | WorkspaceCheckpointCaptureInput,
    ) -> dict[str, Any]:
        model = (
            request
            if isinstance(request, WorkspaceCheckpointCaptureInput)
            else WorkspaceCheckpointCaptureInput.model_validate(request)
        )
        pull_auth = self._pull_auth_diagnostics(model.pull_auth_context_ref)
        provider_refs = self._provider_lease_refs(model.provider_lease_context_ref)

        if isinstance(model.workspace_locator, ManagedWorkspaceLocator):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox worker cannot resolve a managed-runtime workspace",
            )

        if model.kind == "external_state_ref":
            if model.workspace_locator is not None and not isinstance(
                model.workspace_locator, ExternalStateLocator
            ):
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_LOCATOR_UNSUPPORTED,
                    "filesystem workspace locator cannot be used as external state",
                )
            if model.workspace_locator is None and model.workspace_root_ref:
                self._record_legacy_workspace_path_usage("capture_checkpoint")
            source_ref = (
                model.workspace_locator.artifact_ref
                if isinstance(model.workspace_locator, ExternalStateLocator)
                else model.external_state_ref or model.workspace_root_ref or ""
            )
            external_state_ref = await self._put_checkpoint_bytes(
                _json_bytes(
                    {
                        "kind": "external_state_ref",
                        "sourceRef": source_ref,
                        "idempotencyKey": model.idempotency_key,
                        "createdAt": datetime.now(UTC).isoformat(),
                    }
                ),
                content_type="application/json",
                metadata={"artifact_kind": "checkpoint_external_state_ref"},
            )
            result = WorkspaceCheckpointCaptureResult(
                status="captured",
                workspace=WorkspaceCheckpointEvidenceModel(
                    kind="external_state_ref",
                    external_state_ref=external_state_ref,
                    createdAt=datetime.now(UTC),
                ),
                summary="external_state_ref checkpoint captured",
                pullAuth=pull_auth,
                providerLeaseRefs=provider_refs,
            )
            return result.model_dump(by_alias=True, mode="json")

        if isinstance(model.workspace_locator, ExternalStateLocator):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_LOCATOR_UNSUPPORTED,
                "external state cannot be used by a local checkpoint operation",
            )
        if model.workspace_locator is None:
            self._record_legacy_workspace_path_usage("capture_checkpoint")
        workspace = (
            self._resolve_sandbox_locator(model.workspace_locator, must_exist=True)
            if isinstance(model.workspace_locator, SandboxWorkspaceLocator)
            else self._resolve_workspace(
                model.workspace_path or model.workspace_root_ref or "",
                must_exist=True,
            )
        )

        if model.kind == "worktree_archive" and (
            self._workspace_has_traversal(workspace)
            or self._workspace_has_unsafe_skill_projection(workspace)
        ):
            diagnostic_ref = await self._put_checkpoint_bytes(
                b"unsafe workspace materialization",
                content_type="text/plain",
                metadata={"artifact_kind": "checkpoint_diagnostic"},
            )
            cleanup_ref = await self._put_checkpoint_bytes(
                b"skill projection cleanup required",
                content_type="text/plain",
                metadata={"artifact_kind": "checkpoint_cleanup"},
            )
            result = WorkspaceCheckpointCaptureResult(
                status="unsafe",
                summary="workspace archive refused unsafe materialization",
                diagnosticRefs=[diagnostic_ref],
                cleanupRefs=[cleanup_ref],
                pullAuth=pull_auth,
                providerLeaseRefs=provider_refs,
                failureCode="unsafe_checkpoint",
            )
            return result.model_dump(by_alias=True, mode="json")

        try:
            workspace_evidence = await self._capture_workspace_evidence(model, workspace)
        except TemporalActivityRuntimeError:
            raise
        except Exception as exc:
            result = WorkspaceCheckpointCaptureResult(
                status="invalid",
                summary=str(exc)[:1000],
                pullAuth=pull_auth,
                providerLeaseRefs=provider_refs,
                failureCode="invalid_checkpoint",
            )
            return result.model_dump(by_alias=True, mode="json")

        result = WorkspaceCheckpointCaptureResult(
            status="captured",
            workspace=workspace_evidence,
            summary=f"{model.kind} checkpoint captured",
            diagnosticRefs=[workspace_evidence.manifest_ref]
            if workspace_evidence.manifest_ref
            else [],
            pullAuth=pull_auth,
            providerLeaseRefs=provider_refs,
        )
        return result.model_dump(by_alias=True, mode="json", exclude_none=True)

    async def _capture_workspace_evidence(
        self,
        model: WorkspaceCheckpointCaptureInput,
        workspace: Path,
    ) -> WorkspaceCheckpointEvidenceModel:
        if model.kind == "git_patch":
            if model.include_untracked or model.include_ignored_files:
                raise TemporalActivityRuntimeError(
                    "git_patch checkpoint kind does not support including untracked or ignored files"
                )
            diff_result = await _run_command(
                ["git", "diff", "--binary", model.base_commit, "--"],
                cwd=str(workspace),
            )
            patch_payload = diff_result.stdout.encode("utf-8")
            patch_ref = await self._put_checkpoint_bytes(
                patch_payload,
                content_type="text/x-diff",
                metadata={"artifact_kind": "checkpoint_patch"},
            )
            manifest_ref = await self._put_checkpoint_bytes(
                _json_bytes(
                    {
                        "kind": "git_patch",
                        "baseCommit": model.base_commit,
                        "patchRef": patch_ref,
                        "bytes": len(patch_payload),
                    }
                ),
                content_type="application/json",
                metadata={"artifact_kind": "checkpoint_manifest"},
            )
            return WorkspaceCheckpointEvidenceModel(
                kind="git_patch",
                baseCommit=model.base_commit,
                patchRef=patch_ref,
                manifestRef=manifest_ref,
                includesUntracked=model.include_untracked,
                includesIgnoredFiles=model.include_ignored_files,
                createdAt=datetime.now(UTC),
            )
        if model.kind == "git_commit":
            head = (
                await _run_command(["git", "rev-parse", "HEAD"], cwd=str(workspace))
            ).stdout.strip()
            return WorkspaceCheckpointEvidenceModel(
                kind="git_commit",
                baseCommit=model.base_commit,
                headCommit=head,
                createdAt=datetime.now(UTC),
            )
        if model.kind == "ephemeral_workspace_ref":
            workspace_ref = await self._put_checkpoint_bytes(
                _json_bytes(
                    {
                        "kind": "ephemeral_workspace_ref",
                        "workspaceRootRef": model.workspace_root_ref,
                        "idempotencyKey": model.idempotency_key,
                        "createdAt": datetime.now(UTC).isoformat(),
                    }
                ),
                content_type="application/json",
                metadata={"artifact_kind": "checkpoint_workspace_ref"},
            )
            return WorkspaceCheckpointEvidenceModel(
                kind="ephemeral_workspace_ref",
                workspace_artifact_ref=workspace_ref,
                createdAt=datetime.now(UTC),
            )
        if model.kind == "worktree_archive":
            archive_payload, entries = self._build_worktree_archive(workspace)
            archive_ref = await self._put_checkpoint_bytes(
                archive_payload,
                content_type="application/vnd.moonmind.worktree-archive",
                metadata={"artifact_kind": "checkpoint_archive"},
            )
            manifest_body: dict[str, Any] = {
                "schemaVersion": "v1",
                "kind": "worktree_archive",
                "baseCommit": model.base_commit,
                "archiveRef": archive_ref,
                "archiveDigest": "sha256:" + hashlib.sha256(archive_payload).hexdigest(),
                "entries": entries,
                "pathCount": len(entries),
            }
            # ``gitStatusDigest`` lets restore cross-check the worktree's staged and
            # untracked state, but only a git worktree exposes one. A non-git
            # workspace still produces a valid archive + manifest; it simply omits
            # the optional digest (restore already treats it as optional).
            if (workspace / ".git").exists():
                status = await _run_command(
                    ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                    cwd=str(workspace),
                )
                manifest_body["gitStatusDigest"] = (
                    "sha256:"
                    + hashlib.sha256(status.stdout.encode("utf-8")).hexdigest()
                )
            manifest_payload = _json_bytes(manifest_body)
            manifest_ref = await self._put_checkpoint_bytes(
                manifest_payload,
                content_type="application/json",
                metadata={"artifact_kind": "checkpoint_manifest"},
            )
            return WorkspaceCheckpointEvidenceModel(
                kind="worktree_archive",
                baseCommit=model.base_commit,
                archiveRef=archive_ref,
                archiveDigest="sha256:" + hashlib.sha256(archive_payload).hexdigest(),
                manifestRef=manifest_ref,
                manifestDigest="sha256:" + hashlib.sha256(manifest_payload).hexdigest(),
                createdAt=datetime.now(UTC),
            )
        raise TemporalActivityRuntimeError(f"unsupported checkpoint kind: {model.kind}")

    def _build_worktree_archive(self, workspace: Path) -> tuple[bytes, list[dict[str, Any]]]:
        entries: list[dict[str, Any]] = []
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for path in sorted(workspace.rglob("*")):
                relative = path.relative_to(workspace)
                if any(part in {".git", "__pycache__"} for part in relative.parts):
                    continue
                if relative.parts[:2] in {
                    (".agents", "skills"),
                    (".gemini", "skills"),
                }:
                    continue
                if path.is_dir():
                    continue
                if path.is_symlink():
                    resolved = path.resolve()
                    if not resolved.is_relative_to(workspace):
                        raise TemporalActivityRuntimeError(
                            f"workspace archive member escapes workspace: {relative}"
                        )
                    info = archive.gettarinfo(str(path), arcname=str(relative))
                    info.uid = _MANAGED_AGENT_UID
                    info.gid = _MANAGED_AGENT_GID
                    info.uname = "moonmind"
                    info.gname = "moonmind"
                    archive.addfile(info)
                    entries.append({
                        "path": str(relative), "type": "symlink",
                        "target": os.readlink(path),
                        "mode": format(stat.S_IMODE(path.lstat().st_mode), "04o"),
                    })
                    continue
                info = archive.gettarinfo(str(path), arcname=str(relative))
                info.uid = _MANAGED_AGENT_UID
                info.gid = _MANAGED_AGENT_GID
                info.uname = "moonmind"
                info.gname = "moonmind"
                with path.open("rb") as file_handle:
                    hashing_reader = _HashingArchiveReader(file_handle)
                    archive.addfile(info, hashing_reader)
                entries.append({
                    "path": str(relative), "type": "file",
                    "digest": "sha256:" + hashing_reader.hexdigest(),
                    "bytes": info.size,
                    "mode": format(stat.S_IMODE(path.stat().st_mode), "04o"),
                })
        return output.getvalue(), entries

    async def workspace_apply_policy(
        self,
        request: Mapping[str, Any] | WorkspacePolicyApplyInput,
    ) -> dict[str, Any]:
        model = (
            request
            if isinstance(request, WorkspacePolicyApplyInput)
            else WorkspacePolicyApplyInput.model_validate(request)
        )
        provider_refs = self._provider_lease_refs(model.provider_lease_context_ref)
        idempotent_result = self._read_workspace_policy_idempotency(
            model.idempotency_key
        )
        if idempotent_result is not None:
            return idempotent_result

        try:
            checkpoint = await self._load_policy_checkpoint(model)
        except TemporalArtifactValidationError:
            return await self._reject_workspace_policy(
                model,
                failure_code="artifact_unauthorized",
                summary="checkpoint artifact evidence is unauthorized",
                provider_refs=provider_refs,
                diagnostic_refs=[],
            )
        except (ArtifactStoreError, TemporalArtifactError):
            return await self._reject_workspace_policy(
                model,
                failure_code="artifact_missing",
                summary="checkpoint artifact evidence is missing",
                provider_refs=provider_refs,
                diagnostic_refs=[],
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return await self._reject_workspace_policy(
                model,
                failure_code="artifact_corrupted",
                summary="checkpoint artifact evidence is corrupt",
                provider_refs=provider_refs,
                diagnostic_refs=[],
            )
        diagnostic_refs: list[str] = []

        workspace_payload = checkpoint.get("workspace")
        if not isinstance(workspace_payload, dict):
            return await self._reject_workspace_policy(
                model,
                failure_code="invalid_checkpoint",
                summary="checkpoint workspace evidence is invalid",
                provider_refs=provider_refs,
                diagnostic_refs=diagnostic_refs,
                checkpoint=checkpoint,
            )
        kind = workspace_payload.get("kind")
        accepted = checkpoint_kinds_for_workspace_policy(model.workspace_policy)
        if accepted and kind not in accepted:
            return await self._reject_workspace_policy(
                model,
                failure_code="policy_incompatible",
                summary="checkpoint kind does not satisfy workspace policy",
                provider_refs=provider_refs,
                diagnostic_refs=diagnostic_refs,
                checkpoint=checkpoint,
            )
        target = self._policy_target_workspace(model)
        try:
            await self._apply_workspace_policy_to_target(
                model.workspace_policy,
                workspace_payload,
                target,
            )
        except ArtifactStoreError:
            return await self._reject_workspace_policy(
                model,
                failure_code="artifact_missing",
                summary="workspace policy artifact evidence is missing",
                provider_refs=provider_refs,
                diagnostic_refs=diagnostic_refs,
                checkpoint=checkpoint,
                target_workspace_ref=str(target),
            )
        except (json.JSONDecodeError, tarfile.TarError, UnicodeDecodeError):
            return await self._reject_workspace_policy(
                model,
                failure_code="artifact_corrupted",
                summary="workspace policy artifact evidence is corrupt",
                provider_refs=provider_refs,
                diagnostic_refs=diagnostic_refs,
                checkpoint=checkpoint,
                target_workspace_ref=str(target),
            )
        except TemporalActivityRuntimeError as exc:
            return await self._reject_workspace_policy(
                model,
                failure_code="workspace_incompatible",
                summary=str(exc)[:1000],
                provider_refs=provider_refs,
                diagnostic_refs=diagnostic_refs,
                checkpoint=checkpoint,
                target_workspace_ref=str(target),
            )

        result = WorkspacePolicyApplyResult(
            status="applied",
            workspaceRef=str(target),
            appliedCheckpointRef=model.checkpoint_ref,
            providerLeaseRefs=provider_refs,
            summary=f"workspace policy {model.workspace_policy} applied",
        ).model_dump(by_alias=True, mode="json")
        self._write_workspace_policy_idempotency(model.idempotency_key, result)
        return result

    async def _load_policy_checkpoint(
        self,
        model: WorkspacePolicyApplyInput,
    ) -> dict[str, Any]:
        payload = await self._read_checkpoint_bytes(model.checkpoint_ref)
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TemporalActivityRuntimeError(
                "checkpoint artifact payload must be a JSON object"
            )
        return decoded

    def _policy_target_workspace(self, model: WorkspacePolicyApplyInput) -> Path:
        locator = model.target_workspace_locator
        if isinstance(locator, ManagedWorkspaceLocator):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox worker cannot apply recovery to a managed-runtime workspace",
            )
        if isinstance(locator, ExternalStateLocator):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_LOCATOR_UNSUPPORTED,
                "external state cannot be used as a local recovery target",
            )
        if isinstance(locator, SandboxWorkspaceLocator):
            return self._resolve_sandbox_locator(locator, must_exist=False)
        if model.target_workspace_ref:
            self._record_legacy_workspace_path_usage("apply_policy")
            return self._resolve_workspace(model.target_workspace_ref, must_exist=False)
        digest = hashlib.sha256(model.idempotency_key.encode("utf-8")).hexdigest()[:16]
        target = (
            self._workspace_root
            / "temporal_sandbox"
            / "policy-workspaces"
            / digest
        )
        return self._resolve_workspace(target, must_exist=False)

    async def _apply_workspace_policy_to_target(
        self,
        policy: str,
        workspace_payload: Mapping[str, Any],
        target: Path,
    ) -> None:
        if policy == "continue_from_previous_execution":
            if workspace_payload.get("kind") == "external_state_ref":
                raise TemporalActivityRuntimeError(
                    "external_state_ref restoration is unsupported without an "
                    "external provider restore bridge"
                )
            if workspace_payload.get("kind") == "ephemeral_workspace_ref" and str(
                workspace_payload.get("workspaceArtifactRef")
                or workspace_payload.get("workspace_artifact_ref")
                or ""
            ).strip():
                raise TemporalActivityRuntimeError(
                    "artifact-backed ephemeral workspace evidence cannot be "
                    "restored as a local sandbox path"
                )
            workspace_ref = str(
                workspace_payload.get("workspaceRef")
                or workspace_payload.get("workspace_ref")
                or ""
            ).strip()
            if workspace_ref:
                source = self._resolve_workspace(workspace_ref, must_exist=True)
                if source != target:
                    self._replace_workspace_tree(source, target)
                return
            if workspace_payload.get("kind") == "git_commit":
                await self._checkout_commit_to_workspace(workspace_payload, target)
                return
            if workspace_payload.get("kind") == "git_patch":
                await self._checkout_commit_to_workspace(workspace_payload, target)
                await self._apply_patch_artifact(workspace_payload, target)
                return
            await self._restore_archive_to_workspace(workspace_payload, target)
            return
        if policy == "restore_pre_execution":
            if workspace_payload.get("kind") == "git_commit":
                await self._checkout_commit_to_workspace(workspace_payload, target)
                return
            if workspace_payload.get("kind") == "ephemeral_workspace_ref":
                if str(
                    workspace_payload.get("workspaceArtifactRef")
                    or workspace_payload.get("workspace_artifact_ref")
                    or ""
                ).strip():
                    raise TemporalActivityRuntimeError(
                        "artifact-backed ephemeral workspace evidence cannot be "
                        "restored as a local sandbox path"
                    )
                source = self._workspace_ref_source(workspace_payload)
                self._replace_workspace_tree(source, target)
                return
            await self._restore_archive_to_workspace(workspace_payload, target)
            return
        if policy == "apply_previous_execution_diff_to_clean_baseline":
            await self._checkout_commit_to_workspace(workspace_payload, target)
            await self._apply_patch_artifact(workspace_payload, target)
            return
        if policy == "start_from_last_passed_commit":
            await self._checkout_commit_to_workspace(workspace_payload, target)
            return
        if policy == "fresh_branch_from_source":
            source_ref = str(
                workspace_payload.get("workspaceRef")
                or workspace_payload.get("workspace_ref")
                or workspace_payload.get("branch")
                or workspace_payload.get("baseCommit")
                or ""
            ).strip()
            if source_ref:
                source = self._resolve_workspace(source_ref, must_exist=True)
                self._replace_workspace_tree(source, target)
                return
            target.mkdir(parents=True, exist_ok=True)
            await _run_command(["git", "init"], cwd=str(target))
            return
        raise TemporalActivityRuntimeError(f"unsupported workspace policy: {policy}")

    def _workspace_ref_source(self, workspace_payload: Mapping[str, Any]) -> Path:
        workspace_ref = str(
            workspace_payload.get("workspaceRef")
            or workspace_payload.get("workspace_ref")
            or ""
        ).strip()
        if not workspace_ref:
            raise TemporalActivityRuntimeError("workspace ref evidence is missing")
        return self._resolve_workspace(workspace_ref, must_exist=True)

    def _replace_workspace_tree(self, source: Path, target: Path) -> None:
        source = self._resolve_workspace(source, must_exist=True)
        target = self._resolve_workspace(target, must_exist=False)
        if source == target:
            return
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=True)

    async def _restore_archive_to_workspace(
        self,
        workspace_payload: Mapping[str, Any],
        target: Path,
    ) -> None:
        archive_ref = str(workspace_payload.get("archiveRef") or "").strip()
        if not archive_ref:
            raise TemporalActivityRuntimeError("workspace archive evidence is missing")
        archive_payload = await self._read_checkpoint_bytes(archive_ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.restore"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=False)
        try:
            with tarfile.open(fileobj=BytesIO(archive_payload), mode="r:gz") as archive:
                for member in archive.getmembers():
                    member_path = (staging / member.name).resolve()
                    if not member_path.is_relative_to(staging):
                        raise TemporalActivityRuntimeError(
                            f"workspace archive member escapes workspace: {member.name}"
                        )
                    if member.issym() or member.islnk():
                        link_target = (member_path.parent / member.linkname).resolve()
                        if not link_target.is_relative_to(staging):
                            raise TemporalActivityRuntimeError(
                                f"workspace archive link escapes workspace: {member.name}"
                            )
                archive.extractall(staging, filter="data")
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    async def _checkout_commit_to_workspace(
        self,
        workspace_payload: Mapping[str, Any],
        target: Path,
    ) -> None:
        commit = str(
            workspace_payload.get("headCommit")
            or workspace_payload.get("baseCommit")
            or ""
        ).strip()
        if not commit:
            raise TemporalActivityRuntimeError(
                "git commit checkpoint evidence is missing"
            )
        source_ref = str(
            workspace_payload.get("workspaceRef")
            or workspace_payload.get("workspace_ref")
            or ""
        ).strip()
        if source_ref:
            source = self._resolve_workspace(source_ref, must_exist=True)
            self._replace_workspace_tree(source, target)
        else:
            if not (target / ".git").exists():
                raise TemporalActivityRuntimeError(
                    "git checkpoint requires workspaceRef or an existing git target workspace"
                )
        try:
            await _run_command(
                ["git", "checkout", "--force", "--detach", commit],
                cwd=str(target),
            )
        except RuntimeError as exc:
            raise TemporalActivityRuntimeError(
                f"git checkout failed: {exc}"
            ) from exc

    async def _apply_patch_artifact(
        self,
        workspace_payload: Mapping[str, Any],
        target: Path,
    ) -> None:
        patch_ref = str(workspace_payload.get("patchRef") or "").strip()
        if not patch_ref:
            raise TemporalActivityRuntimeError("git patch evidence is missing")
        patch_payload = await self._read_checkpoint_bytes(patch_ref)
        process = await asyncio.create_subprocess_exec(
            "git",
            "apply",
            "--whitespace=nowarn",
            "-",
            cwd=str(target),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate(patch_payload)
        if process.returncode != 0:
            raise TemporalActivityRuntimeError(
                "git patch evidence could not be applied: "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )

    def _workspace_policy_idempotency_path(self, idempotency_key: str) -> Path:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        path = (
            self._workspace_root
            / "temporal_sandbox"
            / ".moonmind-policy-idempotency"
            / f"{digest}.json"
        )
        return self._resolve_workspace(path, must_exist=False)

    def _read_workspace_policy_idempotency(
        self,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        path = self._workspace_policy_idempotency_path(idempotency_key)
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if isinstance(loaded, dict):
            return loaded
        return None

    def _write_workspace_policy_idempotency(
        self,
        idempotency_key: str,
        result: Mapping[str, Any],
    ) -> None:
        path = self._workspace_policy_idempotency_path(idempotency_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(result), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    async def _reject_workspace_policy(
        self,
        model: WorkspacePolicyApplyInput,
        *,
        failure_code: str,
        summary: str,
        provider_refs: list[str],
        diagnostic_refs: list[str] | None = None,
        checkpoint: Mapping[str, Any] | None = None,
        target_workspace_ref: str | None = None,
    ) -> dict[str, Any]:
        refs = list(diagnostic_refs or [])
        refs.append(
            await self._write_policy_diagnostic(
                model,
                failure_code=failure_code,
                summary=summary,
                checkpoint=checkpoint,
                target_workspace_ref=target_workspace_ref or model.target_workspace_ref,
                provider_refs=provider_refs,
            )
        )
        result = WorkspacePolicyApplyResult(
            status="rejected",
            workspaceRef=target_workspace_ref or model.target_workspace_ref,
            appliedCheckpointRef=model.checkpoint_ref,
            providerLeaseRefs=provider_refs,
            diagnosticRefs=refs,
            summary=summary,
            failureCode=failure_code,
        )
        return result.model_dump(by_alias=True, mode="json")

    async def _write_policy_diagnostic(
        self,
        model: WorkspacePolicyApplyInput,
        *,
        failure_code: str,
        summary: str,
        checkpoint: Mapping[str, Any] | None,
        target_workspace_ref: str | None,
        provider_refs: list[str],
    ) -> str:
        source = checkpoint.get("source") if isinstance(checkpoint, Mapping) else None
        if not isinstance(source, Mapping):
            source = model.identity.model_dump(by_alias=True, mode="json")
        workspace = (
            checkpoint.get("workspace") if isinstance(checkpoint, Mapping) else None
        )
        if not isinstance(workspace, Mapping):
            workspace = {}
        checkpoint_kind = workspace.get("kind")
        external_state_ref = (
            workspace.get("externalStateRef") or workspace.get("external_state_ref")
        )
        workspace_artifact_ref = (
            workspace.get("workspaceArtifactRef")
            or workspace.get("workspace_artifact_ref")
        )
        omnigent_session_id = (
            workspace.get("omnigentSessionId")
            or workspace.get("omnigent_session_id")
        )
        provider_session_ref = (
            workspace.get("providerSessionRef")
            or workspace.get("provider_session_ref")
        )
        safe_correlation = {
            "externalStateRef": external_state_ref,
            "workspaceArtifactRef": workspace_artifact_ref,
            "omnigentSessionId": omnigent_session_id,
            "providerSessionRef": provider_session_ref,
            "providerLeaseRefs": provider_refs,
        }
        safe_correlation = {
            key: value for key, value in safe_correlation.items() if value
        }
        payload = {
            "status": "blocked",
            "failureCode": failure_code,
            "summary": summary,
            "checkpointKind": checkpoint_kind,
            "logicalStepId": source.get("logicalStepId"),
            "sourceWorkflowId": source.get("workflowId"),
            "sourceRunId": source.get("runId"),
            "checkpointRef": model.checkpoint_ref,
            "workspacePolicy": model.workspace_policy,
            "targetWorkspaceRef": target_workspace_ref,
            "providerSessionCorrelation": safe_correlation,
            "recommendedNextAction": (
                "Inspect checkpoint evidence and select a compatible workspace "
                "policy before reattempting the Step Execution."
            ),
        }
        return await self._put_checkpoint_bytes(
            _json_bytes(payload),
            content_type="application/json",
            metadata={"artifact_kind": "blocked_step_execution_manifest"},
        )

    async def workspace_classify_git_effect(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _coerce_activity_request(
            request, activity_type="workspace.classify_git_effect"
        )
        raw_locator = payload.get("workspaceLocator")
        locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(raw_locator) if raw_locator else None
        if isinstance(locator, ManagedWorkspaceLocator):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox worker cannot classify a managed-runtime workspace",
            )
        if isinstance(locator, ExternalStateLocator):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_LOCATOR_UNSUPPORTED,
                "external state cannot be used for git-effect classification",
            )
        if locator is None:
            self._record_legacy_workspace_path_usage("classify_git_effect")
        workspace = (
            self._resolve_sandbox_locator(locator, must_exist=True)
            if isinstance(locator, SandboxWorkspaceLocator)
            else self._resolve_workspace(
                payload.get("workspacePath") or payload.get("workspaceRootRef") or "",
                must_exist=True,
            )
        )
        status = (
            await _run_command(["git", "status", "--porcelain"], cwd=str(workspace))
        ).stdout
        disposition = "clean" if not status.strip() else "dirty"
        refs: list[str] = []
        if status.strip():
            refs.append(
                await self._put_checkpoint_bytes(
                    status.encode("utf-8"),
                    content_type="text/plain",
                    metadata={"artifact_kind": "git_status"},
                )
            )
        return {
            "status": disposition,
            "summary": f"git workspace is {disposition}",
            "diagnosticRefs": refs,
        }

    @staticmethod
    def _record_legacy_workspace_path_usage(operation: str) -> None:
        try:
            get_metrics_emitter().increment(
                "workspace_locator.compatibility_path_usage",
                tags={"operation": operation},
            )
        except Exception:
            logger.warning(
                "Failed to emit legacy workspace path compatibility metric",
                exc_info=True,
            )

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

    def _resolve_sandbox_locator(
        self, locator: SandboxWorkspaceLocator, *, must_exist: bool
    ) -> Path:
        sandbox_root = (self._workspace_root / "temporal_sandbox").resolve()
        workspace_root = (sandbox_root / locator.workspace_id).resolve()
        if workspace_root.parent != sandbox_root:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH, "sandbox workspace identity escapes its authority"
            )
        workspace = (workspace_root / locator.relative_path).resolve()
        if not workspace.is_relative_to(workspace_root):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH, "sandbox relative path escapes its workspace"
            )
        if must_exist and not workspace.exists():
            raise TemporalActivityRuntimeError("workspace locator does not resolve to an existing workspace")
        return workspace

    def _normalize_allowed_file_paths(
        self,
        cwd: Path,
        allowed_file_paths: Sequence[str | Path] | None,
    ) -> frozenset[str] | None:
        if allowed_file_paths is None:
            return None
        if isinstance(allowed_file_paths, (str, bytes, bytearray)) or not isinstance(
            allowed_file_paths,
            Sequence,
        ):
            raise TemporalActivityRuntimeError(
                "sandbox file allowlist must be a list of relative file paths"
            )

        allowed: set[str] = set()
        for raw_path in allowed_file_paths:
            if not isinstance(raw_path, (str, Path)):
                raise TemporalActivityRuntimeError(
                    "sandbox file allowlist entries must be relative file paths"
                )
            raw_text = str(raw_path).strip()
            if not raw_text:
                raise TemporalActivityRuntimeError(
                    "sandbox file allowlist entries must not be empty"
                )
            candidate = Path(raw_text)
            if candidate.is_absolute():
                raise TemporalActivityRuntimeError(
                    "sandbox file allowlist entries must be relative file paths"
                )
            resolved = (cwd / candidate).resolve()
            if not resolved.is_relative_to(cwd):
                raise TemporalActivityRuntimeError(
                    "sandbox file allowlist entries must stay within the workspace"
                )
            allowed.add(resolved.relative_to(cwd).as_posix())
        return frozenset(allowed)

    def _sandbox_path_is_allowed(
        self,
        rel_path: str,
        allowed_paths: frozenset[str],
    ) -> bool:
        return any(
            rel_path == allowed_path or rel_path.startswith(f"{allowed_path}/")
            for allowed_path in allowed_paths
        )

    def _sandbox_file_snapshot(
        self,
        cwd: Path,
        *,
        backup_root: Path | None = None,
        allowed_paths: frozenset[str] | None = None,
    ) -> dict[str, _SandboxFileSnapshotEntry]:
        snapshot: dict[str, _SandboxFileSnapshotEntry] = {}
        for path in cwd.rglob("*"):
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                continue
            rel_path = path.relative_to(cwd).as_posix()
            if stat.S_ISREG(info.st_mode):
                try:
                    digest = self._sandbox_file_digest(path)
                except OSError:
                    continue
            elif stat.S_ISLNK(info.st_mode):
                try:
                    digest = hashlib.sha256(
                        os.readlink(path).encode("utf-8")
                    ).hexdigest()
                except OSError:
                    continue
            else:
                digest = hashlib.sha256(str(info.st_rdev).encode("utf-8")).hexdigest()
            backup_path: Path | None = None
            if (
                backup_root is not None
                and allowed_paths is not None
                and not self._sandbox_path_is_allowed(rel_path, allowed_paths)
            ):
                backup_path = backup_root / rel_path
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(path, backup_path, follow_symlinks=False)
                except OSError:
                    backup_path = None
            snapshot[rel_path] = _SandboxFileSnapshotEntry(
                mode=int(info.st_mode),
                size=int(info.st_size),
                mtime_ns=int(info.st_mtime_ns),
                digest=digest,
                backup_path=backup_path,
            )
        return snapshot

    def _sandbox_file_digest(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _changed_sandbox_files(
        self,
        before: Mapping[str, _SandboxFileSnapshotEntry],
        after: Mapping[str, _SandboxFileSnapshotEntry],
    ) -> set[str]:
        changed: set[str] = set()
        for rel_path, before_entry in before.items():
            after_entry = after.get(rel_path)
            if after_entry is None or (
                after_entry.mode,
                after_entry.size,
                after_entry.mtime_ns,
                after_entry.digest,
            ) != (
                before_entry.mode,
                before_entry.size,
                before_entry.mtime_ns,
                before_entry.digest,
            ):
                changed.add(rel_path)
        for rel_path in after:
            if rel_path not in before:
                changed.add(rel_path)
        return changed

    def _disallowed_sandbox_file_changes(
        self,
        *,
        changed_paths: Iterable[str],
        allowed_paths: frozenset[str] | None,
    ) -> list[str]:
        if allowed_paths is None:
            return []
        disallowed = sorted(
            rel_path
            for rel_path in set(changed_paths)
            if not self._sandbox_path_is_allowed(rel_path, allowed_paths)
        )
        return disallowed

    def _reject_disallowed_file_changes(
        self,
        disallowed_paths: Sequence[str],
    ) -> None:
        disallowed = list(disallowed_paths)
        if not disallowed:
            return
        preview = ", ".join(disallowed[:10])
        extra = "" if len(disallowed) <= 10 else f", ... ({len(disallowed)} total)"
        raise TemporalActivityRuntimeError(
            "sandbox command modified files outside the allowlist: "
            f"{preview}{extra}"
        )

    def _restore_disallowed_sandbox_changes(
        self,
        cwd: Path,
        *,
        disallowed_paths: Iterable[str],
        before: Mapping[str, _SandboxFileSnapshotEntry],
    ) -> None:
        for rel_path in sorted(
            set(disallowed_paths),
            key=lambda value: value.count("/"),
            reverse=True,
        ):
            target = cwd / rel_path
            before_entry = before.get(rel_path)
            try:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                continue
            if before_entry is None or before_entry.backup_path is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(before_entry.backup_path, target, follow_symlinks=False)
            except OSError:
                continue

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
        allowed_file_paths: Sequence[str | Path] | None = None,
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
                    "allowed_file_paths": list(allowed_file_paths)
                    if allowed_file_paths is not None
                    else None,
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
        allowed_file_paths: Sequence[str | Path] | None = None,
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
            if allowed_file_paths is None:
                allowed_file_paths = request_payload.get("allowed_file_paths")
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

        normalized_allowed_file_paths = self._normalize_allowed_file_paths(
            cwd,
            allowed_file_paths,
        )
        backup_dir: tempfile.TemporaryDirectory[str] | None = None
        backup_root: Path | None = None
        if normalized_allowed_file_paths is not None:
            backup_dir = tempfile.TemporaryDirectory(prefix="sandbox-allowlist-")
            backup_root = Path(backup_dir.name)
        try:
            before_files = (
                self._sandbox_file_snapshot(
                    cwd,
                    backup_root=backup_root,
                    allowed_paths=normalized_allowed_file_paths,
                )
                if normalized_allowed_file_paths is not None
                else None
            )
        except Exception:
            if backup_dir is not None:
                backup_dir.cleanup()
            raise

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

        if before_files is not None:
            after_files = self._sandbox_file_snapshot(cwd)
            changed_paths = self._changed_sandbox_files(before_files, after_files)
            disallowed_paths = self._disallowed_sandbox_file_changes(
                changed_paths=changed_paths,
                allowed_paths=normalized_allowed_file_paths,
            )
            if disallowed_paths:
                self._restore_disallowed_sandbox_changes(
                    cwd,
                    disallowed_paths=disallowed_paths,
                    before=before_files,
                )
            if backup_dir is not None:
                backup_dir.cleanup()
            self._reject_disallowed_file_changes(disallowed_paths)
        elif backup_dir is not None:
            backup_dir.cleanup()

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

    async def publication_recovery_observe(self, payload, /, **kwargs):
        """Read branch and PR identity from GitHub before any mutation."""
        from moonmind.workflows.adapters.github_service import GitHubService

        contract = dict((payload or {}).get("contract") or {})
        intent = dict(contract.get("intent") or {})
        continuation = dict(contract.get("continuation") or {})
        repository = str(intent.get("repository") or "").strip()
        head = str(intent.get("headRef") or "").strip()
        base = str(intent.get("baseRef") or "").strip()
        token, error = await GitHubService.resolve_github_token(repo=repository)
        if not token:
            return {
                "authoritative": True,
                "authorityAvailable": False,
            }
        headers = GitHubService._github_headers(token)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                branch_response = await client.get(
                    f"https://api.github.com/repos/{repository}/git/ref/heads/{head}",
                    headers=headers,
                )
                if branch_response.status_code not in (200, 404):
                    branch_response.raise_for_status()
                branch_data = (
                    branch_response.json() if branch_response.status_code == 200 else {}
                )
                pull_request = await GitHubService()._find_open_pull_request(
                    client,
                    repo=repository,
                    head=head,
                    base=base,
                    headers=headers,
                )
        except (httpx.HTTPError, ValueError):
            return {
                "authoritative": False,
                "authorityAvailable": True,
                "transientAbsenceOnly": True,
            }
        branch_object = branch_data.get("object") or {}
        pr_head = (pull_request or {}).get("head") or {}
        pr_base = (pull_request or {}).get("base") or {}
        return {
            "authoritative": True,
            "authorityAvailable": True,
            "remoteBranchExists": branch_response.status_code == 200,
            "remoteHeadSha": branch_object.get("sha"),
            "pullRequestExists": pull_request is not None,
            "pullRequestUrl": (pull_request or {}).get("html_url"),
            "pullRequestHeadRef": pr_head.get("ref"),
            "pullRequestBaseRef": pr_base.get("ref"),
            "pullRequestHeadSha": pr_head.get("sha"),
            "pullRequestDraft": (
                (pull_request or {}).get("draft") if pull_request is not None else None
            ),
            "conflictingEvidence": (
                branch_response.status_code == 200
                and branch_object.get("sha")
                != continuation.get("expectedHeadSha")
            ),
            "transientAbsenceOnly": False,
        }

    async def publication_recovery_publish(self, payload, /, **kwargs):
        """Create or adopt exactly one PR using the frozen publication intent."""
        from moonmind.workflows.adapters.github_service import GitHubService

        contract = dict((payload or {}).get("contract") or {})
        intent = dict(contract.get("intent") or {})
        target = dict(contract.get("target") or {})
        result = await GitHubService().create_pull_request(
            repo=str(intent.get("repository") or ""),
            head=str(intent.get("headRef") or ""),
            base=str(intent.get("baseRef") or ""),
            title=f"Publication recovery: {target.get('sourcePublicationOperationId')}",
            body=(
                "Publication-only recovery for accepted source workflow "
                f"{contract.get('sourceWorkflowId')}."
            ),
            draft=intent.get("mode") == "draft_pr",
        )
        if not result.url:
            raise TemporalActivityRuntimeError(
                f"publication recovery failed before a PR was reconciled: {result.summary}"
            )
        return {
            "pullRequestUrl": result.url,
            "headSha": result.head_sha,
            "created": result.created,
            "adopted": result.adopted,
            "reconciliationOutcome": "new" if result.created else "reconciled",
        }

    async def publication_recovery_verify(self, payload, /, **kwargs):
        """Re-observe GitHub and require the frozen head/base/commit identity."""
        publication = dict((payload or {}).get("publication") or {})
        observed = await self.publication_recovery_observe(payload)
        contract = dict((payload or {}).get("contract") or {})
        continuation = dict(contract.get("continuation") or {})
        if (
            not observed.get("authoritative")
            or not observed.get("pullRequestExists")
            or observed.get("remoteHeadSha") != continuation.get("expectedHeadSha")
            or observed.get("pullRequestHeadSha")
            != continuation.get("expectedHeadSha")
        ):
            raise TemporalActivityRuntimeError(
                "publication recovery could not verify the expected remote PR head"
            )
        return {
            "pullRequestUrl": observed.get("pullRequestUrl")
            or publication.get("pullRequestUrl"),
            "headSha": observed.get("pullRequestHeadSha"),
            "verified": True,
            "observation": observed,
        }

    async def memory_evaluate_proposals(
        self,
        *,
        proposal_refs: list[str],
        source: dict[str, Any],
        terminal_disposition: str | None,
        publication_gate: dict[str, Any] | None,
        requested_target: str,
        policy_decision: str | None = None,
        reason: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        from moonmind.memory.services import evaluate_memory_proposals

        return evaluate_memory_proposals(
            proposal_refs=proposal_refs,
            source=source,
            terminal_disposition=terminal_disposition,
            publication_gate=publication_gate,
            requested_target=requested_target,
            policy_decision=policy_decision,
            reason=reason,
            evidence_refs=evidence_refs,
        )

    async def memory_apply_policy(
        self,
        *,
        proposal_ref: str,
        decision_ref: str,
        source: dict[str, Any],
        target: str,
        decision: str,
        result_ref: str | None = None,
        gate_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from moonmind.memory.services import apply_memory_policy

        return apply_memory_policy(
            proposal_ref=proposal_ref,
            decision_ref=decision_ref,
            source=source,
            target=target,
            decision=decision,
            result_ref=result_ref,
            gate_status=gate_status,
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

    async def worker_verify_workflow_capability(self, payload, /, **kwargs):
        """Fail closed unless the deployed workflow fleet proves registration."""

        request = dict(payload) if isinstance(payload, Mapping) else {}
        workflow_type = str(request.get("workflowType") or "").strip()
        task_queue = str(request.get("taskQueue") or "").strip()
        url = str(
            os.environ.get("TEMPORAL_WORKFLOW_READINESS_URL")
            or "http://temporal-worker-workflow:8080/readyz"
        ).strip()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                readiness = response.json()
        except Exception as exc:
            return {
                "available": False,
                "status": "blocked_operator",
                "reasonCode": "worker_capability_unavailable",
                "workflowType": workflow_type,
                "taskQueue": task_queue,
                "agentExecutionLaunched": False,
                "diagnostic": exc.__class__.__name__,
            }
        if not isinstance(readiness, Mapping):
            readiness = {}
        workflow_types = {
            str(item) for item in (readiness.get("workflowTypes") or [])
        }
        task_queues = {str(item) for item in (readiness.get("taskQueues") or [])}
        available = (
            readiness.get("ready") is True
            and workflow_type in workflow_types
            and task_queue in task_queues
        )
        fingerprints = [
            str(item) for item in (readiness.get("registryFingerprints") or [])
        ]
        if not fingerprints and readiness.get("registryFingerprint"):
            fingerprints = [str(readiness["registryFingerprint"])]
        build_ids = [str(item) for item in (readiness.get("buildIds") or [])]
        if not build_ids and readiness.get("buildId"):
            build_ids = [str(readiness["buildId"])]
        children = [
            item
            for item in (readiness.get("children") or [])
            if isinstance(item, Mapping)
            and workflow_type
            in {str(value) for value in (item.get("workflowTypes") or [])}
            and task_queue in {str(value) for value in (item.get("taskQueues") or [])}
        ]
        if not children and workflow_type in workflow_types and task_queue in task_queues:
            children = [readiness]

        def _single_value(key: str) -> str | None:
            values = {
                str(item.get(key))
                for item in children
                if item.get(key) is not None and str(item.get(key)).strip()
            }
            return next(iter(values)) if len(values) == 1 else None

        result = {
            "available": available,
            "status": "ready" if available else "blocked_operator",
            "reasonCode": (
                "worker_capability_ready"
                if available
                else "worker_capability_unavailable"
            ),
            "workflowType": workflow_type,
            "taskQueue": task_queue,
            "agentExecutionLaunched": False,
            "registryFingerprint": fingerprints[0] if len(fingerprints) == 1 else None,
            "observedRegistryFingerprints": fingerprints,
            "observedWorkerBuilds": build_ids,
            "buildId": build_ids[0] if len(build_ids) == 1 else None,
            "buildSha": _single_value("buildSha"),
            "imageDigest": _single_value("imageDigest"),
            "deploymentId": _single_value("deploymentId"),
            "resolverCore": (
                dict(children[0].get("resolverCore") or {})
                if len(children) == 1
                else {}
            ),
        }
        if not available:
            logger.error(
                "workflow capability unavailable workflow_type=%s task_queue=%s "
                "build_ids=%s registry_fingerprints=%s",
                workflow_type,
                task_queue,
                build_ids,
                fingerprints,
            )
        return result

    async def merge_automation_evaluate_readiness(self, payload, /, **kwargs):
        from moonmind.workflows.adapters.github_service import GitHubService

        if not isinstance(payload, Mapping):
            return {
                "headSha": "",
                "ready": False,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                    "summary": "Merge automation readiness payload is invalid.",
                        "retryable": False,
                        "source": "policy",
                    }
                ],
                "policyAllowed": False,
            }

        pull_request = payload.get("pullRequest") or {}
        config = payload.get("mergeAutomationConfig") or {}
        gate = config.get("gate") if isinstance(config, Mapping) else {}
        policy = gate.get("github") if isinstance(gate, Mapping) else {}
        jira_policy = gate.get("jira") if isinstance(gate, Mapping) else {}
        if not isinstance(pull_request, Mapping):
            pull_request = {}
        if not isinstance(policy, Mapping):
            policy = {}
        if not isinstance(jira_policy, Mapping):
            jira_policy = {}

        readiness = await GitHubService().evaluate_pull_request_readiness(
            repo=str(pull_request.get("repo") or ""),
            pr_number=int(pull_request.get("number") or 0),
            head_sha=str(pull_request.get("headSha") or ""),
            policy=dict(policy),
            github_token=payload.get("githubToken"),
        )
        evidence = readiness.model_dump(by_alias=True)

        jira_issue_key = str(payload.get("jiraIssueKey") or "").strip()
        if jira_policy.get("status") == "required":
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

    async def merge_automation_complete_post_merge_jira(self, payload, /, **kwargs):
        if not isinstance(payload, Mapping):
            return {
                "status": "blocked",
                "required": True,
                "reason": "Post-merge Jira completion payload is invalid.",
                "issueResolution": {"status": "invalid", "candidates": []},
            }

        from moonmind.integrations.jira.models import (
            GetIssueRequest,
            GetTransitionsRequest,
            TransitionIssueRequest,
        )
        from moonmind.integrations.jira.tool import JiraToolService
        from moonmind.workflows.temporal.post_merge_jira_completion import (
            complete_post_merge_jira,
        )

        service = JiraToolService()

        async def get_issue(issue_key: str) -> dict[str, Any]:
            return await service.get_issue(
                GetIssueRequest(issueKey=issue_key, fields=["status"])
            )

        async def get_transitions(issue_key: str) -> list[dict[str, Any]]:
            result = await service.get_transitions(
                GetTransitionsRequest(issueKey=issue_key, expandFields=True)
            )
            transitions = result.get("transitions")
            return list(transitions) if isinstance(transitions, list) else []

        async def transition_issue(
            issue_key: str,
            transition_id: str,
            fields: dict[str, Any],
        ) -> dict[str, Any]:
            return await service.transition_issue(
                TransitionIssueRequest(
                    issueKey=issue_key,
                    transitionId=transition_id,
                    fields=fields,
                )
            )

        decision = await complete_post_merge_jira(
            payload,
            get_issue=get_issue,
            get_transitions=get_transitions,
            transition_issue=transition_issue,
        )
        return decision.model_dump(by_alias=True, mode="json")

    async def merge_automation_complete_post_merge_github(self, payload, /, **kwargs):
        config = payload.get("postMergeGithub") if isinstance(payload, Mapping) else None
        if not isinstance(config, Mapping):
            return {
                "status": "blocked",
                "required": True,
                "reason": "Post-merge GitHub completion payload is invalid.",
            }

        from moonmind.workflows.temporal.story_output_tools import (
            update_github_issue_status,
        )

        required = bool(config.get("required", True))
        repository = str(config.get("repository") or "").strip()
        issue_number = config.get("issueNumber") or config.get("issue_number")
        result = await update_github_issue_status(
            {
                "repository": repository,
                "issueNumber": issue_number,
                "mode": "done",
            }
        )
        outputs = dict(result.outputs)
        succeeded = (
            result.status == "COMPLETED"
            and outputs.get("confirmedState") == "closed"
        )
        return {
            "status": "succeeded" if succeeded else "failed",
            "required": required,
            "repository": repository,
            "issueNumber": issue_number,
            **outputs,
        }

    async def pr_resolver_resolve_selector(self, payload, /, **kwargs):
        """Resolve a PR number, URL, or branch to one canonical PR identity."""

        from moonmind.workflows.adapters.github_service import GitHubService

        if not isinstance(payload, Mapping):
            raise TemporalActivityRuntimeError(
                "pr_resolver.resolve_selector requires an object"
            )
        repository = str(payload.get("repository") or "").strip()
        selector = str(payload.get("selector") or "").strip()
        if not repository or not selector:
            raise TemporalActivityRuntimeError(
                "pr_resolver.resolve_selector requires repository and selector"
            )
        result = await GitHubService().resolve_pull_request_selector(
            repo=repository,
            selector=selector,
        )
        return result.model_dump(by_alias=True, mode="json")

    async def pr_resolver_read_snapshot(self, payload, /, **kwargs):
        """Replay-only snapshot support for previously recorded native runs.

        New pr-resolver executions must collect state through their resolved
        Skill bundle and must not call this Activity.
        """

        from moonmind.workflows.adapters.github_service import GitHubService

        if not isinstance(payload, Mapping):
            raise TemporalActivityRuntimeError("pr_resolver.read_snapshot requires an object")
        repository = str(payload.get("repository") or "").strip()
        pr_number = int(payload.get("prNumber") or 0)
        if not repository or pr_number <= 0:
            raise TemporalActivityRuntimeError(
                "pr_resolver.read_snapshot requires repository and prNumber"
            )
        readiness = await GitHubService().evaluate_pull_request_readiness(
            repo=repository,
            pr_number=pr_number,
            head_sha=str(payload.get("headSha") or ""),
            policy=dict(payload.get("policy") or {}),
        )
        result = readiness.model_dump(by_alias=True, mode="json")
        result["idempotencyKey"] = str(payload.get("idempotencyKey") or "")
        return result

    async def pr_resolver_finalize_merge(self, payload, /, **kwargs):
        """Replay-only merge support for previously recorded native runs.

        New pr-resolver executions merge through the portable Skill helper.
        """

        from moonmind.workflows.adapters.github_service import GitHubService

        if not isinstance(payload, Mapping):
            raise TemporalActivityRuntimeError("pr_resolver.finalize_merge requires an object")
        service = GitHubService()
        repository = str(payload.get("repository") or "").strip()
        pr_number = int(payload.get("prNumber") or 0)
        expected_head = str(payload.get("headSha") or "").strip()
        readiness = await service.evaluate_pull_request_readiness(
            repo=repository,
            pr_number=pr_number,
            head_sha=expected_head,
            policy=dict(payload.get("policy") or {}),
        )
        if readiness.pull_request_merged is True:
            return {
                "merged": True,
                "alreadyMerged": True,
                "headSha": readiness.head_sha,
                "idempotencyKey": str(payload.get("idempotencyKey") or ""),
            }
        if expected_head and readiness.head_sha != expected_head:
            return {
                "merged": False,
                "reasonCode": "stale_revision",
                "headSha": readiness.head_sha,
                "idempotencyKey": str(payload.get("idempotencyKey") or ""),
            }
        if not readiness.ready:
            return {
                "merged": False,
                "reasonCode": "gate_not_ready",
                "headSha": readiness.head_sha,
                "idempotencyKey": str(payload.get("idempotencyKey") or ""),
            }
        result = await service.merge_pull_request(
            pr_url=str(payload.get("prUrl") or ""),
            merge_method=str(payload.get("mergeMethod") or "squash"),
            expected_head_sha=expected_head or None,
        )
        output = result.model_dump(by_alias=True, mode="json")
        output["idempotencyKey"] = str(payload.get("idempotencyKey") or "")
        output["headSha"] = readiness.head_sha
        return output

    async def pr_resolver_classify_gate(self, payload, /, **kwargs):
        """Replay-only classifier for previously recorded native runs."""

        from moonmind.workflows.temporal.workflows.pr_resolver import (
            classify_pr_resolver_snapshot,
        )

        if not isinstance(payload, Mapping):
            raise TemporalActivityRuntimeError("pr_resolver.classify_gate requires an object")
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise TemporalActivityRuntimeError(
                "pr_resolver.classify_gate requires snapshot"
            )
        result = classify_pr_resolver_snapshot(snapshot)
        result["idempotencyKey"] = str(payload.get("idempotencyKey") or "")
        return result

    async def pr_resolver_verify_remote_head(self, payload, /, **kwargs):
        """Independently re-read the PR head after a remediation child."""

        return await self.pr_resolver_read_snapshot(payload)

    async def pr_resolver_verify_merged(self, payload, /, **kwargs):
        """Independently verify the authoritative remote merge state."""

        snapshot = await self.pr_resolver_read_snapshot(payload)
        return {
            "merged": snapshot.get("pullRequestMerged") is True,
            "headSha": snapshot.get("headSha"),
            "mergeSha": snapshot.get("mergeSha"),
            "idempotencyKey": str(payload.get("idempotencyKey") or ""),
        }

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

    async def integration_omnigent_execute(self, request, /, **kwargs):
        from moonmind.workflows.temporal.activities.omnigent_activities import omnigent_execute_activity
        from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

        if isinstance(request, Mapping):
            request_payload = _coerce_activity_request(request, activity_type="integration.omnigent.execute")
            if not request_payload:
                raise TemporalActivityRuntimeError("integration.omnigent.execute requires AgentExecutionRequest payload")
            req = AgentExecutionRequest.model_validate(request_payload)
        elif isinstance(request, AgentExecutionRequest):
            req = request
        else:
            raise TemporalActivityRuntimeError("integration.omnigent.execute requires AgentExecutionRequest payload")

        return await omnigent_execute_activity(req)

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
        self._redactor = SecretRedactor.from_environ(placeholder="[REDACTED]")

    @staticmethod
    def _resolve_task_instructions(parameters: Mapping[str, Any]) -> str:
        task_node = parameters.get("workflow")
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
    def _proposal_allowed_actors_from_provider_metadata(
        cls,
        *,
        provider_payload: Mapping[str, Any],
        delivery_provider: str,
    ) -> list[str]:
        provider_cfg = provider_payload.get(delivery_provider)
        if not isinstance(provider_cfg, Mapping):
            return []
        actors: list[str] = []
        seen: set[str] = set()
        for key in ("allowedActors", "allowed_actors", "reviewers"):
            raw = provider_cfg.get(key)
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                continue
            for item in raw:
                actor = cls._normalize_proposal_text(item)
                actor_key = actor.lower()
                if actor and actor_key not in seen:
                    actors.append(actor)
                    seen.add(actor_key)
        return actors

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

    @staticmethod
    def _compact_mapping(value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return deepcopy(dict(value))

    @classmethod
    def _preserve_compact_task_metadata(
        cls, *, source_workflow: Mapping[str, Any], target_workflow: dict[str, Any]
    ) -> None:
        """Carry compact selector/provenance metadata into generated candidates.

        Runtime-local materialization outputs and full skill bodies are not part
        of the canonical task contract, so only already-structured selector and
        provenance fields are copied.
        """

        for key in ("skill", "tool", "skills"):
            value = cls._compact_mapping(source_workflow.get(key))
            if value is not None:
                target_workflow[key] = value

        authored_presets = source_workflow.get("authoredPresets")
        if isinstance(authored_presets, list) and authored_presets:
            target_workflow["authoredPresets"] = deepcopy(authored_presets)

        steps = source_workflow.get("steps")
        if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
            return

        source_steps: list[dict[str, Any]] = []
        for raw_step in steps:
            if not isinstance(raw_step, Mapping):
                continue
            preserved: dict[str, Any] = {}
            for key in ("id", "title", "type"):
                value = raw_step.get(key)
                if isinstance(value, str) and value.strip():
                    preserved[key] = value
            for key in ("tool", "skill", "skills", "source"):
                value = cls._compact_mapping(raw_step.get(key))
                if value is not None:
                    preserved[key] = value
            if not any(
                key in preserved for key in ("source", "skills", "skill", "tool")
            ):
                continue
            step_type = str(preserved.get("type") or "").strip().lower()
            source_kind = ""
            source = preserved.get("source")
            if isinstance(source, Mapping):
                source_kind = str(source.get("kind") or "").strip()
            if source_kind in {"preset-derived", "preset-include", "detached"}:
                if step_type == "tool" and isinstance(preserved.get("tool"), Mapping):
                    source_steps.append(preserved)
                elif step_type == "skill" and (
                    isinstance(preserved.get("skill"), Mapping)
                    or isinstance(preserved.get("skills"), Mapping)
                ):
                    source_steps.append(preserved)
                elif step_type not in {"tool", "skill"}:
                    source_steps.append(preserved)
                continue
            source_steps.append(preserved)

        if source_steps:
            target_workflow["sourceSteps"] = source_steps

    @staticmethod
    def _reject_unsupported_tool_selectors(payload: Mapping[str, Any]) -> None:
        task_node = payload.get("workflow")
        task = task_node if isinstance(task_node, Mapping) else {}

        def check_tool(tool_node: object, path: str) -> None:
            if not isinstance(tool_node, Mapping):
                return
            tool_type = str(
                tool_node.get("type") or tool_node.get("kind") or "skill"
            ).strip()
            if tool_type and tool_type.lower() != "skill":
                raise ValueError(f"{path}.type must be 'skill'")

        check_tool(task.get("tool"), "payload.workflow.tool")
        steps = task.get("steps")
        if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
            return
        for index, step_node in enumerate(steps):
            if not isinstance(step_node, Mapping):
                continue
            check_tool(step_node.get("tool"), f"payload.workflow.steps[{index}].tool")

    @classmethod
    def _stamp_default_runtime(
        cls, request: Mapping[str, Any], default_runtime: str | None
    ) -> dict[str, Any]:
        stamped_request = deepcopy(dict(request))
        if not default_runtime:
            return stamped_request
        payload_node = stamped_request.get("payload")
        if not isinstance(payload_node, dict):
            return stamped_request
        task_node = payload_node.get("workflow")
        if isinstance(task_node, dict):
            runtime_node = task_node.get("runtime")
            if isinstance(runtime_node, dict):
                if not runtime_node.get("mode"):
                    runtime_node["mode"] = default_runtime
            else:
                task_node["runtime"] = {"mode": default_runtime}
        else:
            payload_node["workflow"] = {"runtime": {"mode": default_runtime}}
        return stamped_request

    @staticmethod
    def _task_runtime_mode_from_payload(payload_node: Any) -> str:
        if not isinstance(payload_node, Mapping):
            return ""
        task_node = payload_node.get("workflow") or {}
        if not isinstance(task_node, Mapping):
            return ""
        runtime_node = task_node.get("runtime") or {}
        if isinstance(runtime_node, Mapping):
            runtime_node = runtime_node.get("mode")
        return str(runtime_node or "").strip()

    @classmethod
    def _normalize_signal_tag(cls, value: object) -> str:
        text = cls._normalize_proposal_text(value).lower()
        text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
        if not text:
            return ""
        text = _PROPOSAL_TELEMETRY_TAG_ALIASES.get(text, text)
        if text in _PROPOSAL_TELEMETRY_SIGNAL_TAGS:
            return text
        return ""

    @classmethod
    def _telemetry_signal_tags(cls, signal: Mapping[str, Any]) -> list[str]:
        raw_values: list[object] = []
        for key in ("tag", "type", "kind", "code", "category"):
            if key in signal:
                raw_values.append(signal.get(key))
        raw_tags = signal.get("tags")
        if isinstance(raw_tags, Sequence) and not isinstance(raw_tags, (str, bytes)):
            raw_values.extend(raw_tags)
        elif raw_tags is not None:
            raw_values.append(raw_tags)

        normalized: list[str] = []
        seen: set[str] = set()
        for value in raw_values:
            tag = cls._normalize_signal_tag(value)
            if not tag or tag in seen:
                continue
            normalized.append(tag)
            seen.add(tag)
        return normalized

    @classmethod
    def _telemetry_signal_severity(
        cls, signal: Mapping[str, Any], tags: Sequence[str]
    ) -> str:
        severity = cls._normalize_proposal_text(signal.get("severity")).lower()
        if severity in {"low", "medium", "normal", "high", "critical"}:
            return severity
        if any(tag in {"loop_detected", "conflicting_instructions"} for tag in tags):
            return "high"
        return "medium"

    @classmethod
    def _telemetry_signal_summary(cls, signal: Mapping[str, Any]) -> str:
        for key in ("summary", "message", "reason", "details", "description"):
            summary = cls._normalize_proposal_text(signal.get(key))
            if summary:
                return summary[:500]
        tags = cls._telemetry_signal_tags(signal)
        if tags:
            label = _PROPOSAL_TELEMETRY_TAG_LABELS.get(tags[0], tags[0])
            return f"Telemetry reported a {label} signal."
        return ""

    @classmethod
    def _build_telemetry_signal_instructions(
        cls,
        *,
        workflow_id: str,
        label: str,
        summary: str,
        instructions: str,
        diagnostics_ref: str,
    ) -> str:
        parts = [
            (
                "Investigate and address the run-quality telemetry signal "
                f"'{label}' reported by workflow {workflow_id or 'unknown'}."
            ),
            f"Signal summary: {summary}",
        ]
        if diagnostics_ref:
            parts.append(f"Diagnostics reference: {diagnostics_ref}")
        if instructions:
            parts.append(f"Context from the completed task:\n{instructions}")
        return "\n\n".join(parts)

    @classmethod
    def _telemetry_signal_candidates(
        cls,
        *,
        payload: Mapping[str, Any],
        repo: str,
        workflow_id: str,
        instructions: str,
        task: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        raw_signals = payload.get("telemetrySignals")
        if not isinstance(raw_signals, Sequence) or isinstance(
            raw_signals, (str, bytes)
        ):
            return []

        candidates: list[dict[str, Any]] = []
        for raw_signal in raw_signals[:3]:
            if not isinstance(raw_signal, Mapping):
                continue
            signal = dict(raw_signal)
            tags = cls._telemetry_signal_tags(signal)
            if not tags:
                continue
            summary = cls._telemetry_signal_summary(signal)
            if not summary:
                continue
            primary_tag = tags[0]
            label = _PROPOSAL_TELEMETRY_TAG_LABELS.get(primary_tag, primary_tag)
            title = cls._normalize_proposal_text(signal.get("title")) or (
                f"Review {label} signal from workflow {workflow_id or 'unknown'}"
            )
            if not title.lower().startswith("[run_quality]"):
                title = f"[run_quality] {title}"
            if len(title) > 194:
                title = title[:194].rstrip()

            severity = cls._telemetry_signal_severity(signal, tags)
            diagnostics_ref = cls._normalize_proposal_text(
                signal.get("diagnostics_ref")
            )
            runtime_node = task.get("runtime")
            runtime = dict(runtime_node) if isinstance(runtime_node, Mapping) else {}
            git_node = task.get("git")
            git = dict(git_node) if isinstance(git_node, Mapping) else {}
            publish_node = task.get("publish")
            publish = dict(publish_node) if isinstance(publish_node, Mapping) else {}
            workflow_create_request: dict[str, Any] = {
                "type": "workflow",
                "payload": {
                    "repository": repo,
                    "workflow": {
                        "instructions": cls._build_telemetry_signal_instructions(
                            workflow_id=workflow_id,
                            label=label,
                            summary=summary,
                            instructions=instructions,
                            diagnostics_ref=diagnostics_ref,
                        ),
                        "runtime": runtime,
                        "git": git,
                        "publish": publish,
                    },
                },
            }
            cls._preserve_compact_task_metadata(
                source_workflow=task,
                target_workflow=workflow_create_request["payload"]["workflow"],
            )
            candidate_signal = {
                key: deepcopy(value)
                for key, value in signal.items()
                if key
                in {
                    "type",
                    "kind",
                    "severity",
                    "summary",
                    "message",
                    "reason",
                    "retries",
                    "missing_refs",
                    "diagnostics_ref",
                }
            }
            candidate_signal["type"] = primary_tag
            candidate_signal["severity"] = severity
            candidate_signal["tags"] = list(tags)
            candidate_signal["summary"] = summary
            candidates.append(
                {
                    "title": title,
                    "summary": (
                        f"Telemetry signal from workflow {workflow_id or 'unknown'}: "
                        f"{summary}"
                    ),
                    "category": "run_quality",
                    "tags": list(tags),
                    "severity": severity,
                    "signal": candidate_signal,
                    "workflowCreateRequest": workflow_create_request,
                }
            )
        return candidates

    @classmethod
    def _validate_candidate_workflow_create_request(
        cls, request: Mapping[str, Any], *, default_runtime: str | None
    ) -> dict[str, Any]:
        from moonmind.workflows.proposals.service import WorkflowProposalService
        from moonmind.workflows.executions.execution_contract import (
            CanonicalWorkflowExecutionPayload,
            WorkflowContractError,
        )

        stamped_request = cls._stamp_default_runtime(request, default_runtime)
        job_type = str(stamped_request.get("type") or "workflow").strip().lower()
        if job_type != "workflow":
            raise ValueError("workflowCreateRequest.type must be 'workflow'")
        max_attempts = stamped_request.get("maxAttempts", 3)
        try:
            if int(max_attempts) < 1:
                raise ValueError("maxAttempts must be >= 1")
        except (TypeError, ValueError) as exc:
            raise ValueError("workflowCreateRequest.maxAttempts must be an integer >= 1") from exc

        payload_node = stamped_request.get("payload")
        if not isinstance(payload_node, Mapping):
            raise ValueError("workflowCreateRequest.payload must be an object")
        normalized_payload = WorkflowProposalService._normalize_proposal_runtime_payload(
            payload_node
        )
        validation_payload = deepcopy(normalized_payload)
        task_node = validation_payload.get("workflow")
        task = dict(task_node) if isinstance(task_node, Mapping) else {}
        if not task:
            task = {
                "instructions": (
                    str(
                        validation_payload.get("instructions")
                        or validation_payload.get("instruction")
                        or ""
                    ).strip()
                    or "Queue job"
                ),
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": None, "model": None, "effort": None},
                "git": {"startingBranch": None, "targetBranch": None},
                "publish": {"mode": "pr"},
            }
        elif not str(task.get("instructions") or "").strip():
            skill = task.get("skill")
            has_explicit_skill = False
            if isinstance(skill, Mapping):
                skill_id = str(skill.get("id") or "").strip().lower()
                has_explicit_skill = bool(skill_id and skill_id != "auto")
            if not has_explicit_skill:
                task["instructions"] = (
                    str(
                        validation_payload.get("instructions")
                        or validation_payload.get("instruction")
                        or ""
                    ).strip()
                    or "Queue job"
                )
        validation_payload["workflow"] = task
        cls._reject_unsupported_tool_selectors(validation_payload)
        try:
            CanonicalWorkflowExecutionPayload.model_validate(validation_payload)
        except (ValidationError, WorkflowContractError) as exc:
            raise ValueError(str(exc)) from exc
        return stamped_request

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
        ``category``, ``tags``, and ``workflowCreateRequest``.

        Returns an empty list when insufficient context is available to
        produce a meaningful proposal (e.g. missing instructions).
        """
        payload = dict(request or {})
        parameters: dict[str, Any] = payload.get("parameters") or {}
        repo = str(payload.get("repo") or parameters.get("repository") or "").strip()
        workflow_id = str(payload.get("workflow_id") or "").strip()

        task_node = parameters.get("workflow")
        task = dict(task_node) if isinstance(task_node, Mapping) else {}
        instructions = self._resolve_task_instructions(parameters)

        proposal_idea = self._resolve_proposal_idea(
            payload=payload,
            parameters=parameters,
            task=task,
            instructions=instructions,
        )

        if not proposal_idea:
            telemetry_candidates = self._telemetry_signal_candidates(
                payload=payload,
                repo=repo,
                workflow_id=workflow_id,
                instructions=instructions,
                task=task,
            )
            if telemetry_candidates:
                return telemetry_candidates
            # Do not create generic proposals whose title simply repeats the
            # completed workflow. The fallback path only emits a proposal when
            # it receives an explicit next-step idea or telemetry signal from
            # upstream context.
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

        # Reconstruct a workflow-start request envelope from the original
        # execution context so that the proposal can be promoted to a
        # queued task with one click.
        runtime_node = task.get("runtime")
        runtime = dict(runtime_node) if isinstance(runtime_node, Mapping) else {}
        git_node = task.get("git")
        git = dict(git_node) if isinstance(git_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = dict(publish_node) if isinstance(publish_node, Mapping) else {}

        workflow_create_request: dict[str, Any] = {
            "type": "workflow",
            "payload": {
                "repository": repo,
                "workflow": {
                    "instructions": follow_up_instructions,
                    "runtime": runtime,
                    "git": git,
                    "publish": publish,
                },
            },
        }
        self._preserve_compact_task_metadata(
            source_workflow=task,
            target_workflow=workflow_create_request["payload"]["workflow"],
        )

        candidate: dict[str, Any] = {
            "title": normalized_title,
            "summary": summary,
            "category": "run_quality",
            "tags": ["artifact_gap", "auto-generated", "follow_up"],
            "workflowCreateRequest": workflow_create_request,
        }

        return [candidate]

    async def proposal_submit(
        self,
        request: Mapping[str, Any] | None = None,
        /,
    ) -> dict[str, Any]:
        """Validate, filter, and submit generated proposals to the Proposal Queue API.

        Returns a summary dict with generated/submitted counts, compact delivery
        decisions, and redacted errors.
        """
        logger = getLogger(__name__)
        payload = dict(request or {})
        candidates: list[Any] = payload.get("candidates") or []
        policy: dict[str, Any] = payload.get("policy") or {}
        origin: dict[str, Any] = payload.get("origin") or {}
        workflow_id: str = str(origin.get("workflow_id") or "")
        run_id: str = str(origin.get("temporal_run_id") or "")
        trigger_repo: str = str(origin.get("trigger_repo") or "")
        trigger_job_id: str = str(origin.get("trigger_job_id") or run_id or "")

        from moonmind.workflows.executions.execution_contract import (
            WorkflowProposalPolicy,
            build_effective_proposal_policy,
        )

        generated_count = len(candidates)
        delivery_decisions: list[dict[str, Any]] = []
        errors: list[str] = []
        observability_events: list[dict[str, Any]] = [
            {
                "eventType": "proposal.generation_requested",
                "workflowId": workflow_id or None,
                "temporalRunId": run_id or None,
                "candidateCount": generated_count,
            },
            {
                "eventType": "proposal.candidates_generated",
                "workflowId": workflow_id or None,
                "temporalRunId": run_id or None,
                "candidateCount": generated_count,
            },
        ]

        def append_event(event_type: str, **fields: Any) -> None:
            event = {
                "eventType": event_type,
                "workflowId": workflow_id or None,
                "temporalRunId": run_id or None,
                **fields,
            }
            observability_events.append(
                {
                    key: (
                        self._redactor.scrub(str(value))
                        if isinstance(value, str)
                        else value
                    )
                    for key, value in event.items()
                    if value is not None
                }
            )

        parsed_policy: WorkflowProposalPolicy | None = None
        if isinstance(policy, Mapping) and policy:
            try:
                parsed_policy = WorkflowProposalPolicy.model_validate(policy)
            except Exception as exc:
                redacted = self._redactor.scrub(str(exc))[:200]
                logger.warning("proposal.submit: invalid proposal policy: %s", redacted)
                return {
                    "generated_count": generated_count,
                    "submitted_count": 0,
                    "errors": [f"invalid proposal policy: {redacted}"],
                    "delivery_decisions": [],
                    "observabilityEvents": [
                        *observability_events,
                        {
                            "eventType": "proposal.candidate_rejected",
                            "workflowId": workflow_id or None,
                            "temporalRunId": run_id or None,
                            "reason": "invalid_policy",
                        },
                    ],
                }

        effective_policy = build_effective_proposal_policy(
            policy=parsed_policy,
            default_targets=getattr(
                settings.workflow_proposals,
                "proposal_targets_default",
                "workflow_repo",
            ),
            default_max_items_workflow_repo=getattr(
                settings.workflow_proposals,
                "max_items_workflow_repo_default",
                3,
            ),
            default_max_items_moonmind=getattr(
                settings.workflow_proposals,
                "max_items_moonmind_default",
                2,
            ),
            default_moonmind_severity_floor=getattr(
                settings.workflow_proposals,
                "moonmind_severity_floor_default",
                "high",
            ),
            severity_vocabulary=getattr(
                settings.workflow_proposals,
                "severity_vocabulary",
                None,
            ),
        )
        default_runtime = parsed_policy.default_runtime if parsed_policy else None
        moonmind_repo = str(
            getattr(settings.workflow_proposals, "moonmind_ci_repository", "") or ""
        ).strip()
        approved_moonmind_tags = {
            "retry",
            "duplicate_output",
            "missing_ref",
            "conflicting_instructions",
            "flaky_test",
            "loop_detected",
            "artifact_gap",
        }
        submitted_count = 0

        if not candidates:
            return {
                "generated_count": 0,
                "submitted_count": 0,
                "errors": [],
                "delivery_decisions": [],
                "observabilityEvents": observability_events,
            }

        provider_metadata = dict(effective_policy.provider_metadata or {})
        delivery_provider = effective_policy.delivery_provider
        if delivery_provider == "auto" or (
            not parsed_policy
            or not parsed_policy.delivery
            or not parsed_policy.delivery.provider
            or parsed_policy.delivery.provider == "auto"
        ):
            delivery_provider = str(
                getattr(
                    settings.workflow_proposals,
                    "proposal_delivery_provider_default",
                    "github",
                )
                or "github"
            ).strip().lower()
        provider_payload = {
            key: value
            for key, value in provider_metadata.items()
            if key in {"github", "jira"} and isinstance(value, Mapping)
        }
        delivery_policy_constraints: dict[str, Any] = {}
        if delivery_provider == "github":
            github_policy = provider_payload.get("github")
        else:
            github_policy = None
        if isinstance(github_policy, Mapping):
            for source_key, target_key in (
                ("allowedRepositories", "allowedRepositories"),
                ("allowed_repositories", "allowedRepositories"),
                ("allowedOrganizations", "allowedOrganizations"),
                ("allowed_organizations", "allowedOrganizations"),
                ("allowedActions", "allowedActions"),
                ("allowed_actions", "allowedActions"),
            ):
                if source_key in github_policy:
                    delivery_policy_constraints[target_key] = github_policy[source_key]
            policy_allowed_actors = self._proposal_allowed_actors_from_provider_metadata(
                provider_payload=provider_payload,
                delivery_provider=delivery_provider,
            )
            if policy_allowed_actors:
                delivery_policy_constraints["allowedActors"] = policy_allowed_actors
        if delivery_provider == "jira":
            jira_policy = provider_payload.get("jira")
        else:
            jira_policy = None
        if isinstance(jira_policy, Mapping):
            for source_key, target_key in (
                ("allowedProjects", "allowedProjects"),
                ("allowed_projects", "allowedProjects"),
                ("allowedActions", "allowedActions"),
                ("allowed_actions", "allowedActions"),
            ):
                if source_key in jira_policy:
                    delivery_policy_constraints[target_key] = jira_policy[source_key]

        service_or_ctx = None
        if self._proposal_service_factory is not None:
            try:
                service_or_ctx = self._proposal_service_factory()
                if inspect.isawaitable(service_or_ctx):
                    service_or_ctx = await service_or_ctx
            except Exception as exc:
                logger.warning(
                    "proposal.submit: failed to create proposal service: %s", exc
                )
                return {
                    "generated_count": generated_count,
                    "submitted_count": 0,
                    "errors": ["proposal service unavailable"],
                    "delivery_decisions": [],
                    "observabilityEvents": [
                        *observability_events,
                        {
                            "eventType": "proposal.delivery_failed",
                            "workflowId": workflow_id or None,
                            "temporalRunId": run_id or None,
                            "reason": "proposal_service_unavailable",
                        },
                    ],
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
            if origin and not workflow_id:
                error = "origin.workflow_id is required for workflow proposal submission"
                return {
                    "generated_count": generated_count,
                    "submitted_count": 0,
                    "deliveredCount": 0,
                    "validationErrors": [
                        {"code": "proposal_validation_error", "message": error}
                    ],
                    "deliveryFailures": [],
                    "externalLinks": [],
                    "dedupUpdates": [],
                    "errors": [error],
                    "delivery_decisions": [],
                    "observabilityEvents": [
                        *observability_events,
                        {
                            "eventType": "proposal.candidate_rejected",
                            "workflowId": workflow_id or None,
                            "temporalRunId": run_id or None,
                            "reason": "missing_origin_workflow_id",
                        },
                    ],
                }

            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    errors.append("skipped non-object candidate")
                    append_event(
                        "proposal.candidate_rejected",
                        reason="candidate_not_object",
                    )
                    continue
                title = str(candidate.get("title") or "").strip()
                summary = str(candidate.get("summary") or "").strip()
                workflow_create_request = candidate.get("workflowCreateRequest")
                if (
                    not title
                    or not summary
                    or not isinstance(workflow_create_request, Mapping)
                ):
                    errors.append(f"skipped malformed candidate: {title!r}")
                    append_event(
                        "proposal.candidate_rejected",
                        title=title or None,
                        reason="malformed_candidate",
                    )
                    continue

                original_payload_node = workflow_create_request.get("payload")
                original_target_repo = ""
                if isinstance(original_payload_node, Mapping):
                    original_target_repo = str(
                        original_payload_node.get("repository") or ""
                    ).strip()
                missing_workflow_repo_destination = not original_target_repo
                request_for_validation: Mapping[str, Any] = workflow_create_request
                if missing_workflow_repo_destination:
                    request_copy = deepcopy(dict(workflow_create_request))
                    payload_node = request_copy.get("payload")
                    if isinstance(payload_node, Mapping) and not isinstance(
                        payload_node, dict
                    ):
                        payload_node = dict(payload_node)
                        request_copy["payload"] = payload_node
                    if isinstance(payload_node, dict):
                        payload_node["repository"] = "unresolved-workflow-repo"
                    else:
                        request_copy["payload"] = {
                            "repository": "unresolved-workflow-repo"
                        }
                    request_for_validation = request_copy

                try:
                    stamped_request = self._validate_candidate_workflow_create_request(
                        request_for_validation,
                        default_runtime=(
                            default_runtime if isinstance(default_runtime, str) else None
                        ),
                    )
                except Exception as exc:
                    redacted_error = self._redactor.scrub(str(exc))[:200]
                    errors.append(
                        f"invalid workflowCreateRequest for {title!r}: {redacted_error}"
                    )
                    append_event(
                        "proposal.candidate_rejected",
                        title=title,
                        reason="invalid_workflow_create_request",
                        sanitizedReason=redacted_error,
                    )
                    if "repository" in redacted_error.lower():
                        append_event(
                            "proposal.recovery_unroutable_reported",
                            title=title,
                            reason="invalid_or_missing_repository",
                        )
                    continue

                if missing_workflow_repo_destination:
                    if not effective_policy.allow_workflow_repo:
                        delivery_decisions.append(
                            {
                                "title": title,
                                "target": "workflow_repo",
                                "accepted": False,
                                "reason": "target_disabled",
                            }
                        )
                        continue
                    if not effective_policy.consume_workflow_repo_slot():
                        delivery_decisions.append(
                            {
                                "title": title,
                                "target": "workflow_repo",
                                "accepted": False,
                                "reason": "capacity",
                            }
                        )
                        continue

                    decision = {
                        "title": title,
                        "target": "workflow_repo",
                        "repository": "",
                        "provider": delivery_provider,
                        "accepted": True,
                        "deliveryStatus": "failed",
                    }
                    reason = (
                        "workflowCreateRequest.payload.repository is required for "
                        "workflow_repo proposal delivery"
                    )
                    next_action = (
                        "Set workflowCreateRequest.payload.repository to the workflow "
                        "repository before retrying delivery."
                    )
                    try:
                        if service is not None and hasattr(
                            service, "record_delivery_failure"
                        ):
                            from moonmind.workflows.proposals.models import (
                                WorkflowProposalOriginSource,
                            )

                            origin_metadata = {
                                "source": "workflow",
                                "id": workflow_id,
                                "workflow_id": workflow_id,
                                "temporal_run_id": run_id,
                                "trigger_repo": trigger_repo,
                                "trigger_job_id": trigger_job_id,
                            }
                            proposal = await service.record_delivery_failure(
                                title=title,
                                summary=summary,
                                category=candidate.get("category"),
                                tags=candidate.get("tags"),
                                workflow_create_request=dict(workflow_create_request),
                                origin_source=WorkflowProposalOriginSource.WORKFLOW,
                                origin_id=None,
                                origin_external_id=workflow_id,
                                origin_metadata=origin_metadata,
                                proposed_by_worker_id=f"temporal:{workflow_id}",
                                proposed_by_user_id=None,
                                provider=delivery_provider,
                                target_class="workflow_repo",
                                reason=reason,
                                recoverable_next_action=next_action,
                                retryable=True,
                                repository=None,
                                provider_metadata=provider_payload,
                                resolved_policy={
                                    **delivery_policy_constraints,
                                    "provider": delivery_provider,
                                    "target": "workflow_repo",
                                    "repository": "",
                                    "workflow_id": workflow_id,
                                    "delivery": {
                                        "status": "failed",
                                        "reason": reason,
                                        "recoverableNextAction": next_action,
                                    },
                                },
                            )
                            delivery_metadata = getattr(
                                proposal, "provider_metadata", {}
                            )
                            if isinstance(delivery_metadata, Mapping):
                                delivery_node = delivery_metadata.get("delivery")
                                if isinstance(delivery_node, Mapping):
                                    decision["deliveryStatus"] = delivery_node.get(
                                        "status", "failed"
                                    )
                                    if "error" in delivery_node:
                                        decision["error"] = delivery_node["error"]
                            submitted_count += 1
                        else:
                            decision["error"] = {
                                "provider": delivery_provider,
                                "destination": "",
                                "targetClass": "workflow_repo",
                                "sanitizedReason": reason,
                                "recoverableNextAction": next_action,
                                "retryable": True,
                            }
                    except Exception as exc:
                        redacted_error = self._redactor.scrub(str(exc))[:200]
                        errors.append(
                            f"submission failed for {title!r}: {redacted_error}"
                        )
                        decision["accepted"] = False
                        decision["reason"] = "submission_failed"
                    delivery_decisions.append(decision)
                    continue

                payload_node = stamped_request.get("payload")
                target_repo = ""
                if isinstance(payload_node, Mapping):
                    target_repo = str(payload_node.get("repository") or "").strip()

                original_runtime_mode = self._task_runtime_mode_from_payload(
                    workflow_create_request.get("payload")
                )
                stamped_runtime_mode = self._task_runtime_mode_from_payload(
                    payload_node
                )
                default_runtime_value = (
                    default_runtime if isinstance(default_runtime, str) else None
                )
                default_runtime_applied = bool(
                    default_runtime_value
                    and stamped_runtime_mode == default_runtime_value
                    and not original_runtime_mode
                )

                tags = [
                    str(tag or "").strip().lower()
                    for tag in (candidate.get("tags") or [])
                ]
                tags = [tag for tag in tags if tag]
                category = str(candidate.get("category") or "").strip().lower()
                severity = str(candidate.get("severity") or "medium").strip().lower()
                moonmind_tag_matches = sorted(set(tags) & approved_moonmind_tags)
                moonmind_severity_qualified = effective_policy.severity_meets_floor(
                    severity
                )
                wants_moonmind = (
                    bool(moonmind_repo)
                    and effective_policy.allow_moonmind
                    and (
                        not effective_policy.allow_workflow_repo
                        or target_repo.lower() == moonmind_repo.lower()
                        or category in {"run_quality", "moonmind_ci"}
                    )
                    and category in {"run_quality", "moonmind_ci"}
                    and bool(moonmind_tag_matches)
                    and moonmind_severity_qualified
                )

                target = "workflow_repo"
                if wants_moonmind:
                    if not effective_policy.consume_moonmind_slot():
                        delivery_decisions.append(
                            {
                                "title": title,
                                "target": "moonmind",
                                "accepted": False,
                                "reason": "capacity",
                            }
                        )
                        append_event(
                            "proposal.candidate_rejected",
                            title=title,
                            target="moonmind",
                            reason="capacity",
                        )
                        continue
                    target = "moonmind"
                    if isinstance(payload_node, dict):
                        payload_node["repository"] = moonmind_repo
                        stamped_request["payload"] = payload_node
                    target_repo = moonmind_repo
                else:
                    if not effective_policy.consume_workflow_repo_slot():
                        delivery_decisions.append(
                            {
                                "title": title,
                                "target": "workflow_repo",
                                "accepted": False,
                                "reason": "capacity",
                            }
                        )
                        append_event(
                            "proposal.candidate_rejected",
                            title=title,
                            target="project",
                            reason="capacity",
                        )
                        continue

                decision = {
                    "title": title,
                    "target": target,
                    "repository": target_repo,
                    "provider": delivery_provider,
                    "accepted": True,
                }

                try:
                    if service is not None:
                        from moonmind.workflows.proposals.models import (
                            WorkflowProposalOriginSource,
                        )

                        origin_source = WorkflowProposalOriginSource.WORKFLOW
                        candidate_signal = candidate.get("signal")
                        signal_metadata = (
                            deepcopy(dict(candidate_signal))
                            if isinstance(candidate_signal, Mapping)
                            else {"severity": "normal", "type": "follow_up"}
                        )
                        signal_metadata.setdefault("severity", severity)
                        if tags:
                            signal_metadata.setdefault("tags", list(tags))
                        if not signal_metadata.get("type") and tags:
                            signal_metadata["type"] = tags[0]
                        signal_metadata.setdefault("type", "follow_up")
                        origin_metadata = {
                            "source": "workflow",
                            "id": workflow_id,
                            "workflow_id": workflow_id,
                            "temporal_run_id": run_id,
                            "trigger_repo": trigger_repo,
                            "trigger_job_id": trigger_job_id,
                            "signal": signal_metadata,
                        }
                        proposal = await service.create_proposal(
                            title=title,
                            summary=summary,
                            category=candidate.get("category"),
                            tags=candidate.get("tags"),
                            workflow_create_request=stamped_request,
                            origin_source=origin_source,
                            origin_id=None,
                            origin_external_id=workflow_id,
                            origin_metadata=origin_metadata,
                            proposed_by_worker_id=f"temporal:{workflow_id}",
                            proposed_by_user_id=None,
                            provider=delivery_provider,
                            provider_metadata=provider_payload,
                            resolved_policy={
                                **delivery_policy_constraints,
                                "provider": delivery_provider,
                                "target": target,
                                "repository": target_repo,
                                "workflow_id": workflow_id,
                                "default_runtime": default_runtime_value,
                                "default_runtime_applied": default_runtime_applied,
                                "capacity": {
                                    "workflow_repo": {
                                        "allowed": effective_policy.allow_workflow_repo,
                                        "limit": effective_policy.max_items_workflow_repo,
                                        "remaining": (
                                            effective_policy.remaining_workflow_repo_slots
                                        ),
                                        "accepted": (
                                            effective_policy.max_items_workflow_repo
                                            - effective_policy.remaining_workflow_repo_slots
                                        ),
                                    },
                                    "moonmind": {
                                        "allowed": effective_policy.allow_moonmind,
                                        "limit": effective_policy.max_items_moonmind,
                                        "remaining": (
                                            effective_policy.remaining_moonmind_slots
                                        ),
                                        "accepted": (
                                            effective_policy.max_items_moonmind
                                            - effective_policy.remaining_moonmind_slots
                                        ),
                                    },
                                },
                                "gates": {
                                    "moonmind": {
                                        "severity": severity,
                                        "severity_floor": (
                                            effective_policy.min_severity_for_moonmind
                                        ),
                                        "severity_qualified": (
                                            moonmind_severity_qualified
                                        ),
                                        "approved_tags": moonmind_tag_matches,
                                        "qualified": wants_moonmind,
                                    }
                                },
                                "delivery": {
                                    "provider": delivery_provider,
                                    "metadata": provider_payload,
                                },
                            },
                        )
                        external_key = getattr(proposal, "external_key", None)
                        external_url = getattr(proposal, "external_url", None)
                        if external_key:
                            decision["externalKey"] = external_key
                        if external_url:
                            decision["externalUrl"] = external_url
                        delivery_metadata = getattr(
                            proposal, "provider_metadata", {}
                        )
                        if isinstance(delivery_metadata, Mapping):
                            delivery_node = delivery_metadata.get("delivery")
                            if isinstance(delivery_node, Mapping):
                                decision["deliveryStatus"] = delivery_node.get(
                                    "status", "delivered"
                                )
                                if "created" in delivery_node:
                                    decision["created"] = delivery_node["created"]
                                if "duplicateSource" in delivery_node:
                                    decision["duplicateSource"] = delivery_node[
                                        "duplicateSource"
                                    ]
                                if "error" in delivery_node:
                                    decision["error"] = delivery_node["error"]
                        append_event(
                            "proposal.submitted",
                            proposalId=str(getattr(proposal, "id", "")),
                            title=title,
                            target=target,
                            repository=target_repo,
                            provider=delivery_provider,
                            externalKey=external_key,
                        )
                        submitted_count += 1
                    else:
                        logger.info(
                            "proposal.submit: would submit proposal %r (no service wired)",
                            title,
                        )
                        submitted_count += 1
                        append_event(
                            "proposal.submitted",
                            title=title,
                            target=target,
                            repository=target_repo,
                            provider=delivery_provider,
                        )
                    delivery_decisions.append(decision)
                except Exception as exc:
                    redacted_error = self._redactor.scrub(str(exc))[:200]
                    errors.append(f"submission failed for {title!r}: {redacted_error}")
                    decision["accepted"] = False
                    decision["reason"] = "submission_failed"
                    delivery_decisions.append(decision)
                    append_event(
                        "proposal.delivery_failed",
                        title=title,
                        target=target,
                        repository=target_repo,
                        provider=delivery_provider,
                        reason="submission_failed",
                        sanitizedReason=redacted_error,
                    )
                    logger.warning(
                        "proposal.submit: failed to submit proposal %r: %s",
                        title,
                        redacted_error,
                    )

        delivered_count = 0
        external_links: list[dict[str, Any]] = []
        dedup_updates: list[dict[str, Any]] = []
        delivery_failures: list[dict[str, Any]] = []
        for decision in delivery_decisions:
            if not isinstance(decision, Mapping) or not decision.get("accepted"):
                continue
            status = str(decision.get("deliveryStatus") or "").strip().lower()
            external_url = str(decision.get("externalUrl") or "").strip()
            external_key = str(decision.get("externalKey") or "").strip()
            provider = str(decision.get("provider") or delivery_provider).strip()
            if external_url:
                link: dict[str, Any] = {"externalUrl": external_url}
                if provider:
                    link["provider"] = provider
                if external_key:
                    link["externalKey"] = external_key
                external_links.append(link)
            if external_url and status in {"delivered", "updated", "deduped"}:
                delivered_count += 1
                append_event(
                    (
                        "proposal.github_issue_updated"
                        if decision.get("created") is False
                        or decision.get("duplicateSource")
                        else "proposal.github_issue_created"
                    ),
                    provider=provider,
                    externalKey=external_key or None,
                    externalUrl=external_url,
                )
            if decision.get("created") is False or decision.get("duplicateSource"):
                dedup: dict[str, Any] = {"created": bool(decision.get("created"))}
                if provider:
                    dedup["provider"] = provider
                if external_key:
                    dedup["externalKey"] = external_key
                if decision.get("duplicateSource"):
                    dedup["duplicateSource"] = decision["duplicateSource"]
                dedup_updates.append(dedup)
            if status == "failed":
                failure: dict[str, Any] = {}
                if provider:
                    failure["provider"] = provider
                error = decision.get("error")
                if isinstance(error, Mapping):
                    failure.update(dict(error))
                failure.setdefault("code", "delivery_failed")
                failure.setdefault(
                    "message",
                    str(
                        failure.get("sanitizedReason")
                        or failure.get("reason")
                        or "delivery failed"
                    ),
                )
                delivery_failures.append(failure)
                append_event(
                    "proposal.delivery_failed",
                    provider=provider,
                    externalKey=external_key or None,
                    reason=str(failure.get("code") or "delivery_failed"),
                    sanitizedReason=str(failure.get("message") or "delivery failed"),
                )

        for decision in delivery_decisions:
            if not isinstance(decision, Mapping):
                continue
            if decision.get("accepted") is False and decision.get("repository") == "":
                append_event(
                    "proposal.recovery_unroutable_reported",
                    title=str(decision.get("title") or ""),
                    reason=str(decision.get("reason") or "unroutable"),
                )

        return {
            "generated_count": generated_count,
            "submitted_count": submitted_count,
            "deliveredCount": delivered_count,
            "validationErrors": [
                {"code": "proposal_validation_error", "message": error}
                for error in errors
                if (
                    "missing" in error
                    or "invalid" in error
                    or "malformed" in error
                    or "skipped" in error
                )
            ],
            "deliveryFailures": delivery_failures,
            "externalLinks": external_links,
            "dedupUpdates": dedup_updates,
            "errors": errors,
            "delivery_decisions": delivery_decisions,
            "observabilityEvents": observability_events,
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
        session_store: "ManagedSessionStore | None" = None,
        workload_launcher: Any | None = None,
        workload_registry: Any | None = None,
        container_job_backend: Any | None = None,
        workflow_docker_mode: str = "profiles",
        raw_docker_cli_enabled: bool = False,
        client_adapter: Any = None,
        pentest_provider_lease_manager: PentestProviderLeaseManager | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._run_store = run_store
        self._run_supervisor = run_supervisor
        self._run_launcher = run_launcher
        self._session_controller = session_controller
        self._session_store = session_store
        self._workload_launcher = workload_launcher
        self._workload_registry = workload_registry
        self._container_job_backend = container_job_backend
        self._workflow_docker_mode = normalize_workflow_docker_mode(workflow_docker_mode)
        self._raw_docker_cli_enabled = bool(raw_docker_cli_enabled)
        if client_adapter is None:
            from moonmind.workflows.temporal import client as temporal_client_module

            client_adapter = temporal_client_module.TemporalClientAdapter()
        self._client_adapter = client_adapter
        self._pentest_provider_lease_manager = (
            pentest_provider_lease_manager
            or TemporalPentestProviderLeaseManager(client_adapter)
        )
        self._supervision_tasks: set[asyncio.Task] = set()
        from moonmind.workflows.temporal.runtime.checkpoint_restore import (
            ManagedCheckpointRestoreService,
        )

        self._checkpoint_restore = ManagedCheckpointRestoreService(
            authority_root=os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
            artifact_service=artifact_service,
            run_store=run_store,
        )
        self._checkpoint_capture_locks: dict[str, asyncio.Lock] = {}
        from moonmind.workflows.temporal.runtime.checkpoint_restore import (
            ManagedCheckpointRestoreService,
        )

        self._checkpoint_restore = ManagedCheckpointRestoreService(
            authority_root=os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
            artifact_service=artifact_service,
            run_store=run_store,
        )
        # Pentest-specific activity logic lives in a dedicated module/class.
        # Imported lazily to avoid an import cycle (that module imports this one).
        from moonmind.workflows.temporal.activities.pentest_activities import (
            TemporalPentestActivities,
        )

        self._pentest_activities = TemporalPentestActivities(self)

    async def publication_recovery_restore_candidate(self, payload, /, **kwargs):
        """Reject unsafe path fallback until typed checkpoint restore is supplied."""
        raise TemporalActivityRuntimeError(
            "publication recovery checkpoint restoration requires a typed managed "
            "checkpoint restore request; raw source paths are forbidden"
        )

    async def publication_recovery_cleanup(self, payload, /, **kwargs):
        """Return bounded cleanup evidence for a remote-only recovery."""
        restoration = (payload or {}).get("restoration")
        return {
            "cleaned": restoration is None,
            "workspaceReserved": restoration is not None,
        }

    async def agent_runtime_restore_workspace_checkpoint(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Restore and verify a cold checkpoint before any agent is launched."""
        from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError

        try:
            # Restore performs clone/extract/hash/rename work that can exceed the
            # activity heartbeat timeout on large archives or slow clones.
            # Heartbeat while it runs so Temporal does not time out and retry an
            # attempt that may still be mutating the destination. Outside an
            # activity context this simply awaits the coroutine.
            return await _await_with_activity_heartbeats(
                self._checkpoint_restore.restore(request),
                heartbeat_payload={
                    "activity": "agent_runtime.restore_workspace_checkpoint"
                },
            )
        except CheckpointRestoreError as exc:
            # CheckpointRestoreError is a plain RuntimeError, so Temporal would
            # record type="CheckpointRestoreError" and the catalog's
            # non_retryable_error_types (keyed on the stable failure codes) would
            # never match, retrying deterministic failures up to the attempt cap.
            # Re-raise as an ApplicationError whose type is the failure code and
            # mark it non-retryable unless the envelope recommends a retry.
            raise temporal_exceptions.ApplicationError(
                str(exc),
                type=exc.code,
                non_retryable=(
                    exc.failure_envelope.get("retryRecommendation") != "retry"
                ),
            ) from exc

    async def agent_runtime_capture_workspace_checkpoint(
        self, request: Mapping[str, Any] | ManagedWorkspaceCheckpointCaptureInput
    ) -> dict[str, Any]:
        """Capture a Codex workspace through its owning managed-run store."""

        model = (
            request
            if isinstance(request, ManagedWorkspaceCheckpointCaptureInput)
            else ManagedWorkspaceCheckpointCaptureInput.model_validate(request)
        )
        locator = model.workspace_locator
        capture_started = time.monotonic()
        logger.info("managed_checkpoint_capture_requested")
        lock = self._checkpoint_capture_locks.setdefault(
            model.idempotency_key, asyncio.Lock()
        )
        async with lock:
            try:
                if self._run_store is None or self._artifact_service is None:
                    raise TemporalActivityRuntimeError(
                        "managed checkpoint capture requires run and artifact stores"
                    )
                return await _await_with_activity_heartbeats(
                    self._capture_managed_checkpoint_locked(
                        model, locator, capture_started
                    ),
                    heartbeat_payload={
                        "activity": "agent_runtime.capture_workspace_checkpoint",
                        "phase": "capture",
                    },
                )
            except Exception as exc:
                failure_type = getattr(exc, "type", type(exc).__name__)
                logger.warning(
                    "managed_checkpoint_capture_failed failure_code=%s duration_ms=%s",
                    failure_type,
                    int((time.monotonic() - capture_started) * 1000),
                )
                if failure_type == "CHECKPOINT_CAPTURE_LIMIT_EXCEEDED":
                    logger.warning("managed_checkpoint_capture_limit_exceeded")
                raise

    async def _capture_managed_checkpoint_locked(
        self,
        model: ManagedWorkspaceCheckpointCaptureInput,
        locator: ManagedWorkspaceLocator,
        capture_started: float,
    ) -> dict[str, Any]:
            record_root = self._run_store.store_root / "checkpoint_captures"
            record_root.mkdir(parents=True, exist_ok=True)
            record_name = hashlib.sha256(model.idempotency_key.encode()).hexdigest()
            lock_file = (record_root / f"{record_name}.lock").open("a+b")
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                record_path = record_root / f"{record_name}.json"
                immutable = model.model_dump(by_alias=True, mode="json")
                immutable_digest = hashlib.sha256(_json_bytes(immutable)).hexdigest()
                if record_path.exists():
                    saved = json.loads(record_path.read_text(encoding="utf-8"))
                    if saved.get("immutableDigest") != immutable_digest:
                        raise temporal_exceptions.ApplicationError(
                            "immutable capture inputs changed",
                            type="CHECKPOINT_IDEMPOTENCY_CONFLICT",
                            non_retryable=True,
                        )
                    result = dict(saved["result"])
                    logger.info("managed_checkpoint_capture_reused_idempotently")
                    return result

                expected = resolve_runtime_execution_capabilities("codex_cli")
                if model.capability_digest != expected.capability_digest:
                    raise temporal_exceptions.ApplicationError(
                        "capability snapshot is stale",
                        type="CHECKPOINT_CAPABILITY_DIGEST_MISMATCH",
                        non_retryable=True,
                    )
                record = self._run_store.load(locator.agent_run_id)
                if record is None:
                    raise temporal_exceptions.ApplicationError(
                        "managed run record was not found",
                        type=WORKSPACE_IDENTITY_MISMATCH,
                        non_retryable=True,
                    )
                # Session-backed records keep workflowId bound to the AgentRun
                # child so live task/run lookups remain authoritative. Their
                # stable runId is the managed-session binding's parent task
                # workflow ID. Non-session records bind the parent directly in
                # workflowId.
                step_workflow_id = (
                    record.run_id if record.session_id is not None else record.workflow_id
                )
                correlation = {
                    "workflowId": step_workflow_id,
                    "ownerRunId": record.owner_run_id,
                    "logicalStepId": record.logical_step_id,
                    "executionOrdinal": record.execution_ordinal,
                }
                expected_correlation = {
                    "workflowId": model.identity.workflow_id,
                    "ownerRunId": model.identity.run_id,
                    "logicalStepId": model.identity.logical_step_id,
                    "executionOrdinal": model.identity.execution_ordinal,
                }
                exact_execution_match = correlation == expected_correlation
                prior_execution_baseline_match = (
                    model.boundary == "before_execution"
                    and record.status
                    in {"completed", "failed", "canceled", "timed_out"}
                    and record.finished_at is not None
                    and correlation["workflowId"]
                    == expected_correlation["workflowId"]
                    and correlation["ownerRunId"]
                    == expected_correlation["ownerRunId"]
                    and correlation["logicalStepId"]
                    == expected_correlation["logicalStepId"]
                    and isinstance(correlation["executionOrdinal"], int)
                    and correlation["executionOrdinal"] + 1
                    == expected_correlation["executionOrdinal"]
                )
                if not exact_execution_match and not prior_execution_baseline_match:
                    logger.warning("managed_checkpoint_capture_authority_rejected")
                    raise temporal_exceptions.ApplicationError(
                        "managed run record does not belong to the source Step Execution",
                        type=WORKSPACE_IDENTITY_MISMATCH,
                        non_retryable=True,
                    )
                if prior_execution_baseline_match:
                    logger.info(
                        "managed_checkpoint_capture_prior_execution_baseline_accepted"
                    )
                try:
                    workspace = resolve_managed_workspace_locator(
                        locator,
                        store=self._run_store,
                        current_agent_run_id=locator.agent_run_id,
                        current_runtime_id=model.expected_runtime_id,
                    )
                except WorkspaceLocatorResolutionError as exc:
                    raise temporal_exceptions.ApplicationError(
                        str(exc), type=exc.code, non_retryable=True
                    ) from exc
                result = await self._capture_managed_worktree(model, workspace, record)
                temporary = record_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(
                        {"immutableDigest": immutable_digest, "result": result},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, record_path)
                logger.info(
                    "managed_checkpoint_capture_succeeded bytes=%s duration_ms=%s",
                    result["workspace"].get("archiveBytes", 0),
                    int((time.monotonic() - capture_started) * 1000),
                )
                return result
            finally:
                lock_file.close()

    async def _capture_managed_worktree(
        self,
        model: ManagedWorkspaceCheckpointCaptureInput,
        workspace: Path,
        record: Any,
    ) -> dict[str, Any]:
        policy = model.capture_policy
        enumerate_args = ["ls-files", "-z", "--cached"]
        if policy.include_untracked:
            enumerate_args.extend(["--others", "--exclude-standard"])
        git_files = await _run_command(
            self._workspace_git_command(str(workspace), *enumerate_args),
        )
        paths = sorted(filter(None, git_files.stdout.split("\0")))
        excluded_names = {".env", ".env.local", "credentials", "credentials.json"}
        excluded_parts = {
            ".git", ".codex", ".ssh", ".gnupg", "node_modules", "__pycache__",
            ".cache", ".docker", "credentials", "managed_runs", "managed_sessions",
        }
        selected = [
            path for path in paths
            if Path(path).name not in excluded_names
            and not any(part in excluded_parts for part in Path(path).parts)
            and Path(path).parts[:2]
            not in {(".agents", "skills"), (".gemini", "skills")}
        ]
        if len(selected) > policy.max_file_count:
            raise temporal_exceptions.ApplicationError(
                "maximum file count exceeded", type="CHECKPOINT_CAPTURE_LIMIT_EXCEEDED",
                non_retryable=True,
            )
        entries: list[ManagedCheckpointEntry] = []
        total = 0
        output = BytesIO()

        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed, tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
        ) as archive:
            for relative_text in selected:
                # Archive construction is synchronous. Yield between entries so
                # the bounded heartbeat task can run without enqueueing one
                # heartbeat per file and flooding the Temporal SDK queue.
                await asyncio.sleep(0)
                path = workspace / relative_text
                if not path.exists() and not path.is_symlink():
                    continue
                info_stat = path.lstat()
                if stat.S_ISLNK(info_stat.st_mode):
                    target = os.readlink(path)
                    resolved = (path.parent / target).resolve()
                    if not resolved.is_relative_to(workspace):
                        raise temporal_exceptions.ApplicationError(
                            f"symlink escapes workspace: {relative_text}",
                            type=WORKSPACE_AUTHORITY_MISMATCH, non_retryable=True,
                        )
                    payload = target.encode()
                    entry_type = "symlink"
                elif stat.S_ISREG(info_stat.st_mode):
                    if info_stat.st_size > policy.max_file_bytes:
                        raise temporal_exceptions.ApplicationError(
                            "maximum per-file size exceeded",
                            type="CHECKPOINT_CAPTURE_LIMIT_EXCEEDED", non_retryable=True,
                        )
                    digest = await asyncio.to_thread(_sha256_file, path)
                    payload = None
                    target = None
                    entry_type = "file"
                elif stat.S_ISDIR(info_stat.st_mode):
                    # Gitlinks are represented by repository metadata and should not
                    # recursively capture another repository's worktree.
                    continue
                else:
                    raise temporal_exceptions.ApplicationError(
                        f"unsupported file type: {relative_text}",
                        type="CHECKPOINT_CAPTURE_POLICY_INVALID", non_retryable=True,
                    )
                payload_size = (
                    len(payload) if payload is not None else info_stat.st_size
                )
                total += payload_size
                if total > policy.max_total_bytes:
                    raise temporal_exceptions.ApplicationError(
                        "maximum total size exceeded",
                        type="CHECKPOINT_CAPTURE_LIMIT_EXCEEDED", non_retryable=True,
                    )
                tar_info = archive.gettarinfo(str(path), arcname=relative_text)
                tar_info.uid = tar_info.gid = tar_info.mtime = 0
                tar_info.uname = tar_info.gname = ""
                if entry_type == "file":
                    with path.open("rb") as source:
                        await asyncio.to_thread(archive.addfile, tar_info, source)
                else:
                    archive.addfile(tar_info)
                entries.append(
                    ManagedCheckpointEntry(
                        path=relative_text,
                        type=entry_type,
                        mode=f"{stat.S_IMODE(info_stat.st_mode):06o}",
                        size=payload_size,
                        sha256=(
                            digest
                            if entry_type == "file"
                            else hashlib.sha256(payload).hexdigest()
                        ),
                        linkTarget=target,
                    )
                )
        archive_payload = output.getvalue()
        archive_digest = "sha256:" + hashlib.sha256(archive_payload).hexdigest()
        archive_ref = await self._put_managed_checkpoint_artifact(
            archive_payload,
            "application/vnd.moonmind.worktree-archive",
            "checkpoint_archive",
        )
        async def _git(*args: str) -> str:
            command = self._workspace_git_command(str(workspace), *args)
            return (await _run_command(command)).stdout.strip()
        head = await _git("rev-parse", "HEAD")
        branch = await _git("branch", "--show-current")
        status = (
            await _run_command(
                self._workspace_git_command(
                    str(workspace),
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ),
            )
        ).stdout
        created_at = (record.finished_at or record.started_at).isoformat()
        staged_paths = []
        records = status.split("\0")
        index = 0
        while index < len(records):
            line = records[index]
            index += 1
            if len(line) < 4:
                continue
            if line[0] not in {" ", "?"}:
                staged_paths.append(line[3:])
            if line[0] in {"R", "C"} and index < len(records):
                index += 1
        manifest = {
            "schemaVersion": "v1",
            "contentType": "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1",
            "source": {**model.identity.model_dump(by_alias=True, mode="json"), "boundary": model.boundary},
            "runtime": {"runtimeId": "codex_cli", "capabilitySetVersion": model.capability_set_version, "capabilityDigest": model.capability_digest},
            "workspaceLocator": model.workspace_locator.model_dump(by_alias=True, mode="json"),
            "git": {"baseCommit": head, "headCommit": head, "branch": branch, "isDirty": bool(status), "statusDigest": "sha256:" + hashlib.sha256(status.encode()).hexdigest(), "stagedPaths": staged_paths, "submodules": []},
            "capturePolicy": policy.model_dump(by_alias=True, mode="json"),
            "entries": [entry.model_dump(by_alias=True, mode="json", exclude_none=True) for entry in entries],
            "archive": {"ref": archive_ref, "sha256": archive_digest, "size": len(archive_payload)},
            "createdAt": created_at,
        }
        manifest_payload = _json_bytes(manifest)
        scan = scan_outbound_bundle(
            [
                OutboundBundleItem(location="checkpoint.manifest", content=manifest_payload.decode("utf-8")),
            ],
            high_security_mode=True,
        )
        if not scan.allowed:
            raise temporal_exceptions.ApplicationError(
                "checkpoint metadata failed outbound secret scanning",
                type="CHECKPOINT_CAPTURE_SECRET_DETECTED",
                non_retryable=True,
            )
        manifest_digest = "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
        manifest_ref = await self._put_managed_checkpoint_artifact(
            manifest_payload,
            "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1",
            "checkpoint_manifest",
        )
        logger.info("managed_checkpoint_capture_files files=%s", len(entries))
        logger.info("managed_checkpoint_capture_bytes bytes=%s", len(archive_payload))
        compact = ManagedWorkspaceCheckpointCaptureResult(
            status="captured",
            workspace={
                "kind": "worktree_archive", "baseCommit": head,
                "archiveRef": archive_ref, "archiveDigest": archive_digest,
                "archiveBytes": len(archive_payload),
                "manifestRef": manifest_ref, "manifestDigest": manifest_digest,
                "includesUntracked": policy.include_untracked,
                "includesIgnoredFiles": False,
            },
            sourceWorkspaceLocator=model.workspace_locator,
            diagnosticRefs=[manifest_ref],
            idempotencyKey=model.idempotency_key,
        )
        return compact.model_dump(by_alias=True, mode="json", exclude_none=True)

    async def _put_managed_checkpoint_artifact(
        self, payload: bytes, content_type: str, artifact_kind: str
    ) -> str:
        artifact, _ = await self._artifact_service.create(
            principal="system", content_type=content_type,
            size_bytes=len(payload), metadata_json={"artifact_kind": artifact_kind},
        )
        completed = await self._artifact_service.write_payload_complete(
            artifact_id=artifact.artifact_id, principal="system",
            payload=payload, content_type=content_type,
        )
        return _compact_artifact_ref_text(build_artifact_ref(completed))

    async def agent_runtime_restore_workspace_checkpoint(
        self, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Restore and verify a cold checkpoint before any agent is launched."""
        from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError

        try:
            # Restore performs clone/extract/hash/rename work that can exceed the
            # activity heartbeat timeout on large archives or slow clones.
            # Heartbeat while it runs so Temporal does not time out and retry an
            # attempt that may still be mutating the destination. Outside an
            # activity context this simply awaits the coroutine.
            return await _await_with_activity_heartbeats(
                asyncio.to_thread(
                    lambda: asyncio.run(self._checkpoint_restore.restore(request))
                ),
                heartbeat_payload={
                    "activity": "agent_runtime.restore_workspace_checkpoint"
                },
            )
        except CheckpointRestoreError as exc:
            # CheckpointRestoreError is a plain RuntimeError, so Temporal would
            # record type="CheckpointRestoreError" and the catalog's
            # non_retryable_error_types (keyed on the stable failure codes) would
            # never match, retrying deterministic failures up to the attempt cap.
            # Re-raise as an ApplicationError whose type is the failure code and
            # mark it non-retryable unless the envelope recommends a retry.
            raise temporal_exceptions.ApplicationError(
                str(exc),
                type=exc.code,
                non_retryable=(
                    exc.failure_envelope.get("retryRecommendation") != "retry"
                ),
            ) from exc

    async def execution_notify_completion(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Best-effort webhook notification for terminal agent-run results."""

        payload = _coerce_activity_payload_input(
            request,
            activity_type="execution.notify_completion",
            kwargs=kwargs,
        )
        notification_settings = settings.execution_notifications
        webhook_url = str(notification_settings.webhook_url or "").strip()
        email_recipients = _coerce_notification_recipients(
            notification_settings.email_to
        )
        email_sender = str(notification_settings.email_from or "").strip()
        smtp_host = str(notification_settings.smtp_host or "").strip()
        email_configured = bool(email_recipients and email_sender and smtp_host)
        if not notification_settings.enabled:
            return {"status": "skipped", "reason": "disabled"}
        if not webhook_url and not email_configured:
            return {"status": "skipped", "reason": "no_channels"}

        event = _build_execution_notification_payload(payload, redact=True)
        results: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        timeout_seconds = max(1, int(notification_settings.timeout_seconds or 5))
        if webhook_url:
            headers = {"Content-Type": "application/json"}
            authorization = str(notification_settings.authorization or "").strip()
            if authorization:
                headers["Authorization"] = authorization
            blocked_reason = _scan_execution_notification_before_send(
                event,
                surface="execution.notification.webhook.payload",
            )
            if blocked_reason is not None:
                errors.append(
                    {
                        "channel": "webhook",
                        "reason": blocked_reason,
                        "target": _redacted_webhook_target(webhook_url),
                    }
                )
            else:
                try:
                    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                        response = await client.post(
                            webhook_url,
                            json=event,
                            headers=headers,
                        )
                        response.raise_for_status()
                except Exception as exc:  # pragma: no cover - best effort telemetry
                    logger.warning("Execution completion webhook failed: %s", exc)
                    errors.append(
                        {
                            "channel": "webhook",
                            "reason": redact_sensitive_text(str(exc)),
                            "target": _redacted_webhook_target(webhook_url),
                        }
                    )
                else:
                    results.append(
                        {
                            "channel": "webhook",
                            "target": _redacted_webhook_target(webhook_url),
                        }
                    )
        if email_configured:
            blocked_reason = _scan_execution_notification_before_send(
                event,
                surface="execution.notification.email.payload",
            )
            if blocked_reason is not None:
                errors.append(
                    {
                        "channel": "email",
                        "reason": blocked_reason,
                        "target": _redacted_email_target(email_recipients),
                    }
                )
            else:
                try:
                    await asyncio.to_thread(
                        _send_execution_notification_email,
                        event,
                        sender=email_sender,
                        recipients=email_recipients,
                        smtp_host=smtp_host,
                        smtp_port=int(notification_settings.smtp_port),
                        smtp_username=notification_settings.smtp_username,
                        smtp_password=notification_settings.smtp_password,
                        smtp_use_tls=bool(notification_settings.smtp_use_tls),
                        smtp_use_ssl=bool(notification_settings.smtp_use_ssl),
                        timeout_seconds=timeout_seconds,
                    )
                except Exception as exc:  # pragma: no cover - best effort telemetry
                    logger.warning("Execution completion email failed: %s", exc)
                    errors.append(
                        {
                            "channel": "email",
                            "reason": redact_sensitive_text(str(exc)),
                            "target": _redacted_email_target(email_recipients),
                        }
                    )
                else:
                    results.append(
                        {
                            "channel": "email",
                            "target": _redacted_email_target(email_recipients),
                        }
                    )
        if errors and not results:
            if all(
                error["reason"].startswith("Blocked outbound content")
                for error in errors
            ):
                if len(errors) == 1:
                    return {
                        "status": "blocked",
                        "reason": errors[0]["reason"],
                        "target": errors[0]["target"],
                    }
                return {"status": "blocked", "errors": errors}
            if len(errors) == 1:
                return {
                    "status": "failed",
                    "reason": errors[0]["reason"],
                    "target": errors[0]["target"],
                }
            return {"status": "failed", "errors": errors}
        if len(results) == 1:
            result: dict[str, Any] = {"status": "sent", "target": results[0]["target"]}
            if errors:
                result["errors"] = errors
            return result
        result = {
            "status": "sent",
            "channels": [result["channel"] for result in results],
            "targets": [result["target"] for result in results],
        }
        if errors:
            result["errors"] = errors
        return result

    async def integration_omnigent_profile_bound_execute(
        self, request: Any = None, /, **kwargs: Any
    ) -> AgentRunResult:
        from moonmind.workflows.temporal.activities.omnigent_activities import (
            omnigent_profile_bound_execute_activity,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="integration.omnigent.profile_bound_execute",
            kwargs=kwargs,
        )
        req = AgentExecutionRequest.model_validate(payload)
        return await omnigent_profile_bound_execute_activity(req)

    async def integration_omnigent_oauth_host_janitor(
        self, request: Any = None, /, **kwargs: Any
    ) -> dict[str, object]:
        from moonmind.workflows.temporal.activities.omnigent_activities import (
            omnigent_oauth_host_janitor_activity,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="integration.omnigent.oauth_host_janitor",
            kwargs=kwargs,
        )
        return await omnigent_oauth_host_janitor_activity(payload)

    async def oauth_session_prepare_credential_maintenance(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_prepare_credential_maintenance as _prepare,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.prepare_credential_maintenance",
            kwargs=kwargs,
        )
        return await _prepare(payload)

    async def oauth_session_revalidate_bound_host(
        self, request: Any = None, /, **kwargs: Any
    ) -> dict[str, Any]:
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_revalidate_bound_host as _revalidate,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.revalidate_bound_host",
            kwargs=kwargs,
        )
        return await _revalidate(payload)

    async def oauth_session_ensure_volume(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Ensure an OAuth auth volume from the Docker-capable runtime fleet."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_ensure_volume as _ensure_volume,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.ensure_volume",
            kwargs=kwargs,
        )
        return await _ensure_volume(payload)

    async def oauth_session_start_auth_runner(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Start an OAuth auth runner from the Docker-capable runtime fleet."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_start_auth_runner as _start_auth_runner,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.start_auth_runner",
            kwargs=kwargs,
        )
        return await _start_auth_runner(payload)

    async def oauth_session_stop_auth_runner(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Stop an OAuth auth runner from the Docker-capable runtime fleet."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_stop_auth_runner as _stop_auth_runner,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.stop_auth_runner",
            kwargs=kwargs,
        )
        return await _stop_auth_runner(payload)

    async def oauth_session_verify_volume(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Verify an OAuth auth volume from the Docker-capable runtime fleet."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_verify_volume as _verify_volume,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.verify_volume",
            kwargs=kwargs,
        )
        return await _verify_volume(payload)

    async def oauth_session_verify_cli_fingerprint(
        self,
        request: Any = None,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Verify CLI auth material from the Docker-capable runtime fleet."""
        from moonmind.workflows.temporal.activities.oauth_session_activities import (
            oauth_session_verify_cli_fingerprint as _verify_cli_fingerprint,
        )

        payload = _coerce_activity_payload_input(
            request,
            activity_type="oauth_session.verify_cli_fingerprint",
            kwargs=kwargs,
        )
        return await _verify_cli_fingerprint(payload)

    async def _report_task_run_binding(self, workflow_id: str, run_id: str) -> None:
        """Persist the managed agent-run UUID onto the execution record.

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
                "run_id %r is not a valid UUID; skipping agent run binding for workflow %s",
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
                        "workflow_id %s was not found; cannot persist agent run binding",
                        workflow_id,
                    )
                    return
                memo = dict(record.memo or {})
                if memo.get("agentRunId") == run_id:
                    return
                memo["agentRunId"] = run_id
                record.memo = memo
                await db.commit()
        except Exception:
            logger.warning(
                "Failed to persist agent run binding for workflow %s run %s",
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
        if _provider_profile_prefers_proxy_first(profile):
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
            docker_sidecar_launch_plan=context.docker_sidecar_launch_plan,
        )
        return {
            "profile_id": result.profile_id,
            "credential_source": result.credential_source,
            "delta_env_overrides": result.delta_env_overrides,
            "passthrough_env_keys": result.passthrough_env_keys,
            "env_keys_count": result.env_keys_count,
            "docker_sidecar_launch_plan": result.docker_sidecar_launch_plan,
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
        # Whether a resume must restore a workspace checkpoint is decided and
        # enforced at the authoritative RunWorkflow ``before_recovery_restoration``
        # boundary, which runs the restore activity before the failed step re-runs.
        # The real resume payloads carry that decision under
        # ``parameters["recoverySource"]`` / ``parameters["workflow"]["resume"]`` —
        # never as a top-level ``recoveryMode == "resume_from_workspace_checkpoint"``
        # sentinel (that value is produced nowhere), so the former sentinel gate
        # was dead. When the restoration producer supplies verified evidence in the
        # launch payload, verify it here as defense-in-depth before the agent
        # starts.
        restoration_requirement = payload.get("restoration_requirement")
        if restoration_requirement is not None:
            if not isinstance(restoration_requirement, Mapping):
                raise TemporalActivityRuntimeError("restoration_requirement must be a mapping")
            self._checkpoint_restore.assert_ready_for_launch(
                agent_run_id=str(run_id),
                checkpoint_ref=str(restoration_requirement.get("checkpointRef") or ""),
                capability_digest=str(restoration_requirement.get("capabilityDigest") or ""),
            )
            expected_workspace = (
                self._checkpoint_restore.root / str(run_id) / "repo"
            ).resolve()
            if workspace_path is None or Path(workspace_path).resolve() != expected_workspace:
                from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError

                raise CheckpointRestoreError(
                    "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                    "launch workspace does not match the verified restored destination",
                )

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

        response = record.model_dump(mode="json")
        if request.terminal_contract is not None:
            response["terminalContract"] = request.terminal_contract.model_dump(
                mode="json", by_alias=True
            )

        if process is None:
            # Idempotent path: run is already active, skip secondary supervision
            return response

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

        return response

    async def workload_run(
        self,
        payload: Mapping[str, Any],
        /,
    ) -> dict[str, Any]:
        """Run one validated Docker workload on the agent_runtime fleet."""

        workflow_mode = self._workflow_docker_mode
        if workflow_mode == "disabled":
            raise _docker_workflows_disabled_failure()
        if self._workload_registry is None or self._workload_launcher is None:
            raise TemporalActivityRuntimeError(
                "workload registry and launcher are required for workload.run"
            )
        request_payload = dict(payload.get("request", payload))
        reason = str(request_payload.pop("reason", "") or "bounded_window_complete")
        if request_payload.get("toolName") == _LEGACY_CONTAINER_STOP_HELPER_TOOL:
            request_payload.setdefault("command", ["stop"])
        request = parse_workload_request(request_payload)
        if not _legacy_workload_tool_allowed(request.tool_name, workflow_mode):
            raise _docker_workflow_mode_forbidden_failure(
                workflow_docker_mode=workflow_mode,
                tool_name=request.tool_name,
            )
        validated = self._workload_registry.validate_request(
            request,
            workflow_docker_mode=workflow_mode,
        )
        if request.tool_name == _LEGACY_CONTAINER_START_HELPER_TOOL:
            result = await self._workload_launcher.start_helper(validated)
        elif request.tool_name == _LEGACY_CONTAINER_STOP_HELPER_TOOL:
            result = await self._workload_launcher.stop_helper(
                validated,
                reason=reason,
            )
        else:
            result = await self._workload_launcher.run(validated)
        if not isinstance(result, WorkloadResult):
            result = WorkloadResult.model_validate(result)
        return result.model_dump(mode="json", by_alias=True)

    async def _container_job_call(
        self, operation: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate and delegate one typed request to the trusted backend."""

        if self._container_job_backend is None:
            raise TemporalActivityRuntimeError(
                f"container-job backend is required for container_job.{operation}"
            )
        from temporalio.exceptions import ApplicationError

        from moonmind.schemas.container_job_models import (
            ContainerJobActivityRequest,
            ContainerJobActivityResult,
            ContainerJobBackendError,
            ContainerJobFailureClass,
        )
        from moonmind.workflows.temporal.container_image_acquisition import (
            ImageAcquisitionError,
        )

        request = ContainerJobActivityRequest.model_validate(payload)
        try:
            result = await getattr(self._container_job_backend, operation)(request)
        except ContainerJobBackendError as exc:
            # Preserve the specific failure class across the activity boundary
            # and fail fast on deterministic authority-sensitive denials so a
            # denied image, credential, or scope is not retried pointlessly.
            deterministic = {
                ContainerJobFailureClass.IMAGE_USE_DENIED,
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                ContainerJobFailureClass.CREDENTIAL_UNRESOLVED,
            }
            raise temporal_exceptions.ApplicationError(
                str(exc),
                type=exc.failure_class.value,
                non_retryable=exc.failure_class in deterministic,
            ) from exc
        except ImageAcquisitionError as exc:
            # Surface the granular image failure class to the workflow via the
            # ApplicationError type so the durable terminal outcome is exact.
            raise temporal_exceptions.ApplicationError(
                str(exc),
                *([{"diagnosticsRef": exc.diagnostics_ref}] if exc.diagnostics_ref else []),
                type=exc.failure_class.value,
                non_retryable=exc.terminal,
            ) from exc
        return ContainerJobActivityResult.model_validate(result).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

    async def container_job_submit(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        """Create/reuse API-owned identity and start its durable workflow."""
        from api_service.db.base import get_async_session_context
        from api_service.services.container_jobs import ContainerJobService
        from moonmind.schemas.container_job_models import ContainerJobSubmitRequest, OwnerIdentity

        owner = OwnerIdentity.model_validate(payload.get("owner"))
        request = ContainerJobSubmitRequest.model_validate(payload.get("request"))
        async with get_async_session_context() as session:
            accepted = await ContainerJobService(session).submit(owner=owner, request=request)
            await session.commit()
        return accepted.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def container_job_status(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        from api_service.db.base import get_async_session_context
        from api_service.services.container_jobs import ContainerJobService
        from moonmind.schemas.container_job_models import OwnerIdentity

        owner = OwnerIdentity.model_validate(payload.get("owner"))
        async with get_async_session_context() as session:
            status = await ContainerJobService(session).status(
                owner=owner, job_id=str(payload.get("jobId") or "")
            )
        return status.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def container_job_cancel(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        from api_service.db.base import get_async_session_context
        from api_service.services.container_jobs import ContainerJobService
        from moonmind.schemas.container_job_models import ContainerJobCancelRequest, OwnerIdentity

        owner = OwnerIdentity.model_validate(payload.get("owner"))
        request = ContainerJobCancelRequest.model_validate(payload.get("request"))
        async with get_async_session_context() as session:
            result = await ContainerJobService(session).cancel(
                owner=owner, job_id=str(payload.get("jobId") or ""), request=request
            )
            await session.commit()
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def container_job_resolve_workspace(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("resolve_workspace", payload)

    async def container_job_acquire_image(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("acquire_image", payload)

    async def container_job_create_container(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("create_container", payload)

    async def container_job_start_container(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("start_container", payload)

    async def container_job_observe_container(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("observe_container", payload)

    async def container_job_reconcile_container(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("reconcile_container", payload)

    async def container_job_stop_container(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("stop_container", payload)

    async def container_job_remove_container(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("remove_container", payload)

    async def container_job_publish_evidence(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("publish_evidence", payload)

    async def container_job_project_status(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("project_status", payload)

    async def container_job_repair_projection(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("repair_projection", payload)

    async def container_job_cleanup(self, payload: Mapping[str, Any], /) -> dict[str, Any]:
        return await self._container_job_call("cleanup", payload)

    async def security_pentest_execute(
        self,
        payload: Mapping[str, Any],
        /,
    ) -> dict[str, Any]:
        """Registry-dispatched PentestGPT activity boundary (untrusted input).

        Thin delegate to :class:`TemporalPentestActivities`. This is the
        entrypoint bound to the ``security.pentest.execute`` activity type, so
        its payload may originate from a caller-supplied plan and is always
        treated as untrusted: an inline ``approved_scope`` is rejected and the
        scope is always loaded from the artifact store.
        """

        return await self._pentest_activities.security_pentest_execute(payload)

    async def _security_pentest_execute_trusted_internal(
        self,
        payload: Mapping[str, Any],
        /,
    ) -> dict[str, Any]:
        """Internal entrypoint that may honor an inline ``approved_scope``.

        Thin delegate to :class:`TemporalPentestActivities`. Intentionally
        **not** registered in ``_ACTIVITY_HANDLER_ATTRS`` so it cannot be
        reached through registry/plan dispatch; only trusted workflow-internal
        code can call it.
        """

        return await self._pentest_activities._security_pentest_execute_trusted_internal(
            payload
        )

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

        async def _notify_terminal_result(
            published_result: AgentRunResult | Mapping[str, Any],
        ) -> None:
            result_payload = (
                published_result.model_dump(mode="json", by_alias=True)
                if isinstance(published_result, BaseModel)
                else dict(published_result)
            )
            result_metadata = (
                result_payload.get("metadata")
                if isinstance(result_payload.get("metadata"), Mapping)
                else {}
            )
            try:
                await self.execution_notify_completion(
                    {
                        "workflowId": (
                            info.workflow_id
                            if info is not None
                            else result_metadata.get("childWorkflowId", "")
                        ),
                        "runId": (
                            info.workflow_run_id
                            if info is not None
                            else result_metadata.get("childRunId", "")
                        ),
                        "agentId": result_metadata.get("agentId", ""),
                        "agentKind": result_metadata.get("agentKind", ""),
                        "status": (
                            result_metadata.get("status")
                            or (
                                "failed"
                                if result_payload.get("failureClass")
                                else "completed"
                            )
                        ),
                        "result": result_payload,
                    }
                )
            except Exception:
                logger.warning(
                    "agent_runtime.publish_artifacts completion notification failed",
                    exc_info=True,
                )

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
        if attempt is None:
            attempt = step_ledger_metadata.get("executionOrdinal")
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

        def _metadata_text(*keys: str) -> str:
            for key in keys:
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""

        def _story_output_mapping() -> Mapping[str, Any]:
            value = metadata.get("storyOutput") or metadata.get("story_output")
            return value if isinstance(value, Mapping) else {}

        story_output_metadata = _story_output_mapping()

        def _story_metadata_text(*keys: str) -> str:
            for key in keys:
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                value = story_output_metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""

        def _workspace_story_path_candidates(
            workspace: Path,
            raw_path: str,
        ) -> list[Path]:
            if not raw_path:
                return []
            candidate = Path(raw_path)
            candidates: list[Path] = []
            if not candidate.is_absolute():
                candidates.append(workspace / candidate)
                # Managed agent workspaces have a checked-out repo plus a
                # sibling job-level artifact root. If repo-local artifacts/ is
                # not writable, agents may still correctly write to the
                # per-job artifact directory.
                if candidate.parts and candidate.parts[0] == "artifacts":
                    candidates.append(workspace.parent / candidate)
            else:
                candidates.append(candidate)

            resolved_candidates: list[Path] = []
            job_artifact_root = (workspace.parent / "artifacts").resolve()
            for path in candidates:
                resolved = path.expanduser().resolve()
                allowed = resolved.is_relative_to(workspace) or resolved.is_relative_to(
                    job_artifact_root
                )
                if not allowed:
                    logger.warning(
                        "Skipping story breakdown artifact publication outside "
                        "workspace or job artifact root: %s",
                        raw_path,
                    )
                    continue
                if resolved not in resolved_candidates:
                    resolved_candidates.append(resolved)
            return resolved_candidates

        def _workspace_story_path(workspace: Path, raw_path: str) -> Path | None:
            candidates = _workspace_story_path_candidates(workspace, raw_path)
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
            if candidates:
                logger.warning(
                    "Story breakdown handoff file was not found for publication: %s",
                    raw_path,
                )
            return None

        async def _publish_story_breakdown_file(
            *,
            workspace: Path,
            raw_path: str,
            link_type: str,
            content_type: str,
            metadata_name: str,
            label: str,
        ) -> str:
            path = _workspace_story_path(workspace, raw_path)
            if path is None:
                return ""
            payload = path.read_bytes()
            artifact, _upload = await self._artifact_service.create(
                principal="system:agent_runtime",
                content_type=content_type,
                size_bytes=len(payload),
                link=_execution_ref(link_type),
                metadata_json={
                    "name": metadata_name,
                    "path": raw_path,
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": ["agent_runtime", link_type, "story_breakdown"],
                    **step_artifact_metadata,
                },
            )
            completed = await self._artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="system:agent_runtime",
                payload=payload,
                content_type=content_type,
            )
            logger.info(
                "Published %s story breakdown handoff artifact for %s",
                label,
                raw_path,
            )
            return completed.artifact_id

        async def _publish_story_breakdown_handoff() -> dict[str, Any]:
            json_path = _story_metadata_text(
                "storyBreakdownPath",
                "story_breakdown_path",
            )
            if not json_path:
                return {}
            agent_run_id = _metadata_text("agentRunId", "agent_run_id")
            if not agent_run_id or self._run_store is None:
                return {}
            record = self._run_store.load(agent_run_id)
            if record is None:
                logger.warning(
                    "Skipping story breakdown artifact publication: run record not "
                    "found for %s",
                    agent_run_id,
                )
                return {}
            workspace_path = str(getattr(record, "workspace_path", "") or "").strip()
            if not workspace_path:
                return {}
            workspace = Path(workspace_path).expanduser().resolve()
            published: dict[str, Any] = {}
            json_ref = await _publish_story_breakdown_file(
                workspace=workspace,
                raw_path=json_path,
                link_type="output.story_breakdown",
                content_type="application/json",
                metadata_name="stories.json",
                label="JSON",
            )
            if json_ref:
                published["storyBreakdownArtifactRef"] = json_ref
            markdown_path = _story_metadata_text(
                "storyBreakdownMarkdownPath",
                "story_breakdown_markdown_path",
            )
            markdown_ref = await _publish_story_breakdown_file(
                workspace=workspace,
                raw_path=markdown_path,
                link_type="output.story_breakdown_markdown",
                content_type="text/markdown",
                metadata_name="stories.md",
                label="markdown",
            )
            if markdown_ref:
                published["storyBreakdownMarkdownArtifactRef"] = markdown_ref
            if not published:
                return {}
            story_output = (
                dict(story_output_metadata)
                if isinstance(story_output_metadata, Mapping)
                else {}
            )
            story_output.update(published)
            if json_path:
                story_output.setdefault("storyBreakdownPath", json_path)
            if markdown_path:
                story_output.setdefault("storyBreakdownMarkdownPath", markdown_path)
            published["storyOutput"] = story_output
            return published

        def _workspace_moonspec_verify_path_candidates(
            workspace: Path,
            raw_path: str,
        ) -> list[Path]:
            if not raw_path:
                return []
            candidate = Path(raw_path)
            candidates: list[Path] = []
            if not candidate.is_absolute():
                candidates.append(workspace / candidate)
                if candidate.parts and candidate.parts[0] == "artifacts":
                    candidates.append(workspace.parent / candidate)
            else:
                candidates.append(candidate)

            resolved_candidates: list[Path] = []
            job_artifact_root = (workspace.parent / "artifacts").resolve()
            for path in candidates:
                resolved = path.expanduser().resolve()
                allowed = resolved.is_relative_to(workspace) or resolved.is_relative_to(
                    job_artifact_root
                )
                if not allowed:
                    logger.warning(
                        "Skipping MoonSpec verify artifact publication outside "
                        "workspace or job artifact root: %s",
                        raw_path,
                    )
                    continue
                if resolved not in resolved_candidates:
                    resolved_candidates.append(resolved)
            return resolved_candidates

        def _workspace_moonspec_verify_path(
            workspace: Path,
            raw_path: str,
        ) -> Path | None:
            candidates = _workspace_moonspec_verify_path_candidates(
                workspace,
                raw_path,
            )
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
            if candidates:
                logger.warning(
                    "MoonSpec verify artifact file was not found for publication: %s",
                    raw_path,
                )
            return None

        def _first_non_empty_text(
            payload: Mapping[str, Any],
            *keys: str,
        ) -> str | None:
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        def _compact_moonspec_verify_metadata(
            gate_payload: Mapping[str, Any],
            *,
            gate_result_ref: str,
            contract_violations: Sequence[str],
        ) -> dict[str, Any]:
            """Return the compact gate projection safe to carry in workflow history."""

            def _text(value: Any, *, max_chars: int = 700) -> str | None:
                if not isinstance(value, str):
                    return None
                text = value.strip()
                if not text:
                    return None
                if len(text) > max_chars:
                    return text[: max_chars - 3].rstrip() + "..."
                return text

            def _scalar(value: Any) -> Any:
                if isinstance(value, str):
                    return _text(value)
                if value is None or isinstance(value, (bool, int, float)):
                    return value
                return None

            def _text_list(
                value: Any,
                *,
                max_items: int = 20,
                max_chars: int = 400,
            ) -> list[str]:
                if not isinstance(value, (list, tuple)):
                    return []
                compact: list[str] = []
                for item in value:
                    text = _text(item, max_chars=max_chars)
                    if text:
                        compact.append(text)
                    if len(compact) >= max_items:
                        break
                return compact

            def _text_mapping(value: Any) -> dict[str, str]:
                if not isinstance(value, Mapping):
                    return {}
                compact: dict[str, str] = {}
                for raw_key, raw_value in value.items():
                    key = _text(str(raw_key), max_chars=120)
                    text = _text(raw_value, max_chars=400)
                    if key and text:
                        compact[key] = text
                    if len(compact) >= 20:
                        break
                return compact

            compact: dict[str, Any] = {"gateResultRef": gate_result_ref}
            scalar_keys = (
                "schemaVersion",
                "verdict",
                "gateVerdict",
                "gate_verdict",
                "moonSpecVerdict",
                "moonspecVerdict",
                "verificationVerdict",
                "verification_verdict",
                "confidence",
                "recommendedNextAction",
                "recommended_next_action",
                "targetLogicalStepId",
                "target_logical_step_id",
                "workspacePolicyRecommendation",
                "workspace_policy_recommendation",
                "recoverableInCurrentRuntime",
                "recoverable_in_current_runtime",
                "invalid",
                "degraded",
                "remainingWorkRef",
                "remaining_work_ref",
                "diagnosticsRef",
                "diagnostics_ref",
                "verificationReportRef",
                "verification_report_ref",
                "reportRef",
                "report_ref",
                "rawRecommendedNextAction",
                "raw_recommended_next_action",
            )
            for key in scalar_keys:
                value = _scalar(gate_payload.get(key))
                if value is not None:
                    compact[key] = value

            for key in ("feedback", "summary", "message", "downgradeReason"):
                value = _text(gate_payload.get(key), max_chars=900)
                if value:
                    compact[key] = value

            for key in ("invalidatedRefs", "invalidated_refs"):
                refs = _text_list(gate_payload.get(key))
                if refs:
                    compact[key] = refs
                    break
            for key in ("blockingEvidenceRefs", "blocking_evidence_refs"):
                refs = _text_list(gate_payload.get(key))
                if refs:
                    compact[key] = refs
                    break

            validated_refs = _text_mapping(
                gate_payload.get("validatedRefs")
                or gate_payload.get("validated_refs")
            )
            if validated_refs:
                compact["validatedRefs"] = validated_refs

            compact_violations = _text_list(
                list(contract_violations),
                max_items=10,
                max_chars=700,
            )
            if compact_violations:
                compact["contractViolations"] = compact_violations

            return compact

        def _canonicalize_moonspec_verify_gate_payload(
            gate_payload: Mapping[str, Any],
        ) -> dict[str, Any]:
            """Derive MoonSpec gate action from verdict, preserving model output."""

            canonical_payload = dict(gate_payload)
            recoverable_raw = (
                canonical_payload.get("recoverableInCurrentRuntime")
                if "recoverableInCurrentRuntime" in canonical_payload
                else canonical_payload.get("recoverable_in_current_runtime")
            )
            try:
                recoverable = _coerce_bool(recoverable_raw, default=False)
            except ValueError:
                recoverable = False
            canonical_action = recommended_next_action_for_verdict(
                canonical_payload.get("verdict"),
                recoverable_in_current_runtime=recoverable,
            )
            if not canonical_action:
                return canonical_payload
            raw_action = canonical_payload.get("recommendedNextAction")
            if raw_action is None:
                raw_action = canonical_payload.get("recommended_next_action")
            raw_action_text = (
                raw_action.strip() if isinstance(raw_action, str) else None
            )
            if raw_action is not None and raw_action_text != canonical_action:
                canonical_payload.setdefault(
                    "rawRecommendedNextAction",
                    raw_action if isinstance(raw_action, str) else str(raw_action),
                )
            canonical_payload["recommendedNextAction"] = canonical_action
            canonical_payload.pop("recommended_next_action", None)
            return canonical_payload

        def _remediation_cadence() -> Mapping[str, Any]:
            value = moonmind_metadata.get("remediationCadence")
            return value if isinstance(value, Mapping) else {}

        def _positive_int(value: Any) -> int | None:
            if isinstance(value, bool):
                return None
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 1 else None

        def _cadence_role() -> str:
            return str(_remediation_cadence().get("role") or "").strip().lower()

        def _cadence_attempt() -> int | None:
            return _positive_int(_remediation_cadence().get("attempt"))

        def _cadence_max_attempts() -> int | None:
            return _positive_int(_remediation_cadence().get("maxAttempts"))

        def _workspace_json_path(raw_path: str) -> Path | None:
            agent_run_id = _metadata_text("agentRunId", "agent_run_id")
            if not agent_run_id or self._run_store is None:
                return None
            record = self._run_store.load(agent_run_id)
            if record is None:
                logger.warning(
                    "Skipping remediation artifact publication: run record not "
                    "found for %s",
                    agent_run_id,
                )
                return None
            workspace_path = str(getattr(record, "workspace_path", "") or "").strip()
            if not workspace_path:
                return None
            workspace = Path(workspace_path).expanduser().resolve()
            candidate = Path(raw_path)
            candidates: list[Path]
            if candidate.is_absolute():
                candidates = [candidate]
            else:
                candidates = [workspace / candidate]
                if candidate.parts and candidate.parts[0] == "artifacts":
                    candidates.append(workspace.parent / candidate)
            job_artifact_root = (workspace.parent / "artifacts").resolve()
            for path in candidates:
                resolved = path.expanduser().resolve()
                if not (
                    resolved.is_relative_to(workspace)
                    or resolved.is_relative_to(job_artifact_root)
                ):
                    continue
                if resolved.exists():
                    return resolved
            return None

        def _read_workspace_json(raw_path: str) -> dict[str, Any] | None:
            path = _workspace_json_path(raw_path)
            if path is None:
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning(
                    "Remediation artifact could not be read as JSON: %s",
                    raw_path,
                    exc_info=True,
                )
                return None
            return dict(payload) if isinstance(payload, Mapping) else None

        async def _publish_moonspec_remediation_attempt_artifact() -> dict[str, Any]:
            if _cadence_role() != "moonspec-remediation":
                return {}
            attempt = _cadence_attempt()
            if attempt is None:
                return {}
            cadence = _remediation_cadence()
            max_attempts = _cadence_max_attempts()
            name = str(
                cadence.get("attemptArtifactPath")
                or f"reports/remediation_attempt-{attempt}.json"
            ).strip()
            payload = _read_workspace_json(name) or {}
            payload.setdefault("schemaVersion", "v1")
            payload.setdefault("artifactType", "remediation.attempt")
            payload["attempt"] = attempt
            if max_attempts is not None:
                payload["maxAttempts"] = max_attempts
            input_ref = payload.get("inputVerificationRef")
            if not isinstance(input_ref, Mapping):
                latest_path = str(cadence.get("latestVerificationPath") or "").strip()
                payload["inputVerificationRef"] = (
                    {"artifact_path": latest_path} if latest_path else {}
                )
            for key in ("knownGaps", "changedFiles", "targetedChecks"):
                if not isinstance(payload.get(key), list):
                    payload[key] = []
            payload.setdefault("nextVerificationRequired", True)
            ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=payload,
                execution_ref=_execution_ref("remediation.attempt"),
                metadata_json={
                    "name": name,
                    "artifact_type": "remediation.attempt",
                    "schemaVersion": "v1",
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": [
                        "agent_runtime",
                        "remediation.attempt",
                        "moonspec_remediation",
                    ],
                    "moonSpecRemediationAttempt": attempt,
                    **(
                        {"moonSpecRemediationMaxAttempts": max_attempts}
                        if max_attempts is not None
                        else {}
                    ),
                    **step_artifact_metadata,
                },
            )
            return {
                "remediationAttemptArtifactRef": ref.artifact_id,
                "remediationAttempt": {
                    "artifactRef": ref.artifact_id,
                    "attempt": attempt,
                    "maxAttempts": max_attempts,
                    "name": name,
                },
            }

        async def _publish_moonspec_remediation_verification_artifact(
            gate_payload: Mapping[str, Any],
            *,
            source_verify_ref: str,
        ) -> ArtifactRef | None:
            if _cadence_role() != "moonspec-verification-gate":
                return None
            attempt = _cadence_attempt()
            if attempt is None:
                return None
            cadence = _remediation_cadence()
            max_attempts = _cadence_max_attempts()
            name = str(
                cadence.get("verificationArtifactPath")
                or f"reports/remediation_verification-{attempt}.json"
            ).strip()
            attempt_name = str(
                cadence.get("attemptArtifactPath")
                or f"reports/remediation_attempt-{attempt}.json"
            ).strip()
            remaining_gaps = gate_payload.get("remainingGaps")
            if not isinstance(remaining_gaps, list):
                remaining_gaps = gate_payload.get("remainingWork")
            verdict = _first_non_empty_text(
                gate_payload,
                "verdict",
                "gateVerdict",
                "gate_verdict",
                "moonSpecVerdict",
                "moonspecVerdict",
                "verificationVerdict",
                "verification_verdict",
            )
            payload = {
                "schemaVersion": "v1",
                "artifactType": "remediation.verification",
                "verifiesAttempt": attempt,
                "inputRemediationAttemptRef": {
                    "artifact_type": "remediation.attempt",
                    "name": attempt_name,
                },
                "verdict": verdict,
                "remainingGaps": remaining_gaps if isinstance(remaining_gaps, list) else [],
                "verifierEvidenceRefs": {
                    "moonSpecVerifyArtifactRef": source_verify_ref,
                    "gateResultRef": source_verify_ref,
                },
                "moonSpecVerify": dict(gate_payload),
            }
            if max_attempts is not None:
                payload["maxAttempts"] = max_attempts
            ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=payload,
                execution_ref=_execution_ref("remediation.verification"),
                metadata_json={
                    "name": name,
                    "artifact_type": "remediation.verification",
                    "schemaVersion": "v1",
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": [
                        "agent_runtime",
                        "remediation.verification",
                        "moonspec_verification",
                    ],
                    "verifiesAttempt": attempt,
                    "inputRemediationAttemptName": attempt_name,
                    **step_artifact_metadata,
                },
            )
            return ref

        async def _publish_moonspec_verify_artifact() -> dict[str, Any]:
            verify_path = _metadata_text(
                "verify_artifact_path",
                "verifyArtifactPath",
                "verification_artifact_path",
                "verificationArtifactPath",
            )
            if not verify_path:
                return {}
            agent_run_id = _metadata_text("agentRunId", "agent_run_id")
            if not agent_run_id or self._run_store is None:
                return {}
            record = self._run_store.load(agent_run_id)
            if record is None:
                logger.warning(
                    "Skipping MoonSpec verify artifact publication: run record not "
                    "found for %s",
                    agent_run_id,
                )
                return {}
            workspace_path = str(getattr(record, "workspace_path", "") or "").strip()
            if not workspace_path:
                return {}
            workspace = Path(workspace_path).expanduser().resolve()
            path = _workspace_moonspec_verify_path(workspace, verify_path)
            if path is None:
                return {}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning(
                    "MoonSpec verify artifact could not be read as JSON: %s",
                    verify_path,
                    exc_info=True,
                )
                return {}
            if not isinstance(payload, Mapping):
                logger.warning(
                    "MoonSpec verify artifact payload must be a JSON object: %s",
                    verify_path,
                )
                return {}
            gate_payload = _canonicalize_moonspec_verify_gate_payload(payload)
            contract_violations = step_gate_contract_violations(gate_payload)
            if contract_violations:
                # Surface violations at the boundary where the verifier JSON
                # enters MoonMind so the workflow gate can request a bounded
                # corrective re-verify instead of failing the run on a
                # malformed-but-possibly-approving payload.
                gate_payload["contractViolations"] = list(contract_violations)
                logger.warning(
                    "MoonSpec verify artifact violates the gate contract "
                    "(%s): %s",
                    verify_path,
                    "; ".join(contract_violations),
                )
            verify_ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=gate_payload,
                execution_ref=_execution_ref("output.moonspec_verify"),
                metadata_json={
                    "name": "moonspec-verify-result.json",
                    "path": verify_path,
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": [
                        "agent_runtime",
                        "output.moonspec_verify",
                        "moonspec_verify",
                    ],
                    **step_artifact_metadata,
                },
            )
            gate_payload["gateResultRef"] = verify_ref.artifact_id
            authoritative_ref = verify_ref.artifact_id
            remediation_verify_ref = (
                await _publish_moonspec_remediation_verification_artifact(
                    gate_payload,
                    source_verify_ref=verify_ref.artifact_id,
                )
            )
            remediation_verify_artifact_ref = None
            if remediation_verify_ref is not None:
                remediation_verify_artifact_ref = remediation_verify_ref.artifact_id
                gate_payload["remediationVerificationRef"] = (
                    remediation_verify_artifact_ref
                )
                authoritative_ref = remediation_verify_artifact_ref
            compact_gate_payload = _compact_moonspec_verify_metadata(
                gate_payload,
                gate_result_ref=authoritative_ref,
                contract_violations=contract_violations,
            )
            result_refs = {
                "moonSpecVerify": compact_gate_payload,
                "gateResultRef": authoritative_ref,
                "moonSpecVerifyArtifactRef": authoritative_ref,
                "sourceMoonSpecVerifyArtifactRef": verify_ref.artifact_id,
            }
            if remediation_verify_artifact_ref:
                result_refs["remediationVerificationArtifactRef"] = (
                    remediation_verify_artifact_ref
                )
            return result_refs

        async def _publish_assessment_verdict_artifact() -> dict[str, Any]:
            assessment_path = _metadata_text(
                "assessment_artifact_path",
                "assessmentArtifactPath",
            )
            if not assessment_path:
                return {}
            agent_run_id = _metadata_text("agentRunId", "agent_run_id")
            if not agent_run_id or self._run_store is None:
                return {}
            record = self._run_store.load(agent_run_id)
            if record is None:
                logger.warning(
                    "Skipping assessment verdict artifact publication: run record "
                    "not found for %s",
                    agent_run_id,
                )
                return {}
            workspace_path = str(getattr(record, "workspace_path", "") or "").strip()
            if not workspace_path:
                return {}
            workspace = Path(workspace_path).expanduser().resolve()
            candidates = _workspace_moonspec_verify_path_candidates(
                workspace,
                assessment_path,
            )
            path = next(
                (candidate for candidate in candidates if candidate.is_file()),
                None,
            )
            if path is None:
                logger.warning(
                    "Assessment verdict artifact file was not found for "
                    "publication: %s",
                    assessment_path,
                )
                return {}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning(
                    "Assessment verdict artifact could not be read as JSON: %s",
                    assessment_path,
                    exc_info=True,
                )
                return {}
            if not isinstance(payload, Mapping):
                logger.warning(
                    "Assessment verdict artifact payload must be a JSON object: %s",
                    assessment_path,
                )
                return {}
            verdict_ref = await _write_json_artifact(
                self._artifact_service,
                principal="system:agent_runtime",
                payload=dict(payload),
                execution_ref=_execution_ref("output.assessment_verdict"),
                metadata_json={
                    "name": "assessment-verdict.json",
                    "path": assessment_path,
                    "producer": "activity:agent_runtime.publish_artifacts",
                    "labels": [
                        "agent_runtime",
                        "output.assessment_verdict",
                        "assessment",
                    ],
                    **step_artifact_metadata,
                },
            )
            published: dict[str, Any] = {
                "assessmentArtifactRef": verdict_ref.artifact_id,
            }
            # Compact structured verdict rides previousOutputs as a fast path for
            # adjacent steps; the ref is the durable, bridge-compatible channel.
            verdict = str(payload.get("verdict") or "").strip().upper()
            if verdict in {
                "FULLY_IMPLEMENTED",
                "PARTIALLY_IMPLEMENTED",
                "NOT_IMPLEMENTED",
                "BLOCKED",
            }:
                published["assessmentVerdict"] = verdict
            return published

        result_summary = result_dict.get("summary") or result_dict.get("raw", "")
        operator_summary = self._sanitize_operator_summary(
            _metadata_text("operator_summary", "operatorSummary")
        )
        effective_summary = (
            operator_summary
            if operator_summary
            and not is_generic_completion_summary(operator_summary)
            and is_generic_completion_summary(result_summary)
            else result_summary
        )

        # Build summary payload for the artifact
        summary_payload: dict[str, Any] = {
            "summary": effective_summary,
            "output_refs": result_dict.get("output_refs") or result_dict.get("outputRefs") or [],
            "failure_class": result_dict.get("failure_class") or result_dict.get("failureClass"),
            "provider_error_code": result_dict.get("provider_error_code") or result_dict.get("providerErrorCode"),
            "metrics": result_dict.get("metrics") or {},
        }
        report_output = (
            moonmind_metadata.get("reportOutput")
            if isinstance(moonmind_metadata.get("reportOutput"), Mapping)
            else {}
        )
        try:
            report_output_enabled = _coerce_bool(
                report_output.get("enabled"), default=False
            )
            report_output_required = _coerce_bool(
                report_output.get("required"), default=True
            )
        except ValueError as exc:
            logger.warning("Invalid reportOutput contract: %s", exc)
            report_output_enabled = False
            report_output_required = True

        def _report_execution_identity() -> tuple[str, str, str]:
            execution_ref = (
                report_output.get("executionRef")
                if isinstance(report_output.get("executionRef"), Mapping)
                else {}
            )
            if info is not None:
                namespace = str(execution_ref.get("namespace") or info.namespace)
                workflow_id = str(
                    execution_ref.get("workflow_id")
                    or execution_ref.get("workflowId")
                    or info.workflow_id
                )
                run_id = str(
                    execution_ref.get("run_id")
                    or execution_ref.get("runId")
                    or info.workflow_run_id
                )
                return namespace, workflow_id, run_id
            return (
                str(execution_ref.get("namespace") or "default"),
                str(
                    execution_ref.get("workflow_id")
                    or execution_ref.get("workflowId")
                    or ""
                ),
                str(execution_ref.get("run_id") or execution_ref.get("runId") or ""),
            )

        def _report_body() -> str:
            assistant_text = ""
            for key in ("assistantText", "lastAssistantText"):
                assistant_text = _metadata_text(key)
                if assistant_text:
                    break
            summary_text = str(summary_payload.get("summary") or "").strip()
            body = assistant_text or operator_summary or summary_text
            if not body:
                body = "Agent run completed without a textual report body."
            if body.lstrip().startswith("#"):
                return body.rstrip() + "\n"
            title = str(report_output.get("title") or "Final report").strip()
            return f"# {title}\n\n{body.rstrip()}\n"

        try:
            published_refs: dict[str, Any] = {}
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
            story_breakdown_refs = await _publish_story_breakdown_handoff()
            if story_breakdown_refs:
                published_refs.update(story_breakdown_refs)
                enriched_metadata_for_result = (
                    dict(result_dict.get("metadata") or {})
                    if isinstance(result_dict.get("metadata"), Mapping)
                    else {}
                )
                story_output_ref_payload = story_breakdown_refs.get("storyOutput")
                enriched_metadata_for_result.update(
                    {
                        key: value
                        for key, value in story_breakdown_refs.items()
                        if key != "storyOutput"
                    }
                )
                if isinstance(story_output_ref_payload, Mapping):
                    enriched_metadata_for_result["storyOutput"] = dict(
                        story_output_ref_payload
                    )
                result_dict["metadata"] = enriched_metadata_for_result
            remediation_attempt_refs = (
                await _publish_moonspec_remediation_attempt_artifact()
            )
            if remediation_attempt_refs:
                published_refs.update(remediation_attempt_refs)
                enriched_metadata_for_result = (
                    dict(result_dict.get("metadata") or {})
                    if isinstance(result_dict.get("metadata"), Mapping)
                    else {}
                )
                enriched_metadata_for_result.update(remediation_attempt_refs)
                result_dict["metadata"] = enriched_metadata_for_result
            moonspec_verify_refs = await _publish_moonspec_verify_artifact()
            if moonspec_verify_refs:
                published_refs.update(moonspec_verify_refs)
                enriched_metadata_for_result = (
                    dict(result_dict.get("metadata") or {})
                    if isinstance(result_dict.get("metadata"), Mapping)
                    else {}
                )
                enriched_metadata_for_result.update(moonspec_verify_refs)
                result_dict["metadata"] = enriched_metadata_for_result
            assessment_verdict_refs = await _publish_assessment_verdict_artifact()
            if assessment_verdict_refs:
                published_refs.update(assessment_verdict_refs)
                enriched_metadata_for_result = (
                    dict(result_dict.get("metadata") or {})
                    if isinstance(result_dict.get("metadata"), Mapping)
                    else {}
                )
                enriched_metadata_for_result.update(assessment_verdict_refs)
                result_dict["metadata"] = enriched_metadata_for_result
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
            if report_output_enabled:
                namespace, workflow_id, run_id = _report_execution_identity()
                if not workflow_id or not run_id:
                    raise TemporalActivityRuntimeError(
                        "reportOutput enabled but executionRef is incomplete"
                    )
                primary_report_name = report_output_display_name(
                    report_output.get("primaryPath")
                    or report_output.get("primary_path")
                )
                report_type = str(
                    report_output.get("reportType")
                    or report_output.get("report_type")
                    or "agent_run_report"
                ).strip() or "agent_run_report"
                report_bundle = await self._artifact_service.publish_report_bundle(
                    principal="system:agent_runtime",
                    namespace=namespace,
                    workflow_id=workflow_id,
                    run_id=run_id,
                    report_type=report_type,
                    report_scope="final",
                    primary={
                        "payload": _report_body(),
                        "content_type": "text/markdown",
                        "label": "Final report",
                        "metadata": {
                            "artifact_type": report_type,
                            "title": str(
                                report_output.get("title") or "Final report"
                            ).strip()
                            or "Final report",
                            "description": str(
                                report_output.get("description") or ""
                            ).strip()
                            or "Agent-authored final report",
                            "producer": "activity:agent_runtime.publish_artifacts",
                            "render_hint": "text",
                            "name": primary_report_name,
                            **step_artifact_metadata,
                        },
                    },
                    summary={
                        "payload": str(summary_payload.get("summary") or "").strip()
                        or "Report generated.",
                        "content_type": "text/plain",
                        "label": "Report summary",
                        "metadata": {
                            "artifact_type": report_type,
                            "title": "Report summary",
                            "producer": "activity:agent_runtime.publish_artifacts",
                            "render_hint": "text",
                            "name": "report-summary.txt",
                            **step_artifact_metadata,
                        },
                    },
                    structured={
                        "payload": {
                            "summary": summary_payload.get("summary") or "",
                            "output_refs": summary_payload.get("output_refs") or [],
                            "diagnostics_ref": agent_result_ref.artifact_id,
                            "failure_class": summary_payload.get("failure_class"),
                            "provider_error_code": summary_payload.get(
                                "provider_error_code"
                            ),
                        },
                        "content_type": "application/json",
                        "label": "Report structured output",
                        "metadata": {
                            "artifact_type": report_type,
                            "title": "Structured report data",
                            "producer": "activity:agent_runtime.publish_artifacts",
                            "render_hint": "json",
                            "name": "report-structured.json",
                            **step_artifact_metadata,
                        },
                    },
                    step_id=step_artifact_metadata.get("step_id"),
                    attempt=step_artifact_metadata.get("attempt"),
                    scope=step_artifact_metadata.get("scope") or "final",
                )
                published_refs["reportBundle"] = report_bundle
                primary_report_ref = report_bundle.get("primary_report_ref")
                if isinstance(primary_report_ref, Mapping):
                    primary_report_id = str(
                        primary_report_ref.get("artifact_id")
                        or primary_report_ref.get("artifactId")
                        or ""
                    ).strip()
                    if primary_report_id:
                        published_refs["primaryReportRef"] = primary_report_id
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
                await _notify_terminal_result(enriched)
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
            await _notify_terminal_result(result)
            return result
        except Exception as exc:
            logger.warning(
                "agent_runtime.publish_artifacts failed to publish managed-session artifacts",
                exc_info=True,
            )
            if report_output_enabled and report_output_required:
                raise
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
        github_credential = request.github_credential
        if github_credential is None:
            repository = str(
                request.workspace_spec.get("repository")
                or request.workspace_spec.get("repo")
                or ""
            ).strip()
            github_credential = build_github_credential_descriptor_for_launch(
                environment,
                ambient_github_token=os.environ.get("GITHUB_TOKEN"),
                enable_managed_secret_fallback=bool(repository),
            )

        if not str(environment.get("GITHUB_TOKEN", "")).strip():
            environment.pop("GITHUB_TOKEN", None)
        environment.pop("GIT_TERMINAL_PROMPT", None)
        return request.model_copy(
            update={
                "environment": environment,
                "github_credential": github_credential,
            }
        )

    @staticmethod
    def _managed_session_auth_diagnostics(
        *,
        request: LaunchCodexManagedSessionRequest,
        profile: ManagedRuntimeProfile | None,
        readiness: str,
        validation_failure_reason: str | None = None,
    ) -> dict[str, str]:
        diagnostics: dict[str, str] = {
            "component": "managed_session_controller",
            "readiness": readiness,
            "codexHomePath": request.codex_home_path,
        }
        if profile is not None:
            optional_profile_fields = {
                "profileRef": profile.profile_id,
                "runtimeId": profile.runtime_id,
                "providerId": profile.provider_id,
                "credentialSource": profile.credential_source,
                "runtimeMaterializationMode": profile.runtime_materialization_mode,
                "volumeRef": profile.volume_ref,
            }
            for key, value in optional_profile_fields.items():
                if value is not None and str(value).strip():
                    diagnostics[key] = str(value).strip()
        auth_mount_target = str(
            request.environment.get("MANAGED_AUTH_VOLUME_PATH") or ""
        ).strip()
        if not auth_mount_target and profile is not None and profile.volume_mount_path:
            auth_mount_target = str(profile.volume_mount_path).strip()
        if auth_mount_target:
            diagnostics["authMountTarget"] = auth_mount_target
        if request.github_credential is not None:
            diagnostics["githubCredentialSource"] = request.github_credential.source
            diagnostics["githubCredentialMaterialization"] = "required"
        if validation_failure_reason:
            diagnostics["validationFailureReason"] = validation_failure_reason
        return diagnostics

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
            response = await _await_with_activity_heartbeats(
                controller.launch_session(validated),
                heartbeat_payload={
                    "activityType": "agent_runtime.launch_session",
                    "agentRunId": validated.agent_run_id,
                    "runtimeFamily": validated.runtime_family,
                    "sessionId": validated.session_id,
                    "threadId": validated.thread_id,
                },
            )
        except Exception as exc:
            from moonmind.utils.logging import redact_sensitive_text

            detail = (
                self._sanitize_operator_summary(redact_sensitive_text(str(exc)))
                or "managed session launch failed"
            )
            raise TemporalActivityRuntimeError(
                "agent_runtime.launch_session failed: "
                f"component=managed_session_controller reason={detail}"
            ) from exc
        response = self._validate_session_response(
            response,
            activity_type="agent_runtime.launch_session",
            model_type=CodexManagedSessionHandle,
        )
        response = response.model_copy(
            update={
                "metadata": {
                    **response.metadata,
                    "authDiagnostics": self._managed_session_auth_diagnostics(
                        request=validated,
                        profile=profile,
                        readiness=(
                            response.status
                            if response.status != "failed"
                            else "failed"
                        ),
                    ),
                }
            }
        )
        return response

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
    ) -> Any:
        request_raw = payload.get("request")
        if not isinstance(request_raw, Mapping):
            raise TemporalActivityRuntimeError(
                "payload.request is required for agent_runtime.prepare_turn_instructions"
            )
        request = AgentExecutionRequest.model_validate(dict(request_raw))
        workspace_path_raw = str(
            payload.get("workspace_path") or payload.get("workspacePath") or ""
        ).strip()
        skip_skill_materialization = bool(
            payload.get("skipSkillMaterialization")
            or payload.get("skip_skill_materialization")
        )
        skill_materialization_metadata: Mapping[str, Any] | None = None
        if not skip_skill_materialization:
            skill_materialization_metadata = await self._materialize_selected_agent_skill_for_turn(
                request=request,
                workspace_path=workspace_path_raw,
            )

        def _prepared_request_metadata() -> dict[str, Any]:
            prepared_payload: dict[str, Any] = {
                "durableRetrievalMetadata": extract_durable_retrieval_metadata(
                    request.parameters
                )
            }
            if skill_materialization_metadata:
                prepared_payload["activeSkillsDir"] = str(
                    skill_materialization_metadata.get("visiblePath") or ""
                )
            if request.terminal_contract is not None:
                prepared_payload["terminalContract"] = (
                    request.terminal_contract.model_dump(
                        by_alias=True, exclude_none=True
                    )
                )
            return prepared_payload

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
            if self._session_controller is not None:
                await self._session_controller.ensure_repo_artifacts_writable_by_runtime_user(
                    workspace_path_raw
                )
            instruction_ref = str(request.instruction_ref or "").strip()
            if payload.get("metadataOnly") or payload.get("metadata_only"):
                return _prepared_request_metadata()
            if instruction_ref:
                prepared = self._prepare_managed_codex_turn_text(
                    instruction_ref,
                    parameters=request.parameters,
                    skill_materialization_metadata=skill_materialization_metadata,
                )
                if payload.get("includePreparedRequestMetadata"):
                    prepared_payload = {
                        "instructions": prepared,
                        "durableRetrievalMetadata": extract_durable_retrieval_metadata(
                            request.parameters
                        ),
                    }
                    if skill_materialization_metadata:
                        prepared_payload["activeSkillsDir"] = str(
                            skill_materialization_metadata.get("visiblePath") or ""
                        )
                    if request.terminal_contract is not None:
                        prepared_payload["terminalContract"] = (
                            request.terminal_contract.model_dump(
                                by_alias=True, exclude_none=True
                            )
                        )
                    return prepared_payload
                return prepared
        if payload.get("metadataOnly") or payload.get("metadata_only"):
            return _prepared_request_metadata()
        parameters = request.parameters if isinstance(request.parameters, dict) else {}
        instructions = str(parameters.get("instructions") or "").strip()
        if instructions:
            prepared = self._prepare_managed_codex_turn_text(
                instructions,
                parameters=parameters,
                skill_materialization_metadata=skill_materialization_metadata,
            )
            if payload.get("includePreparedRequestMetadata"):
                prepared_payload = {
                    "instructions": prepared,
                    "durableRetrievalMetadata": extract_durable_retrieval_metadata(
                        request.parameters
                    ),
                }
                if skill_materialization_metadata:
                    prepared_payload["activeSkillsDir"] = str(
                        skill_materialization_metadata.get("visiblePath") or ""
                    )
                if request.terminal_contract is not None:
                    prepared_payload["terminalContract"] = (
                        request.terminal_contract.model_dump(
                            by_alias=True, exclude_none=True
                        )
                    )
                return prepared_payload
            return prepared
        raise TemporalActivityRuntimeError(
            "request.instructionRef or request.parameters.instructions is required"
        )

    async def _materialize_selected_agent_skill_for_turn(
        self,
        *,
        request: AgentExecutionRequest,
        workspace_path: str,
    ) -> Mapping[str, Any] | None:
        """Materialize the resolved active skill snapshot for a managed-session turn."""

        params = request.parameters if isinstance(request.parameters, Mapping) else {}
        selected_skill = selected_agent_skill(params)
        if (
            not selected_skill
            or selected_skill == _AUTO_SKILL_SENTINEL
            or not workspace_path
        ):
            return None

        workspace = Path(workspace_path).expanduser().resolve()
        run_root = self._managed_session_run_root_for_workspace(workspace)
        if run_root is None:
            return None

        skillset_ref = str(request.resolved_skillset_ref or "").strip()
        if not skillset_ref:
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"selected skill '{selected_skill}' requires request.resolvedSkillsetRef"
            )

        try:
            resolved_skillset = await self._load_resolved_skillset(skillset_ref)
            resolved_names = {entry.skill_name for entry in resolved_skillset.skills}
            if selected_skill not in resolved_names:
                raise TemporalActivityRuntimeError(
                    "selected skill materialization failed before runtime launch: "
                    f"selected skill '{selected_skill}' is not present in resolvedSkillsetRef {skillset_ref}"
                )
            self._validate_resolved_skillset_source_policy(resolved_skillset)
            skills_backing_root = (
                run_root
                / "runtime"
                / "skills_active"
                / resolved_skillset.snapshot_id
            )
            skill_source_preservation_root = (
                run_root / "runtime" / "skill_sources" / "repo_agents_skills"
            )
            project_adapter_aliases = not self._requires_projectionless_skill_delivery(
                selected_skill
            )
            if not project_adapter_aliases:
                active_root = run_root / "runtime" / "skills_active"
                cleanup_moonmind_skill_projections(
                    run_root=workspace,
                    skills_active_path=active_root,
                    owned_roots=(active_root,),
                )
            materializer = AgentSkillMaterializer(
                workspace_root=str(workspace),
                artifact_service=self._artifact_service,
                backing_root=str(skills_backing_root),
                source_preservation_root=str(skill_source_preservation_root),
                projection_owner_uid=_MANAGED_AGENT_UID,
                projection_owner_gid=_MANAGED_AGENT_GID,
                project_adapter_aliases=project_adapter_aliases,
            )
            materialization = await materializer.materialize(
                resolved_skillset=resolved_skillset,
                runtime_id=str(request.agent_id or "managed-runtime"),
                mode=RuntimeMaterializationMode.HYBRID,
            )
            if materialization is None:
                raise TemporalActivityRuntimeError(
                    "selected skill materialization failed before runtime launch: "
                    "active skills visiblePath metadata is missing"
                )
            metadata = materialization.metadata
            visible_path_raw = str(metadata.get("visiblePath") or "").strip()
            if not visible_path_raw:
                raise TemporalActivityRuntimeError(
                    "selected skill materialization failed before runtime launch: "
                    "active skills visiblePath metadata is missing"
                )
            self._validate_selected_skill_projection(
                visible_path=Path(visible_path_raw),
                selected_skill=selected_skill,
                resolved_skillset=resolved_skillset,
            )
            selected_entry = next(
                entry for entry in resolved_skillset.skills
                if entry.skill_name == selected_skill
            )
            if selected_entry.terminal_contract is not None:
                from moonmind.schemas.agent_runtime_models import AgentTerminalContract

                execution_ref = (
                    request.step_execution.step_execution_id
                    if request.step_execution is not None
                    else ""
                )
                request.terminal_contract = AgentTerminalContract.model_validate(
                    {
                        **selected_entry.terminal_contract.model_dump(),
                        "executionRef": execution_ref,
                    }
                )
        except TemporalActivityRuntimeError:
            raise
        except (RuntimeError, OSError, ValueError, ValidationError) as exc:
            raise TemporalActivityRuntimeError(
                f"selected skill materialization failed for '{selected_skill}': {exc}"
            ) from exc

        return metadata

    @staticmethod
    def _requires_projectionless_skill_delivery(selected_skill: str) -> bool:
        return str(selected_skill or "").strip().lower() == "moonspec-verify"

    @staticmethod
    def _validate_selected_skill_projection(
        *,
        visible_path: Path,
        selected_skill: str,
        resolved_skillset: ResolvedSkillSet,
    ) -> None:
        """Fail before launch if the runtime-visible active skill projection is absent."""

        visible_skills_dir = visible_path.expanduser()
        if not visible_skills_dir.exists() or not visible_skills_dir.is_dir():
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"active skills visiblePath is missing at {visible_skills_dir}"
            )

        selected_skill_doc = visible_skills_dir / selected_skill / "SKILL.md"
        if not selected_skill_doc.exists() or not selected_skill_doc.is_file():
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"selected skill '{selected_skill}' is missing {selected_skill_doc}"
            )

        manifest_path = visible_skills_dir / "_manifest.json"
        if not manifest_path.exists() or not manifest_path.is_file():
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"active skill manifest is missing at {manifest_path}"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, Mapping):
                raise ValueError(f"manifest at {manifest_path} is not a mapping")
        except (OSError, TypeError, ValueError) as exc:
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"active skill manifest is unreadable at {manifest_path}: {exc}"
            ) from exc

        snapshot_id = str(manifest.get("snapshot_id") or "").strip()
        if snapshot_id != resolved_skillset.snapshot_id:
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                "active skill manifest snapshot_id does not match resolvedSkillsetRef "
                f"({snapshot_id!r} != {resolved_skillset.snapshot_id!r})"
            )
        manifest_skills = {
            str(entry.get("name") or "").strip()
            for entry in manifest.get("skills", [])
            if isinstance(entry, Mapping)
        }
        if selected_skill not in manifest_skills:
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"active skill manifest does not include selected skill '{selected_skill}'"
            )

        selected_entry = next(
            (
                entry
                for entry in resolved_skillset.skills
                if entry.skill_name == selected_skill
            ),
            None,
        )
        if selected_entry is None:
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                f"resolvedSkillsetRef does not include selected skill '{selected_skill}'"
            )
        TemporalAgentRuntimeActivities._validate_resolved_skillset_source_policy(
            resolved_skillset
        )

    @staticmethod
    def _validate_resolved_skillset_source_policy(
        resolved_skillset: ResolvedSkillSet,
    ) -> None:
        policy_summary = resolved_skillset.policy_summary or {}
        for entry in resolved_skillset.skills:
            source_kind = entry.provenance.source_kind
            if source_kind == AgentSkillSourceKind.REPO and (
                policy_summary.get("repo_skills_allowed") is not True
            ):
                raise TemporalActivityRuntimeError(
                    "selected skill materialization failed before runtime launch: "
                    f"repo skill source for '{entry.skill_name}' is disabled by skill source policy"
                )
            if source_kind == AgentSkillSourceKind.LOCAL and (
                policy_summary.get("local_skills_allowed") is not True
            ):
                raise TemporalActivityRuntimeError(
                    "selected skill materialization failed before runtime launch: "
                    f"local skill source for '{entry.skill_name}' is disabled by skill source policy"
                )

    async def _load_resolved_skillset(self, skillset_ref: str) -> ResolvedSkillSet:
        if self._artifact_service is None:
            raise TemporalActivityRuntimeError(
                "selected skill materialization failed before runtime launch: "
                "artifact service is required to read request.resolvedSkillsetRef"
            )
        try:
            _artifact, payload = await self._artifact_service.read(
                artifact_id=skillset_ref,
                principal="agent_runtime",
                allow_restricted_raw=True,
            )
            data = json.loads(payload.decode("utf-8"))
            return ResolvedSkillSet.model_validate(data)
        except TemporalActivityRuntimeError:
            raise
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise TemporalActivityRuntimeError(
                f"failed to read resolvedSkillsetRef {skillset_ref}: {exc}"
            ) from exc

    @staticmethod
    def _managed_session_run_root_for_workspace(workspace: Path) -> Path | None:
        """Return a run root only for MoonMind-managed job workspaces."""

        store_root = Path(
            os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
        ).expanduser().resolve()
        try:
            relative = workspace.relative_to(store_root)
        except ValueError:
            return None
        if len(relative.parts) != 2 or relative.parts[1] != "repo":
            return None
        run_id = str(relative.parts[0]).strip()
        if not run_id:
            return None
        return store_root / run_id

    @classmethod
    def _prepare_managed_codex_turn_text(
        cls,
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
        skill_materialization_metadata: Mapping[str, Any] | None = None,
    ) -> str:
        prepared = cls._append_selected_jira_tool_hint(
            instructions,
            parameters=parameters,
        )
        prepared = cls._append_pr_resolver_initial_state_hint(
            prepared,
            parameters=parameters,
        )
        prepared = cls._prepend_selected_skill_activation(
            prepared,
            parameters=parameters,
            skill_materialization_metadata=skill_materialization_metadata,
        )
        prepared = cls._append_moonspec_verify_artifact_hint(
            prepared,
            parameters=parameters,
        )
        prepared = cls._append_skills_on_demand_notice(
            prepared,
            parameters=parameters,
        )
        prepared = cls._append_managed_step_boundary(prepared)
        return append_managed_codex_runtime_note(prepared)

    @staticmethod
    def _first_mapping(*values: Any) -> Mapping[str, Any]:
        for value in values:
            if isinstance(value, Mapping):
                return value
        return {}

    @staticmethod
    def _state_value(
        *mappings: Mapping[str, Any],
        keys: tuple[str, ...],
    ) -> Any:
        for mapping in mappings:
            for key in keys:
                value = mapping.get(key)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _compact_state_value(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip()
        if not text:
            return None
        return text[:200]

    @classmethod
    def _append_pr_resolver_initial_state_hint(
        cls,
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
    ) -> str:
        params = parameters if isinstance(parameters, Mapping) else {}
        if selected_agent_skill(params) != "pr-resolver":
            return instructions
        if "MoonMind PR resolver initial state:" in instructions:
            return instructions

        state = cls._first_mapping(
            params.get("initialPrState"),
            params.get("mergeGate"),
            params.get("prResolverInitialState"),
            params,
        )
        pr_state = cls._first_mapping(
            state.get("pr"),
            state.get("pullRequest"),
            params.get("pr"),
            params.get("pullRequest"),
        )
        ci_state = cls._first_mapping(
            state.get("ci"),
            state.get("checks"),
            params.get("ci"),
            params.get("checks"),
        )
        comments_state = cls._first_mapping(
            state.get("commentsSummary"),
            state.get("comments"),
            params.get("commentsSummary"),
            params.get("comments"),
        )

        pr_url = cls._state_value(
            pr_state,
            state,
            params,
            keys=("url", "htmlUrl", "pullRequestUrl", "prUrl"),
        )
        mergeable = cls._state_value(
            pr_state,
            state,
            keys=("mergeable", "mergeableState"),
        )
        merge_state = cls._state_value(
            pr_state,
            state,
            keys=("mergeStateStatus", "merge_state_status"),
        )
        ci_running = cls._state_value(ci_state, state, keys=("isRunning", "ciRunning"))
        ci_failures = cls._state_value(ci_state, state, keys=("hasFailures", "ciFailed"))
        ci_signal = cls._state_value(
            ci_state,
            state,
            keys=("signalQuality", "statusCheckRollupQuality"),
        )
        rollup_count = cls._state_value(
            ci_state,
            state,
            keys=("statusCheckRollupCount", "checkRunCount", "checksCount"),
        )
        comments_fetch = cls._state_value(
            comments_state,
            state,
            keys=("fetchSucceeded", "succeeded", "commentsFetchSucceeded"),
        )
        actionable_comments = cls._state_value(
            comments_state,
            state,
            keys=("hasActionableComments", "actionableComments"),
        )

        observed_values = (
            pr_url,
            mergeable,
            merge_state,
            ci_running,
            ci_failures,
            ci_signal,
            rollup_count,
            comments_fetch,
            actionable_comments,
        )
        if all(value is None for value in observed_values):
            return instructions

        blockers: list[str] = []
        mergeable_text = str(mergeable or "").strip().upper()
        merge_state_text = str(merge_state or "").strip().upper()
        if mergeable_text == "CONFLICTING" or merge_state_text in {"DIRTY", "BLOCKED"}:
            blockers.append("merge_conflicts")
        if ci_running is True:
            blockers.append("ci_running")
        if ci_failures is True:
            blockers.append("ci_failures")
        ci_signal_text = str(ci_signal or "").strip().lower()
        if ci_signal_text in {"missing", "unavailable", "none", "empty"}:
            blockers.append("ci_unavailable")
        if rollup_count == 0:
            blockers.append("ci_unavailable")
        if actionable_comments is True:
            blockers.append("actionable_comments")

        lines = [
            "MoonMind PR resolver initial state:",
            "- Source: deterministic pre-agent launch context.",
        ]
        compact_pr_url = cls._compact_state_value(pr_url)
        if compact_pr_url:
            lines.append(f"- PR: {compact_pr_url}")
        merge_parts = []
        for label, value in (
            ("mergeable", mergeable),
            ("mergeStateStatus", merge_state),
        ):
            compact = cls._compact_state_value(value)
            if compact is not None:
                merge_parts.append(f"{label}={compact}")
        if merge_parts:
            lines.append("- Merge gate: " + ", ".join(merge_parts))
        ci_parts = []
        for label, value in (
            ("isRunning", ci_running),
            ("hasFailures", ci_failures),
            ("signalQuality", ci_signal),
            ("statusCheckRollupCount", rollup_count),
        ):
            compact = cls._compact_state_value(value)
            if compact is not None:
                ci_parts.append(f"{label}={compact}")
        if ci_parts:
            lines.append("- CI: " + ", ".join(ci_parts))
        comment_parts = []
        for label, value in (
            ("fetchSucceeded", comments_fetch),
            ("hasActionableComments", actionable_comments),
        ):
            compact = cls._compact_state_value(value)
            if compact is not None:
                comment_parts.append(f"{label}={compact}")
        if comment_parts:
            lines.append("- Comments: " + ", ".join(comment_parts))
        lines.append(
            "- Initial blocker hint: "
            + (", ".join(dict.fromkeys(blockers)) if blockers else "none_detected")
        )
        lines.append(
            "- Report this blocker explicitly if it prevents CI-dependent resolver work."
        )
        return instructions.rstrip() + "\n\n" + "\n".join(lines)

    @staticmethod
    def _append_skills_on_demand_notice(
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
    ) -> str:
        selected_skill = selected_agent_skill(parameters)
        if not selected_skill or selected_skill == _AUTO_SKILL_SENTINEL:
            return instructions
        on_demand_instruction = skills_on_demand_runtime_instruction(
            enabled=settings.workflow.skills_on_demand_enabled
        )
        if not on_demand_instruction or on_demand_instruction in instructions:
            return instructions
        return instructions.rstrip() + "\n\n" + on_demand_instruction

    @staticmethod
    def _append_moonspec_verify_artifact_hint(
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
    ) -> str:
        params = parameters if isinstance(parameters, Mapping) else {}
        if selected_agent_skill(params) != "moonspec-verify":
            return instructions
        verify_artifact_path = ""
        for key in (
            "verify_artifact_path",
            "verifyArtifactPath",
            "verification_artifact_path",
            "verificationArtifactPath",
        ):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                verify_artifact_path = value.strip()
                break
        if not verify_artifact_path:
            return instructions
        verdict_values = " | ".join(f'"{value}"' for value in sorted(REVIEW_VERDICTS))
        next_action_values = " | ".join(
            f'"{value}"' for value in recommended_next_actions()
        )
        path_hint = (
            ""
            if verify_artifact_path in instructions
            else (
                "- Write the complete structured verifier JSON to "
                f"`{verify_artifact_path}`.\n"
            )
        )
        block = (
            "MoonSpec verification output contract:\n"
            f"{path_hint}"
            "- The JSON must include the canonical `verdict`, `recommendedNextAction`, "
            "`recoverableInCurrentRuntime`, and `remainingWork` fields.\n"
            f"- `verdict` must be exactly one of: {verdict_values}.\n"
            f"- `recommendedNextAction` must be exactly one of: {next_action_values}. "
            "Any other model-authored value is contract drift; MoonMind preserves "
            "it as a raw diagnostic and derives the canonical action from the "
            "verdict.\n"
            '- For `FULLY_IMPLEMENTED`, set `recommendedNextAction` to "advance"; '
            "do not encode pull request creation or any other workflow-specific "
            "destination in this field.\n"
            "- `recommendedNextAction` is advisory semantic metadata. The workflow "
            "runtime owns routing and selects the next logical plan node. Never "
            "encode a remediation node, publication node, or pull request destination.\n"
            "- For `ADDITIONAL_WORK_NEEDED`, include bounded concrete remaining "
            "work, recoverability, and evidence references. A read-only verifier "
            "must not ask its own rerun to perform implementation remediation.\n"
            '- Use "reattempt_current_step" only when rerunning this verifier can '
            "obtain different evidence, especially for a recoverable "
            "`NO_DETERMINATION`.\n"
            "- Treat integration, e2e, smoke, quickstart, map-entry, UI/browser, "
            "deployment, and external-service checks as advisory when they depend "
            "on unavailable non-repo assets, services, credentials, or tooling; "
            "missing map assets or environment-only failures must be reported as "
            "non-blocking limitations, not used as the sole reason for a blocking "
            "verdict.\n"
            "- Still return the Markdown MoonSpec Verification Report in the assistant response."
        )
        return instructions.rstrip() + "\n\n" + block

    @staticmethod
    def _append_managed_step_boundary(instructions: str) -> str:
        if "MoonMind managed step boundary:" in instructions:
            return instructions
        block = (
            "MoonMind managed step boundary:\n"
            "- Treat the text under `TASK INSTRUCTION`, or the inline instruction "
            "when no `TASK INSTRUCTION` header is present, as the only work "
            "authorized for this turn.\n"
            "- Execute only this current plan step. Do not perform later workflow "
            "steps such as specification, planning, task generation, "
            "implementation, verification, publishing, pull request creation, or "
            "Jira transitions unless this turn's instruction explicitly asks for "
            "them.\n"
            "- Repository `AGENTS.md` autonomy instructions apply only within this "
            "current step boundary.\n"
            "- Always finish with a brief assistant message describing this step's "
            "outcome. If the step is already satisfied and no action is needed, "
            "say that explicitly with the evidence.\n"
        )
        return instructions.rstrip() + "\n\n" + block

    @staticmethod
    def _prepend_selected_skill_activation(
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
        skill_materialization_metadata: Mapping[str, Any] | None = None,
    ) -> str:
        selected_skill = selected_agent_skill(parameters)
        if (
            not selected_skill
            or selected_skill == _AUTO_SKILL_SENTINEL
            or not skill_materialization_metadata
        ):
            return instructions
        if "Active MoonMind skill snapshot:" in instructions:
            return instructions
        visible_path = str(skill_materialization_metadata.get("visiblePath") or "").strip()
        skill_doc = f"{visible_path}/{selected_skill}/SKILL.md" if visible_path else ""
        alias_available = bool(
            skill_materialization_metadata.get("canonicalAliasAvailable")
        )
        block = (
            "Active MoonMind skill snapshot:\n"
            f"- Selected skill: {selected_skill}\n"
            f"- Full active MoonMind skill content is available at: {visible_path}\n"
            f"- Read `{skill_doc}` first and follow that active snapshot.\n"
            "- Do not discover skills from repo-local or local-only source folders during execution.\n\n"
        )
        on_demand_instruction = skills_on_demand_runtime_instruction(
            enabled=settings.workflow.skills_on_demand_enabled
        )
        if on_demand_instruction:
            block = block.rstrip() + f"\n{on_demand_instruction}\n\n"
        if not alias_available:
            block = block.rstrip() + (
                "\n- The repository also contains `.agents/skills`; that directory "
                "is repo-authored source and must not be modified or treated as "
                "the active selected skill snapshot.\n\n"
            )
        return block + instructions

    @staticmethod
    def _append_selected_jira_tool_hint(
        instructions: str,
        *,
        parameters: Mapping[str, Any] | None,
    ) -> str:
        return append_selected_jira_tool_hint(instructions, parameters=parameters)

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
        bridge_publication = validated.bridge_publication

        async def _publish_active_observations(
            observations: list[Any],
            turn_id: str,
            locator: CodexManagedSessionLocator,
        ) -> None:
            if not bridge_publication or not observations:
                return
            try:
                await self.agent_runtime_publish_bridge_events(
                    {
                        **bridge_publication,
                        "locator": locator.model_dump(mode="json", by_alias=True),
                        "turnId": turn_id,
                        "observations": observations,
                        "phase": "active",
                    }
                )
            except Exception:
                logger.warning(
                    "Active managed-session bridge publication failed; "
                    "continuing the already-started turn",
                    exc_info=True,
                )

        send_turn_call = (
            controller.send_turn(
                validated,
                observation_sink=_publish_active_observations,
            )
            if bridge_publication
            else controller.send_turn(validated)
        )
        response = await _await_with_activity_heartbeats(
            send_turn_call,
            heartbeat_payload={
                "activityType": "agent_runtime.send_turn",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
                "threadId": validated.thread_id,
            },
        )
        validated_response = self._validate_session_response(
            response,
            activity_type="agent_runtime.send_turn",
            model_type=CodexManagedSessionTurnResponse,
        )
        if validated_response.status == "failed":
            metadata = validated_response.metadata or {}
            reason = str(metadata.get("reason") or "").strip()
            failure_class = str(metadata.get("failureClass") or "").strip()
            if failure_class == "transient":
                raise _codex_transient_turn_failure(reason, metadata=metadata)
            raise _codex_permanent_turn_failure(reason, metadata=metadata)
        return validated_response

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

    async def agent_runtime_ensure_docker_sidecar(
        self,
        request: Mapping[str, Any]
        | ManagedSessionEnsureDockerSidecarRequest
        | None = None,
        /,
    ) -> ManagedSessionEnsureDockerSidecarResponse:
        controller = self._require_session_controller(
            activity_type="agent_runtime.ensure_docker_sidecar"
        )
        validated = self._validate_session_request(
            request,
            activity_type="agent_runtime.ensure_docker_sidecar",
            model_type=ManagedSessionEnsureDockerSidecarRequest,
        )
        response = await _await_with_activity_heartbeats(
            controller.ensure_docker_sidecar(validated),
            heartbeat_payload={
                "activityType": "agent_runtime.ensure_docker_sidecar",
                "sessionId": validated.session_id,
                "containerId": validated.container_id,
            },
        )
        return self._validate_session_response(
            response,
            activity_type="agent_runtime.ensure_docker_sidecar",
            model_type=ManagedSessionEnsureDockerSidecarResponse,
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

    async def agent_runtime_publish_bridge_events(
        self,
        payload: Mapping[str, Any] | None = None,
        /,
    ) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise TemporalActivityRuntimeError(
                "payload is required for agent_runtime.publish_bridge_events"
            )
        request_raw = payload.get("request")
        if not isinstance(request_raw, Mapping):
            raise TemporalActivityRuntimeError(
                "payload.request is required for agent_runtime.publish_bridge_events"
            )
        request = AgentExecutionRequest.model_validate(dict(request_raw))
        parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
        communication = parameters.get("communication")
        if not isinstance(communication, Mapping):
            return {"skipped": True, "reason": "communication_mode_absent"}
        mode = str(communication.get("mode") or "").strip()
        if mode != "omnigent_bridge":
            return {"skipped": True, "reason": "communication_mode_mismatch"}

        binding_raw = payload.get("binding")
        locator_raw = payload.get("locator")
        phase = str(payload.get("phase") or "terminal").strip()
        if phase not in {"started", "active", "terminal"}:
            raise TemporalActivityRuntimeError(
                "payload.phase must be 'started', 'active', or 'terminal'"
            )
        if not all(isinstance(value, Mapping) for value in (binding_raw, locator_raw)):
            raise TemporalActivityRuntimeError(
                "payload.binding and locator are required"
            )

        binding = CodexManagedSessionBinding.model_validate(dict(binding_raw))
        locator = CodexManagedSessionLocator.model_validate(dict(locator_raw))
        compatibility_profile = (
            str(
                payload.get("compatibilityProfile")
                or communication.get("compatibilityProfile")
                or "moonmind.codex_direct_compat.v1"
            ).strip()
            or "moonmind.codex_direct_compat.v1"
        )

        from api_service.db.base import get_async_session_context
        from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore

        store = OmnigentBridgeSessionStore(get_async_session_context)
        workspace = _string_or_none_for_activity(parameters.get("repository"))
        if workspace is None:
            workspace = _string_or_none_for_activity(parameters.get("workspace"))
        row = await store.get_or_create(
            request=request,
            endpoint_ref="direct-codex-compat",
            agent_id=request.agent_id,
            agent_name="Codex CLI",
            target_metadata={
                "hostType": "managed",
                "workspace": workspace,
                "compatibilityProfile": compatibility_profile,
                "producer": "direct_codex_managed_session",
                "temporaryMigrationPath": True,
            },
        )
        row = await store.attach_session(
            request.idempotency_key,
            locator.session_id,
        )
        row = await store.record_session_created(
            request.idempotency_key,
            session_id=locator.session_id,
            agent_id=request.agent_id,
            endpoint_ref="direct-codex-compat",
        )

        source_metadata = {
            "source": "codex_direct_compat",
            "compatibilityProfile": compatibility_profile,
            "directManagedSessionId": locator.session_id,
            "sessionEpoch": locator.session_epoch,
            "containerId": locator.container_id,
        }
        if phase == "started":
            event_payloads = [
                {
                    "type": "session.started",
                    "status": "running",
                    "data": {
                        **source_metadata,
                        "managedSessionWorkflowId": binding.workflow_id,
                    },
                },
                {
                    "type": "session.input.user_message",
                    "status": "running",
                    "direction": "moonmind_to_host",
                    "text": str(payload.get("userMessage") or ""),
                    "data": {
                        **source_metadata,
                        "requestId": f"{request.idempotency_key}:initial",
                    },
                },
                {
                    "type": "session.item.turn_started",
                    "status": "running",
                    "data": source_metadata,
                },
            ]
            return await self._append_direct_codex_bridge_events(
                store=store,
                row=row,
                request=request,
                locator=locator,
                event_payloads=event_payloads,
                compatibility_profile=compatibility_profile,
            )

        if phase == "active":
            observations = payload.get("observations")
            turn_id = _string_or_none_for_activity(payload.get("turnId"))
            if not isinstance(observations, list) or turn_id is None:
                raise TemporalActivityRuntimeError(
                    "active payload requires turnId and observations"
                )
            source_metadata["turnId"] = turn_id
            active_event_payloads = self._direct_codex_active_event_payloads(
                observations=observations,
                source_metadata=source_metadata,
                locator=locator,
                turn_id=turn_id,
            )
            return await self._append_direct_codex_bridge_events(
                store=store,
                row=row,
                request=request,
                locator=locator,
                event_payloads=active_event_payloads,
                compatibility_profile=compatibility_profile,
            )

        turn_raw = payload.get("turnResponse")
        summary_raw = payload.get("summary")
        publication_raw = payload.get("publication")
        if not all(
            isinstance(value, Mapping)
            for value in (turn_raw, summary_raw, publication_raw)
        ):
            raise TemporalActivityRuntimeError(
                "terminal payload requires turnResponse, summary, and publication"
            )
        turn_response = CodexManagedSessionTurnResponse.model_validate(dict(turn_raw))
        summary = CodexManagedSessionSummary.model_validate(dict(summary_raw))
        publication = CodexManagedSessionArtifactsPublication.model_validate(
            dict(publication_raw)
        )
        source_metadata["turnId"] = turn_response.turn_id
        assistant_text = str(
            turn_response.metadata.get("assistantText")
            or summary.metadata.get("lastAssistantText")
            or ""
        ).strip()
        event_payloads: list[dict[str, Any]] = [
            {
                "type": "session.started",
                "status": "running",
                "data": {
                    **source_metadata,
                    "threadId": locator.thread_id,
                    "managedAgentRunId": binding.agent_run_id,
                },
            }
        ]
        for intervention in turn_response.metadata.get("sessionInterventions") or []:
            if isinstance(intervention, Mapping):
                event_payloads.append(
                    {
                        "type": "session.item.reset_boundary",
                        "status": "running",
                        "data": {**source_metadata, **dict(intervention)},
                        "latestResetBoundaryRef": publication.latest_reset_boundary_ref,
                    }
                )
        mapped_observations = (
            ("toolEvents", "session.item.tool"),
            ("controlEvents", "session.item.control"),
            ("approvalEvents", "session.item.approval"),
        )
        allowed_intervention_outcomes = {
            "requested", "accepted", "rejected", "completed", "failed",
            "delivery_unknown",
        }
        for metadata_key, event_prefix in mapped_observations:
            for observation in turn_response.metadata.get(metadata_key) or []:
                if not isinstance(observation, Mapping):
                    continue
                outcome = str(
                    observation.get("outcome")
                    or observation.get("status")
                    or "completed"
                ).strip()
                if outcome not in allowed_intervention_outcomes:
                    raise TemporalActivityRuntimeError(
                        f"{metadata_key} outcome must be one of: "
                        + ", ".join(sorted(allowed_intervention_outcomes))
                    )
                if metadata_key in {"controlEvents", "approvalEvents"}:
                    required = (
                        "actorId", "idempotencyKey", "expectedSessionId",
                        "expectedSessionEpoch", "expectedTurnId", "auditRef",
                    )
                    missing_fields = [name for name in required if not observation.get(name)]
                    if missing_fields:
                        raise TemporalActivityRuntimeError(
                            f"{metadata_key} requires authoritative intervention evidence: "
                            + ", ".join(missing_fields)
                        )
                    if str(observation["expectedSessionId"]) != locator.session_id or int(
                        observation["expectedSessionEpoch"]
                    ) != locator.session_epoch or str(observation["expectedTurnId"]) != turn_response.turn_id:
                        raise TemporalActivityRuntimeError(
                            f"{metadata_key} expected session/epoch/turn does not match the active turn"
                        )
                event_payloads.append(
                    {
                        "type": f"{event_prefix}.{outcome}",
                        "status": (
                            "waiting"
                            if outcome in {"requested", "accepted"}
                            else "running"
                        ),
                        "data": {**source_metadata, **dict(observation)},
                        "artifactRef": observation.get("auditRef"),
                    }
                )
        existing_events = await store.list_events(row.bridge_session_id)
        active_assistant_output_published = any(
            event.event_type in {"response.output", "response.output.completed"}
            and str(
                (getattr(event, "metadata_", {}) or {})
                .get("moonmind", {})
                .get("turnId")
                or ""
            )
            == turn_response.turn_id
            for event in existing_events
        )
        if assistant_text and not active_assistant_output_published:
            event_payloads.append(
                {
                    "type": "response.output",
                    "status": "running",
                    "text": assistant_text,
                    "data": {
                        **source_metadata,
                        "text": assistant_text,
                    },
                }
            )
        terminal_status = str(
            payload.get("terminalStatus")
            or ("completed" if turn_response.status == "completed" else "failed")
        ).strip()
        if terminal_status not in {"completed", "failed", "canceled", "timed_out"}:
            raise TemporalActivityRuntimeError(
                "payload.terminalStatus must be completed, failed, canceled, or timed_out"
            )
        terminal_type = {
            "completed": "response.completed",
            "failed": "response.failed",
            "canceled": "session.item.terminal.canceled",
            "timed_out": "session.item.terminal.timed_out",
        }[terminal_status]
        event_payloads.append(
            {
                "type": terminal_type,
                "status": terminal_status,
                "data": {
                    **source_metadata,
                    "outputRefs": list(turn_response.output_refs),
                    "publishedArtifactRefs": list(publication.published_artifact_refs),
                },
            }
        )
        resource_refs = {
            "summaryRef": publication.latest_summary_ref,
            "checkpointRef": publication.latest_checkpoint_ref,
            "controlEventRef": publication.latest_control_event_ref,
            "resetBoundaryRef": publication.latest_reset_boundary_ref,
        }
        resource_refs.update(
            {
                key: value
                for key, value in publication.metadata.items()
                if key
                in {
                    "stdoutArtifactRef",
                    "stderrArtifactRef",
                    "diagnosticsRef",
                    "observabilityEventsRef",
                }
            }
        )
        typed_output_refs: dict[str, str] = {}
        for index, artifact_ref in enumerate(turn_response.output_refs):
            typed_output_refs[f"outputRef:{index}"] = artifact_ref
        for key in ("workspaceArtifactRef", "diffArtifactRef", "continuityMetadataRef", "capabilityGapsRef"):
            value = turn_response.metadata.get(key) or publication.metadata.get(key)
            if value:
                typed_output_refs[key] = str(value)
        resource_refs.update(typed_output_refs)
        for resource_kind, artifact_ref in resource_refs.items():
            if artifact_ref:
                event_payloads.insert(
                    -1,
                    {
                        "type": "session.item.resource_published",
                        "status": "running",
                        "artifactRef": artifact_ref,
                        "data": {
                            **source_metadata,
                            "resourceKind": resource_kind,
                            "artifactRef": artifact_ref,
                        },
                    },
                )
        append_result = await self._append_direct_codex_bridge_events(
            store=store,
            row=row,
            request=request,
            locator=locator,
            event_payloads=event_payloads,
            compatibility_profile=compatibility_profile,
        )
        if str(communication.get("comparisonMode") or "").strip() == "dual_write":
            durable_events = await store.list_events(row.bridge_session_id)

            def event_source(event: Any) -> str:
                metadata = getattr(event, "metadata_", {})
                moonmind = (
                    metadata.get("moonmind", {})
                    if isinstance(metadata, Mapping)
                    else {}
                )
                return str(moonmind.get("source") or "")

            direct_events = [
                event for event in durable_events
                if event_source(event) == "codex_direct_compat"
            ]
            comparison_events = [
                event for event in durable_events
                if event_source(event)
                and event_source(event) != "codex_direct_compat"
                and not str(event.event_type).startswith("lifecycle.")
            ]
            comparison = self._compare_bridge_event_streams(
                direct_events=direct_events,
                comparison_events=comparison_events,
            )
            actual = comparison.pop("actualEventClasses")
            expected = comparison.pop("expectedEventClasses")
            missing = comparison["missingEventClasses"]
            extra = comparison["unexpectedEventClasses"]
            dropped_count = comparison["droppedEventCount"]
            duplicate_count = comparison["duplicateEventCount"]
            reordered = comparison["reordered"]
            semantic_mismatch_count = comparison["semanticMismatchCount"]
            comparison_available = comparison["comparisonAvailable"]
            matched = comparison["matched"]
            await store.record_lifecycle_event(
                request.idempotency_key,
                event_type="codex_direct_compat.comparison",
                code=(
                    "comparison_source_unavailable"
                    if not comparison_available
                    else "projection_parity" if matched else "projection_mismatch"
                ),
                summary=(
                    "Independent comparison producer has not emitted durable events."
                    if not comparison_available
                    else "Direct compatibility and independent projections matched."
                    if matched
                    else "Direct compatibility projection differed from the independent projection."
                ),
                metadata={
                    "expectedEventClasses": expected,
                    "actualEventClasses": actual,
                    "missingEventClasses": missing,
                    "unexpectedEventClasses": extra,
                    "droppedEventCount": dropped_count,
                    "duplicateEventCount": duplicate_count,
                    "reordered": reordered,
                    "semanticMismatchCount": semantic_mismatch_count,
                    "comparisonAvailable": comparison_available,
                },
            )
            append_result["comparison"] = {
                **comparison,
            }
        await store.mark_terminal(
            request.idempotency_key,
            status=terminal_status,
            terminal_refs={
                "metadataRefs": {
                    "latestSummaryRef": publication.latest_summary_ref,
                    "latestCheckpointRef": publication.latest_checkpoint_ref,
                    "latestControlEventRef": publication.latest_control_event_ref,
                    "latestResetBoundaryRef": publication.latest_reset_boundary_ref,
                },
                "publishedArtifactRefs": list(publication.published_artifact_refs),
            },
        )
        return append_result

    @staticmethod
    def _compare_bridge_event_streams(
        *, direct_events: list[Any], comparison_events: list[Any]
    ) -> dict[str, Any]:
        """Compare two independently persisted producer streams."""
        actual = [str(event.event_type) for event in direct_events]
        expected = [str(event.event_type) for event in comparison_events]
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        dropped_count = sum(max(expected.count(value) - actual.count(value), 0) for value in set(expected))
        duplicate_count = sum(max(actual.count(value) - expected.count(value), 0) for value in set(actual))
        reordered = bool(expected and actual and expected != actual and sorted(expected) == sorted(actual))
        semantic_fields = ("normalized_status", "artifact_ref", "text_preview")
        semantic_mismatch_count = sum(
            1
            for direct, comparison in zip(direct_events, comparison_events)
            if direct.event_type == comparison.event_type
            and any(getattr(direct, field, None) != getattr(comparison, field, None) for field in semantic_fields)
        )
        comparison_available = bool(comparison_events)
        matched = bool(
            comparison_available and not missing and not extra
            and not dropped_count and not duplicate_count and not reordered
            and not semantic_mismatch_count
        )
        return {
            "expectedEventClasses": expected,
            "actualEventClasses": actual,
            "missingEventClasses": missing,
            "unexpectedEventClasses": extra,
            "droppedEventCount": dropped_count,
            "duplicateEventCount": duplicate_count,
            "reordered": reordered,
            "semanticMismatchCount": semantic_mismatch_count,
            "comparisonAvailable": comparison_available,
            "matched": matched,
        }

    @staticmethod
    def _direct_codex_active_event_payloads(
        *,
        observations: list[Any],
        source_metadata: Mapping[str, Any],
        locator: CodexManagedSessionLocator,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        """Map the runtime-neutral observation contract to bridge event classes."""
        kind_map = {
            "assistant_message_delta": "response.output.delta",
            "assistant_message": "response.output",
            "assistant_message_completed": "response.output.completed",
            "tool_call_started": "session.item.tool.started",
            "tool_call_output": "session.item.tool.output",
            "tool_call_completed": "session.item.tool.completed",
            "tool_call_failed": "session.item.tool.failed",
            "approval_requested": "session.item.approval.requested",
            "approval_resolved": "session.item.approval.resolved",
            "intervention_requested": "session.item.control.requested",
            "intervention_accepted": "session.item.control.accepted",
            "intervention_rejected": "session.item.control.rejected",
            "intervention_completed": "session.item.control.completed",
            "intervention_failed": "session.item.control.failed",
            "intervention_delivery_unknown": "session.item.control.delivery_unknown",
            "turn_interrupted": "session.item.control.interrupted",
            "turn_canceled": "session.item.terminal.canceled",
            "turn_timed_out": "session.item.terminal.timed_out",
            "turn_started": "session.item.turn_started",
            "turn_completed": "session.item.turn.completed",
            "turn_failed": "session.item.turn_failed",
            "reset_boundary_published": "session.item.reset_boundary",
            "summary_published": "session.item.resource_published",
            "checkpoint_published": "session.item.resource_published",
            "artifact_published": "session.item.resource_published",
            "continuity_published": "session.item.resource_published",
            "cleanup_completed": "session.item.cleanup.completed",
            "cleanup_failed": "session.item.cleanup.failed",
        }
        intervention_kinds = {
            "approval_requested",
            "approval_resolved",
            "intervention_requested",
            "intervention_accepted",
            "intervention_rejected",
            "intervention_completed",
            "intervention_failed",
            "intervention_delivery_unknown",
            "turn_interrupted",
        }
        mapped: list[dict[str, Any]] = []
        for index, raw in enumerate(observations):
            if not isinstance(raw, Mapping):
                continue
            kind = str(raw.get("kind") or raw.get("type") or "").strip()
            event_type = kind_map.get(kind)
            if event_type is None:
                continue
            metadata = raw.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            source_event_id = str(
                metadata.get("sourceEventId")
                or metadata.get("eventId")
                or f"{locator.session_id}:{locator.session_epoch}:{turn_id}:{index}:{kind}"
            )
            data = {
                **dict(source_metadata),
                **metadata,
                "sourceEventId": source_event_id,
                "turnId": str(raw.get("turnId") or turn_id),
            }
            if kind in intervention_kinds:
                required = {
                    "actorId", "idempotencyKey", "expectedSessionId",
                    "expectedSessionEpoch", "expectedTurnId", "outcome", "auditRef",
                }
                missing = sorted(name for name in required if not data.get(name))
                if missing:
                    raise TemporalActivityRuntimeError(
                        f"{kind} requires authoritative intervention evidence: "
                        + ", ".join(missing)
                    )
                if (
                    str(data["expectedSessionId"]) != locator.session_id
                    or int(data["expectedSessionEpoch"]) != locator.session_epoch
                    or str(data["expectedTurnId"]) != turn_id
                ):
                    raise TemporalActivityRuntimeError(
                        f"{kind} expected session/epoch/turn does not match the active turn"
                    )
            event: dict[str, Any] = {
                "type": event_type,
                "status": "running",
                "eventId": source_event_id,
                "data": data,
                "metadata": metadata,
            }
            text = str(raw.get("text") or "").strip()
            if text:
                event["text"] = text[:500]
            artifact_ref = data.get("auditRef") or data.get("artifactRef")
            if artifact_ref:
                event["artifactRef"] = str(artifact_ref)
            mapped.append(event)
        return mapped

    async def _append_direct_codex_bridge_events(
        self,
        *,
        store: Any,
        row: Any,
        request: AgentExecutionRequest,
        locator: CodexManagedSessionLocator,
        event_payloads: list[dict[str, Any]],
        compatibility_profile: str,
    ) -> dict[str, Any]:
        """Append compatibility events idempotently across activity retries."""
        from moonmind.omnigent.bridge_events import build_omnigent_bridge_event

        existing = await store.list_events(row.bridge_session_id)

        def identity(event: Mapping[str, Any]) -> tuple[Any, ...]:
            data = (
                event.get("data")
                if isinstance(event.get("data"), Mapping)
                else {}
            )
            mm = (
                ((event.get("metadata") or {}).get("moonmind") or {})
                if isinstance(event.get("metadata"), Mapping)
                else {}
            )
            event_type = event.get("eventType") or event.get("type")
            turn_id = mm.get("turnId") or data.get("turnId")
            if event_type == "session.started":
                turn_id = None
            return (
                event_type,
                mm.get("directManagedSessionId")
                or data.get("directManagedSessionId"),
                mm.get("sessionEpoch") or data.get("sessionEpoch"),
                turn_id,
                mm.get("sourceEventId")
                or data.get("requestId")
                or data.get("idempotencyKey")
                or data.get("controlId")
                or data.get("approvalId"),
                mm.get("sourceOutcome") or data.get("outcome"),
                event.get("textPreview") or "",
                event.get("artifactRef") or "",
            )

        seen = {
            identity(
                {
                    "eventType": event.event_type,
                    "textPreview": event.text_preview,
                    "artifactRef": event.artifact_ref,
                    "metadata": event.metadata_,
                }
            )
            for event in existing
        }
        normalized = []
        for event_payload in event_payloads:
            source_data = (
                event_payload.get("data")
                if isinstance(event_payload.get("data"), Mapping)
                else {}
            )
            event = build_omnigent_bridge_event(
                payload=event_payload,
                sequence=1,
                request=request,
                omnigent_session_id=locator.session_id,
                bridge_session_id=row.bridge_session_id,
            ).event
            event["metadata"]["moonmind"].update(
                {
                    "source": "codex_direct_compat",
                    "compatibilityProfile": compatibility_profile,
                    "directManagedSessionId": locator.session_id,
                    "sessionEpoch": locator.session_epoch,
                    "turnId": (
                        str((event.get("data") or {}).get("turnId") or "") or None
                    ),
                    "sourceEventId": next(
                        (
                            str((event.get("data") or {}).get(name))
                            for name in (
                                "sourceEventId", "requestId", "idempotencyKey",
                                "controlId", "approvalId",
                            )
                            if (event.get("data") or {}).get(name)
                        ),
                        None,
                    ),
                    "sourceOutcome": source_data.get("outcome"),
                }
            )
            event["metadata"].update(
                {
                    key: value
                    for key, value in source_data.items()
                    if key
                    not in {
                        "source",
                        "compatibilityProfile",
                        "directManagedSessionId",
                        "sessionEpoch",
                        "turnId",
                        "sourceEventId",
                    }
                }
            )
            key = identity(event)
            if key not in seen:
                seen.add(key)
                normalized.append(event)
        if normalized:
            await store.append_events(row.bridge_session_id, normalized)
        return {
            "bridgeSessionId": row.bridge_session_id,
            "omnigentSessionId": locator.session_id,
            "eventCount": len(normalized),
            "compatibilityProfile": compatibility_profile,
        }

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

        # Best-effort orphan sweep: removing leaked session containers must not
        # fail the reconcile activity, whose reattach/degrade work is primary.
        orphan_containers_reaped = 0
        orphan_reap_skipped_recent = 0
        orphan_reap_forced_stale = 0
        orphan_volumes_scanned = 0
        orphan_volumes_reaped = 0
        orphan_volume_reap_skipped_active = 0
        orphan_volume_reap_skipped_recent = 0
        orphan_session_ids_reaped: list[str] = []
        try:
            reap_result = await _await_with_activity_heartbeats(
                controller.reap_orphan_session_containers(),
                heartbeat_payload={
                    "activityType": "agent_runtime.reconcile_managed_sessions",
                },
            )
        except Exception:
            logger.warning(
                "Managed session orphan container reap failed during reconcile",
                exc_info=True,
            )
        else:
            orphan_containers_reaped = int(
                getattr(reap_result, "reaped_containers", 0) or 0
            )
            orphan_reap_skipped_recent = int(
                getattr(reap_result, "skipped_recent", 0) or 0
            )
            orphan_reap_forced_stale = int(
                getattr(reap_result, "forced_stale", 0) or 0
            )
            orphan_volumes_scanned = int(
                getattr(reap_result, "scanned_volumes", 0) or 0
            )
            orphan_volumes_reaped = int(
                getattr(reap_result, "reaped_volumes", 0) or 0
            )
            orphan_volume_reap_skipped_active = int(
                getattr(reap_result, "skipped_active_volumes", 0) or 0
            )
            orphan_volume_reap_skipped_recent = int(
                getattr(reap_result, "skipped_recent_volumes", 0) or 0
            )
            orphan_session_ids_reaped = list(
                getattr(reap_result, "reaped_session_ids", ()) or ()
            )[:session_id_limit]

        return {
            "managedSessionRecordsReconciled": reconciled_count,
            "degradedSessionRecords": degraded_count,
            "sessionIds": session_ids,
            "truncated": reconciled_count > session_id_limit,
            "orphanContainersReaped": orphan_containers_reaped,
            "orphanSessionIdsReaped": orphan_session_ids_reaped,
            "orphanReapSkippedRecent": orphan_reap_skipped_recent,
            "orphanReapForcedStale": orphan_reap_forced_stale,
            "orphanVolumesScanned": orphan_volumes_scanned,
            "orphanVolumesReaped": orphan_volumes_reaped,
            "orphanVolumeReapSkippedActive": orphan_volume_reap_skipped_active,
            "orphanVolumeReapSkippedRecent": orphan_volume_reap_skipped_recent,
        }

    async def agent_runtime_cleanup_managed_runtime_files(
        self,
        payload: Mapping[str, Any] | None = None,
        /,
    ) -> dict[str, Any]:
        from moonmind.workflows.temporal.runtime.cleanup import (
            DockerReferenceState,
            ManagedRuntimeCleanupConfig,
            cleanup_managed_runtime_files,
        )
        from moonmind.workflows.temporal.runtime.managed_session_store import (
            ManagedSessionStore,
        )

        if self._run_store is None:
            raise TemporalActivityRuntimeError(
                "run_store is required for agent_runtime.cleanup_managed_runtime_files"
            )
        config = ManagedRuntimeCleanupConfig.from_env()
        if isinstance(payload, Mapping) and isinstance(payload.get("config"), Mapping):
            config_payload = payload["config"]
            config = ManagedRuntimeCleanupConfig(
                enabled=bool(config_payload.get("enabled", config.enabled)),
                dry_run=bool(config_payload.get("dryRun", config.dry_run)),
                workspace_retention=timedelta(
                    days=int(
                        config_payload.get(
                            "workspaceRetentionDays",
                            config.workspace_retention.days,
                        )
                    )
                ),
                artifact_retention=timedelta(
                    days=int(
                        config_payload.get(
                            "artifactRetentionDays",
                            config.artifact_retention.days,
                        )
                    )
                ),
                record_retention=(
                    None
                    if config_payload.get("recordRetentionDays") is None
                    else timedelta(days=int(config_payload["recordRetentionDays"]))
                ),
                grace=timedelta(
                    seconds=int(
                        config_payload.get(
                            "graceSeconds", config.grace.total_seconds()
                        )
                    )
                ),
                max_delete_paths=int(
                    config_payload.get("maxDeletePaths", config.max_delete_paths)
                ),
                max_delete_bytes=(
                    None
                    if config_payload.get("maxDeleteBytes") is None
                    else int(config_payload["maxDeleteBytes"])
                ),
                lock_path=Path(config_payload.get("lockPath", config.lock_path)),
                runtime_store_root=Path(
                    config_payload.get("runtimeStoreRoot", config.runtime_store_root)
                ),
                artifact_root=Path(config_payload.get("artifactRoot", config.artifact_root)),
            )
        session_store = ManagedSessionStore(config.runtime_store_root / "managed_sessions")
        docker_state: DockerReferenceState | Mapping[str, object] | None = None
        if self._session_controller is not None and hasattr(
            self._session_controller,
            "collect_managed_runtime_cleanup_docker_references",
        ):
            docker_state = await _await_with_activity_heartbeats(
                self._session_controller.collect_managed_runtime_cleanup_docker_references(),
                heartbeat_payload={
                    "activityType": "agent_runtime.cleanup_managed_runtime_files",
                },
            )
        elif not config.dry_run:
            docker_state = DockerReferenceState(
                failed=True,
                reason="docker reference scan unavailable",
            )
        result = cleanup_managed_runtime_files(
            run_store=self._run_store,
            session_store=session_store,
            config=config,
            docker_reference_provider=(
                None if docker_state is None else lambda: docker_state
            ),
            progress_callback=(
                lambda progress: temporal_activity.heartbeat(
                    {
                        "activityType": "agent_runtime.cleanup_managed_runtime_files",
                        **dict(progress),
                    }
                )
                if temporal_activity.in_activity()
                else None
            ),
        )
        result_payload = result.to_dict()
        logger.info(
            "Managed runtime cleanup pass completed: enabled=%s dry_run=%s "
            "scanned_run_records=%s scanned_session_records=%s "
            "scanned_workspace_roots=%s scanned_artifact_dirs=%s "
            "protected_roots=%s eligible_roots=%s deleted_roots=%s "
            "deleted_artifact_dirs=%s deleted_record_files=%s "
            "estimated_deleted_bytes=%s skipped_active=%s skipped_recent=%s "
            "skipped_unsafe_path=%s skipped_ambiguous_owner=%s "
            "delete_budget_exhausted=%s errors=%s candidate_samples=%s",
            not result_payload.get("disabled", False),
            result_payload.get("dryRun"),
            result_payload.get("scannedRunRecords", 0),
            result_payload.get("scannedSessionRecords", 0),
            result_payload.get("scannedWorkspaceRoots", 0),
            result_payload.get("scannedArtifactDirs", 0),
            result_payload.get("protectedRoots", 0),
            result_payload.get("eligibleRoots", 0),
            result_payload.get("deletedRoots", 0),
            result_payload.get("deletedArtifactDirs", 0),
            result_payload.get("deletedRecordFiles", 0),
            result_payload.get("estimatedDeletedBytes", 0),
            result_payload.get("skippedActive", 0),
            result_payload.get("skippedRecent", 0),
            result_payload.get("skippedUnsafePath", 0),
            result_payload.get("skippedAmbiguousOwner", 0),
            result_payload.get("deleteBudgetExhausted", 0),
            len(result_payload.get("errors", [])),
            result_payload.get("candidateSamples", []),
        )
        return result_payload

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
            metadata=managed_run_status_metadata(record),
        )
        return status

    async def agent_runtime_publish_terminal_checkpoint(
        self,
        request: Any = None,
        /,
    ) -> AgentRuntimeTerminalCheckpointResult:
        """Preserve controlled-failure work without changing its outcome.

        This is the single reusable semantic boundary for live workspaces and
        restored checkpoints.  It deliberately returns failure evidence
        instead of raising over the primary workflow failure.
        """
        request = _validate_agent_runtime_terminal_checkpoint_input(request)
        if not request.publication_enabled:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="policy_disabled",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        if request.no_remote_writes:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="no_remote_writes",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        if request.read_only:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="read_only",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        if request.dry_run:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="dry_run",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        if not request.runtime_capability_supported:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="runtime_capability_unsupported",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        if not request.workspace_authoritative:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="workspace_unavailable",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        record = self._run_store.load(request.run_id) if self._run_store else None
        if record is None or not record.workspace_path:
            return AgentRuntimeTerminalCheckpointResult(
                status="skipped",
                reasonCode="checkpoint_unavailable",
                source=request.source,
                attempted=False,
                idempotencyKey=request.idempotency_key,
            )
        token = await self._resolve_workspace_push_github_token(record.workspace_path)
        env = self._workspace_command_env(record.workspace_path, github_token=token)
        if request.existing_branch and request.existing_head_sha:
            try:
                remote_sha = await self._resolve_workspace_remote_branch_sha(
                    workspace=record.workspace_path,
                    branch=request.existing_branch,
                    run_id=request.run_id,
                    env=env,
                )
            except Exception:
                remote_sha = None
            if remote_sha == request.existing_head_sha:
                return AgentRuntimeTerminalCheckpointResult(
                    status="already_published",
                    reasonCode="equivalent_remote_head_verified",
                    source=request.source,
                    attempted=False,
                    branchName=request.existing_branch,
                    headSha=request.existing_head_sha,
                    baseBranch=request.target_branch,
                    prUrl=request.existing_pr_url,
                    remoteVerified=True,
                    idempotencyKey=request.idempotency_key,
                )

        recovery_branch = generate_checkpoint_branch_name(
            workflow_id=request.run_id,
            logical_step_id="workflow",
            checkpoint_ref=f"managed-run:{request.run_id}",
            product_branch_id="terminal",
            label="recovered-work",
            idempotency_key=request.idempotency_key,
        )
        try:
            info = await self._push_workspace_branch(
                request.run_id,
                github_token=token,
                target_branch=request.target_branch,
                head_branch=recovery_branch,
                allow_target_branch_push=False,
                commit_message=(
                    f"Preserve failed workflow work for {request.run_id} "
                    "(MoonLadderStudios/MoonMind#3229)"
                ),
            )
            raw_status = str(info.get("push_status") or "failed")
            status = "no_changes" if raw_status == "no_commits" else raw_status
            if status not in {"pushed", "already_published", "no_changes"}:
                status = "failed"
            verified = info.get("remote_verified") is True
            if status == "pushed" and not verified:
                expected_sha = str(info.get("push_head_sha") or "").strip()
                remote_sha = await self._resolve_workspace_remote_branch_sha(
                    workspace=record.workspace_path,
                    branch=str(info.get("push_branch") or recovery_branch),
                    run_id=request.run_id,
                    env=env,
                )
                verified = bool(expected_sha and remote_sha == expected_sha)
                if not verified:
                    status = "failed"
                    info["push_error"] = "remote head verification failed"
            return AgentRuntimeTerminalCheckpointResult(
                status=status,
                reasonCode=(
                    "graceful_failure_checkpoint_pushed"
                    if status == "pushed"
                    else f"terminal_checkpoint_{status}"
                ),
                source=request.source,
                attempted=True,
                commitCreated=bool(info.get("push_commit_message")),
                branchPushed=status == "pushed",
                branchName=info.get("push_branch"),
                headSha=info.get("push_head_sha"),
                baseBranch=info.get("push_base_branch"),
                remoteVerified=verified,
                idempotencyKey=request.idempotency_key,
                error=info.get("push_error"),
            )
        except Exception as exc:
            logger.warning(
                "Terminal checkpoint publication failed for managed run %s: %s",
                request.run_id,
                exc,
            )
            return AgentRuntimeTerminalCheckpointResult(
                status="failed",
                reasonCode="terminal_checkpoint_failed",
                source=request.source,
                attempted=True,
                idempotencyKey=request.idempotency_key,
                error=str(exc),
            )

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
        run_id, agent_id = self._agent_runtime_request_identifiers(request)

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
        pr_resolver_merge_gate_owned = False
        if isinstance(request, AgentRuntimeFetchResultInput):
            pr_resolver_merge_gate_owned = request.pr_resolver_merge_gate_owned
            if (
                pr_resolver_expected
                and "pr_resolver_merge_gate_owned" not in request.model_fields_set
            ):
                pr_resolver_merge_gate_owned = True

        adapter = ManagedAgentAdapter(
            profile_fetcher=_unused_profile_fetcher,
            slot_requester=_unused_slot_signal,
            slot_releaser=_unused_slot_signal,
            cooldown_reporter=_unused_slot_signal,
            workflow_id=f"agent_runtime_activity:{run_id}",
            run_store=self._run_store,
        )
        workspace_github_token: str | None = None
        try:
            fetch_kwargs: dict[str, Any] = {
                "pr_resolver_expected": pr_resolver_expected,
                "pr_resolver_merge_gate_owned": pr_resolver_merge_gate_owned,
            }
            if publish_mode == "auto":
                fetch_kwargs["include_workspace_auto_publish_evidence"] = True
            result = await adapter.fetch_result(run_id, **fetch_kwargs)
            record = self._run_store.load(run_id)
            if record is not None:
                if record.workspace_path:
                    self._normalize_workspace_git_alternates(record.workspace_path)
                    self._recover_orphan_workspace_object_stores(
                        record.workspace_path
                    )
                    workspace_github_token = (
                        await self._resolve_workspace_push_github_token(
                            record.workspace_path
                        )
                    )
            # pr-resolver runs inside the session container where GitHub auth
            # or mergeability state may lag the activity worker's view. Re-check
            # against GitHub before surfacing a terminal resolver failure. If
            # the PR is actually merged, clear the stale failure so the workflow
            # sees success.
            pr_number = self._pr_number_from_run_id(run_id)
            if (
                pr_resolver_expected
                and result.failure_class is not None
                and (head_branch or pr_number is not None)
                and (
                    "pr-resolver" in (result.summary or "").lower()
                    or pr_number is not None
                )
            ):
                merged_pr = self._reverify_pr_merged_state(
                    run_id=run_id,
                    head_branch=head_branch,
                    base_branch=target_branch,
                    github_token=workspace_github_token,
                )
                if merged_pr is not None:
                    result = self._apply_pr_reverify_override(
                        result=result, merged_pr=merged_pr,
                    )

            # Build merged metadata from the typed result, then enrich with
            # push/PR URL info using model_copy to preserve the typed contract.
            meta = dict(result.metadata or {})
            if record is not None:
                meta.setdefault("agentRunId", record.run_id)
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
                assistant_text = None
                if record.status == "completed" and result.failure_class is None:
                    session_metadata = await self._managed_session_summary_metadata(record)
                    if session_metadata:
                        for key in (
                            "lastAssistantText",
                            "lastAssistantTextTruncated",
                            "lastAssistantTextOriginalChars",
                        ):
                            if key in session_metadata:
                                meta.setdefault(key, session_metadata[key])
                        assistant_text = self._assistant_text_from_session_metadata(
                            session_metadata
                        )
                final_operator_summary = operator_summary or assistant_text
                if final_operator_summary:
                    meta["operator_summary"] = final_operator_summary

            # Push successful results normally. Controlled failures use the
            # same scan/commit/lease pipeline, but an isolated deterministic
            # branch and explicitly secondary terminal-publication evidence.
            publish_agent_id = agent_id
            if record is not None:
                publish_agent_id = record.runtime_id or record.agent_id or agent_id
            controlled_failure = result.failure_class in {
                "user_error",
                "execution_error",
                "integration_error",
                "timed_out",
            }
            if (
                controlled_failure
                and isinstance(request, AgentRuntimeFetchResultInput)
                and request.terminal_checkpoint_publication_enabled
                and publish_mode != "none"
                and _normalize_provider_native_pr_agent_id(publish_agent_id)
                not in _PROVIDER_NATIVE_PR_AGENT_IDS
            ):
                existing = meta.get("terminalPublication")
                existing = existing if isinstance(existing, Mapping) else {}
                publication = await self.agent_runtime_publish_terminal_checkpoint(
                    AgentRuntimeTerminalCheckpointInput(
                        runId=run_id,
                        agentId=publish_agent_id,
                        failureClass=result.failure_class,
                        targetBranch=target_branch,
                        existingBranch=(
                            existing.get("branchName") or meta.get("push_branch")
                        ),
                        existingHeadSha=(
                            existing.get("headSha") or meta.get("push_head_sha")
                        ),
                        existingPrUrl=(
                            existing.get("prUrl") or meta.get("pull_request_url")
                        ),
                        publicationEnabled=(
                            request.terminal_checkpoint_publication_enabled
                        ),
                        noRemoteWrites=request.no_remote_writes,
                        readOnly=request.read_only,
                        dryRun=request.dry_run,
                        workspaceAuthoritative=request.workspace_authoritative,
                        runtimeCapabilitySupported=(
                            request.terminal_checkpoint_capability_supported
                        ),
                        idempotencyKey=f"terminal-checkpoint-v1:{run_id}",
                    )
                )
                publication_payload = publication.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                meta["terminalPublication"] = publication_payload
                meta.update(
                    {
                        "push_status": publication.status,
                        "push_branch": publication.branch_name,
                        "push_head_sha": publication.head_sha,
                        "push_base_branch": publication.base_branch,
                        "remote_verified": publication.remote_verified,
                    }
                )
            elif (
                result.failure_class is None
                and publish_mode != "none"
                and _normalize_provider_native_pr_agent_id(publish_agent_id)
                not in _PROVIDER_NATIVE_PR_AGENT_IDS
            ):
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
                try:
                    push_info = await self._push_workspace_branch(
                        run_id,
                        github_token=workspace_github_token,
                        **push_kwargs,
                    )
                except Exception:
                    raise
                meta.update(push_info)

            # Enrich result with pull_request_url detected from workspace git
            # state (CLI stdout may not always surface PR URLs reliably).
            if result.failure_class is None:
                pr_url = self._detect_pr_url_from_workspace(
                    run_id,
                    github_token=workspace_github_token,
                )
                if pr_url:
                    meta["pull_request_url"] = pr_url

            if meta:
                result = result.model_copy(update={"metadata": meta})

            return result
        finally:
            await self._cleanup_managed_run_publish_support_best_effort(run_id)

    async def agent_runtime_evaluate_terminal_evidence(
        self,
        request: Mapping[str, Any],
        /,
    ) -> AgentRunResult:
        """Apply an execution-bound terminal contract above provider adapters."""
        from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence

        result = AgentRunResult.model_validate(request.get("result") or {})
        contract = request.get("terminalContract")
        if not isinstance(contract, Mapping):
            return result

        workspace_path = str(request.get("workspacePath") or "").strip()
        if not workspace_path:
            workspace_path = str((result.metadata or {}).get("workspacePath") or "").strip()
        run_id = str(request.get("runId") or "").strip()
        if not workspace_path and run_id and self._run_store is not None:
            record = self._run_store.load(run_id)
            workspace_path = str(getattr(record, "workspace_path", "") or "").strip()

        evaluation = evaluate_terminal_evidence(
            dict(contract),
            workspace_path=workspace_path,
            artifact_spool_path=str(request.get("artifactSpoolPath") or "").strip(),
        )
        metadata = {**dict(result.metadata or {}), **dict(evaluation.metadata)}
        metadata["terminalContractId"] = str(contract.get("contractId") or "")
        metadata["terminalContractAuthority"] = "MoonMind.AgentRun"
        metadata["terminalContractOutcome"] = evaluation.outcome
        if evaluation.failure_code:
            metadata["failureCode"] = evaluation.failure_code
        if evaluation.satisfied:
            metadata["terminalContractSatisfied"] = True
            return result.model_copy(update={"metadata": metadata})

        if evaluation.outcome == "continuation_requested":
            metadata.update(
                {
                    "terminalContractSatisfied": False,
                    "terminalContractMissingEvidence": [],
                    "terminalContractRecoveryOutcome": "durable_parent_required",
                }
            )
            if result.failure_class is not None:
                return result.model_copy(update={"metadata": metadata})
            return result.model_copy(
                update={
                    "summary": "Agent completed an authoritative durable continuation handoff.",
                    "failure_class": "execution_error",
                    "provider_error_code": "PR_RESOLVER_REENTER_GATE",
                    "metadata": metadata,
                }
            )

        metadata.update(
            {
                "terminalContractSatisfied": False,
                "terminalContractMissingEvidence": list(evaluation.missing_evidence),
                "terminalContractRecoveryOutcome": "unsupported_or_exhausted",
            }
        )
        missing = ", ".join(evaluation.missing_evidence) or "valid terminal evidence"
        terminal_failure_message = str(
            metadata.get("terminalFailureMessage") or ""
        ).strip()
        terminal_failure_code = str(
            metadata.get("terminalFailureCode") or evaluation.failure_code or ""
        ).strip()
        if evaluation.failure_code == "BATCH_FANOUT_INPUT_INVALID":
            return result.model_copy(
                update={
                    "summary": terminal_failure_message
                    or "Batch fan-out input validation failed.",
                    "failure_class": "user_error",
                    "provider_error_code": terminal_failure_code,
                    "metadata": metadata,
                }
            )
        if result.failure_class is not None:
            return result.model_copy(
                update={
                    "provider_error_code": result.provider_error_code
                    or evaluation.failure_code
                    or "missing_terminal_evidence",
                    "metadata": metadata,
                }
            )
        return result.model_copy(
            update={
                "summary": f"Agent completed without required terminal evidence: {missing}",
                "failure_class": "execution_error",
                "provider_error_code": evaluation.failure_code
                or "missing_terminal_evidence",
                "metadata": metadata,
            }
        )
    async def _managed_session_summary_metadata(
        self,
        record: ManagedRunRecord,
    ) -> Mapping[str, Any]:
        if self._session_controller is None:
            return {}
        if (
            not record.session_id
            or record.session_epoch is None
            or not record.container_id
            or not record.thread_id
        ):
            return {}

        try:
            summary = await self._session_controller.fetch_session_summary(
                FetchCodexManagedSessionSummaryRequest(
                    sessionId=record.session_id,
                    sessionEpoch=record.session_epoch,
                    containerId=record.container_id,
                    threadId=record.thread_id,
                )
            )
            summary_model = (
                summary
                if isinstance(summary, CodexManagedSessionSummary)
                else CodexManagedSessionSummary.model_validate(summary)
            )
            return (
                summary_model.metadata
                if isinstance(summary_model.metadata, Mapping)
                else {}
            )
        except Exception:
            logger.debug(
                "Failed to fetch managed-session summary metadata for run %s",
                record.run_id,
                exc_info=True,
            )
            return {}

    def _assistant_text_from_session_metadata(
        self,
        metadata: Mapping[str, Any],
    ) -> str | None:
        for key in ("assistantText", "lastAssistantText"):
            value = metadata.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            summary = self._sanitize_operator_summary(value)
            if summary and not is_generic_completion_summary(summary):
                return summary
        return None

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
        if summary and not is_generic_completion_summary(summary):
            return summary
        return None

    async def _extract_stdout_operator_summary(
        self,
        record: ManagedRunRecord,
    ) -> str | None:
        if not record.stdout_artifact_ref:
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
        text = self._read_managed_runtime_artifact_tail(
            artifact_ref=artifact_id,
            max_bytes=max_bytes,
        )
        if text is not None:
            return text

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
            _, payload = await self._artifact_service.read(
                artifact_id=artifact_id,
                principal="system:agent_runtime",
                allow_restricted_raw=False,
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
    def _read_managed_runtime_artifact_tail(
        *,
        artifact_ref: str,
        max_bytes: int,
    ) -> str | None:
        ref = str(artifact_ref or "").strip()
        if not ref:
            return None

        root = _managed_runtime_artifact_root().resolve()
        path = (root / ref).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            logger.warning("Rejected managed runtime artifact ref outside root: %s", ref)
            return None
        if not path.is_file():
            return None
        try:
            return TemporalAgentRuntimeActivities._read_path_tail_text(
                path,
                max_bytes=max_bytes,
            )
        except OSError:
            logger.debug(
                "Failed to read managed runtime artifact tail for ref %s",
                ref,
                exc_info=True,
            )
            return None

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
        records = TemporalAgentRuntimeActivities._parse_git_status_records(status_output)
        return tuple(path for _status, path in records)

    @staticmethod
    def _parse_git_status_records(status_output: bytes) -> tuple[tuple[str, str], ...]:
        """Extract ``(status, path)`` records from `git status --porcelain=v1 -z`."""
        entries = TemporalAgentRuntimeActivities._parse_git_status_record_entries(
            status_output
        )
        return tuple((status, path) for status, path, _is_source_path in entries)

    @staticmethod
    def _parse_git_status_record_entries(
        status_output: bytes,
    ) -> tuple[tuple[str, str, bool], ...]:
        """Extract Git status records and mark rename/copy source paths."""

        def _decode_path(path_bytes: bytes) -> str:
            return os.fsdecode(path_bytes)

        raw_output = bytes(status_output or b"")
        if not raw_output:
            return ()

        entries = raw_output.split(b"\0")
        records: list[tuple[str, str, bool]] = []
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
            records.append((status, _decode_path(path_bytes), False))

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
                records.append((status, _decode_path(original_path_bytes), True))

            index += 1

        deduped: dict[str, tuple[str, str, bool]] = {}
        for status, path, is_source_path in records:
            deduped.setdefault(path, (status, path, is_source_path))
        return tuple(deduped.values())

    @staticmethod
    def _git_status_needs_worktree_stage(status: str) -> bool:
        """Return True when a porcelain status has unstaged worktree changes."""
        if len(status) != 2 or status in {"??", "!!"}:
            return False
        return status[1] != " "

    @staticmethod
    def _should_exclude_publish_path(
        path_text: str,
        *,
        workspace: Path | None = None,
    ) -> bool:
        """Skip runtime scaffolding paths that should never be published."""
        normalized = str(path_text or "").strip().rstrip("/")
        if not normalized:
            return True
        for excluded in _PUBLISH_GIT_EXCLUDED_PATHS:
            if normalized == excluded or normalized.startswith(f"{excluded}/"):
                return True
        if workspace is not None and (
            normalized == ".agents/skills"
            or normalized.startswith(".agents/skills/")
            or normalized == ".gemini/skills"
            or normalized.startswith(".gemini/skills/")
            or normalized == "skills_active"
            or normalized.startswith("skills_active/")
        ):
            root = normalized.split("/", 2)
            if root[:2] == [".agents", "skills"]:
                projection = workspace.expanduser().resolve() / ".agents" / "skills"
            elif root[:2] == [".gemini", "skills"]:
                projection = workspace.expanduser().resolve() / ".gemini" / "skills"
            else:
                projection = workspace.expanduser().resolve() / "skills_active"
            if projection.is_symlink():
                projection_resolved = projection.resolve(strict=False)
                if (projection_resolved / "_manifest.json").is_file():
                    return True
                for owned_root in [
                    workspace.expanduser().resolve() / "runtime" / "skills_active",
                    workspace.expanduser().resolve().parent / "runtime" / "skills_active",
                    workspace.expanduser().resolve() / "skills_active",
                ]:
                    try:
                        projection_resolved.relative_to(
                            owned_root.resolve(strict=False)
                        )
                        return True
                    except ValueError:
                        continue
        return False

    @staticmethod
    def _git_alternate_candidate(objects_dir: Path, path_text: str) -> Path:
        alternate_path = Path(path_text)
        if alternate_path.is_absolute():
            return alternate_path
        return objects_dir / alternate_path

    @staticmethod
    def _workspace_local_alternate_text(
        *,
        git_dir: Path,
        objects_dir: Path,
        candidate: Path,
    ) -> str:
        resolved_git_dir = git_dir.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_candidate.is_relative_to(resolved_git_dir):
            return os.path.relpath(resolved_candidate, objects_dir.resolve())
        return str(candidate)

    @staticmethod
    def _normalize_workspace_git_alternates(workspace: str) -> None:
        """Repair stale agent-written Git alternates before publishing.

        Managed run ids contain ``:`` characters, and Git treats ``:`` as an
        alternate object path separator. Workspace-local alternates therefore
        need to be relative paths such as ``../objects_app``.
        """
        git_dir = Path(workspace) / ".git"
        objects_dir = git_dir / "objects"
        alternates_path = objects_dir / "info" / "alternates"
        if not git_dir.is_dir() or not alternates_path.is_file():
            return

        try:
            raw_lines = alternates_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.warning(
                "Unable to read Git alternates for workspace %s",
                workspace,
                exc_info=True,
            )
            return

        normalized_lines: list[str] = []
        changed = False
        for raw_line in raw_lines:
            path_text = raw_line.strip()
            if not path_text:
                changed = True
                continue

            candidate = TemporalAgentRuntimeActivities._git_alternate_candidate(
                objects_dir,
                path_text,
            )
            if candidate.is_dir():
                normalized_text = (
                    TemporalAgentRuntimeActivities._workspace_local_alternate_text(
                        git_dir=git_dir,
                        objects_dir=objects_dir,
                        candidate=candidate,
                    )
                )
                if normalized_text == ".":
                    changed = True
                    continue
                normalized_lines.append(normalized_text)
                changed = changed or normalized_text != path_text
                continue

            replacement_name = Path(path_text).name
            replacement = git_dir / replacement_name
            if (
                replacement_name.startswith("objects")
                and replacement.is_dir()
                and replacement.resolve() != objects_dir.resolve()
            ):
                normalized_lines.append(
                    TemporalAgentRuntimeActivities._workspace_local_alternate_text(
                        git_dir=git_dir,
                        objects_dir=objects_dir,
                        candidate=replacement,
                    )
                )
                changed = True
                continue

            logger.warning(
                "Dropping missing Git alternate for workspace %s: %s",
                workspace,
                path_text,
            )
            changed = True

        unique_normalized = list(dict.fromkeys(normalized_lines))
        if not changed and len(unique_normalized) == len(normalized_lines):
            return

        try:
            if unique_normalized:
                alternates_path.write_text(
                    "\n".join(unique_normalized) + "\n",
                    encoding="utf-8",
                )
            else:
                alternates_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Unable to update Git alternates for workspace %s",
                workspace,
                exc_info=True,
            )

    @staticmethod
    def _looks_like_git_loose_objects_dir(directory: Path) -> bool:
        """Return True when ``directory`` contains git-style loose objects.

        A git loose-objects directory has two-character hex subdirectories
        (e.g. ``6d/``) whose children are SHA-1 or SHA-256 hex blob filenames.
        Also accepted as a positive signal: a ``pack/`` subdirectory with at
        least one ``.pack`` file. Both signals are evaluated cheaply without
        walking the full tree.
        """
        if not directory.is_dir():
            return False
        try:
            entries = list(directory.iterdir())
        except OSError:
            return False

        hex_chars = set("0123456789abcdef")
        loose_object_name_lengths = {38, 62}
        for entry in entries:
            name = entry.name
            if entry.is_dir() and name == "pack":
                try:
                    for child in entry.iterdir():
                        if child.is_file() and child.name.endswith(".pack"):
                            return True
                except OSError:
                    continue
                continue
            if (
                entry.is_dir()
                and len(name) == 2
                and all(c in hex_chars for c in name.lower())
            ):
                try:
                    for child in entry.iterdir():
                        cname = child.name
                        if (
                            child.is_file()
                            and len(cname) in loose_object_name_lengths
                            and all(c in hex_chars for c in cname.lower())
                        ):
                            return True
                except OSError:
                    continue
        return False

    @staticmethod
    def _recover_orphan_workspace_object_stores(workspace: str) -> None:
        """Register orphan object-stores as Git alternates before commit.

        Some managed-runtime images stage loose objects in a directory that
        sits outside ``.git/objects`` without writing a corresponding
        ``.git/objects/info/alternates`` entry. Known shapes include sibling
        directories such as ``<workspace_parent>/git-objects`` and in-git
        directories such as ``<workspace>/.git/agent-objects``. When that
        happens, later plain Git commands can fail with missing object errors
        because refs or the index point at objects Git cannot resolve.

        This helper finds such orphan directories, verifies they look like
        git object stores, and appends them to ``info/alternates`` using a
        path relative to ``.git/objects`` (so that ``:`` characters in
        managed run ids do not break alternate parsing).
        """
        workspace_path = Path(workspace)
        git_dir = workspace_path / ".git"
        objects_dir = git_dir / "objects"
        if not git_dir.is_dir() or not objects_dir.is_dir():
            return

        try:
            workspace_resolved = workspace_path.resolve()
        except OSError:
            return
        workspace_parent = workspace_resolved.parent

        alternates_path = objects_dir / "info" / "alternates"
        existing_lines: list[str] = []
        if alternates_path.is_file():
            try:
                existing_lines = [
                    line.strip()
                    for line in alternates_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            except OSError:
                existing_lines = []

        try:
            objects_dir_resolved = objects_dir.resolve()
        except OSError:
            return

        registered: set[Path] = set()
        for raw in existing_lines:
            candidate = (
                Path(raw)
                if Path(raw).is_absolute()
                else (objects_dir / raw)
            )
            try:
                registered.add(candidate.resolve())
            except OSError:
                continue

        candidate_dirs: list[Path] = []
        try:
            candidate_dirs.extend(workspace_parent.iterdir())
        except OSError as exc:
            logger.debug(
                "Could not scan workspace sibling object-store candidates for %s: %s",
                workspace,
                exc,
            )
        try:
            candidate_dirs.extend(git_dir.iterdir())
        except OSError as exc:
            logger.debug(
                "Could not scan in-git object-store candidates for %s: %s",
                workspace,
                exc,
            )

        additions: list[str] = []
        for candidate_dir in candidate_dirs:
            try:
                candidate_resolved = candidate_dir.resolve()
            except OSError:
                continue
            if candidate_resolved == workspace_resolved:
                continue
            if candidate_resolved == objects_dir_resolved:
                continue
            if candidate_resolved in registered:
                continue
            if not TemporalAgentRuntimeActivities._looks_like_git_loose_objects_dir(
                candidate_dir
            ):
                continue
            try:
                relative = os.path.relpath(
                    candidate_resolved,
                    objects_dir_resolved,
                )
            except ValueError:
                relative = str(candidate_resolved)
            additions.append(relative)
            registered.add(candidate_resolved)

        if not additions:
            return

        merged: list[str] = []
        seen: set[str] = set()
        for line in existing_lines + additions:
            if line not in seen:
                seen.add(line)
                merged.append(line)

        try:
            (objects_dir / "info").mkdir(parents=True, exist_ok=True)
            alternates_path.write_text(
                "\n".join(merged) + "\n",
                encoding="utf-8",
            )
            logger.info(
                "Registered %d sibling object store(s) as Git alternates for "
                "workspace %s: %s",
                len(additions),
                workspace,
                ", ".join(additions),
            )
        except OSError:
            logger.warning(
                "Unable to register sibling object stores for workspace %s",
                workspace,
                exc_info=True,
            )

    @staticmethod
    def _workspace_command_env(
        workspace: str,
        *,
        github_token: str | None = None,
    ) -> dict[str, str]:
        """Build a subprocess env that exposes workspace-local command shims."""
        env = dict(os.environ)
        support_root = Path(workspace).resolve().parent / ".moonmind"
        support_bin = support_root / "bin"
        support_gitconfig = support_root / "gitconfig"
        normalized_github_token = str(
            github_token or env.get("GITHUB_TOKEN", "")
        ).strip()
        if normalized_github_token:
            env["GITHUB_TOKEN"] = normalized_github_token
            env["GH_TOKEN"] = normalized_github_token
        git_helper_path = support_bin / "git-credential-moonmind"
        helper_command: str | None = None

        try:
            support_root.mkdir(parents=True, exist_ok=True)
            support_bin.mkdir(parents=True, exist_ok=True)
            if normalized_github_token:
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

        _normalize_managed_path_owners(
            (support_root, support_bin, git_helper_path, support_gitconfig)
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
        else:
            env.pop("GIT_CONFIG_GLOBAL", None)
        git_name = str(settings.workflow.git_user_name or "").strip()
        git_email = str(settings.workflow.git_user_email or "").strip()
        if git_name:
            env["GIT_AUTHOR_NAME"] = git_name
            env["GIT_COMMITTER_NAME"] = git_name
        if git_email:
            env["GIT_AUTHOR_EMAIL"] = git_email
            env["GIT_COMMITTER_EMAIL"] = git_email
        env["HOME"] = str(support_root)
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    @staticmethod
    async def _resolve_workspace_push_github_token(workspace: str) -> str:
        repo = TemporalAgentRuntimeActivities._detect_repo_from_workspace(workspace)
        from moonmind.auth.github_credentials import resolve_github_credential

        resolved = await resolve_github_credential(repo=repo or None)
        return str(resolved.token or "").strip()

    async def _commit_workspace_changes_if_needed(
        self,
        workspace: str,
        *,
        run_id: str,
        commit_message: str | None = None,
        env: Mapping[str, str] | None = None,
        auth_env: Mapping[str, str] | None = None,
        head_branch: str | None = None,
    ) -> dict[str, Any]:
        """Create one deterministic commit when the workspace is dirty."""
        _normalize_managed_git_ownership(workspace)
        command_env = dict(env) if env is not None else self._workspace_command_env(workspace)

        async def _read_status() -> tuple[int, bytes, bytes]:
            status_proc = await _create_managed_agent_subprocess(
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
            return status_proc.returncode, status_stdout, status_stderr

        status_returncode, status_stdout, status_stderr = await _read_status()
        if status_returncode != 0:
            detail = status_stderr.decode("utf-8", errors="replace").strip() or (
                status_stdout.decode("utf-8", errors="replace").strip() or "(no stderr)"
            )
            if (
                self._is_missing_head_object_error(detail)
                and isinstance(head_branch, str)
                and head_branch.strip()
            ):
                repaired = await self._fetch_workspace_branch_for_missing_head(
                    workspace=workspace,
                    branch=head_branch,
                    run_id=run_id,
                    env=dict(auth_env) if auth_env is not None else command_env,
                )
                if repaired:
                    status_returncode, status_stdout, status_stderr = (
                        await _read_status()
                    )

        if status_returncode != 0:
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
            workspace_path = Path(workspace).expanduser().resolve()
            tracked_paths: list[str] = []
            untracked_paths: list[str] = []
            for (
                status,
                path,
                is_source_path,
            ) in self._parse_git_status_record_entries(status_stdout):
                if self._should_exclude_publish_path(
                    path,
                    workspace=workspace_path,
                ):
                    continue
                if status == "??":
                    untracked_paths.append(path)
                elif (
                    status != "!!"
                    and not is_source_path
                    and self._git_status_needs_worktree_stage(status)
                ):
                    tracked_paths.append(path)
        except ValueError as exc:
            return {
                "push_status": "failed",
                "push_error": f"could not parse workspace changes: {exc}",
            }

        async def _stage_paths(
            *,
            mode: str,
            paths: Sequence[str],
        ) -> dict[str, str] | None:
            if not paths:
                return None
            add_proc = await _create_managed_agent_subprocess(
                *self._workspace_git_command(
                    workspace, "add", mode, "--", *paths,
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
                    add_stdout.decode("utf-8", errors="replace").strip()
                    or "(no stderr)"
                )
                return {
                    "push_status": "failed",
                    "push_error": f"could not stage workspace changes: {detail}",
                }
            return None

        stage_error = await _stage_paths(mode="-u", paths=tracked_paths)
        if stage_error is not None:
            return stage_error
        stage_error = await _stage_paths(mode="-A", paths=untracked_paths)
        if stage_error is not None:
            return stage_error

        staged_proc = await _create_managed_agent_subprocess(
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

        staged_paths = [
            path.strip()
            for path in staged_stdout.decode("utf-8", errors="replace").splitlines()
            if path.strip()
        ]
        publishable_staged_paths = [
            path
            for path in staged_paths
            if not self._should_exclude_publish_path(path, workspace=workspace_path)
        ]
        if not publishable_staged_paths:
            return {}

        normalized_message = (
            str(commit_message).strip()
            if isinstance(commit_message, str) and commit_message.strip()
            else f"MoonMind workflow result for run {run_id}"
        )
        commit_proc = await _create_managed_agent_subprocess(
            *self._workspace_git_command(
                workspace,
                "commit",
                "-m",
                normalized_message,
                "--",
                *publishable_staged_paths,
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
    def _is_missing_head_object_error(detail: str) -> bool:
        normalized = str(detail or "").strip().lower()
        if not normalized:
            return False
        return "bad object head" in normalized or (
            "head" in normalized and "missing object" in normalized
        )

    async def _fetch_workspace_branch_for_missing_head(
        self,
        *,
        workspace: str,
        branch: str,
        run_id: str,
        env: Mapping[str, str],
    ) -> bool:
        branch_name = str(branch or "").strip()
        if not branch_name or branch_name == "HEAD" or branch_name.startswith("-"):
            return False

        fetch_proc = await _create_managed_agent_subprocess(
            *self._workspace_git_command(
                workspace,
                "fetch",
                "origin",
                f"refs/heads/{branch_name}",
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
        fetch_stdout, fetch_stderr = await asyncio.wait_for(
            fetch_proc.communicate(), timeout=60,
        )
        if fetch_proc.returncode != 0:
            detail = fetch_stderr.decode("utf-8", errors="replace").strip() or (
                fetch_stdout.decode("utf-8", errors="replace").strip() or "(no stderr)"
            )
            logger.warning(
                "Could not repair missing workspace HEAD for run %s "
                "(branch=%s): %s",
                run_id,
                branch_name,
                detail,
            )
            return False

        verify_proc = await _create_managed_agent_subprocess(
            *self._workspace_git_command(
                workspace,
                "cat-file",
                "-e",
                "HEAD^{commit}",
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
        _, verify_stderr = await asyncio.wait_for(
            verify_proc.communicate(), timeout=10,
        )
        if verify_proc.returncode == 0:
            logger.info(
                "Repaired missing workspace HEAD object for run %s by fetching %s.",
                run_id,
                branch_name,
            )
            return True

        logger.warning(
            "Workspace HEAD still invalid after fetching %s for run %s: %s",
            branch_name,
            run_id,
            verify_stderr.decode("utf-8", errors="replace").strip()
            or f"git cat-file exited with {verify_proc.returncode}",
        )
        return False

    async def _push_workspace_branch(
        self,
        run_id: str,
        *,
        target_branch: str | None = None,
        head_branch: str | None = None,
        commit_message: str | None = None,
        allow_target_branch_push: bool = False,
        github_token: str | None = None,
    ) -> dict[str, Any]:
        """Push the workspace branch to origin.

        Returns a dict with ``push_status``, ``push_branch``, and optionally
        ``push_error``, ``push_base_ref``, ``push_head_sha``, and
        ``push_commit_count`` that the caller merges into result metadata.

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
            self._normalize_workspace_git_alternates(workspace)
            self._recover_orphan_workspace_object_stores(workspace)
            _normalize_managed_git_ownership(workspace)
            resolved_github_token = str(github_token or "").strip()
            if not resolved_github_token:
                resolved_github_token = await self._resolve_workspace_push_github_token(
                    workspace
                )
            command_env = self._workspace_command_env(workspace)
            auth_command_env = self._workspace_command_env(
                workspace,
                github_token=resolved_github_token,
            )
            branch_proc = await _create_managed_agent_subprocess(
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
                head_branch_name
                and current_branch != head_branch_name
                and head_branch_name not in protected
                and (
                    current_branch not in protected
                    or current_branch in {"main", "master"}
                )
            ):
                checkout_proc = await _create_managed_agent_subprocess(
                    *self._workspace_git_command(
                        workspace,
                        "checkout",
                        "-B",
                        head_branch_name,
                    ),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=command_env,
                )
                try:
                    checkout_stdout, checkout_stderr = await asyncio.wait_for(
                        checkout_proc.communicate(), timeout=30,
                    )
                except asyncio.TimeoutError:
                    checkout_proc.kill()
                    await checkout_proc.wait()
                    raise
                if checkout_proc.returncode != 0:
                    detail = (
                        checkout_stderr.decode("utf-8", errors="replace").strip()
                        or checkout_stdout.decode("utf-8", errors="replace").strip()
                        or "(no stderr)"
                    )
                    return {
                        "push_status": "failed",
                        "push_branch": current_branch,
                        "push_error": _scrub_temporal_failure_text(
                            "could not switch protected workspace branch "
                            f"'{current_branch}' to publish branch "
                            f"'{head_branch_name}': {detail}",
                            redactor=SecretRedactor.from_environ(
                                placeholder="[REDACTED]"
                            ),
                        ),
                    }
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
                auth_env=auth_command_env,
                head_branch=current_branch,
            )
            if commit_info.get("push_status") == "failed":
                commit_info.setdefault("push_branch", current_branch)
                return commit_info

            same_branch_publish = (
                target_branch_push_allowed
                and bool(target_branch_name)
                and current_branch == target_branch_name
            )
            base_branch_name = (
                target_branch_name
                or await self._resolve_workspace_default_branch(
                    workspace=workspace,
                    run_id=run_id,
                    env=command_env,
                )
            )
            base_ref = f"origin/{base_branch_name}"
            commit_count: int | None = None
            if same_branch_publish:
                commit_count = await self._count_branch_commits_ahead(
                    workspace=workspace,
                    base_ref=base_ref,
                    branch=current_branch,
                    run_id=run_id,
                    env=command_env,
                )

            async def _finalize_push_success(
                *,
                extra: Mapping[str, Any] | None = None,
                precomputed_commit_count: int | None = commit_count,
            ) -> dict[str, Any]:
                head_sha = await self._resolve_workspace_head_sha(
                    workspace=workspace,
                    run_id=run_id,
                    env=command_env,
                )

                final_commit_count = precomputed_commit_count
                # Verify the branch actually has commits over the publish base.
                # git push succeeds as a no-op when the branch is already
                # up-to-date, which would cause repo.create_pr to fail
                # with HTTP 422 ("No commits between main and <branch>").
                if final_commit_count is None:
                    final_commit_count = await self._count_branch_commits_ahead(
                        workspace=workspace,
                        base_ref=base_ref,
                        branch=current_branch,
                        run_id=run_id,
                        env=command_env,
                    )

                if final_commit_count == 0:
                    logger.warning(
                        "Post-agent git push completed for run %s but branch "
                        "'%s' has no commits over %s",
                        run_id,
                        current_branch,
                        base_ref,
                    )
                    result: dict[str, Any] = {
                        "push_status": "no_commits",
                        "push_branch": current_branch,
                        "push_base_branch": base_branch_name,
                        "push_base_ref": base_ref,
                        "push_commit_count": 0,
                    }
                    if head_sha:
                        result["push_head_sha"] = head_sha
                    if extra:
                        result.update(extra)
                    return result

                logger.info(
                    "Post-agent git push completed for run %s (branch=%s)",
                    run_id,
                    current_branch,
                )
                result = {
                    "push_status": "pushed",
                    "push_branch": current_branch,
                    "push_base_branch": base_branch_name,
                    "push_base_ref": base_ref,
                }
                result.update(commit_info)
                if extra:
                    result.update(extra)
                if head_sha:
                    result["push_head_sha"] = head_sha
                if final_commit_count >= 0:
                    result["push_commit_count"] = final_commit_count
                return result

            remote_sha = await self._resolve_workspace_remote_branch_sha(
                workspace=workspace,
                branch=current_branch,
                run_id=run_id,
                env=auth_command_env,
            )
            pre_push_scan_result = await self._scan_workspace_push_range(
                workspace=workspace,
                run_id=run_id,
                base_ref=base_ref,
                branch=current_branch,
                remote_sha=remote_sha,
                env=command_env,
            )
            if pre_push_scan_result is not None:
                return pre_push_scan_result

            push_proc = await _create_managed_agent_subprocess(
                *self._workspace_git_command(
                    workspace,
                    *build_git_push_with_lease_args(
                        branch=current_branch,
                        recorded_remote_sha=remote_sha,
                    ),
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=auth_command_env,
            )
            try:
                push_stdout, push_stderr = await asyncio.wait_for(
                    push_proc.communicate(), timeout=120,
                )
            except asyncio.TimeoutError:
                push_proc.kill()
                await push_proc.wait()
                raise
            if push_proc.returncode != 0:
                error_detail = (
                    push_stderr.decode("utf-8", errors="replace").strip()
                    or "(no stderr)"
                )
                logger.error(
                    "Post-agent git push FAILED for run %s "
                    "(branch=%s, rc=%d): %s",
                    run_id,
                    current_branch,
                    push_proc.returncode,
                    error_detail,
                )
                classified = classify_git_push_failure(
                    stderr=error_detail,
                    branch=current_branch,
                    base_branch=base_branch_name,
                )
                if classified.get("push_status") == "lease_conflict":
                    retry_metadata: dict[str, Any] = {"push_retry_count": 1}
                    remote_tracking_ref = f"refs/remotes/origin/{current_branch}"
                    fetch_proc = await _create_managed_agent_subprocess(
                        *self._workspace_git_command(
                            workspace,
                            "fetch",
                            "origin",
                            (
                                f"+refs/heads/{current_branch}:"
                                f"{remote_tracking_ref}"
                            ),
                        ),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=auth_command_env,
                    )
                    try:
                        fetch_stdout, fetch_stderr = await asyncio.wait_for(
                            fetch_proc.communicate(), timeout=60,
                        )
                    except asyncio.TimeoutError:
                        fetch_proc.kill()
                        await fetch_proc.wait()
                        raise
                    classified["fetch_status"] = (
                        "fetched" if fetch_proc.returncode == 0 else "failed"
                    )
                    retry_metadata["fetch_status"] = classified["fetch_status"]
                    if fetch_proc.returncode != 0:
                        classified["fetch_error"] = (
                            fetch_stderr.decode(
                                "utf-8", errors="replace"
                            ).strip()
                            or fetch_stdout.decode(
                                "utf-8", errors="replace"
                            ).strip()
                        )
                    else:
                        local_head_after_fetch = await self._resolve_workspace_head_sha(
                            workspace=workspace,
                            run_id=run_id,
                            env=command_env,
                        )
                        remote_sha_after_fetch = await self._resolve_workspace_ref_sha(
                            workspace=workspace,
                            ref=remote_tracking_ref,
                            run_id=run_id,
                            env=command_env,
                        )
                        if not remote_sha_after_fetch:
                            remote_sha_after_fetch = (
                                await self._resolve_workspace_remote_branch_sha(
                                    workspace=workspace,
                                    branch=current_branch,
                                    run_id=run_id,
                                    env=auth_command_env,
                                )
                            )
                        if (
                            local_head_after_fetch
                            and remote_sha_after_fetch
                            and local_head_after_fetch == remote_sha_after_fetch
                        ):
                            retry_metadata["rebase_status"] = "not_needed"
                            return await _finalize_push_success(
                                extra=retry_metadata,
                                precomputed_commit_count=None,
                            )

                        rebase_proc = await _create_managed_agent_subprocess(
                            *self._workspace_git_command(
                                workspace, "rebase", remote_tracking_ref,
                            ),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=command_env,
                        )
                        try:
                            rebase_stdout, rebase_stderr = await asyncio.wait_for(
                                rebase_proc.communicate(), timeout=120,
                            )
                        except asyncio.TimeoutError:
                            rebase_proc.kill()
                            await rebase_proc.wait()
                            raise
                        if rebase_proc.returncode != 0:
                            rebase_detail = (
                                rebase_stderr.decode(
                                    "utf-8", errors="replace"
                                ).strip()
                                or rebase_stdout.decode(
                                    "utf-8", errors="replace"
                                ).strip()
                                or "(no stderr)"
                            )
                            classified["rebase_status"] = "failed"
                            classified["retryable"] = False
                            classified["push_error"] = (
                                f"{classified['push_error']}; automatic rebase "
                                f"onto {remote_tracking_ref} failed: {rebase_detail}"
                            )
                            abort_proc = await _create_managed_agent_subprocess(
                                *self._workspace_git_command(
                                    workspace, "rebase", "--abort",
                                ),
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                                env=command_env,
                            )
                            try:
                                await asyncio.wait_for(
                                    abort_proc.communicate(), timeout=30,
                                )
                            except asyncio.TimeoutError:
                                abort_proc.kill()
                                await abort_proc.wait()
                            return classified

                        retry_metadata["rebase_status"] = "rebased"
                        retry_remote_sha = remote_sha_after_fetch
                        retry_scan_result = await self._scan_workspace_push_range(
                            workspace=workspace,
                            run_id=run_id,
                            base_ref=base_ref,
                            branch=current_branch,
                            remote_sha=retry_remote_sha,
                            env=command_env,
                        )
                        if retry_scan_result is not None:
                            retry_scan_result.update(retry_metadata)
                            return retry_scan_result
                        retry_push_proc = await _create_managed_agent_subprocess(
                            *self._workspace_git_command(
                                workspace,
                                *build_git_push_with_lease_args(
                                    branch=current_branch,
                                    recorded_remote_sha=retry_remote_sha,
                                ),
                            ),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=auth_command_env,
                        )
                        try:
                            _, retry_push_stderr = await asyncio.wait_for(
                                retry_push_proc.communicate(), timeout=120,
                            )
                        except asyncio.TimeoutError:
                            retry_push_proc.kill()
                            await retry_push_proc.wait()
                            raise
                        if retry_push_proc.returncode == 0:
                            return await _finalize_push_success(
                                extra=retry_metadata,
                                precomputed_commit_count=None,
                            )
                        retry_error_detail = (
                            retry_push_stderr.decode(
                                "utf-8", errors="replace"
                            ).strip()
                            or "(no stderr)"
                        )
                        retry_classified = classify_git_push_failure(
                            stderr=retry_error_detail,
                            branch=current_branch,
                            base_branch=base_branch_name,
                        )
                        retry_classified.update(retry_metadata)
                        return retry_classified
                return classified

            return await _finalize_push_success()
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

    async def _resolve_workspace_remote_branch_sha(
        self,
        *,
        workspace: str,
        branch: str,
        run_id: str,
        env: Mapping[str, str],
    ) -> str | None:
        """Resolve a remote branch SHA without requiring a tracking ref.

        Managed workspaces may not fetch every task branch into
        ``refs/remotes/origin``. A force-with-lease push must still pin the
        current remote value when the branch exists, otherwise Git reports
        ``stale info`` even though the operation is safe to retry.
        """

        branch_name = str(branch or "").strip()
        if not branch_name:
            return None
        remote_ref = f"refs/remotes/origin/{branch_name}"
        remote_sha = await self._resolve_workspace_ref_sha(
            workspace=workspace,
            ref=remote_ref,
            run_id=run_id,
            env=env,
        )
        if remote_sha:
            return remote_sha

        try:
            proc = await _create_managed_agent_subprocess(
                *self._workspace_git_command(
                    workspace,
                    "ls-remote",
                    "origin",
                    f"refs/heads/{branch_name}",
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=30,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return None
            if proc.returncode != 0:
                logger.debug(
                    "Could not resolve remote branch SHA for run %s: %s",
                    run_id,
                    stderr_bytes.decode("utf-8", errors="replace").strip(),
                )
                return None
            first_line = (
                stdout_bytes.decode("utf-8", errors="replace").splitlines() or [""]
            )[0].strip()
            if not first_line:
                return None
            remote_sha = first_line.split(maxsplit=1)[0].strip()
            return remote_sha or None
        except Exception:
            logger.debug(
                "Failed to resolve remote branch SHA for run %s (branch=%s)",
                run_id,
                branch_name,
                exc_info=True,
            )
            return None

    async def _resolve_workspace_ref_sha(
        self,
        *,
        workspace: str,
        ref: str,
        run_id: str,
        env: Mapping[str, str],
    ) -> str | None:
        try:
            proc = await _create_managed_agent_subprocess(
                *self._workspace_git_command(workspace, "rev-parse", "--verify", ref),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=10,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return None
            if proc.returncode != 0:
                return None
            return stdout_bytes.decode("utf-8", errors="replace").strip() or None
        except Exception:
            logger.debug(
                "Failed to resolve workspace ref sha for run %s (ref=%s)",
                run_id,
                ref,
                exc_info=True,
            )
            return None

    async def _resolve_workspace_head_sha(
        self,
        *,
        workspace: str,
        run_id: str,
        env: Mapping[str, str],
    ) -> str | None:
        try:
            proc = await _create_managed_agent_subprocess(
                *self._workspace_git_command(workspace, "rev-parse", "HEAD"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=10,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
        except Exception:
            logger.warning(
                "Could not resolve pushed HEAD SHA for run %s",
                run_id,
                exc_info=True,
            )
            return None
        if proc.returncode != 0:
            detail = stderr_bytes.decode("utf-8", errors="replace").strip()
            logger.warning(
                "Could not resolve pushed HEAD SHA for run %s: %s",
                run_id,
                detail or f"git rev-parse exited with {proc.returncode}",
            )
            return None
        head_sha = stdout_bytes.decode("utf-8", errors="replace").strip()
        return head_sha or None

    async def _scan_workspace_push_range(
        self,
        *,
        workspace: str,
        run_id: str,
        base_ref: str,
        branch: str,
        remote_sha: str | None,
        env: Mapping[str, str],
    ) -> dict[str, Any] | None:
        """Block MoonMind-owned pushes when outbound commit content has secrets."""

        if not resolve_high_security_mode():
            return None

        range_base = str(remote_sha or base_ref or "").strip()
        branch_name = str(branch or "").strip()
        if not range_base or not branch_name:
            return {
                "push_status": "blocked",
                "push_branch": branch_name or "(unknown)",
                "push_error": (
                    "outbound git push blocked: could not resolve deterministic "
                    "commit range for high security scan"
                ),
                "diagnostic_kind": "outbound_scan_blocked",
            }

        fetch_ref = branch_name if remote_sha else None
        if fetch_ref is None and base_ref.startswith("origin/"):
            fetch_ref = base_ref.removeprefix("origin/")
        if fetch_ref:
            with contextlib.suppress(Exception):
                await self._read_workspace_git_text(
                    workspace=workspace,
                    env=env,
                    timeout=30,
                    args=("fetch", "origin", fetch_ref),
                )

        commit_range = f"{range_base}..{branch_name}"
        try:
            commit_metadata = await self._read_workspace_git_text(
                workspace=workspace,
                env=env,
                timeout=15,
                args=(
                    "log",
                    (
                        "--format=commit %H%nparents %P%nauthor %an <%ae>%n"
                        + "subject %s%nbody%n%B%n---END-COMMIT---"
                    ),
                    commit_range,
                ),
            )
            changed_files_text = await self._read_workspace_git_text(
                workspace=workspace,
                env=env,
                timeout=15,
                args=("diff", "--name-only", commit_range),
            )
            changed_files = [
                line.strip()
                for line in changed_files_text.splitlines()
                if line.strip()
            ][:_GIT_PUSH_SCAN_MAX_CHANGED_FILES]
            bundle: list[OutboundBundleItem] = [
                OutboundBundleItem(
                    location=f"git.push.commits:{commit_range}",
                    content=commit_metadata[
                        :_GIT_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS
                    ],
                )
            ]
            diff_semaphore = asyncio.Semaphore(10)

            async def _diff_item(changed_file: str) -> OutboundBundleItem:
                async with diff_semaphore:
                    file_diff = await self._read_workspace_git_text(
                        workspace=workspace,
                        env=env,
                        timeout=20,
                        args=(
                            "diff",
                            "--no-ext-diff",
                            commit_range,
                            "--",
                            changed_file,
                        ),
                    )
                return OutboundBundleItem(
                    location=f"git.push.diff:{changed_file}",
                    content=file_diff[:_GIT_PUSH_SCAN_MAX_FILE_DIFF_CHARS],
                )

            bundle.extend(
                await asyncio.gather(*(_diff_item(path) for path in changed_files))
            )
        except Exception as exc:
            safe_detail = redact_sensitive_text(str(exc))
            return {
                "push_status": "blocked",
                "push_branch": branch_name,
                "push_base_ref": base_ref,
                "push_error": (
                    "outbound git push blocked: could not build high security "
                    f"scan payload for {commit_range}: {safe_detail}"
                ),
                "diagnostic_kind": "outbound_scan_blocked",
            }

        scan_result = scan_outbound_bundle(bundle, high_security_mode=True)
        if scan_result.allowed:
            return None

        diagnostics = list(scan_result.sanitized_diagnostics)
        return {
            "push_status": "blocked",
            "push_branch": branch_name,
            "push_base_ref": base_ref,
            "push_error": (
                "outbound git push blocked by high security scan: "
                + "; ".join(diagnostics)
            ),
            "diagnostic_kind": "outbound_scan_blocked",
            "outbound_scan_diagnostics": diagnostics,
        }

    async def _read_workspace_git_text(
        self,
        *,
        workspace: str,
        env: Mapping[str, str],
        timeout: int,
        args: Sequence[str],
    ) -> str:
        proc = await _create_managed_agent_subprocess(
            *self._workspace_git_command(workspace, *args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env) if env is not None else None,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            detail = (
                stderr_bytes.decode("utf-8", errors="replace").strip()
                or stdout_bytes.decode("utf-8", errors="replace").strip()
                or f"git exited with {proc.returncode}"
            )
            raise RuntimeError(detail)
        return stdout_bytes.decode("utf-8", errors="replace")

    async def _resolve_workspace_default_branch(
        self,
        *,
        workspace: str,
        run_id: str,
        env: Mapping[str, str],
    ) -> str:
        """Resolve the remote default branch for branchless PR publishing."""

        try:
            proc = await _create_managed_agent_subprocess(
                *self._workspace_git_command(
                    workspace,
                    "symbolic-ref",
                    "--quiet",
                    "--short",
                    "refs/remotes/origin/HEAD",
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception:
            logger.debug(
                "Could not resolve remote default branch for run %s; using main",
                run_id,
                exc_info=True,
            )
            return "main"

        if proc.returncode != 0:
            logger.debug(
                "Could not resolve remote default branch for run %s: %s",
                run_id,
                stderr.decode("utf-8", errors="replace").strip(),
            )
            return "main"

        raw_ref = stdout.decode("utf-8", errors="replace").strip()
        if raw_ref.startswith("origin/"):
            branch = raw_ref.removeprefix("origin/").strip()
            if branch and branch != "HEAD":
                return branch
            return "main"
        if raw_ref and raw_ref != "HEAD":
            return raw_ref
        return "main"

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
            count_proc = await _create_managed_agent_subprocess(
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

    def _detect_pr_url_from_workspace(
        self,
        run_id: str,
        *,
        github_token: str | None = None,
    ) -> str | None:
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
                env=self._workspace_command_env(workspace),
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
                env=self._workspace_command_env(
                    workspace,
                    github_token=github_token,
                ),
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

    @staticmethod
    def _pr_number_from_run_id(run_id: str | None) -> int | None:
        """Extract a resolver PR number from stable run ids when present."""
        import re

        match = re.search(r"(?:^|:)pr:(\d+)(?::|$)", str(run_id or ""))
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _reverify_pr_merged_state(
        self,
        *,
        run_id: str,
        head_branch: str | None,
        base_branch: str | None = None,
        github_token: str | None = None,
    ) -> dict[str, Any] | None:
        """Return PR metadata when *head_branch*'s PR is merged on GitHub.

        Used to recover from stale pr-resolver failures after GitHub has already
        accepted or observed the merge.
        """
        import subprocess

        branch = (head_branch or "").strip()
        expected_base = (base_branch or "").strip()
        pr_number = self._pr_number_from_run_id(run_id)
        if not branch and pr_number is None:
            return None
        if self._run_store is None:
            return None
        record = self._run_store.load(run_id)
        if record is None or not record.workspace_path:
            return None

        workspace = record.workspace_path
        try:
            repo = self._detect_repo_from_workspace(workspace)
            if not repo:
                return None
            if pr_number is not None:
                pr_list_cmd = [
                    "gh", "pr", "view", str(pr_number),
                    "--repo", repo,
                    "--json", "number,state,mergedAt,url,baseRefName,headRefName",
                ]
            else:
                pr_list_cmd = [
                    "gh", "pr", "list",
                    "--repo", repo,
                    "--head", branch,
                    "--state", "all",
                    "--json", "number,state,mergedAt,url,baseRefName,headRefName",
                    "--limit", "20",
                ]
                if expected_base:
                    pr_list_cmd.extend(["--base", expected_base])
            pr_result = subprocess.run(
                pr_list_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=workspace,
                env=self._workspace_command_env(
                    workspace,
                    github_token=github_token,
                ),
            )
            if pr_result.returncode != 0:
                return None
            raw_stdout = pr_result.stdout.strip()
            try:
                parsed = json.loads(raw_stdout or "null")
            except json.JSONDecodeError:
                logger.debug(
                    "Failed to parse GitHub PR re-verify output for run %s; "
                    "stdout=%r stderr=%r",
                    run_id,
                    raw_stdout[:500],
                    (pr_result.stderr or "").strip()[:500],
                    exc_info=True,
                )
                return None
        except Exception:
            logger.debug(
                "Failed to re-verify PR state for run %s",
                run_id,
                exc_info=True,
            )
            return None

        prs = parsed if isinstance(parsed, list) else [parsed]
        if not prs:
            return None
        for pr in prs:
            if not isinstance(pr, dict):
                continue
            if expected_base:
                actual_base = str(pr.get("baseRefName") or "").strip()
                if actual_base != expected_base:
                    continue
            if str(pr.get("state") or "").strip().upper() == "MERGED":
                return pr
        return None

    @staticmethod
    def _apply_pr_reverify_override(
        *,
        result: AgentRunResult,
        merged_pr: Mapping[str, Any],
    ) -> AgentRunResult:
        """Return *result* with pr-resolver failure cleared after server re-verify."""
        override_meta = dict(result.metadata or {})
        override_meta["prResolverReverified"] = True
        override_meta["mergeAutomationDisposition"] = "already_merged"
        if result.summary:
            override_meta["prResolverStaleSummary"] = result.summary
        url = str(merged_pr.get("url") or "").strip()
        if url:
            override_meta["pull_request_url"] = url
        number = merged_pr.get("number")
        new_summary = (
            f"pr-resolver result was stale; PR #{number} confirmed merged on GitHub"
        )
        return result.model_copy(
            update={
                "summary": new_summary,
                "failure_class": None,
                "provider_error_code": None,
                "metadata": override_meta,
            }
        )

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


def _compact_artifact_ref_text(artifact: Any) -> str:
    ref = getattr(artifact, "artifact_ref", None)
    if ref:
        return str(ref)
    artifact_id = getattr(artifact, "artifact_id", None)
    if artifact_id:
        return str(artifact_id)
    return str(artifact)


def _json_bytes(payload: Mapping[str, Any] | list[Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TemporalCheckpointActivities:
    """Artifact-fleet activities for canonical Step Execution checkpoints."""

    def __init__(
        self,
        *,
        artifact_store: Any | None = None,
        artifact_service: TemporalArtifactService | None = None,
        principal: str = "system",
    ) -> None:
        self._artifact_store = artifact_store or InMemoryArtifactStore()
        self._artifact_service = artifact_service
        self._principal = principal

    async def _put_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if self._artifact_service is not None:
            artifact, _upload = await self._artifact_service.create(
                principal=self._principal,
                content_type=content_type,
                metadata_json=dict(metadata or {}),
            )
            completed = await self._artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal=self._principal,
                payload=payload,
                content_type=content_type,
            )
            return _compact_artifact_ref_text(build_artifact_ref(completed))
        artifact = self._artifact_store.put_bytes(
            payload,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        return _compact_artifact_ref_text(artifact)

    async def _read_bytes(self, artifact_ref: str) -> bytes:
        if self._artifact_service is not None:
            _artifact, payload = await self._artifact_service.read(
                artifact_id=artifact_ref,
                principal=self._principal,
                allow_restricted_raw=True,
            )
            return payload
        return self._artifact_store.get_bytes(artifact_ref)

    async def step_checkpoint_create(
        self,
        request: Mapping[str, Any] | StepCheckpointCreateInput,
    ) -> dict[str, Any]:
        model = (
            request
            if isinstance(request, StepCheckpointCreateInput)
            else StepCheckpointCreateInput.model_validate(request)
        )
        payload = build_step_checkpoint_payload(
            identity=model.identity,
            boundary=model.boundary,
            task_input_snapshot_ref=model.task_input_snapshot_ref,
            workspace=model.workspace.model_dump(by_alias=True, mode="json"),
            created_at=model.created_at,
            plan_ref=model.plan_ref,
            plan_digest=model.plan_digest,
            prepared_input_refs=model.prepared_input_refs,
            step_outputs=model.step_outputs,
        )
        checkpoint_ref = await self._put_bytes(
            _json_bytes(payload),
            content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
            metadata={
                "artifact_kind": "step_execution_checkpoint",
                "checkpoint_id": payload["checkpointId"],
            },
        )
        return build_step_checkpoint_create_result(
            checkpoint_ref=checkpoint_ref,
            checkpoint_id=payload["checkpointId"],
            workspace_kind=model.workspace.kind,
            diagnostic_refs=model.diagnostic_refs,
            idempotency_key=model.idempotency_key,
        )

    async def step_checkpoint_validate(
        self,
        request: Mapping[str, Any] | StepCheckpointValidateInput,
    ) -> dict[str, Any]:
        model = (
            request
            if isinstance(request, StepCheckpointValidateInput)
            else StepCheckpointValidateInput.model_validate(request)
        )
        checkpoint_id = "unknown-checkpoint"
        if isinstance(model.checkpoint, Mapping):
            checkpoint_id = str(model.checkpoint.get("checkpointId") or checkpoint_id)

        for refs, code, message in (
            (
                model.unsafe_artifact_refs,
                "unsafe_checkpoint",
                "checkpoint evidence is unsafe",
            ),
            (
                model.unsupported_artifact_refs,
                "unsupported_checkpoint_kind",
                "checkpoint kind is unsupported",
            ),
            (
                model.workspace_incompatible_refs,
                "workspace_incompatible",
                "checkpoint evidence is workspace-incompatible",
            ),
        ):
            if refs:
                result = StepCheckpointValidateResult(
                    valid=False,
                    failureCode=code,
                    message=message,
                    checkpointId=checkpoint_id,
                    checkpointRef=model.checkpoint_ref,
                    diagnosticRefs=list(refs),
                )
                return result.model_dump(by_alias=True, mode="json")

        checkpoint_payload: Any = model.checkpoint
        if model.checkpoint_ref:
            try:
                raw_checkpoint = await self._read_bytes(model.checkpoint_ref)
                checkpoint_payload = json.loads(raw_checkpoint.decode("utf-8"))
            except TemporalArtifactValidationError:
                result = StepCheckpointValidateResult(
                    valid=False,
                    failureCode="artifact_unauthorized",
                    message="checkpoint artifact evidence is unauthorized",
                    checkpointId=checkpoint_id,
                    checkpointRef=model.checkpoint_ref,
                    diagnosticRefs=[model.checkpoint_ref],
                )
                return result.model_dump(by_alias=True, mode="json")
            except (ArtifactStoreError, TemporalArtifactError):
                result = StepCheckpointValidateResult(
                    valid=False,
                    failureCode="artifact_missing",
                    message="checkpoint artifact evidence is missing",
                    checkpointId=checkpoint_id,
                    checkpointRef=model.checkpoint_ref,
                    diagnosticRefs=[model.checkpoint_ref],
                )
                return result.model_dump(by_alias=True, mode="json")
            except (json.JSONDecodeError, UnicodeDecodeError):
                result = StepCheckpointValidateResult(
                    valid=False,
                    failureCode="artifact_corrupted",
                    message="checkpoint artifact evidence is corrupt",
                    checkpointId=checkpoint_id,
                    checkpointRef=model.checkpoint_ref,
                    diagnosticRefs=[model.checkpoint_ref],
                )
                return result.model_dump(by_alias=True, mode="json")

        if (
            not isinstance(checkpoint_payload, Mapping)
            or "contentType" not in checkpoint_payload
        ):
            missing_refs = list(model.required_artifact_refs)
            if missing_refs:
                result = StepCheckpointValidateResult(
                    valid=False,
                    failureCode="artifact_missing",
                    message=(
                        "checkpoint missing required artifact ref "
                        f"{missing_refs[0]}"
                    ),
                    checkpointId=checkpoint_id,
                    checkpointRef=model.checkpoint_ref,
                    diagnosticRefs=missing_refs,
                )
                return result.model_dump(by_alias=True, mode="json")

        result = validate_step_checkpoint_payload(
            checkpoint_payload,
            expected_source=model.expected_source,
            expected_task_input_snapshot_ref=model.expected_task_input_snapshot_ref,
            expected_plan_ref=model.expected_plan_ref,
            expected_plan_digest=model.expected_plan_digest,
            workspace_policy=model.workspace_policy,
            required_artifact_refs=model.required_artifact_refs,
            unauthorized_artifact_refs=model.unauthorized_artifact_refs,
            corrupted_artifact_refs=model.corrupted_artifact_refs,
            expected_workspace=model.expected_workspace,
            checkpoint_ref=model.checkpoint_ref,
        )
        return StepCheckpointValidateResult(
            **result.model_dump(by_alias=True),
            diagnosticRefs=[],
        ).model_dump(by_alias=True, mode="json")

def build_activity_bindings(
    catalog: TemporalActivityCatalog,
    *,
    artifact_activities: Any | None = None,
    plan_activities: Any | None = None,
    manifest_activities: Any | None = None,
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
        "manifest": manifest_activities,
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
    "validate_activity_catalog_runtime_bindings",
]
