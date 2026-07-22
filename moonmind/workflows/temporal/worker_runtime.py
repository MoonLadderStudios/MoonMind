import contextlib
import re
import uuid

def _build_proposal_service_factory():
    from api_service.db.base import get_async_session_context
    from moonmind.workflows import get_workflow_proposal_service

    @contextlib.asynccontextmanager
    async def factory():
        async with get_async_session_context() as db_session:
            yield get_workflow_proposal_service(db_session)
    return factory

"""Temporal worker runtime entrypoint."""

import asyncio
from copy import deepcopy
import json
import logging
import mimetypes
import os
import shutil
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import temporalio.activity
import temporalio.workflow
from opentelemetry import trace as otel_trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client
from temporalio.common import VersioningBehavior
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from temporalio.worker import (
    UnsandboxedWorkflowRunner,
    Worker,
    WorkerDeploymentConfig,
    WorkerDeploymentVersion,
)
from structlog.stdlib import ProcessorFormatter

from api_service.db.base import get_async_session_context
from api_service.db.models import (
    ContainerJobRecord,
    TemporalArtifactRetentionClass,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalExecutionRecord,
)
from moonmind.capabilities.input_contracts import validate_capability_inputs
from moonmind.config.logging import configure_logging, default_log_fields_from_env
from moonmind.config.settings import settings
from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.workflows.skills.deployment_execution import (
    DeploymentUpdateExecutor,
    DeploymentUpdateLockManager,
    FileDeploymentUpdateLockManager,
    FileDesiredStateStore,
    HostDockerComposeRunner,
    InMemoryDesiredStateStore,
    InMemoryEvidenceWriter,
    register_deployment_update_tool_handler,
)
from moonmind.workflows.skills.ops_diagnostics_execution import (
    HostDockerComposeOpsDiagnosisRunner,
    OpsStackDiagnosisExecutor,
    register_ops_diagnose_stack_tool_handler,
)
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalIntegrationActivities,
    TemporalManifestActivities,
    TemporalPlanActivities,
    TemporalProposalActivities,
    TemporalReviewActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.workers import (
    AGENT_RUNTIME_FLEET,
    DEPLOYMENT_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    build_worker_spec,
    describe_configured_worker,
    list_registered_workflow_types,
)
from moonmind.workflows.executions.execution_contract import (
    build_authoritative_workflow_input_snapshot,
)
from moonmind.workflows.executions.preset_expansion import (
    expand_preset_for_child_run,
)
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow as MoonMindManifestIngest,  # noqa: F401
)
from moonmind.workflows.temporal.jules_bundle import JULES_AGENT_IDS
from moonmind.workflows.temporal.jira_agent_skills import JIRA_AGENT_SKILLS
from moonmind.workflows.temporal.worker_healthcheck import (
    WorkerHealthState,
    start_healthcheck_server,
)
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,  # noqa: F401
    resolve_adapter_metadata,
    get_activity_route,
    resolve_external_adapter,
    external_adapter_execution_style,
)
from moonmind.workflows.temporal.workflows.merge_gate import (
    DEFAULT_RESOLVER_TIMEOUT_SECONDS,
)
from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_activity_handlers,
    workflow_fleet_workflow_classes,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
    _managed_session_docker_network,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor
from moonmind.workflows.temporal.story_output_tools import (
    DOCUMENT_UPDATE_TASKS_TOOL_NAME,
    GITHUB_STORY_TOOL_NAMES,
    JIRA_STORY_TOOL_NAMES,
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.service import TemporalExecutionService

logger = logging.getLogger(__name__)

_TASK_INPUT_SNAPSHOT_CONTENT_TYPE = (
    "application/vnd.moonmind.workflow-input-snapshot+json;version=1"
)
_TASK_INPUT_SNAPSHOT_LINK_TYPE = "input.original_snapshot"
_WORKFLOW_INPUT_SNAPSHOT_VERSION = 1

_MANAGED_SESSION_LOG_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("agentRunId", "managed_session_agent_run_id"),
    ("runtimeId", "managed_session_runtime_id"),
    ("sessionId", "managed_session_id"),
    ("sessionEpoch", "managed_session_epoch"),
    ("sessionStatus", "managed_session_status"),
    ("isDegraded", "managed_session_is_degraded"),
    ("activityType", "managed_session_activity_type"),
    ("transition", "managed_session_transition"),
    ("containerId", "managed_session_container_id"),
    ("threadId", "managed_session_thread_id"),
    ("turnId", "managed_session_turn_id"),
)
_OPENTELEMETRY_LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "[service=%(service)s component=%(component)s "
    "worker_fleet=%(worker_fleet)s worker_id=%(worker_id)s] "
    "[trace_id=%(trace_id)s span_id=%(span_id)s] "
    "[workflow_id=%(temporal_workflow_id)s run_id=%(temporal_run_id)s "
    "activity_id=%(temporal_activity_id)s] "
    "[managed_session_id=%(managed_session_id)s "
    "agent_run_id=%(managed_session_agent_run_id)s "
    "runtime_id=%(managed_session_runtime_id)s "
    "epoch=%(managed_session_epoch)s "
    "status=%(managed_session_status)s "
    "degraded=%(managed_session_is_degraded)s "
    "activity=%(managed_session_activity_type)s "
    "transition=%(managed_session_transition)s "
    "container_id=%(managed_session_container_id)s "
    "thread_id=%(managed_session_thread_id)s "
    "turn_id=%(managed_session_turn_id)s] %(message)s"
)

async def _expand_preset_for_child_run(
    *,
    session: Any,
    initial_parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return await expand_preset_for_child_run(
        session=session,
        initial_parameters=initial_parameters,
    )


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _child_task_snapshot_shape(task_payload: Mapping[str, Any]) -> str:
    instructions = str(task_payload.get("instructions") or "").strip()
    steps = task_payload.get("steps")
    if isinstance(steps, list) and steps:
        return "multi_step"
    if task_payload.get("inputArtifactRef"):
        return "artifact_backed"
    if task_payload.get("appliedStepTemplates"):
        return "template_derived"
    if not instructions and (
        isinstance(task_payload.get("tool"), Mapping)
        or isinstance(task_payload.get("skill"), Mapping)
        or task_payload.get("skills")
    ):
        return "skill_only"
    return "inline_instructions"


def _owner_principal_for_child_snapshot(
    record: TemporalExecutionCanonicalRecord | TemporalExecutionRecord,
) -> str:
    owner_type = getattr(record, "owner_type", None)
    owner_type_value = _enum_value(owner_type).lower()
    owner_id = str(getattr(record, "owner_id", "") or "").strip()
    if owner_type_value == TemporalExecutionOwnerType.USER.value and owner_id:
        return owner_id
    if owner_id:
        return owner_id
    return "service:jira-orchestrate"


def _build_child_run_task_input_snapshot_payload(
    *,
    parameters: Mapping[str, Any],
    task_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "snapshotVersion": _WORKFLOW_INPUT_SNAPSHOT_VERSION,
        "source": {"kind": "create"},
        "draft": {
            "workflowShape": _child_task_snapshot_shape(task_payload),
            "repository": parameters.get("repository"),
            "targetRuntime": parameters.get("targetRuntime"),
            "requiredCapabilities": list(parameters.get("requiredCapabilities") or []),
            "workflow": dict(task_payload),
            "authoredWorkflowInput": build_authoritative_workflow_input_snapshot(
                task_payload=task_payload,
                repository=parameters.get("repository"),
                target_runtime=parameters.get("targetRuntime"),
                required_capabilities=parameters.get("requiredCapabilities"),
                dependency_declarations=parameters.get("dependencies"),
                attachment_refs=[],
            ),
        },
        "largeContentRefs": {},
        "attachmentRefs": [],
        "lineage": {},
        "excluded": {
            "schedule": (
                "Schedule controls are creation-only and are not editable through "
                "workflow edit/rerun."
            )
        },
    }


async def _create_child_run_task_input_snapshot_artifact(
    *,
    service: TemporalArtifactService,
    canonical: TemporalExecutionCanonicalRecord,
    snapshot_payload: Mapping[str, Any],
    principal: str,
) -> str:
    body = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    artifact, _upload = await service.create(
        principal=principal,
        content_type=_TASK_INPUT_SNAPSHOT_CONTENT_TYPE,
        size_bytes=len(body),
        retention_class=TemporalArtifactRetentionClass.LONG,
        link={
            "namespace": canonical.namespace,
            "workflow_id": canonical.workflow_id,
            "run_id": canonical.run_id,
            "link_type": _TASK_INPUT_SNAPSHOT_LINK_TYPE,
            "label": "Original workflow input snapshot",
        },
        metadata_json={
            "artifact_class": _TASK_INPUT_SNAPSHOT_LINK_TYPE,
            "snapshot_version": _WORKFLOW_INPUT_SNAPSHOT_VERSION,
            "workflow_type": "MoonMind.UserWorkflow",
            "source_kind": "create",
            "draft_shape": snapshot_payload["draft"]["workflowShape"],
            "schema_name": "OriginalWorkflowInputSnapshot",
            "created_by": principal,
            "attachment_refs": [],
        },
    )
    completed = await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=body,
        content_type=_TASK_INPUT_SNAPSHOT_CONTENT_TYPE,
    )
    return completed.artifact_id


def _apply_child_snapshot_ref_to_records(
    *,
    records: list[TemporalExecutionCanonicalRecord | TemporalExecutionRecord],
    artifact_id: str,
) -> None:
    for target_record in records:
        memo = dict(target_record.memo or {})
        memo["task_input_snapshot_ref"] = artifact_id
        memo["task_input_snapshot_version"] = _WORKFLOW_INPUT_SNAPSHOT_VERSION
        memo["task_input_snapshot_source_kind"] = "create"
        target_record.memo = memo
        artifact_refs = list(target_record.artifact_refs or [])
        if artifact_id not in artifact_refs:
            artifact_refs.append(artifact_id)
        target_record.artifact_refs = artifact_refs


async def _persist_child_run_task_input_snapshot(
    *,
    session: AsyncSession,
    record: TemporalExecutionRecord | TemporalExecutionCanonicalRecord,
    parameters: Mapping[str, Any],
    artifact_service: TemporalArtifactService | None = None,
) -> str:
    """Persist the original workflow payload for worker-created child runs.

    Jira Orchestrate creates child executions from a worker activity, bypassing
    the API route that normally stores the authoritative edit/rerun snapshot.
    The detail UI intentionally gates edit and rerun actions on this compact
    artifact ref so that reconstruction does not depend on mutable workflow
    parameters alone.
    """

    if _enum_value(getattr(record, "workflow_type", None)) != "MoonMind.UserWorkflow":
        return ""
    workflow_payload = _coerce_mapping(
        parameters.get("workflow") or parameters.get("task")
    )
    if not workflow_payload:
        return ""

    workflow_id = str(getattr(record, "workflow_id", "") or "").strip()
    if not workflow_id:
        return ""
    canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
    if canonical is None:
        return ""
    existing_ref = str(
        (canonical.memo or {}).get("task_input_snapshot_ref") or ""
    ).strip()
    if existing_ref:
        return existing_ref

    service = artifact_service or TemporalArtifactService(
        TemporalArtifactRepository(session)
    )
    principal = _owner_principal_for_child_snapshot(canonical)
    snapshot_payload = _build_child_run_task_input_snapshot_payload(
        parameters=parameters,
        task_payload=workflow_payload,
    )
    artifact_id = await _create_child_run_task_input_snapshot_artifact(
        service=service,
        canonical=canonical,
        snapshot_payload=snapshot_payload,
        principal=principal,
    )

    projection = await session.get(TemporalExecutionRecord, workflow_id)
    records_to_update = [canonical]
    if projection is not None:
        records_to_update.append(projection)
    _apply_child_snapshot_ref_to_records(
        records=records_to_update,
        artifact_id=artifact_id,
    )

    await session.commit()
    await session.refresh(canonical)
    if projection is not None:
        await session.refresh(projection)
    return artifact_id


def _build_jira_orchestrate_execution_creator():
    async def _create_execution(**kwargs):
        async with get_async_session_context() as session:
            kwargs["initial_parameters"] = await _expand_preset_for_child_run(
                session=session,
                initial_parameters=kwargs.get("initial_parameters"),
            )
            service = TemporalExecutionService(session)
            record = await service.create_execution(**kwargs)
            await _persist_child_run_task_input_snapshot(
                session=session,
                record=record,
                parameters=kwargs["initial_parameters"],
            )
            return {
                "workflowId": record.workflow_id,
                "runId": record.run_id,
                "title": (
                    record.memo.get("title")
                    if isinstance(record.memo, dict)
                    else None
                ),
            }

    return _create_execution

_CODEX_CONFIG_FLEETS = frozenset({SANDBOX_FLEET, AGENT_RUNTIME_FLEET})
# Agent runtimes where PR creation is driven by the provider API (e.g. Jules
# ``automationMode`` / ``AUTO_CREATE_PR``), not by appending ``gh pr create``
# to plan instructions.
_TOOLS_WITH_AUTO_PR_CREATION = frozenset({"jules", "jules_api"})


_DEFAULT_DEPLOYMENT_LOCAL_PROJECT_DIR = "/workspace/host_project"


def _read_self_container_id() -> str | None:
    hostname = os.environ.get("HOSTNAME") or os.environ.get("CONTAINER_ID")
    if hostname:
        return hostname.strip() or None
    try:
        return Path("/etc/hostname").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


_DETECT_HOST_PROJECT_DIR_RETRIES = 3
_DETECT_HOST_PROJECT_DIR_BACKOFF_SECONDS = 2.0


def _detect_host_project_dir(local_mount: str) -> str | None:
    """Resolve ``local_mount`` to its host filesystem path via ``docker inspect``.

    The worker reaches the host daemon through ``docker-proxy`` (DOCKER_HOST),
    which exposes the ``CONTAINERS`` API. Inspecting our own container yields
    the ``Mounts`` array; the entry whose ``Destination`` matches the local
    bind-mount has ``Source`` set to the host path.

    Retries a small number of times with linear backoff so transient bootstrap
    races (e.g. ``docker-proxy`` is still coming up) don't permanently disable
    deployment updates. Returns ``None`` when detection still fails after the
    final attempt so the caller can fall back to explicit configuration.
    """

    container_id = _read_self_container_id()
    if not container_id:
        return None
    import subprocess  # local import — only needed at worker bootstrap.
    import time

    last_error: Exception | None = None
    for attempt in range(_DETECT_HOST_PROJECT_DIR_RETRIES):
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .Mounts}}",
                    container_id,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except (
            subprocess.TimeoutExpired,
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
        ) as exc:
            last_error = exc
            if attempt + 1 < _DETECT_HOST_PROJECT_DIR_RETRIES:
                time.sleep(_DETECT_HOST_PROJECT_DIR_BACKOFF_SECONDS * (attempt + 1))
            continue
        try:
            mounts = json.loads(result.stdout)
        except (ValueError, TypeError):
            return None
        if not isinstance(mounts, list):
            return None
        target = local_mount.rstrip("/") or "/"
        for mount in mounts:
            if not isinstance(mount, Mapping):
                continue
            destination = str(mount.get("Destination") or "").rstrip("/") or "/"
            if destination != target:
                continue
            source = str(mount.get("Source") or "").strip()
            if source:
                return source
        return None
    if last_error is not None:
        logger.debug(
            "Failed to detect host project dir after %d attempts: %s",
            _DETECT_HOST_PROJECT_DIR_RETRIES,
            last_error,
        )
    return None


def _build_deployment_update_executor() -> DeploymentUpdateExecutor | None:
    explicit_project_dir = str(
        os.environ.get("MOONMIND_DEPLOYMENT_PROJECT_DIR") or ""
    ).strip()
    local_project_dir = (
        str(os.environ.get("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR") or "").strip()
        or _DEFAULT_DEPLOYMENT_LOCAL_PROJECT_DIR
    )
    if not Path(local_project_dir).exists():
        # The worker isn't running with the project bind-mounted, so the
        # deployment-update tool can't read the compose file. Disable.
        if not explicit_project_dir:
            return None
        # Tests / non-container callers may set MOONMIND_DEPLOYMENT_PROJECT_DIR
        # directly to a path that exists locally; fall back to legacy single-
        # path behavior in that case.
        if Path(explicit_project_dir).exists():
            local_project_dir = explicit_project_dir
        else:
            return None
    project_dir = explicit_project_dir or _detect_host_project_dir(local_project_dir)
    if not project_dir:
        return None
    compose_file = (
        str(os.environ.get("MOONMIND_DEPLOYMENT_COMPOSE_FILE") or "").strip() or None
    )
    desired_state_env_file = (
        str(os.environ.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE") or "").strip()
        or None
    )
    desired_state_json_file = (
        str(os.environ.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE") or "").strip()
        or None
    )
    lock_dir = (
        str(os.environ.get("MOONMIND_DEPLOYMENT_LOCK_DIR") or "").strip()
        or None
    )
    project_name = (
        str(os.environ.get("MOONMIND_DEPLOYMENT_PROJECT_NAME") or "").strip()
        or "moonmind"
    )
    excluded_services = tuple(
        part.strip()
        for part in str(
            os.environ.get("MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES") or ""
        ).split(",")
        if part.strip()
    )
    timeout_raw = str(
        os.environ.get("MOONMIND_DEPLOYMENT_COMMAND_TIMEOUT_SECONDS") or ""
    ).strip()
    try:
        timeout_seconds = int(timeout_raw) if timeout_raw else 900
    except ValueError:
        timeout_seconds = 900
    runner_local = (
        local_project_dir if local_project_dir != project_dir else None
    )
    desired_state_store = (
        FileDesiredStateStore(
            env_file_path=desired_state_env_file,
            json_file_path=desired_state_json_file,
        )
        if desired_state_env_file
        else InMemoryDesiredStateStore()
    )
    lock_manager = (
        FileDeploymentUpdateLockManager(lock_dir=lock_dir)
        if lock_dir
        else DeploymentUpdateLockManager()
    )
    return DeploymentUpdateExecutor(
        lock_manager=lock_manager,
        desired_state_store=desired_state_store,
        evidence_writer=InMemoryEvidenceWriter(),
        runner=HostDockerComposeRunner(
            project_dir=project_dir,
            compose_file=compose_file,
            project_name=project_name,
            command_timeout_seconds=timeout_seconds,
            local_project_dir=runner_local,
            env_file=desired_state_env_file,
            excluded_services=excluded_services,
        ),
        excluded_services=excluded_services,
    )


def _build_ops_diagnosis_executor() -> OpsStackDiagnosisExecutor | None:
    update_executor = _build_deployment_update_executor()
    if update_executor is None:
        return None
    runner = update_executor.runner
    if not isinstance(runner, HostDockerComposeRunner):
        return None
    return OpsStackDiagnosisExecutor(
        evidence_writer=InMemoryEvidenceWriter(),
        runner=HostDockerComposeOpsDiagnosisRunner(
            project_dir=runner.project_dir,
            compose_file=runner.compose_file,
            project_name=runner.project_name,
            command_timeout_seconds=runner.command_timeout_seconds,
            local_project_dir=runner.local_project_dir,
            env_file=runner.env_file,
            excluded_services=runner.excluded_services,
        ),
    )


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}

def _coerce_non_empty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = _coerce_non_empty_text(value)
        if text:
            return text
    return ""


def _repository_default_branch_candidate(*payloads: Mapping[str, Any]) -> str:
    for payload in payloads:
        value = _first_non_empty_text(
            payload.get("defaultBranch"),
            payload.get("default_branch"),
            payload.get("repositoryDefaultBranch"),
            payload.get("repository_default_branch"),
        )
        if value:
            return value
    return ""


def _resolve_authored_branch(
    *,
    git_payload: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    selected_skill_inputs: Mapping[str, Any],
    parameter_payload: Mapping[str, Any],
    input_payload: Mapping[str, Any],
) -> str:
    repository_default_branch = _repository_default_branch_candidate(
        git_payload,
        task_payload,
        selected_skill_inputs,
        parameter_payload,
        input_payload,
    )
    return _first_non_empty_text(
        git_payload.get("branch"),
        task_payload.get("branch"),
        selected_skill_inputs.get("branch"),
        parameter_payload.get("branch"),
        input_payload.get("branch"),
        repository_default_branch,
    )


def _resolve_workspace_work_branch(
    *,
    task_payload: Mapping[str, Any],
    parameter_payload: Mapping[str, Any],
    input_payload: Mapping[str, Any],
) -> str:
    task_workspace_spec = _coerce_mapping(
        task_payload.get("workspaceSpec") or task_payload.get("workspace_spec")
    )
    parameter_workspace_spec = _coerce_mapping(
        parameter_payload.get("workspaceSpec") or parameter_payload.get("workspace_spec")
    )
    input_workspace_spec = _coerce_mapping(
        input_payload.get("workspaceSpec") or input_payload.get("workspace_spec")
    )
    workspace_payload = _coerce_mapping(
        task_payload.get("workspace")
        or parameter_payload.get("workspace")
        or input_payload.get("workspace")
    )
    return _first_non_empty_text(
        task_workspace_spec.get("targetBranch"),
        parameter_workspace_spec.get("targetBranch"),
        input_workspace_spec.get("targetBranch"),
        workspace_payload.get("targetBranch"),
    )


def _generate_runtime_pr_branch(prefix: str) -> str:
    branch_prefix = f"{prefix}-" if prefix else ""
    return f"{branch_prefix}{str(uuid.uuid4())[:8]}"


def _slugify_branch_prefix(value: Any, *, max_length: int = 40) -> str:
    candidate = _coerce_non_empty_text(value)
    if not candidate:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
    return cleaned[:max_length].strip("-")

def _derive_pr_branch_prefix(
    task_payload: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    selected_skill_name: str,
) -> str:
    for raw in (
        task_payload.get("title"),
        publish_payload.get("prTitle"),
        task_payload.get("instructions"),
    ):
        prefix = _slugify_branch_prefix(raw)
        if prefix:
            return prefix

    steps = task_payload.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            step_title = _coerce_non_empty_text(step.get("title"))
            step_prefix = _slugify_branch_prefix(step_title)
            if step_prefix:
                return step_prefix

    if selected_skill_name.strip().lower() not in {"", "auto"}:
        return _slugify_branch_prefix(selected_skill_name)
    return ""


_PR_RESOLVER_SELECTOR_ERROR = (
    "pr-resolver workflow requires workflow.tool.inputs.pr, "
    "workflow.tool.inputs.branch, workflow.git.startingBranch, "
    "or a non-default workflow.git.branch"
)
_PR_RESOLVER_DEFAULT_BRANCH_NAMES = frozenset(
    {"main", "master", "develop", "development", "trunk"}
)


def _pr_resolver_default_branch_names(
    *payloads: Mapping[str, Any],
) -> set[str]:
    names = set(_PR_RESOLVER_DEFAULT_BRANCH_NAMES)
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for key in (
            "defaultBranch",
            "default_branch",
            "repositoryDefaultBranch",
            "repository_default_branch",
        ):
            value = str(payload.get(key) or "").strip().lower()
            if value:
                names.add(value)
    return names


def _pr_resolver_authored_branch_selector(
    *,
    task_payload: Mapping[str, Any],
    selected_skill_inputs: Mapping[str, Any],
    git_payload: Mapping[str, Any],
) -> str:
    default_names = _pr_resolver_default_branch_names(
        task_payload,
        selected_skill_inputs,
        git_payload,
    )
    for value in (
        git_payload.get("branch"),
        task_payload.get("branch"),
    ):
        text = str(value or "").strip()
        if text and text.lower() not in default_names:
            return text
    return ""


def _pr_resolver_structured_selector(
    *,
    task_payload: Mapping[str, Any],
    selected_skill_inputs: Mapping[str, Any],
) -> str:
    git_payload = _coerce_mapping(task_payload.get("git"))
    tool_payload = _coerce_mapping(task_payload.get("tool"))
    skill_payload = _coerce_mapping(task_payload.get("skill"))
    tool_inputs = _coerce_mapping(
        tool_payload.get("inputs") or tool_payload.get("args")
    )
    skill_inputs = _coerce_mapping(
        skill_payload.get("inputs") or skill_payload.get("args")
    )
    return _first_non_empty_text(
        str(selected_skill_inputs.get("pr") or "").strip(),
        str(tool_inputs.get("pr") or "").strip(),
        str(skill_inputs.get("pr") or "").strip(),
        str(selected_skill_inputs.get("startingBranch") or "").strip(),
        str(tool_inputs.get("startingBranch") or "").strip(),
        str(skill_inputs.get("startingBranch") or "").strip(),
        str(git_payload.get("startingBranch") or "").strip(),
        str(task_payload.get("startingBranch") or "").strip(),
        str(selected_skill_inputs.get("branch") or "").strip(),
        str(tool_inputs.get("branch") or "").strip(),
        str(skill_inputs.get("branch") or "").strip(),
        _pr_resolver_authored_branch_selector(
            task_payload=task_payload,
            selected_skill_inputs=selected_skill_inputs,
            git_payload=git_payload,
        ),
    )


def _derive_pr_resolver_title(
    task_payload: Mapping[str, Any],
    selected_skill_inputs: Mapping[str, Any],
) -> str:
    selected_skill_payload = (
        _coerce_mapping(task_payload.get("tool"))
        or _coerce_mapping(task_payload.get("skill"))
    )
    selected_skill_name = str(
        selected_skill_payload.get("name")
        or selected_skill_payload.get("id")
        or ""
    ).strip()
    if selected_skill_name.lower() != "pr-resolver":
        return ""
    return _pr_resolver_structured_selector(
        task_payload=task_payload,
        selected_skill_inputs=selected_skill_inputs,
    )

def _normalize_runtime_mode(raw_mode: Any) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if not normalized:
        return str(settings.workflow.default_runtime or "codex_cli").strip().lower()
    return normalized

_JIRA_AGENT_SKILLS = JIRA_AGENT_SKILLS
_STORY_OUTPUT_TASK_TOOLS = frozenset(
    {
        *JIRA_STORY_TOOL_NAMES,
        *GITHUB_STORY_TOOL_NAMES,
        DOCUMENT_UPDATE_TASKS_TOOL_NAME,
    }
)
_MOONSPEC_BREAKDOWN_TOOLS = frozenset({"moonspec-breakdown"})
_DIRECT_STORY_TOOL_CONTEXT_KEYS = (
    "repository",
    "repo",
    "branch",
    "startingBranch",
    "targetBranch",
    "storyBreakdownPath",
    "storyBreakdownMarkdownPath",
    "storyOutput",
)
_AUTHORED_STEP_METADATA_KEYS = (
    "source",
    "annotations",
    "skill",
    "jiraOrchestration",
    "jira_orchestration",
    "githubOrchestration",
    "github_orchestration",
    "documentUpdateOrchestration",
    "document_update_orchestration",
    "storyOutput",
    "story_output",
)
_INHERITED_SKILL_CONTEXT_KEYS = (
    "selectedSkill",
    "skill",
    "inputs",
    "inputContractDigest",
    "contentDigest",
    "contentRef",
    "skillInputWarnings",
)

def _normalize_required_capability_tokens(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in value:
        token = str(raw or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _has_jira_prefetch_context(task_payload: Mapping[str, Any]) -> bool:
    if task_payload.get("jiraIssueKey") or task_payload.get("jira_issue_key"):
        return True
    for key in ("jira", "jiraIssue", "jira_issue", "jiraPresetBrief", "jira_preset_brief"):
        if isinstance(task_payload.get(key), Mapping):
            return True
    steps = task_payload.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            tool = _coerce_mapping(step.get("tool")) or _coerce_mapping(step.get("skill"))
            tool_id = str(tool.get("id") or tool.get("name") or "").strip().lower()
            if tool_id.startswith("jira.") or tool_id.startswith("story.create_jira"):
                return True
    return False


def _required_capability_blockers(
    *,
    parameters: Mapping[str, Any],
    task_payload: Mapping[str, Any],
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    capabilities = _normalize_required_capability_tokens(
        parameters.get("requiredCapabilities")
    )
    if not capabilities:
        return blockers

    repository = str(parameters.get("repository") or "").strip()

    def add(
        capability: str,
        *,
        check: str,
        reason: str,
        remediation: str,
        target: str = "workflow",
    ) -> None:
        blockers.append(
            {
                "capability": capability,
                "source": "requiredCapabilities",
                "target": target,
                "check": check,
                "reason": reason,
                "remediation": remediation,
            }
        )

    if "git" in capabilities and not repository:
        add(
            "git",
            check="repository_target",
            reason="A repository target is required before git-backed execution can launch.",
            remediation="Select an allowed repository for this workflow.",
        )

    if "gh" in capabilities:
        has_token = bool(
            os.getenv("GH_TOKEN")
            or os.getenv("GITHUB_TOKEN")
            or os.getenv("MOONMIND_GITHUB_TOKEN_SECRET_REF")
        )
        if not has_token and shutil.which("gh") is None:
            add(
                "gh",
                check="github_cli_or_token",
                reason="GitHub CLI access or a GitHub token reference is required before launch.",
                remediation="Configure a GitHub token/provider profile with repository and pull-request access.",
            )

    if "jira" in capabilities:
        atlassian_settings = getattr(settings, "atlassian", None)
        jira_settings = getattr(settings, "jira", None) or getattr(
            atlassian_settings,
            "jira",
            None,
        )
        jira_ready = bool(
            getattr(jira_settings, "jira_tool_enabled", False)
            or getattr(jira_settings, "jira_enabled", False)
            or getattr(settings, "jira_tool_enabled", False)
            or getattr(settings, "jira_enabled", False)
            or _has_jira_prefetch_context(task_payload)
        )
        if not jira_ready:
            add(
                "jira",
                check="trusted_jira_readiness",
                reason="Trusted Jira tool access or prefetched Jira context is required before launch.",
                remediation="Enable the Jira tool integration or attach a trusted Jira issue artifact.",
            )

    if "docker" in capabilities:
        docker_host = os.getenv("DOCKER_HOST")
        default_socket = Path("/var/run/docker.sock")
        if not docker_host and not default_socket.exists():
            add(
                "docker",
                check="docker_runtime",
                reason="Docker capability is required but no Docker endpoint is visible to this worker.",
                remediation="Run on a Docker-capable worker fleet or disable Docker-required execution.",
            )

    return blockers


def _enforce_required_capability_readiness(
    *,
    parameters: Mapping[str, Any],
    task_payload: Mapping[str, Any],
) -> None:
    blockers = _required_capability_blockers(
        parameters=parameters,
        task_payload=task_payload,
    )
    if not blockers:
        return
    raise RuntimeError(
        "requiredCapabilities readiness blocked launch: "
        + json.dumps({"blockers": blockers}, sort_keys=True)
    )

def _requires_branch_publish_for_story_output(value: Any) -> bool:
    return str(value or "").strip().lower() not in {"branch", "pr"}

def _slug_for_story_breakdown(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:48] or "story-breakdown"

def _story_breakdown_paths(*, title: str, existing: Mapping[str, Any]) -> dict[str, str]:
    json_path = str(
        existing.get("storyBreakdownPath")
        or existing.get("story_breakdown_path")
        or existing.get("storiesJsonPath")
        or ""
    ).strip()
    markdown_path = str(
        existing.get("storyBreakdownMarkdownPath")
        or existing.get("story_breakdown_markdown_path")
        or existing.get("storiesMarkdownPath")
        or ""
    ).strip()
    if not json_path:
        story_slug = _slug_for_story_breakdown(title)
        folder = f"artifacts/story-breakdowns/{story_slug}-{str(uuid.uuid4())[:8]}"
        json_path = f"{folder}/stories.json"
    if not markdown_path:
        markdown_path = (
            json_path[:-5] + ".md"
            if json_path.endswith(".json")
            else f"{json_path}.md"
        )
    return {
        "storyBreakdownPath": json_path,
        "storyBreakdownMarkdownPath": markdown_path,
    }

def _selected_step_tool_name(step_entry: Mapping[str, Any]) -> str:
    step_tool = _coerce_mapping(step_entry.get("tool")) or _coerce_mapping(
        step_entry.get("skill")
    )
    return str(step_tool.get("name") or step_tool.get("id") or "").strip()


def _has_explicit_step_skill_name(tool_name: str) -> bool:
    return tool_name.strip().lower() not in {"", "auto"}


def _selected_step_tool_inputs(step_entry: Mapping[str, Any]) -> dict[str, Any]:
    step_tool = _coerce_mapping(step_entry.get("tool")) or _coerce_mapping(
        step_entry.get("skill")
    )
    return dict(
        _coerce_mapping(step_tool.get("inputs"))
        or _coerce_mapping(step_tool.get("args"))
    )


def _validated_step_skill_inputs(
    *,
    step_entry: Mapping[str, Any],
    step_index: int,
    raw_inputs: Mapping[str, Any],
    workflow_context: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    skill_payload = _coerce_mapping(step_entry.get("skill")) or _coerce_mapping(
        step_entry.get("tool")
    )
    return _validated_skill_payload_inputs(
        skill_payload=skill_payload,
        raw_inputs=raw_inputs,
        workflow_context=workflow_context,
        path_prefix=f"steps[{step_index}].skill.inputs",
    )


def _validated_skill_payload_inputs(
    *,
    skill_payload: Mapping[str, Any],
    raw_inputs: Mapping[str, Any],
    workflow_context: Mapping[str, Any],
    path_prefix: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    input_schema = _coerce_mapping(skill_payload.get("inputSchema"))
    if not input_schema:
        return dict(raw_inputs), {}
    contract = {
        "inputSchema": input_schema,
        "uiSchema": _coerce_mapping(skill_payload.get("uiSchema")),
        "defaults": _coerce_mapping(skill_payload.get("defaults")),
        "contractDigest": skill_payload.get("inputContractDigest")
        or skill_payload.get("contractDigest"),
        "contentDigest": skill_payload.get("contentDigest"),
        "contentRef": skill_payload.get("contentRef"),
    }
    result = validate_capability_inputs(
        contract=contract,
        values=raw_inputs,
        workflow_context=workflow_context,
        path_prefix=path_prefix,
    )
    errors = result.get("errors") if isinstance(result, Mapping) else []
    if errors:
        raise RuntimeError(json.dumps({"skillInputErrors": errors}, sort_keys=True))
    evidence = {
        key: value
        for key, value in {
            "inputContractDigest": result.get("contractDigest"),
            "contentDigest": result.get("contentDigest"),
            "contentRef": result.get("contentRef"),
            "skillInputWarnings": result.get("warnings"),
        }.items()
        if value
    }
    return _coerce_mapping(result.get("values")), evidence


def _append_selected_skill_inputs(
    instructions: str,
    skill_inputs: Mapping[str, Any],
) -> str:
    if not skill_inputs:
        return instructions
    return (
        f"{instructions.rstrip()}\n\n"
        "Selected skill inputs:\n"
        + json.dumps(skill_inputs, indent=2, sort_keys=True)
    )


def _normalized_agent_skill_payload(
    skill_payload: Mapping[str, Any],
    *,
    selected_skill_name: str,
    selected_skill_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    compact_payload = {
        key: deepcopy(skill_payload[key])
        for key in (
            "contentRef",
            "contentDigest",
            "inputContractDigest",
        )
        if key in skill_payload
    }
    compact_payload["name"] = selected_skill_name
    if selected_skill_inputs:
        compact_payload["inputs"] = deepcopy(dict(selected_skill_inputs))
    return compact_payload


def _merge_story_output_inputs(
    existing: Mapping[str, Any] | None,
    override: Any,
) -> dict[str, Any]:
    return {
        **_coerce_mapping(existing),
        **_coerce_mapping(override),
    }


def _authored_step_metadata_inputs(
    step_entry: Mapping[str, Any],
    *,
    include_type: bool,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    step_type = _selected_step_type(step_entry)
    if include_type and step_type:
        metadata["type"] = step_type
    for key in _AUTHORED_STEP_METADATA_KEYS:
        if key in step_entry:
            metadata[key] = deepcopy(step_entry[key])
    return metadata


def _drop_inherited_skill_context(inputs: dict[str, Any]) -> None:
    for key in _INHERITED_SKILL_CONTEXT_KEYS:
        inputs.pop(key, None)


def _direct_story_tool_context_inputs(
    node_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: deepcopy(node_inputs[key])
        for key in _DIRECT_STORY_TOOL_CONTEXT_KEYS
        if key in node_inputs
    }

def _canonical_step_fingerprint(step_entry: Mapping[str, Any]) -> str:
    try:
        return json.dumps(step_entry, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return repr(sorted(step_entry.items(), key=lambda item: str(item[0])))


def _dedupe_repeated_step_entries(
    raw_steps: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    deduped: list[Mapping[str, Any]] = []
    fingerprints_by_id: dict[str, str] = {}

    for step_entry in raw_steps:
        step_id = str(step_entry.get("id") or "").strip()
        if not step_id:
            deduped.append(step_entry)
            continue

        fingerprint = _canonical_step_fingerprint(step_entry)
        existing_fingerprint = fingerprints_by_id.get(step_id)
        if existing_fingerprint is None:
            fingerprints_by_id[step_id] = fingerprint
            deduped.append(step_entry)
            continue
        if existing_fingerprint == fingerprint:
            continue
        raise RuntimeError(
            f"task step id {step_id!r} is duplicated with different payloads; "
            "step ids must be unique"
        )

    return deduped

def _selected_step_type(step_entry: Mapping[str, Any]) -> str:
    return str(step_entry.get("type") or "").strip().lower()

def _selected_step_tool_type(step_entry: Mapping[str, Any]) -> str:
    if _selected_step_tool_name(step_entry).lower() in _STORY_OUTPUT_TASK_TOOLS:
        return "skill"
    if _selected_step_type(step_entry) == "tool":
        return "skill"
    return "agent_runtime"

def _jira_agent_skill_selected(tool_name: str) -> bool:
    return tool_name.lower() in _JIRA_AGENT_SKILLS

def _story_output_task_tool_selected(tool_name: str) -> bool:
    return tool_name.lower() in _STORY_OUTPUT_TASK_TOOLS

def _task_uses_only_jira_agent_skill(
    *, selected_skill_name: str, raw_steps: Any
) -> bool:
    if isinstance(raw_steps, list) and len(raw_steps) > 1:
        if not all(isinstance(step, Mapping) for step in raw_steps):
            return False
        effective_step_tool_names = [
            _selected_step_tool_name(step).lower()
            for step in raw_steps
        ]
        return bool(effective_step_tool_names) and all(
            _has_explicit_step_skill_name(name)
            and (
                _jira_agent_skill_selected(name)
                or _story_output_task_tool_selected(name)
            )
            for name in effective_step_tool_names
        )
    return _jira_agent_skill_selected(
        selected_skill_name
    ) or _story_output_task_tool_selected(selected_skill_name)

def _append_agent_skill_instructions(instructions: str, *, selected_skill: str) -> str:
    selected = selected_skill.strip()
    if not _jira_agent_skill_selected(selected):
        return instructions
    marker = f"${selected}"
    if marker in instructions:
        return instructions
    return f"Use {marker}.\n\n{instructions.strip()}"

def _plan_node_selected_skill(node: Mapping[str, Any]) -> str:
    inputs = node.get("inputs")
    if not isinstance(inputs, Mapping):
        return ""
    return str(inputs.get("selectedSkill") or "").strip()


def _task_has_applied_template(task_payload: Mapping[str, Any], slug: str) -> bool:
    applied = task_payload.get("appliedStepTemplates")
    if not isinstance(applied, list):
        return False
    target = slug.strip().lower()
    for entry in applied:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("slug") or "").strip().lower() == target:
            return True
    return False


def _template_step_id_matches(
    node: Mapping[str, Any],
    *,
    slug: str,
    step_index: int,
) -> bool:
    node_id = str(node.get("id") or "").strip().lower()
    target_prefix = f"tpl:{slug.strip().lower()}:"
    if not node_id.startswith(target_prefix):
        return False
    parts = node_id.split(":")
    return len(parts) >= 4 and parts[3] == f"{step_index:02d}"


def _is_explicit_pr_handoff_node(
    node: Mapping[str, Any],
    *,
    task_payload: Mapping[str, Any],
) -> bool:
    inputs = node.get("inputs")
    if not isinstance(inputs, Mapping):
        return False
    annotations = inputs.get("annotations")
    if isinstance(annotations, Mapping):
        role = (
            str(
                annotations.get("jiraOrchestrateRole")
                or annotations.get("jiraImplementRole")
                or annotations.get("issueImplementRole")
                or ""
            )
            .strip()
            .lower()
        )
        if role == "pull-request-handoff":
            return True
    has_jira_implement = _task_has_applied_template(task_payload, "jira-implement")
    if has_jira_implement and _template_step_id_matches(
        node,
        slug="jira-implement",
        step_index=7,
    ):
        return True
    return any(
        _template_step_id_matches(
            node,
            slug="jira-orchestrate",
            step_index=step_index,
        )
        for step_index in (12, 13)
    )


def _append_story_breakdown_instructions(
    instructions: str,
    *,
    story_breakdown_path: str,
    story_breakdown_markdown_path: str,
    story_output_mode: str,
) -> str:
    if story_breakdown_path in instructions:
        return instructions
    return (
        instructions.rstrip()
        + "\n\nStory breakdown output contract:\n"
        + f"- Write machine-readable stories to `{story_breakdown_path}`.\n"
        + f"- Write a human-readable summary to `{story_breakdown_markdown_path}`.\n"
        + "- Do not create or modify any `spec.md` files during breakdown.\n"
        + "- Do not create directories under `specs/`; that happens only during specify.\n"
        + f"- The requested story output mode is `{story_output_mode or 'docs_tmp'}`."
    )

def _build_runtime_planner():
    """Build a plan generator that produces ``agent_runtime`` plan nodes.

    The generated plan dispatches to ``MoonMind.AgentRun`` child workflows
    via the ``tool.type: "agent_runtime"`` discriminator in
    ``MoonMindRunWorkflow._run_execution_stage()``.
    """

    def _runtime_planner(
        inputs: Any,
        parameters: Mapping[str, Any],
        snapshot: Any,
    ) -> dict[str, Any]:
        if snapshot is None:
            raise RuntimeError("runtime planner requires a registry snapshot")

        parameter_payload = dict(parameters or {})
        input_payload = _coerce_mapping(inputs)
        task_payload = _coerce_mapping(
            input_payload.get("workflow") or input_payload.get("task")
        )
        if not task_payload:
            task_payload = _coerce_mapping(
                parameter_payload.get("workflow") or parameter_payload.get("task")
            )
        _enforce_required_capability_readiness(
            parameters=parameter_payload,
            task_payload=task_payload,
        )
        git_payload = _coerce_mapping(task_payload.get("git"))
        task_skill_payload = _coerce_mapping(task_payload.get("skill"))
        selected_skill_payload = _coerce_mapping(
            task_payload.get("tool")
        ) or task_skill_payload
        selected_skill_name = str(
            selected_skill_payload.get("name")
            or selected_skill_payload.get("id")
            or ""
        ).strip()
        selected_skill_inputs = _coerce_mapping(task_payload.get("inputs"))
        if not selected_skill_inputs:
            selected_skill_inputs = _coerce_mapping(
                selected_skill_payload.get("inputs")
                or selected_skill_payload.get("args")
            )
        if not selected_skill_inputs and task_skill_payload:
            selected_skill_inputs = _coerce_mapping(
                task_skill_payload.get("inputs") or task_skill_payload.get("args")
            )
        selected_skill_evidence: dict[str, Any] = {}
        skill_contract_payload = task_skill_payload or selected_skill_payload
        if skill_contract_payload:
            selected_skill_inputs, selected_skill_evidence = (
                _validated_skill_payload_inputs(
                    skill_payload=skill_contract_payload,
                    raw_inputs=selected_skill_inputs,
                    workflow_context={
                        **parameter_payload,
                        **_coerce_mapping(parameter_payload.get("context")),
                        **git_payload,
                    },
                    path_prefix="steps[0].skill.inputs",
                )
            )

        # --- Resolve instructions ---
        instructions = (
            task_payload.get("instructions")
            or input_payload.get("instructions")
            or parameter_payload.get("instructions")
        )
        has_explicit_instructions = isinstance(instructions, str) and bool(
            instructions.strip()
        )
        if instructions and not has_explicit_instructions:
            instructions = None

        if not instructions:
            if selected_skill_name:
                instructions = f"Execute skill '{selected_skill_name}'"
                if selected_skill_inputs:
                    instructions += " with inputs:\n" + json.dumps(
                        selected_skill_inputs,
                        indent=2,
                        sort_keys=True,
                    )
            else:
                raise RuntimeError(
                    "agent_runtime plan requires non-empty instructions in "
                    "workflow.instructions, inputs.instructions, or parameters.instructions"
                )

        if selected_skill_name.lower() == "pr-resolver":
            pr_selector = str(selected_skill_inputs.get("pr") or "").strip()
            pr_resolver_selector = _pr_resolver_structured_selector(
                task_payload=task_payload,
                selected_skill_inputs=selected_skill_inputs,
            )
            if not pr_resolver_selector:
                raise RuntimeError(_PR_RESOLVER_SELECTOR_ERROR)
            # Ensure the auto-generated instruction includes the PR/branch
            # selector so the agent knows which PR to target.  The selector
            # may come from git_payload rather than selected_skill_inputs, so
            # the generic " with inputs:" block above can miss it.
            effective_selector = pr_selector or pr_resolver_selector
            if effective_selector and not pr_selector:
                merged_inputs = (
                    dict(selected_skill_inputs) if selected_skill_inputs else {}
                )
                merged_inputs["pr"] = effective_selector
                selected_skill_inputs = merged_inputs
                if not has_explicit_instructions:
                    instructions = (
                        f"Execute skill '{selected_skill_name}' with inputs:\n"
                        + json.dumps(merged_inputs, indent=2, sort_keys=True)
                    )

        if has_explicit_instructions and selected_skill_inputs:
            instructions = _append_selected_skill_inputs(
                str(instructions),
                selected_skill_inputs,
            )

        # --- Resolve runtime mode ---
        runtime_payload = _coerce_mapping(task_payload.get("runtime"))
        runtime_mode = _normalize_runtime_mode(
            runtime_payload.get("mode")
            or parameter_payload.get("targetRuntime")
        )
        runtime_node: dict[str, Any] = {"mode": runtime_mode}

        model = runtime_payload.get("model") or parameter_payload.get("model")
        if isinstance(model, str) and model.strip():
            runtime_node["model"] = model.strip()

        effort = runtime_payload.get("effort") or parameter_payload.get("effort")
        if isinstance(effort, str) and effort.strip():
            runtime_node["effort"] = effort.strip()

        profile_id = (
            runtime_payload.get("profileId")
            or runtime_payload.get("providerProfile")
            or parameter_payload.get("profileId")
        )
        if isinstance(profile_id, str) and profile_id.strip():
            normalized_profile_id = profile_id.strip()
            runtime_node["profileId"] = normalized_profile_id
            runtime_node["providerProfile"] = normalized_profile_id

        exec_profile_ref = (
            runtime_payload.get("executionProfileRef")
            or runtime_payload.get("execution_profile_ref")
            or parameter_payload.get("executionProfileRef")
            or parameter_payload.get("execution_profile_ref")
        )
        if isinstance(exec_profile_ref, str) and exec_profile_ref.strip():
            runtime_node["executionProfileRef"] = exec_profile_ref.strip()

        # --- Build node inputs ---
        node_inputs: dict[str, Any] = {
            "instructions": instructions,
            "runtime": runtime_node,
        }
        if selected_skill_name.lower() == "pr-resolver":
            node_inputs["timeoutPolicy"] = {
                "timeout_seconds": DEFAULT_RESOLVER_TIMEOUT_SECONDS,
            }
        if selected_skill_inputs:
            node_inputs["inputs"] = dict(selected_skill_inputs)
        node_inputs.update(selected_skill_evidence)

        step_count = task_payload.get("stepCount") or parameter_payload.get("stepCount")
        if step_count is not None:
            try:
                node_inputs["stepCount"] = int(step_count)
            except (ValueError, TypeError):
                pass

        max_attempts = task_payload.get("maxAttempts") or parameter_payload.get("maxAttempts")
        if max_attempts is not None:
            try:
                node_inputs["maxAttempts"] = int(max_attempts)
            except (ValueError, TypeError):
                pass

        publish_payload = _coerce_mapping(task_payload.get("publish"))
        publish_mode = publish_payload.get(
            "mode", parameter_payload.get("publishMode")
        )
        if isinstance(publish_mode, str) and publish_mode.strip():
            node_inputs["publishMode"] = publish_mode.strip()
        commit_message = publish_payload.get(
            "commitMessage", parameter_payload.get("commitMessage")
        )
        if isinstance(commit_message, str) and commit_message.strip():
            node_inputs["commitMessage"] = commit_message.strip()
        publish_base_branch = _first_non_empty_text(
            publish_payload.get("prBaseBranch"),
            publish_payload.get("baseBranch"),
            parameter_payload.get("publishBaseBranch"),
            parameter_payload.get("prBaseBranch"),
            parameter_payload.get("baseBranch"),
        )
        if (
            isinstance(publish_mode, str)
            and publish_mode.strip().lower() == "pr"
            and publish_base_branch
        ):
            node_inputs["publishBaseBranch"] = publish_base_branch

        repository = (
            task_payload.get("repository")
            or input_payload.get("repository")
            or parameter_payload.get("repository")
            or parameter_payload.get("repo")
            or selected_skill_inputs.get("repository")
            or selected_skill_inputs.get("repo")
        )
        if isinstance(repository, str) and repository.strip():
            node_inputs["repository"] = repository.strip()
            node_inputs["repo"] = repository.strip()
        if selected_skill_name:
            node_inputs["selectedSkill"] = selected_skill_name
            node_inputs["skill"] = _normalized_agent_skill_payload(
                skill_contract_payload,
                selected_skill_name=selected_skill_name,
                selected_skill_inputs=selected_skill_inputs,
            )

        raw_steps = task_payload.get("steps")
        if (
            isinstance(raw_steps, list)
            and len(raw_steps) > 1
            and all(isinstance(s, Mapping) for s in raw_steps)
        ):
            raw_steps = _dedupe_repeated_step_entries(raw_steps)
        publish_uses_git = not _task_uses_only_jira_agent_skill(
            selected_skill_name=selected_skill_name,
            raw_steps=raw_steps,
        )
        node_publish_mode = str(node_inputs.get("publishMode") or "").lower()
        if not publish_uses_git and node_publish_mode in {"pr", "branch"}:
            node_inputs["publishMode"] = "none"
        publish_mode_is_pr = (
            publish_uses_git
            and isinstance(publish_mode, str)
            and publish_mode.strip().lower() == "pr"
        )
        authored_branch = _resolve_authored_branch(
            git_payload=git_payload,
            task_payload=task_payload,
            selected_skill_inputs=selected_skill_inputs,
            parameter_payload=parameter_payload,
            input_payload=input_payload,
        )
        for git_key in ("startingBranch", "targetBranch"):
            if publish_mode_is_pr and git_key == "targetBranch":
                git_val = _resolve_workspace_work_branch(
                    task_payload=task_payload,
                    parameter_payload=parameter_payload,
                    input_payload=input_payload,
                )
                if git_val:
                    node_inputs[git_key] = git_val
                continue
            git_val = (
                git_payload.get(git_key)
                or task_payload.get(git_key)
                or selected_skill_inputs.get(git_key)
                or parameter_payload.get(git_key)
                or input_payload.get(git_key)
            )
            if isinstance(git_val, str) and git_val.strip():
                node_inputs[git_key] = git_val.strip()
        if authored_branch:
            node_inputs["branch"] = authored_branch

        if publish_mode_is_pr:
            # In the single authored branch model, ``branch`` is the PR base,
            # not the work/head branch. Always resolve a distinct head branch
            # for PR publishing when one was not explicitly provided.
            if node_inputs.get("branch") and not node_inputs.get("startingBranch"):
                node_inputs["startingBranch"] = node_inputs["branch"]
            if not node_inputs.get("targetBranch"):
                prefix = _derive_pr_branch_prefix(
                    task_payload=task_payload,
                    publish_payload=publish_payload,
                    selected_skill_name=selected_skill_name,
                )
                if not prefix:
                    prefix = _derive_pr_branch_prefix(
                        task_payload=parameter_payload,
                        publish_payload=publish_payload,
                        selected_skill_name=selected_skill_name,
                    )

                node_inputs["targetBranch"] = _generate_runtime_pr_branch(prefix)
            if not _coerce_non_empty_text(node_inputs.get("targetBranch")):
                raise RuntimeError(
                    "publishMode 'pr' requested but no PR head branch could be "
                    "resolved from workspace metadata or runtime planner generation"
                )

        # --- Assemble plan ---
        pr_resolver_title = _derive_pr_resolver_title(
            task_payload,
            selected_skill_inputs,
        )
        title = str(
            task_payload.get("title")
            or parameter_payload.get("title")
            or pr_resolver_title
            or ""
        ).strip() or "Generated Plan"
        created_at = (
            datetime.now(tz=UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        failure_mode = str(
            parameter_payload.get("failurePolicy") or "FAIL_FAST"
        ).strip()
        if failure_mode not in {"FAIL_FAST", "CONTINUE"}:
            failure_mode = "FAIL_FAST"

        explicit_plan = task_payload.get("plan")
        if isinstance(explicit_plan, list) and explicit_plan:
            nodes: list[dict[str, Any]] = []
            node_ids: set[str] = set()
            for idx, plan_entry in enumerate(explicit_plan, start=1):
                if not isinstance(plan_entry, Mapping):
                    raise RuntimeError("task.plan entries must be objects")
                has_authored_tool = isinstance(plan_entry.get("tool"), Mapping)
                tool_payload = _coerce_mapping(
                    plan_entry.get("tool")
                ) or _coerce_mapping(plan_entry.get("skill"))
                if not tool_payload:
                    raise RuntimeError("task.plan entries require a tool object")
                authored_tool_type = str(
                    tool_payload.get("type")
                    or tool_payload.get("kind")
                    or "agent_runtime"
                ).strip().lower() or "agent_runtime"
                authored_tool_name = str(
                    tool_payload.get("name") or tool_payload.get("id") or ""
                ).strip()
                if not authored_tool_name:
                    raise RuntimeError("task.plan tool name is required")
                is_legacy_agent_skill_plan_entry = not has_authored_tool
                tool_type = (
                    "agent_runtime"
                    if is_legacy_agent_skill_plan_entry
                    else authored_tool_type
                )
                tool_name = (
                    runtime_mode
                    if is_legacy_agent_skill_plan_entry
                    else authored_tool_name
                )
                node_inputs = _coerce_mapping(plan_entry.get("inputs"))
                if not node_inputs:
                    node_inputs = _coerce_mapping(
                        tool_payload.get("inputs") or tool_payload.get("args")
                    )
                if is_legacy_agent_skill_plan_entry:
                    node_inputs = {
                        "instructions": f"Execute skill '{authored_tool_name}'",
                        "runtime": dict(runtime_node),
                        "selectedSkill": authored_tool_name,
                        **dict(node_inputs),
                    }
                node_id = str(plan_entry.get("id") or f"node-{idx}").strip()
                if not node_id:
                    node_id = f"node-{idx}"
                if node_id in node_ids:
                    raise RuntimeError(f"task.plan duplicate node id: {node_id}")
                node_ids.add(node_id)
                node: dict[str, Any] = {
                    "id": node_id,
                    "tool": {
                        "type": tool_type,
                        "name": tool_name,
                    },
                    "inputs": dict(node_inputs),
                }
                options = _coerce_mapping(plan_entry.get("options"))
                if options:
                    node["options"] = dict(options)
                for key, value in plan_entry.items():
                    if key in {"id", "tool", "skill", "inputs", "options"}:
                        continue
                    node[str(key)] = deepcopy(value)
                nodes.append(node)

            explicit_edges = task_payload.get("edges")
            edges = (
                [dict(edge) for edge in explicit_edges if isinstance(edge, Mapping)]
                if isinstance(explicit_edges, list)
                else []
            )
            return {
                "plan_version": "1.0",
                "metadata": {
                    "title": title,
                    "created_at": created_at,
                    "registry_snapshot": {
                        "digest": snapshot.digest,
                        "artifact_ref": snapshot.artifact_ref,
                    },
                },
                "policy": {"failure_mode": failure_mode, "max_concurrency": 1},
                "nodes": nodes,
                "edges": edges,
            }

        story_output_payload = _coerce_mapping(
            task_payload.get("storyOutput")
            or task_payload.get("story_output")
            or parameter_payload.get("storyOutput")
            or parameter_payload.get("story_output")
        )
        story_output_mode = str(
            story_output_payload.get("mode")
            or story_output_payload.get("target")
            or ""
        ).strip().lower()
        should_prepare_story_breakdown = bool(story_output_payload)
        creates_story_breakdown_artifact = False

        # --- Expand task.steps[] or stepCount into multiple plan nodes ---
        has_explicit_step_types = (
            isinstance(raw_steps, list)
            and any(isinstance(s, Mapping) and _selected_step_type(s) for s in raw_steps)
        )
        has_multi_steps = (
            isinstance(raw_steps, list)
            and (len(raw_steps) > 1 or has_explicit_step_types)
            and all(isinstance(s, Mapping) for s in raw_steps)
        )
        if has_multi_steps:
            step_tool_names = {
                _selected_step_tool_name(step).lower()
                for step in raw_steps
                if isinstance(step, Mapping)
            }
            creates_story_breakdown_artifact = bool(
                step_tool_names & _MOONSPEC_BREAKDOWN_TOOLS
            )
            if not story_output_payload:
                for step in raw_steps:
                    if not isinstance(step, Mapping):
                        continue
                    step_story_output = _coerce_mapping(
                        step.get("storyOutput") or step.get("story_output")
                    )
                    if step_story_output:
                        story_output_payload = dict(step_story_output)
                        story_output_mode = str(
                            story_output_payload.get("mode")
                            or story_output_payload.get("target")
                            or ""
                        ).strip().lower()
                        break
            should_prepare_story_breakdown = should_prepare_story_breakdown or bool(
                step_tool_names & (_STORY_OUTPUT_TASK_TOOLS | _MOONSPEC_BREAKDOWN_TOOLS)
            )
        elif selected_skill_name:
            creates_story_breakdown_artifact = (
                selected_skill_name.lower() in _MOONSPEC_BREAKDOWN_TOOLS
            )
            should_prepare_story_breakdown = (
                should_prepare_story_breakdown
                or creates_story_breakdown_artifact
            )

        if should_prepare_story_breakdown:
            story_paths = _story_breakdown_paths(
                title=title,
                existing={**story_output_payload, **node_inputs},
            )
            node_inputs.update(story_paths)
            if (
                story_output_mode == "jira"
                and creates_story_breakdown_artifact
                and not node_inputs.get("targetBranch")
            ):
                if node_inputs.get("branch") and not node_inputs.get("startingBranch"):
                    node_inputs["startingBranch"] = node_inputs["branch"]
                prefix = _derive_pr_branch_prefix(
                    task_payload=task_payload,
                    publish_payload=publish_payload,
                    selected_skill_name=selected_skill_name,
                ) or "story-breakdown"
                node_inputs["targetBranch"] = _generate_runtime_pr_branch(prefix)
            story_output_payload = dict(story_output_payload)
            story_output_payload.setdefault("mode", story_output_mode or "docs_tmp")
            if story_output_mode == "jira" and creates_story_breakdown_artifact:
                story_output_payload.setdefault("handoff", "artifact")
                story_output_payload.setdefault(
                    "requiresStoryBreakdownArtifactRef",
                    True,
                )
            story_output_payload.update(story_paths)
            node_inputs["storyOutput"] = story_output_payload

        # If no explicit steps but stepCount > 1, synthesise N identical
        # nodes for runtimes that still execute sequentially step-by-step.
        # Jules is excluded because the workflow now bundles standard
        # multi-step work into one one-shot execution brief instead of
        # chaining provider follow-up messages.
        effective_step_count = node_inputs.get("stepCount")
        expand_step_count = (
            not has_multi_steps
            and isinstance(effective_step_count, int)
            and effective_step_count > 1
            and str(runtime_mode).strip().lower() not in JULES_AGENT_IDS
        )

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []

        if has_multi_steps:
            prev_step_id: str | None = None
            for idx, step_entry in enumerate(raw_steps):
                step_instructions = str(step_entry.get("instructions") or "").strip()
                if not step_instructions:
                    step_instructions = instructions  # fall back to task-level

                step_id = str(step_entry.get("id") or "").strip() or f"step-{idx + 1}"
                base_runtime_payload = _coerce_mapping(node_inputs.get("runtime"))
                step_runtime_payload = _coerce_mapping(step_entry.get("runtime"))
                step_tool_inputs = _selected_step_tool_inputs(step_entry)

                # Per-step tool/skill override
                step_tool_name = _selected_step_tool_name(step_entry)
                has_explicit_step_skill = _has_explicit_step_skill_name(
                    step_tool_name
                )
                tool_type = _selected_step_tool_type(step_entry)
                is_agent_runtime_step = tool_type == "agent_runtime"
                step_metadata_inputs = _authored_step_metadata_inputs(
                    step_entry,
                    include_type=is_agent_runtime_step or "source" in step_entry,
                )
                step_skill_evidence: dict[str, Any] = {}
                if is_agent_runtime_step:
                    step_tool_inputs, step_skill_evidence = _validated_step_skill_inputs(
                        step_entry=step_entry,
                        step_index=idx,
                        raw_inputs=step_tool_inputs,
                        workflow_context={
                            **parameter_payload,
                            **_coerce_mapping(parameter_payload.get("context")),
                            **git_payload,
                        },
                    )
                step_extra_inputs = {
                    k: v
                    for k, v in step_entry.items()
                    if k
                    not in {
                        "id",
                        "type",
                        "tool",
                        "skill",
                        "instructions",
                        "runtime",
                        "dependsOn",
                        "depends_on",
                        "dependencies",
                    }
                }
                if is_agent_runtime_step:
                    step_node_inputs: dict[str, Any] = {
                        **node_inputs,
                        **step_extra_inputs,
                        "instructions": step_instructions,
                    }
                    if not has_explicit_step_skill:
                        _drop_inherited_skill_context(step_node_inputs)
                    step_node_inputs.update(step_metadata_inputs)
                    if base_runtime_payload or step_runtime_payload:
                        step_node_inputs["runtime"] = {
                            **base_runtime_payload,
                            **step_runtime_payload,
                        }
                    base_story_output = _coerce_mapping(node_inputs.get("storyOutput"))
                    step_story_output = _coerce_mapping(
                        step_entry.get("storyOutput") or step_entry.get("story_output")
                    )
                    if base_story_output or step_story_output:
                        step_node_inputs["storyOutput"] = {
                            **base_story_output,
                            **step_story_output,
                        }
                    for key, value in step_tool_inputs.items():
                        step_node_inputs.setdefault(key, value)
                    if step_tool_inputs:
                        step_node_inputs["inputs"] = dict(step_tool_inputs)
                    step_node_inputs.update(step_skill_evidence)
                else:
                    step_node_inputs = (
                        _direct_story_tool_context_inputs(node_inputs)
                        if _story_output_task_tool_selected(step_tool_name)
                        else {}
                    )
                    step_node_inputs.update(dict(step_tool_inputs))
                    for key, value in step_metadata_inputs.items():
                        if key in {"storyOutput", "story_output"}:
                            step_node_inputs["storyOutput"] = _merge_story_output_inputs(
                                _coerce_mapping(step_node_inputs.get("storyOutput")),
                                value,
                            )
                            continue
                        step_node_inputs[key] = value
                    if step_tool_name.lower() in _MOONSPEC_BREAKDOWN_TOOLS:
                        step_node_inputs.setdefault("instructions", step_instructions)
                step_runtime_payload = _coerce_mapping(step_node_inputs.get("runtime"))
                step_runtime_raw = (
                    step_runtime_payload.get("mode")
                    or step_runtime_payload.get("targetRuntime")
                    or runtime_mode
                )
                step_runtime = (
                    _normalize_runtime_mode(step_runtime_raw)
                    if step_runtime_raw is not None
                    else None
                )
                is_story_output_tool = (
                    step_tool_name.lower() in _STORY_OUTPUT_TASK_TOOLS
                )
                effective_step_skill_name = (
                    step_tool_name
                    if is_agent_runtime_step and has_explicit_step_skill
                    else ""
                )
                if effective_step_skill_name.lower() != "pr-resolver":
                    step_node_inputs.pop("timeoutPolicy", None)
                if is_agent_runtime_step and has_explicit_step_skill:
                    step_node_inputs["selectedSkill"] = step_tool_name
                    step_skill_payload = _coerce_mapping(step_entry.get("skill"))
                    if step_skill_payload:
                        step_node_inputs["skill"] = _normalized_agent_skill_payload(
                            step_skill_payload,
                            selected_skill_name=step_tool_name,
                            selected_skill_inputs=step_tool_inputs,
                        )
                    if (
                        _jira_agent_skill_selected(step_tool_name)
                        and step_tool_name.lower() not in _MOONSPEC_BREAKDOWN_TOOLS
                    ):
                        step_node_inputs["publishMode"] = "none"
                if effective_step_skill_name:
                    step_node_inputs["instructions"] = _append_agent_skill_instructions(
                        step_node_inputs["instructions"],
                        selected_skill=effective_step_skill_name,
                    )
                    step_node_inputs["instructions"] = _append_selected_skill_inputs(
                        step_node_inputs["instructions"],
                        step_tool_inputs,
                    )
                if step_tool_name.lower() in _MOONSPEC_BREAKDOWN_TOOLS:
                    if (
                        story_output_mode == "jira"
                        and _requires_branch_publish_for_story_output(
                            step_node_inputs.get("publishMode")
                        )
                    ):
                        step_node_inputs["publishMode"] = "branch"
                    step_node_inputs["instructions"] = (
                        _append_story_breakdown_instructions(
                            step_node_inputs["instructions"],
                            story_breakdown_path=str(
                                step_node_inputs.get("storyBreakdownPath") or ""
                            ),
                            story_breakdown_markdown_path=str(
                                step_node_inputs.get("storyBreakdownMarkdownPath")
                                or ""
                            ),
                            story_output_mode=story_output_mode,
                        )
                    )
                if not is_agent_runtime_step:
                    step_node_inputs.pop("selectedSkill", None)
                if is_story_output_tool:
                    step_node_inputs["publishMode"] = "none"

                nodes.append({
                    "id": step_id,
                    "tool": {
                        "type": tool_type,
                        "name": step_runtime if is_agent_runtime_step else step_tool_name,
                    },
                    "inputs": step_node_inputs,
                })

                if prev_step_id:
                    edges.append({"from": prev_step_id, "to": step_id})
                prev_step_id = step_id
        elif expand_step_count:
            # Expand stepCount into N sequential nodes with the same
            # instructions for runtimes that still execute step-by-step.
            prev_step_id = None
            for idx in range(effective_step_count):
                expanded_inputs = dict(node_inputs)
                if selected_skill_name.lower() in _MOONSPEC_BREAKDOWN_TOOLS:
                    if (
                        story_output_mode == "jira"
                        and _requires_branch_publish_for_story_output(
                            expanded_inputs.get("publishMode")
                        )
                    ):
                        expanded_inputs["publishMode"] = "branch"
                    expanded_inputs["instructions"] = (
                        _append_story_breakdown_instructions(
                            str(expanded_inputs.get("instructions") or ""),
                            story_breakdown_path=str(
                                expanded_inputs.get("storyBreakdownPath") or ""
                            ),
                            story_breakdown_markdown_path=str(
                                expanded_inputs.get("storyBreakdownMarkdownPath") or ""
                            ),
                            story_output_mode=story_output_mode,
                        )
                    )
                expanded_inputs["instructions"] = _append_agent_skill_instructions(
                    str(expanded_inputs.get("instructions") or ""),
                    selected_skill=selected_skill_name,
                )
                if _jira_agent_skill_selected(selected_skill_name):
                    expanded_inputs["publishMode"] = "none"
                expanded_publish_mode = str(
                    expanded_inputs.get("publishMode") or ""
                ).strip().lower()
                if expanded_publish_mode in {"auto", "none"}:
                    expanded_inputs["instructions"] = (
                        str(expanded_inputs.get("instructions") or "")
                        + "\n\n"
                        + _publish_mode_agent_instructions(expanded_publish_mode)
                    )
                step_id = f"node-{idx + 1}"
                nodes.append({
                    "id": step_id,
                    "tool": {
                        "type": "agent_runtime",
                        "name": runtime_mode,
                    },
                    "inputs": expanded_inputs,
                })
                if prev_step_id:
                    edges.append({"from": prev_step_id, "to": step_id})
                prev_step_id = step_id
        else:
            node_id = str(task_payload.get("id") or "node-1").strip() or "node-1"
            selected_skill_lower = selected_skill_name.lower()
            is_story_output_tool = selected_skill_lower in _STORY_OUTPUT_TASK_TOOLS
            if selected_skill_lower in _MOONSPEC_BREAKDOWN_TOOLS:
                if (
                    story_output_mode == "jira"
                    and _requires_branch_publish_for_story_output(
                        node_inputs.get("publishMode")
                    )
                ):
                    node_inputs["publishMode"] = "branch"
                node_inputs["instructions"] = _append_story_breakdown_instructions(
                    str(node_inputs.get("instructions") or ""),
                    story_breakdown_path=str(
                        node_inputs.get("storyBreakdownPath") or ""
                    ),
                    story_breakdown_markdown_path=str(
                        node_inputs.get("storyBreakdownMarkdownPath") or ""
                    ),
                    story_output_mode=story_output_mode,
                )
            node_inputs["instructions"] = _append_agent_skill_instructions(
                str(node_inputs.get("instructions") or ""),
                selected_skill=selected_skill_name,
            )
            node_tool_type = "skill" if is_story_output_tool else "agent_runtime"
            node_tool_name = (
                selected_skill_name if is_story_output_tool else runtime_mode
            )
            if is_story_output_tool:
                node_inputs.pop("selectedSkill", None)
                node_inputs["publishMode"] = "none"
            node_publish_mode = str(node_inputs.get("publishMode") or "").strip().lower()
            if node_publish_mode in {"auto", "none"} and not is_story_output_tool:
                node_inputs["instructions"] = (
                    str(node_inputs.get("instructions") or "")
                    + "\n\n"
                    + _publish_mode_agent_instructions(node_publish_mode)
                )
            nodes.append({
                "id": node_id,
                "tool": {
                    "type": node_tool_type,
                    "name": node_tool_name,
                },
                "inputs": node_inputs,
            })

        # Append PR creation instructions to the last node so CLI-based agents
        # create the PR in the same workspace where the changes were made.
        # Skip Jules: session creation uses Jules API ``automationMode`` =
        # ``AUTO_CREATE_PR`` when ``publishMode`` is ``pr`` or ``branch``
        # (see ``JulesAgentAdapter.do_start``), not shell instructions.
        publish_requested = (
            isinstance(publish_mode, str)
            and publish_mode.strip().lower() in ("pr", "branch")
            and publish_uses_git
        )
        if publish_requested or story_output_mode == "jira":
            if story_output_mode == "jira":
                publish_node = next(
                    (
                        node
                        for node in nodes
                        if str(
                            node.get("tool", {}).get("type") or ""
                        ).strip().lower()
                        == "agent_runtime"
                        and _plan_node_selected_skill(node).lower()
                        in _MOONSPEC_BREAKDOWN_TOOLS
                    ),
                    nodes[-1],
                )
            else:
                publish_node = next(
                    (
                        node
                        for node in reversed(nodes)
                        if str(
                            node.get("tool", {}).get("type") or ""
                        ).strip().lower()
                        == "agent_runtime"
                        and not _jira_agent_skill_selected(
                            _plan_node_selected_skill(node)
                        )
                    ),
                    nodes[-1],
                )
            publish_tool = str(
                publish_node.get("tool", {}).get("name") or ""
            ).strip().lower()
            publish_tool_type = str(
                publish_node.get("tool", {}).get("type") or ""
            ).strip().lower()
            publish_selected_skill = _plan_node_selected_skill(publish_node)
            if (
                publish_tool_type == "agent_runtime"
                and publish_tool not in _TOOLS_WITH_AUTO_PR_CREATION
                and (
                    not _jira_agent_skill_selected(publish_selected_skill)
                    or publish_selected_skill.lower() in _MOONSPEC_BREAKDOWN_TOOLS
                )
                and not _is_explicit_pr_handoff_node(
                    publish_node,
                    task_payload=task_payload,
                )
            ):
                publish_inputs = publish_node["inputs"]
                if (
                    story_output_mode == "jira"
                    and _requires_branch_publish_for_story_output(
                        publish_inputs.get("publishMode")
                    )
                ):
                    publish_inputs["publishMode"] = "branch"
                commit_suffix = "\n\n" + _publish_mode_agent_instructions(
                    publish_inputs.get("publishMode")
                )
                publish_inputs["instructions"] = (
                    publish_inputs["instructions"] + commit_suffix
                )

        return {
            "plan_version": "1.0",
            "metadata": {
                "title": title,
                "created_at": created_at,
                "registry_snapshot": {
                    "digest": snapshot.digest,
                    "artifact_ref": snapshot.artifact_ref,
                },
            },
            "policy": {"failure_mode": failure_mode, "max_concurrency": 1},
            "nodes": nodes,
            "edges": edges,
        }

    return _runtime_planner


def _publish_mode_agent_instructions(publish_mode: object) -> str:
    mode = str(publish_mode or "none").strip().lower()
    if mode == "auto":
        return (
            "Publishing is in auto mode. Determine the correct publish action "
            "for this task. You may commit, push, or merge only when required "
            "by the selected skill. Write artifacts/publish_result.json proving "
            "the outcome before reporting success."
        )
    if mode in {"branch", "pr"}:
        return (
            "After completing the changes above, commit your work "
            "(`git add -A && git commit -m '<summary>'`). "
            "Do NOT push or create a pull request - that is handled automatically."
        )
    return "Do NOT commit or push. Publishing is disabled for this task."

def _csv_env_tuple(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(
        dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
    )

def _positive_int_env(name: str) -> int | None:
    import os

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = int(raw)
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value

def _pentest_runner_image_overrides() -> dict[str, str]:
    pentest = settings.pentest
    return {
        pentest.claude_oauth_runner_profile_id: pentest.runner_image,
    }

def _container_job_evidence_content_type(name: str) -> str:
    """Pick a stable media type for a container-job evidence artifact name."""

    lowered = name.lower()
    if lowered.endswith((".json", ".jsonl")):
        return "application/json"
    if lowered.endswith((".tar.gz", ".tgz")):
        return "application/gzip"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _container_job_evidence_publisher(artifact_service: TemporalArtifactService):
    async def publish(request, name: str, payload: bytes) -> str:
        principal = f"{request.owner.principal_type}:{request.owner.principal_id}"
        content_type = _container_job_evidence_content_type(name)
        artifact, _ = await artifact_service.create(
            principal=principal,
            content_type=content_type,
            metadata_json={
                "artifact_type": "container_job.evidence",
                "name": name,
                "container_job_id": request.job_id,
            },
        )
        await artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=payload,
            content_type=content_type,
        )
        return artifact.artifact_id
    return publish

def _container_job_projection_writer(backend_kind: str, backend_ref: str):
    """Build a projection writer bound to the deployment-selected backend."""

    async def _write(request) -> None:
        """Persist workflow projections in the API-owned job record."""
        async with get_async_session_context() as session:
            result = await session.execute(
                select(ContainerJobRecord).where(
                    ContainerJobRecord.job_id == request.job_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise RuntimeError(f"container job record not found: {request.job_id}")
            state_value = request.state or request.terminal_state
            record.state = (
                state_value.value if hasattr(state_value, "value") else state_value
            ) or record.state
            record.backend_kind = backend_kind
            record.backend_ref = backend_ref
            if request.image_observation is not None:
                # Persist the exact, backend-scoped observation produced by the
                # trusted acquisition service; never fabricate cache/pull evidence.
                record.image_observation_json = request.image_observation.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            elif request.resolved_image_ref:
                record.image_observation_json = {
                    "requestedReference": request.request.spec.image,
                    "resolvedDigest": request.resolved_image_ref
                    if request.resolved_image_ref.startswith("sha256:") else None,
                    "cachePresent": True,
                    "cacheHit": True,
                    "pullLockWaitMs": 0,
                }
            if (
                request.exit_code is not None
                or request.failure_class
                or request.message
            ):
                record.terminal_outcome_json = {
                    "exitCode": request.exit_code,
                    "failureClass": request.failure_class,
                    "message": request.message,
                }
            if request.publication is not None:
                record.publication_outcome_json = request.publication.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            if request.cleanup_outcome is not None:
                record.cleanup_outcome_json = request.cleanup_outcome.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            if request.logs_ref:
                record.logs_ref = request.logs_ref
            if request.artifacts_ref:
                record.artifacts_ref = request.artifacts_ref
            if request.events_ref:
                record.events_ref = request.events_ref
            # Compact, non-sensitive execution observations. Only write when the
            # trusted backend produced them so intermediate projections never
            # clobber earlier timing/probe evidence with nulls.
            if request.workspace_probe is not None:
                record.workspace_probe = request.workspace_probe
            if request.started_at is not None:
                record.started_at = request.started_at
            if request.finished_at is not None:
                record.completed_at = request.finished_at
            if request.duration_ms is not None:
                record.duration_seconds = request.duration_ms / 1000.0
            await session.commit()

    return _write


def _container_job_secret_resolver():
    """Materialize an execution-time secret reference to plaintext.

    Plaintext is returned only for injection into the container launch
    environment and is never persisted or rendered into diagnostics/evidence.
    """

    from moonmind.workflows.adapters.secret_boundary import DatabaseSecretResolver

    async def _resolve(secret_ref: str) -> str:
        async with get_async_session_context() as session:
            resolved = await DatabaseSecretResolver(session).resolve_secrets(
                {"value": secret_ref}
            )
        if "value" not in resolved:
            raise RuntimeError(
                "execution-time secret reference could not be resolved"
            )
        return resolved["value"]

    return _resolve

def _build_agent_runtime_deps(
    *,
    artifact_service: Any | None = None,
) -> tuple[
    ManagedRunStore,
    ManagedRunSupervisor,
    ManagedRuntimeLauncher,
    DockerCodexManagedSessionController,
    Any,
    Any,
    ManagedSessionStore,
]:
    """Build shared runtime dependencies for the ``agent_runtime`` fleet."""
    import os
    from pathlib import Path
    from moonmind.workloads import (
        DockerWorkloadConcurrencyLimiter,
        DockerWorkloadLauncher,
        RunnerProfileRegistry,
    )

    class LocalRuntimeArtifactStorage:
        def __init__(self, root: str) -> None:
            self._root = Path(root)

        def write_artifact(
            self, *, job_id: str, artifact_name: str, data: bytes
        ) -> tuple[Path, str]:
            target_dir = self._root / job_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / artifact_name
            target.write_bytes(data)
            return target, f"{job_id}/{artifact_name}"

        def resolve_storage_path(self, ref: str) -> Path:
            return self._root / ref

    store_root = os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
        "managed_runs",
    )
    artifact_root = str(managed_runtime_artifact_root())
    os.makedirs(store_root, exist_ok=True)
    os.makedirs(artifact_root, exist_ok=True)

    store = ManagedRunStore(store_root)
    artifact_storage = LocalRuntimeArtifactStorage(artifact_root)
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)
    launcher = ManagedRuntimeLauncher(
        store,
        log_streamer=log_streamer,
        artifact_service=artifact_service,
    )
    session_store = ManagedSessionStore(
        os.path.join(
            os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
            "managed_sessions",
        )
    )
    session_log_streamer = RuntimeLogStreamer(artifact_storage)
    session_supervisor = ManagedSessionSupervisor(
        store=session_store,
        log_streamer=session_log_streamer,
        artifact_storage=artifact_storage,
    )
    workspace_root = os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
    workspace_volume_name = os.environ.get(
        "MOONMIND_AGENT_WORKSPACES_VOLUME_NAME",
        "agent_workspaces",
    )
    codex_volume_name = (
        settings.workflow.codex_volume_name
        or os.environ.get("CODEX_VOLUME_NAME")
        or "codex_auth_volume"
    )
    docker_host = (
        os.environ.get("DOCKER_HOST")
        or os.environ.get("SYSTEM_DOCKER_HOST")
        or "tcp://docker-proxy:2375"
    )
    session_moonmind_url = (
        os.environ.get("MOONMIND_MANAGED_SESSION_MOONMIND_URL")
        or os.environ.get("MOONMIND_URL")
        or "http://api:8000"
    ).strip() or None
    session_network_name = _managed_session_docker_network(
        {"MOONMIND_URL": session_moonmind_url or ""}
    )
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    temporal_client_adapter = TemporalClientAdapter()

    async def _owner_workflow_status_resolver(workflow_id: str) -> object | None:
        description = await temporal_client_adapter.describe_workflow(workflow_id)
        return getattr(description, "status", None)

    session_controller = DockerCodexManagedSessionController(
        workspace_volume_name=workspace_volume_name,
        codex_volume_name=codex_volume_name,
        workspace_root=workspace_root,
        network_name=session_network_name,
        moonmind_url=session_moonmind_url,
        session_store=session_store,
        session_supervisor=session_supervisor,
        docker_binary=os.environ.get("MOONMIND_DOCKER_BINARY", "docker"),
        docker_host=docker_host,
        owner_workflow_status_resolver=_owner_workflow_status_resolver,
    )
    workload_registry_path = os.environ.get("MOONMIND_WORKLOAD_PROFILE_REGISTRY", "")
    allowed_image_registries = _csv_env_tuple(
        os.environ.get("MOONMIND_WORKLOAD_ALLOWED_IMAGE_REGISTRIES")
    )
    if workload_registry_path.strip():
        workload_registry = RunnerProfileRegistry.load_file(
            workload_registry_path,
            workspace_root=workspace_root,
            allowed_image_registries=allowed_image_registries or None,
            profile_image_overrides=_pentest_runner_image_overrides(),
        )
    else:
        default_workload_registry = (
            Path(__file__).resolve().parents[3]
            / "config"
            / "workloads"
            / "default-runner-profiles.yaml"
        )
        workload_registry = RunnerProfileRegistry.load_file(
            default_workload_registry,
            workspace_root=workspace_root,
            allowed_image_registries=allowed_image_registries or None,
            profile_image_overrides=_pentest_runner_image_overrides(),
        )
    workload_fleet_limit = _positive_int_env("MOONMIND_DOCKER_WORKLOAD_FLEET_CONCURRENCY")
    workload_launcher = DockerWorkloadLauncher(
        docker_binary=os.environ.get("MOONMIND_DOCKER_BINARY", "docker"),
        docker_host=docker_host,
        concurrency_limiter=DockerWorkloadConcurrencyLimiter(
            fleet_limit=workload_fleet_limit
        ),
    )
    return (
        store,
        supervisor,
        launcher,
        session_controller,
        workload_registry,
        workload_launcher,
        session_store,
    )

async def _build_runtime_activities(topology) -> tuple[AsyncExitStack, list[object]]:
    """Build activity handlers for the configured non-workflow fleet.

    Agent execution is handled by MoonMind.AgentRun (a child workflow on the
    workflow fleet).  Plan generation and skill dispatch are handled by
    activity fleets (llm, sandbox, etc.).
    """
    resources = AsyncExitStack()
    container_job_backend = None
    enforced_network_refs: list[str] = []
    class ArtifactServiceProxy:
        def __getattr__(self, name):
            async def wrapper(*args, **kwargs):
                async with get_async_session_context() as session:
                    service = TemporalArtifactService(TemporalArtifactRepository(session))
                    func = getattr(service, name)
                    return await func(*args, **kwargs)
            return wrapper

    try:
        artifact_service = ArtifactServiceProxy()  # type: ignore
        sandbox_activities = TemporalSandboxActivities(artifact_service=artifact_service)
        planner = _build_runtime_planner()

        dispatcher = SkillActivityDispatcher()

        async def _read_story_output_artifact(artifact_ref: str) -> bytes:
            _artifact, payload = await artifact_service.read(
                artifact_id=artifact_ref,
                principal="system:story_output",
                allow_restricted_raw=True,
            )
            return payload

        register_story_output_tool_handlers(
            dispatcher,
            execution_creator=_build_jira_orchestrate_execution_creator(),
            artifact_reader=_read_story_output_artifact,
        )

        run_store = None
        run_supervisor = None
        run_launcher = None
        session_controller = None
        workload_registry = None
        workload_launcher = None
        session_store = None
        agent_runtime_activities = None
        if topology.fleet == AGENT_RUNTIME_FLEET:
            # Docker-backed managed-session reconciliation only belongs on the
            # agent_runtime fleet, which owns the required privileges.
            (
                run_store,
                run_supervisor,
                run_launcher,
                session_controller,
                workload_registry,
                workload_launcher,
                session_store,
            ) = _build_agent_runtime_deps(artifact_service=artifact_service)
            reconciled = await run_supervisor.reconcile()
            if reconciled:
                logger.info(
                    "Reconciled %d stale managed run records during startup",
                    len(reconciled),
                )
            session_reconciled = await session_controller.reconcile()
            if session_reconciled:
                logger.info(
                    "Reconciled %d managed session records during startup",
                    len(session_reconciled),
                )
            try:
                reap_result = await session_controller.reap_orphan_session_containers()
            except Exception:
                logger.warning(
                    "Managed session orphan sweep failed during agent_runtime startup",
                    exc_info=True,
                )
            else:
                reaped_containers = int(
                    getattr(reap_result, "reaped_containers", 0) or 0
                )
                reaped_volumes = int(getattr(reap_result, "reaped_volumes", 0) or 0)
                if reaped_containers or reaped_volumes:
                    logger.info(
                        "Reaped managed session orphans during startup: "
                        "%d container(s), %d volume(s)",
                        reaped_containers,
                        reaped_volumes,
                    )
            container_backend_settings = resolve_container_backend_settings()
            _container_job_store = os.environ.get(
                "MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"
            )
            container_job_backend = DockerContainerJobBackend(
                workspace_root=_container_job_store,
                settings=container_backend_settings,
                backend_ref=container_backend_settings.default_backend_ref,
                docker_binary=os.environ.get("MOONMIND_DOCKER_BINARY", "docker"),
                evidence_publisher=_container_job_evidence_publisher(artifact_service),
                projection_writer=_container_job_projection_writer(
                    container_backend_settings.kind,
                    container_backend_settings.default_backend_ref,
                ),
                secret_resolver=_container_job_secret_resolver(),
                managed_run_store=run_store,
                workspace_volume_name=os.environ.get(
                    "MOONMIND_AGENT_WORKSPACES_VOLUME_NAME", "agent_workspaces"
                ),
                # Publish bounded live incremental logs to a MoonMind-controlled
                # spool root that is never mounted into a job container.
                log_spool_root=os.environ.get(
                    "MOONMIND_CONTAINER_JOB_LOG_SPOOL_ROOT"
                )
                or str(
                    Path(_container_job_store).resolve().parent
                    / ".mm-container-job-logs"
                ),
            )
            if container_backend_settings.enabled:
                # Fail fast at startup when the deployment-selected endpoint is
                # missing or unreachable rather than at first job launch.
                await container_job_backend.check_readiness()
                from moonmind.omnigent.execution_profiles import POLICIES

                enforced_network_refs = []
                for policy in POLICIES.values():
                    if (
                        policy.enabled
                        and policy.enforced_egress
                        and await container_job_backend.network_ready(policy.network_ref)
                    ):
                        enforced_network_refs.append(policy.network_ref)
            else:
                container_job_backend = None
                enforced_network_refs = []
            agent_runtime_activities = TemporalAgentRuntimeActivities(
                artifact_service=artifact_service,
                run_store=run_store,
                run_supervisor=run_supervisor,
                run_launcher=run_launcher,
                session_controller=session_controller,
                session_store=session_store,
                workload_registry=workload_registry,
                workload_launcher=workload_launcher,
                workflow_docker_mode=settings.workflow.workflow_docker_mode,
                raw_docker_cli_enabled=container_backend_settings.raw_cli_enabled,
                container_job_backend=container_job_backend,
            )
        if topology.fleet == DEPLOYMENT_FLEET:
            register_deployment_update_tool_handler(
                dispatcher,
                executor=_build_deployment_update_executor(),
            )
            register_ops_diagnose_stack_tool_handler(
                dispatcher,
                executor=_build_ops_diagnosis_executor(),
            )

        bindings = build_worker_activity_bindings(
            fleet=topology.fleet,
            artifact_activities=TemporalArtifactActivities(artifact_service),
            plan_activities=TemporalPlanActivities(
                artifact_service=artifact_service,
                planner=planner,
            ),
            manifest_activities=TemporalManifestActivities(
                artifact_service=artifact_service,
            ),
            skill_activities=TemporalSkillActivities(
                dispatcher=dispatcher,
                artifact_service=artifact_service,
            ),
            sandbox_activities=sandbox_activities,
            integration_activities=TemporalIntegrationActivities(
                artifact_service=artifact_service
            ),
            agent_runtime_activities=agent_runtime_activities,
            proposal_activities=TemporalProposalActivities(
                artifact_service=artifact_service,
                proposal_service_factory=_build_proposal_service_factory(),
            ),
            review_activities=TemporalReviewActivities(),
            agent_skills_activities=AgentSkillsActivities(
                artifact_service=artifact_service,
                async_session_maker=get_async_session_context,
            ),
        )
        binding_descriptors = sorted(
            f"{binding.activity_type}->{binding.task_queue}" for binding in bindings
        )
        logger.info(
            "Temporal activity bindings for fleet %s: %s",
            topology.fleet,
            ", ".join(binding_descriptors) if binding_descriptors else "(none)",
        )
        resources.container_job_backend = container_job_backend  # type: ignore[attr-defined]
        resources.enforced_network_refs = tuple(enforced_network_refs)  # type: ignore[attr-defined]
        return resources, [
            binding.handler for binding in bindings
        ] + [
            resolve_adapter_metadata,
            get_activity_route,
            resolve_external_adapter,
            external_adapter_execution_style,
        ]
    except Exception:
        await resources.aclose()
        raise

def _worker_concurrency_kwargs(topology) -> dict[str, int]:
    if topology.concurrency_limit is None:
        return {}
    if topology.fleet == WORKFLOW_FLEET:
        return {"max_concurrent_workflow_tasks": topology.concurrency_limit}
    return {"max_concurrent_activities": topology.concurrency_limit}

def _enforce_codex_config_for_managed_fleet(fleet: str) -> None:
    """Apply Codex managed-runtime defaults for fleets that launch CLI tasks."""

    normalized = str(fleet or "").strip().lower()
    if normalized not in _CODEX_CONFIG_FLEETS:
        return

    from api_service.scripts.ensure_codex_config import (
        CodexConfigError,
        ensure_codex_config,
    )

    try:
        result = ensure_codex_config()
    except CodexConfigError as exc:
        raise RuntimeError(
            "Codex configuration enforcement failed for worker fleet "
            f"{normalized}: {exc}"
        ) from exc

    logger.info(
        "Codex managed defaults enforced for fleet %s at %s",
        normalized,
        result.path,
    )

async def main_async() -> None:
    """Run the Temporal worker."""
    topology = describe_configured_worker()
    _enforce_codex_config_for_managed_fleet(topology.fleet)

    logger.info(
        f"Starting {topology.service_name} [{topology.fleet}] "
        f"queues={','.join(topology.task_queues)} "
        f"concurrency={topology.concurrency_limit}"
    )

    # Liveness starts immediately. Readiness remains false until the Temporal
    # connection, executable spec, SDK workers, and polling tasks all exist.
    health_state = WorkerHealthState()
    healthcheck_server = await start_healthcheck_server(health_state)

    import os
    interceptors = []
    runtime = None
    if os.environ.get("MOONMIND_ENABLE_OPENTELEMETRY", "0") == "1":
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry import trace
            from temporalio.contrib.opentelemetry import TracingInterceptor

            if not isinstance(trace.get_tracer_provider(), TracerProvider):
                resource = Resource.create({"service.name": topology.service_name})
                trace.set_tracer_provider(TracerProvider(resource=resource))
            interceptors.append(TracingInterceptor())

            # Setup Prometheus metrics for worker health and queue polling behavior.
            # Bind address can be configured via MOONMIND_PROMETHEUS_BIND_ADDRESS.
            # Default to localhost-only to avoid exposing metrics on all interfaces.
            prometheus_bind_address = os.environ.get(
                "MOONMIND_PROMETHEUS_BIND_ADDRESS",
                "127.0.0.1:9090",
            )
            runtime = Runtime(
                telemetry=TelemetryConfig(
                    metrics=PrometheusConfig(bind_address=prometheus_bind_address)
                )
            )

            logger.info(
                "OpenTelemetry tracing enabled for Temporal worker with "
                "service.name=%s.",
                topology.service_name,
            )
        except ImportError as e:
            logger.warning(f"OpenTelemetry tracing requested but failed to initialize: {e}")

    client_kwargs = {
        "namespace": settings.temporal.namespace,
        "data_converter": pydantic_data_converter,
        "interceptors": interceptors,
    }
    if runtime:
        client_kwargs["runtime"] = runtime

    try:
        client = await Client.connect(settings.temporal.address, **client_kwargs)
        health_state.temporal_connected = True
    except Exception as exc:
        health_state.startup_error = exc.__class__.__name__
        raise

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = workflow_fleet_workflow_classes()
        activities = workflow_fleet_activity_handlers()
        logger.info(
            "Temporal workflow fleet registrations: %s",
            ", ".join(list_registered_workflow_types()),
        )
    else:
        runtime_resources, activities = await _build_runtime_activities(topology)

    try:
        spec = build_worker_spec(
            topology=topology,
            workflows=workflows,
            activities=activities,
        )
        worker_kwargs = {
            "workflows": spec.workflows,
            "activities": spec.activities,
            "workflow_runner": UnsandboxedWorkflowRunner(),
            **_worker_concurrency_kwargs(topology),
        }
        if spec.versioning_enabled:
            worker_kwargs["deployment_config"] = WorkerDeploymentConfig(
                version=WorkerDeploymentVersion(
                    deployment_name=spec.deployment_id,
                    build_id=spec.build_id,
                ),
                use_worker_versioning=True,
                default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
            )
        workers = [
            Worker(
                client,
                task_queue=task_queue,
                **worker_kwargs,
            )
            for task_queue in topology.task_queues
        ]
        health_state.workers_constructed = True
        health_state.readiness_metadata = spec.readiness_payload()
        if topology.fleet == AGENT_RUNTIME_FLEET:
            container_job_backend = getattr(
                runtime_resources, "container_job_backend", None
            )
            enforced_network_refs = getattr(
                runtime_resources, "enforced_network_refs", ()
            )
            health_state.readiness_metadata["containerBackend"] = {
                "ready": container_job_backend is not None,
                "enforcedNetworkRefs": sorted(set(enforced_network_refs)),
            }

        logger.info(
            "Temporal executable worker specification: %s",
            json.dumps(spec.readiness_payload(), sort_keys=True),
        )
        async with asyncio.TaskGroup() as tg:
            for worker in workers:
                tg.create_task(worker.run())
            await asyncio.sleep(0)
            health_state.pollers_started = True
            logger.info(
                "Worker ready, polling task queues: %s",
                ", ".join(topology.task_queues),
            )
    except Exception as exc:
        health_state.startup_error = exc.__class__.__name__
        raise
    finally:
        if runtime_resources is not None:
            await runtime_resources.aclose()
        if healthcheck_server is not None:
            healthcheck_server.close()
            await healthcheck_server.wait_closed()

class OpenTelemetryLoggingFilter(logging.Filter):
    """Injects OpenTelemetry and Temporal trace context into standard logging."""

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self._default_fields = default_log_fields_from_env()

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in self._default_fields.items():
            setattr(record, key, value)
        record.trace_id = ""
        record.span_id = ""
        record.temporal_workflow_id = ""
        record.temporal_run_id = ""
        record.temporal_activity_id = ""
        for _, record_field in _MANAGED_SESSION_LOG_FIELD_MAP:
            setattr(record, record_field, "")

        # 1. OpenTelemetry trace/span IDs
        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            record.trace_id = otel_trace.format_trace_id(ctx.trace_id)
            record.span_id = otel_trace.format_span_id(ctx.span_id)

        # 2. Temporal execution context
        try:
            if temporalio.workflow.in_workflow():
                info = temporalio.workflow.info()
                record.temporal_workflow_id = info.workflow_id
                record.temporal_run_id = info.run_id
        except Exception:
            logging.debug("Failed to retrieve Temporal workflow context", exc_info=True)

        try:
            if temporalio.activity.in_activity():
                info = temporalio.activity.info()
                record.temporal_workflow_id = info.workflow_id
                record.temporal_run_id = info.workflow_run_id
                record.temporal_activity_id = info.activity_id
        except Exception:
            logging.debug("Failed to retrieve Temporal activity context", exc_info=True)

        managed_session = getattr(record, "managed_session", None)
        if isinstance(managed_session, Mapping):
            sanitized_managed_session = {}
            for context_key, record_field in _MANAGED_SESSION_LOG_FIELD_MAP:
                value = managed_session.get(context_key)
                normalized_value = None
                if isinstance(value, bool):
                    normalized_value = str(value).lower()
                elif isinstance(value, int) and not isinstance(value, bool):
                    normalized_value = str(value)
                elif isinstance(value, str) and value.strip():
                    normalized_value = value.strip()

                if normalized_value is not None:
                    setattr(record, record_field, normalized_value)
                    sanitized_managed_session[context_key] = normalized_value
            record.managed_session = sanitized_managed_session
        elif hasattr(record, "managed_session"):
            delattr(record, "managed_session")

        return True

def _configure_worker_logging(*, enable_opentelemetry: bool) -> None:
    configure_logging(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        structured=None,
        default_fields=default_log_fields_from_env(),
    )
    if not enable_opentelemetry:
        return

    for handler in logging.root.handlers:
        if not isinstance(handler.formatter, ProcessorFormatter):
            handler.setFormatter(logging.Formatter(_OPENTELEMETRY_LOG_FORMAT))
        if not any(
            isinstance(existing_filter, OpenTelemetryLoggingFilter)
            for existing_filter in handler.filters
        ):
            handler.addFilter(OpenTelemetryLoggingFilter())

if __name__ == "__main__":
    import os

    _configure_worker_logging(
        enable_opentelemetry=os.environ.get("MOONMIND_ENABLE_OPENTELEMETRY", "0") == "1"
    )
    asyncio.run(main_async())
