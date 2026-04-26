import contextlib
import re
import uuid

def _build_proposal_service_factory():
    from api_service.db.base import get_async_session_context
    from moonmind.workflows.task_proposals.repositories import TaskProposalRepository
    from moonmind.workflows.task_proposals.service import TaskProposalService

    @contextlib.asynccontextmanager
    async def factory():
        async with get_async_session_context() as db_session:
            yield TaskProposalService(
                TaskProposalRepository(db_session),
            )
    return factory

"""Temporal worker runtime entrypoint."""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import temporalio.activity
import temporalio.workflow
from opentelemetry import trace as otel_trace
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.skills.deployment_execution import (
    register_deployment_update_tool_handler,
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
from moonmind.workflows.temporal.workers import (
    AGENT_RUNTIME_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    describe_configured_worker,
    list_registered_workflow_types,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import MoonMindProviderProfileManagerWorkflow as MoonMindProviderProfileManager
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow as MoonMindManifestIngest,
)
from moonmind.workflows.temporal.jules_bundle import JULES_AGENT_IDS
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow as MoonMindRun
from moonmind.workflows.temporal.worker_healthcheck import start_healthcheck_server
from moonmind.workflows.temporal.workflows.agent_session import (
    MoonMindAgentSessionWorkflow as MoonMindAgentSession,
)
from moonmind.workflows.temporal.workflows.managed_session_reconcile import (
    MoonMindManagedSessionReconcileWorkflow as MoonMindManagedSessionReconcile,
)
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    resolve_adapter_metadata,
    get_activity_route,
    resolve_external_adapter,
    external_adapter_execution_style,
)
from moonmind.workflows.temporal.workflows.oauth_session import (
    MoonMindOAuthSessionWorkflow as MoonMindOAuthSession,
)
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow as MoonMindMergeAutomation,
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
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workloads.tool_bridge import register_workload_tool_handlers

logger = logging.getLogger(__name__)

_MANAGED_SESSION_LOG_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("taskRunId", "managed_session_task_run_id"),
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
    "[trace_id=%(trace_id)s span_id=%(span_id)s] "
    "[workflow_id=%(temporal_workflow_id)s run_id=%(temporal_run_id)s "
    "activity_id=%(temporal_activity_id)s] "
    "[managed_session_id=%(managed_session_id)s "
    "task_run_id=%(managed_session_task_run_id)s "
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

def _task_template_seed_dir() -> Path:
    import api_service

    return Path(api_service.__file__).resolve().parent / "data" / "task_step_templates"

def _template_slug_from_task(task_payload: Mapping[str, Any]) -> str:
    template_payload = _coerce_mapping(
        task_payload.get("taskTemplate") or task_payload.get("task_template")
    )
    return str(
        template_payload.get("slug")
        or template_payload.get("name")
        or template_payload.get("id")
        or ""
    ).strip()

async def _expand_task_template_for_child_run(
    *,
    session: Any,
    initial_parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Expand stored task-template provenance into executable child-run steps.

    Internal story-output tools create child executions directly, bypassing the
    API/UI submit path that normally expands presets. The stored run contract
    must still contain the flattened steps before ``MoonMind.Run`` plans it.
    """

    parameters = dict(initial_parameters or {})
    task_payload = _coerce_mapping(parameters.get("task"))
    if not task_payload:
        return parameters
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list) and raw_steps:
        return parameters

    template_payload = _coerce_mapping(
        task_payload.get("taskTemplate") or task_payload.get("task_template")
    )
    template_slug = _template_slug_from_task(task_payload)
    if not template_slug:
        return parameters

    from api_service.services.task_templates.catalog import (
        ExpandOptions,
        TaskTemplateCatalogService,
        TaskTemplateNotFoundError,
    )

    template_version = str(template_payload.get("version") or "1.0.0").strip()
    template_scope = str(template_payload.get("scope") or "global").strip() or "global"
    template_scope_ref = (
        str(template_payload.get("scopeRef") or template_payload.get("scope_ref") or "")
        .strip()
        or None
    )
    template_inputs = _coerce_mapping(task_payload.get("inputs"))
    catalog = TaskTemplateCatalogService(session)
    expand_kwargs = {
        "slug": template_slug,
        "scope": template_scope,
        "scope_ref": template_scope_ref,
        "version": template_version,
        "inputs": template_inputs,
        "context": {},
        "options": ExpandOptions(should_enforce_step_limit=True),
    }
    try:
        expanded = await catalog.expand_template(**expand_kwargs)
    except TaskTemplateNotFoundError:
        await catalog.sync_seed_templates(seed_dir=_task_template_seed_dir())
        expanded = await catalog.expand_template(**expand_kwargs)

    expanded_steps = expanded.get("steps") if isinstance(expanded, Mapping) else None
    if not isinstance(expanded_steps, list) or not expanded_steps:
        raise RuntimeError(
            f"Task template '{template_slug}' expansion produced no executable steps."
        )

    applied_template = _coerce_mapping(expanded.get("appliedTemplate"))
    task_payload["steps"] = list(expanded_steps)
    task_payload["appliedStepTemplates"] = [
        {
            "slug": str(applied_template.get("slug") or template_slug),
            "version": str(applied_template.get("version") or template_version),
            "inputs": _coerce_mapping(applied_template.get("inputs"))
            or template_inputs,
            "stepIds": list(applied_template.get("stepIds") or []),
            "appliedAt": str(applied_template.get("appliedAt") or ""),
            "capabilities": list(expanded.get("capabilities") or []),
        }
    ]
    task_payload["taskTemplate"] = {
        **template_payload,
        "slug": str(applied_template.get("slug") or template_slug),
        "version": str(applied_template.get("version") or template_version),
        "scope": template_scope,
    }
    parameters["task"] = task_payload
    parameters["stepCount"] = len(expanded_steps)
    return parameters

def _build_jira_orchestrate_execution_creator():
    async def _create_execution(**kwargs):
        async with get_async_session_context() as session:
            kwargs["initial_parameters"] = await _expand_task_template_for_child_run(
                session=session,
                initial_parameters=kwargs.get("initial_parameters"),
            )
            service = TemporalExecutionService(session)
            record = await service.create_execution(**kwargs)
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

_SUPPORTED_AGENT_RUNTIMES = frozenset({"codex", "gemini_cli", "claude", "jules"})
_CODEX_CONFIG_FLEETS = frozenset({SANDBOX_FLEET, AGENT_RUNTIME_FLEET})
# Agent runtimes where PR creation is driven by the provider API (e.g. Jules
# ``automationMode`` / ``AUTO_CREATE_PR``), not by appending ``gh pr create``
# to plan instructions.
_TOOLS_WITH_AUTO_PR_CREATION = frozenset({"jules", "jules_api"})

def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}

def _coerce_non_empty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""

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

def _derive_pr_resolver_title(
    task_payload: Mapping[str, Any],
    selected_skill_inputs: Mapping[str, Any],
) -> str:
    selected_skill_payload = _coerce_mapping(task_payload.get("tool")) or _coerce_mapping(
        task_payload.get("skill")
    )
    selected_skill_name = str(
        selected_skill_payload.get("name")
        or selected_skill_payload.get("id")
        or ""
    ).strip()
    if selected_skill_name.lower() != "pr-resolver":
        return ""
    git_payload = _coerce_mapping(task_payload.get("git"))
    selected_skill_payload_inputs = _coerce_mapping(
        selected_skill_payload.get("inputs")
        or selected_skill_payload.get("args")
    )
    return str(
        git_payload.get("startingBranch")
        or task_payload.get("startingBranch")
        or git_payload.get("branch")
        or task_payload.get("branch")
        or selected_skill_inputs.get("startingBranch")
        or selected_skill_inputs.get("branch")
        or selected_skill_payload_inputs.get("startingBranch")
        or selected_skill_payload_inputs.get("branch")
        or ""
    ).strip()

def _normalize_runtime_mode(raw_mode: Any) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if not normalized:
        return str(settings.workflow.default_task_runtime or "gemini_cli").strip().lower()
    return normalized

_JIRA_AGENT_SKILLS = frozenset({"jira-issue-creator", "jira-pr-verify", "jira-verify"})
_JIRA_STORY_OUTPUT_TOOLS = frozenset(
    {"story.create_jira_issues", "story.create_jira_orchestrate_tasks"}
)
_MOONSPEC_BREAKDOWN_TOOLS = frozenset({"moonspec-breakdown"})

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

def _selected_step_tool_type(tool_name: str) -> str:
    if tool_name.lower() in _JIRA_STORY_OUTPUT_TOOLS:
        return "skill"
    return "agent_runtime"

def _jira_agent_skill_selected(tool_name: str) -> bool:
    return tool_name.lower() in _JIRA_AGENT_SKILLS

def _task_uses_only_jira_agent_skill(
    *, selected_skill_name: str, raw_steps: Any
) -> bool:
    if isinstance(raw_steps, list) and len(raw_steps) > 1:
        if not all(isinstance(step, Mapping) for step in raw_steps):
            return False
        effective_step_tool_names = [
            (_selected_step_tool_name(step) or selected_skill_name).lower()
            for step in raw_steps
        ]
        return bool(effective_step_tool_names) and all(
            _jira_agent_skill_selected(name) for name in effective_step_tool_names
        )
    return _jira_agent_skill_selected(selected_skill_name)

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
        task_payload = _coerce_mapping(input_payload.get("task"))
        if not task_payload:
            task_payload = _coerce_mapping(parameter_payload.get("task"))
        git_payload = _coerce_mapping(task_payload.get("git"))
        selected_skill_payload = _coerce_mapping(task_payload.get("tool")) or _coerce_mapping(
            task_payload.get("skill")
        )
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
                    "task.instructions, inputs.instructions, or parameters.instructions"
                )

        if (
            not has_explicit_instructions
            and selected_skill_name.lower() == "pr-resolver"
        ):
            pr_selector = str(selected_skill_inputs.get("pr") or "").strip()
            branch_selector = str(
                git_payload.get("startingBranch")
                or task_payload.get("startingBranch")
                or git_payload.get("branch")
                or task_payload.get("branch")
                or selected_skill_inputs.get("startingBranch")
                or selected_skill_inputs.get("branch")
                or ""
            ).strip()
            if not pr_selector and not branch_selector:
                raise RuntimeError(
                    "pr-resolver task requires task.tool.inputs.pr or task.git.startingBranch "
                    "when task.instructions is not explicitly provided"
                )
            # Ensure the auto-generated instruction includes the PR/branch
            # selector so the agent knows which PR to target.  The selector
            # may come from git_payload rather than selected_skill_inputs, so
            # the generic " with inputs:" block above can miss it.
            effective_selector = pr_selector or branch_selector
            if effective_selector and not pr_selector:
                merged_inputs = dict(selected_skill_inputs) if selected_skill_inputs else {}
                merged_inputs["pr"] = effective_selector
                instructions = f"Execute skill '{selected_skill_name}' with inputs:\n" + json.dumps(
                    merged_inputs, indent=2, sort_keys=True,
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

        raw_steps = task_payload.get("steps")
        publish_uses_git = not _task_uses_only_jira_agent_skill(
            selected_skill_name=selected_skill_name,
            raw_steps=raw_steps,
        )
        node_publish_mode = str(node_inputs.get("publishMode") or "").lower()
        if not publish_uses_git and node_publish_mode in {"pr", "branch"}:
            node_inputs["publishMode"] = "none"
        for git_key in ("startingBranch", "targetBranch", "branch"):
            git_val = (
                git_payload.get(git_key)
                or task_payload.get(git_key)
                or selected_skill_inputs.get(git_key)
                or parameter_payload.get(git_key)
                or input_payload.get(git_key)
            )
            if isinstance(git_val, str) and git_val.strip():
                node_inputs[git_key] = git_val.strip()

        if (
            publish_uses_git
            and isinstance(publish_mode, str)
            and publish_mode.strip().lower() == "pr"
        ):
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

                branch_prefix = f"{prefix}-" if prefix else ""
                node_inputs["targetBranch"] = (
                    f"{branch_prefix}{str(uuid.uuid4())[:8]}"
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

        # --- Expand task.steps[] or stepCount into multiple plan nodes ---
        has_multi_steps = (
            isinstance(raw_steps, list)
            and len(raw_steps) > 1
            and all(isinstance(s, Mapping) for s in raw_steps)
        )
        if has_multi_steps:
            step_tool_names = {
                _selected_step_tool_name(step).lower()
                for step in raw_steps
                if isinstance(step, Mapping)
            }
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
                step_tool_names & (_JIRA_STORY_OUTPUT_TOOLS | _MOONSPEC_BREAKDOWN_TOOLS)
            )
        elif selected_skill_name:
            should_prepare_story_breakdown = (
                should_prepare_story_breakdown
                or selected_skill_name.lower() in _MOONSPEC_BREAKDOWN_TOOLS
            )

        if should_prepare_story_breakdown:
            story_paths = _story_breakdown_paths(
                title=title,
                existing={**story_output_payload, **node_inputs},
            )
            node_inputs.update(story_paths)
            if story_output_mode == "jira" and not (
                node_inputs.get("targetBranch") or node_inputs.get("branch")
            ):
                prefix = _derive_pr_branch_prefix(
                    task_payload=task_payload,
                    publish_payload=publish_payload,
                    selected_skill_name=selected_skill_name,
                ) or "story-breakdown"
                node_inputs["targetBranch"] = f"{prefix}-{str(uuid.uuid4())[:8]}"
            story_output_payload = dict(story_output_payload)
            story_output_payload.setdefault("mode", story_output_mode or "docs_tmp")
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
                step_node_inputs: dict[str, Any] = {
                    **node_inputs,
                    **{
                        k: v
                        for k, v in step_entry.items()
                        if k not in {"id", "tool", "skill", "instructions"}
                    },
                    "instructions": step_instructions,
                }

                # Per-step tool/skill override
                step_tool_name = _selected_step_tool_name(step_entry)
                step_runtime = runtime_mode
                tool_type = _selected_step_tool_type(step_tool_name)
                effective_step_skill_name = step_tool_name or selected_skill_name
                if step_tool_name:
                    step_runtime = step_tool_name
                    step_node_inputs["selectedSkill"] = step_tool_name
                    if _jira_agent_skill_selected(step_tool_name):
                        step_node_inputs["publishMode"] = "none"
                if effective_step_skill_name:
                    step_node_inputs["instructions"] = _append_agent_skill_instructions(
                        step_node_inputs["instructions"],
                        selected_skill=effective_step_skill_name,
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

                nodes.append({
                    "id": step_id,
                    "tool": {
                        "type": tool_type,
                        "name": step_runtime,
                        "version": "1.0",
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
                step_id = f"node-{idx + 1}"
                nodes.append({
                    "id": step_id,
                    "tool": {
                        "type": "agent_runtime",
                        "name": runtime_mode,
                        "version": "1.0",
                    },
                    "inputs": expanded_inputs,
                })
                if prev_step_id:
                    edges.append({"from": prev_step_id, "to": step_id})
                prev_step_id = step_id
        else:
            node_id = str(task_payload.get("id") or "node-1").strip() or "node-1"
            if selected_skill_name.lower() in _MOONSPEC_BREAKDOWN_TOOLS:
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
            node_tool_type = (
                "skill"
                if selected_skill_name.lower() in _JIRA_STORY_OUTPUT_TOOLS
                else "agent_runtime"
            )
            node_tool_name = (
                selected_skill_name if node_tool_type == "skill" else runtime_mode
            )
            nodes.append({
                "id": node_id,
                "tool": {
                    "type": node_tool_type,
                    "name": node_tool_name,
                    "version": "1.0",
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
            publish_node = next(
                (
                    node
                    for node in reversed(nodes)
                    if str(node.get("tool", {}).get("type") or "").strip().lower()
                    == "agent_runtime"
                    and not _jira_agent_skill_selected(_plan_node_selected_skill(node))
                ),
                nodes[-1],
            )
            publish_tool = str(
                publish_node.get("tool", {}).get("name") or ""
            ).strip().lower()
            publish_selected_skill = _plan_node_selected_skill(publish_node)
            if (
                publish_tool not in _TOOLS_WITH_AUTO_PR_CREATION
                and not _jira_agent_skill_selected(publish_selected_skill)
            ):
                publish_inputs = publish_node["inputs"]
                if (
                    story_output_mode == "jira"
                    and _requires_branch_publish_for_story_output(
                        publish_inputs.get("publishMode")
                    )
                ):
                    publish_inputs["publishMode"] = "branch"
                commit_suffix = (
                    "\n\nAfter completing the changes above, commit your work "
                    "(`git add -A && git commit -m '<summary>'`). "
                    "Do NOT push or create a pull request — that is handled automatically."
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

def _build_agent_runtime_deps() -> tuple[
    ManagedRunStore,
    ManagedRunSupervisor,
    ManagedRuntimeLauncher,
    DockerCodexManagedSessionController,
    "RunnerProfileRegistry",
    "DockerWorkloadLauncher",
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
    launcher = ManagedRuntimeLauncher(store, log_streamer=log_streamer)
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
    )

async def _build_runtime_activities(topology) -> tuple[AsyncExitStack, list[object]]:
    """Build activity handlers for the configured non-workflow fleet.

    Agent execution is handled by MoonMind.AgentRun (a child workflow on the
    workflow fleet).  Plan generation and skill dispatch are handled by
    activity fleets (llm, sandbox, etc.).
    """
    resources = AsyncExitStack()
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
        register_story_output_tool_handlers(
            dispatcher,
            execution_creator=_build_jira_orchestrate_execution_creator(),
        )
        register_deployment_update_tool_handler(dispatcher)

        run_store = None
        run_supervisor = None
        run_launcher = None
        session_controller = None
        workload_registry = None
        workload_launcher = None
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
            ) = _build_agent_runtime_deps()
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
            agent_runtime_activities = TemporalAgentRuntimeActivities(
                artifact_service=artifact_service,
                run_store=run_store,
                run_supervisor=run_supervisor,
                run_launcher=run_launcher,
                session_controller=session_controller,
                workload_registry=workload_registry,
                workload_launcher=workload_launcher,
                workflow_docker_mode=settings.workflow.workflow_docker_mode,
            )
            register_workload_tool_handlers(
                dispatcher,
                registry=workload_registry,
                launcher=workload_launcher,
                workflow_docker_mode=settings.workflow.workflow_docker_mode,
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

    # Start healthcheck server before connecting to Temporal so probes
    # can confirm the process is alive even during initial connection.
    healthcheck_server = await start_healthcheck_server()

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

    client = await Client.connect(settings.temporal.address, **client_kwargs)

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [
            MoonMindRun,
            MoonMindManifestIngest,
            MoonMindProviderProfileManager,
            MoonMindAgentSession,
            MoonMindManagedSessionReconcile,
            MoonMindAgentRun,
            MoonMindOAuthSession,
            MoonMindMergeAutomation,
        ]
        activities = [
            resolve_adapter_metadata,
            get_activity_route,
            resolve_external_adapter,
            external_adapter_execution_style,
        ]
        logger.info(
            "Temporal workflow fleet registrations: %s",
            ", ".join(list_registered_workflow_types()),
        )
    else:
        runtime_resources, activities = await _build_runtime_activities(topology)

    try:
        worker = Worker(
            client,
            task_queue=topology.task_queues[0],
            workflows=workflows,
            activities=activities,
            workflow_runner=UnsandboxedWorkflowRunner(),
            **_worker_concurrency_kwargs(topology),
        )

        logger.info("Worker started, polling task queues...")
        await worker.run()
    finally:
        if runtime_resources is not None:
            await runtime_resources.aclose()
        if healthcheck_server is not None:
            healthcheck_server.close()
            await healthcheck_server.wait_closed()

class OpenTelemetryLoggingFilter(logging.Filter):
    """Injects OpenTelemetry and Temporal trace context into standard logging."""
    def filter(self, record: logging.LogRecord) -> bool:
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
    if not enable_opentelemetry:
        logging.basicConfig(level=logging.INFO)
        return

    logging.basicConfig(level=logging.INFO, format=_OPENTELEMETRY_LOG_FORMAT)
    formatter = logging.Formatter(_OPENTELEMETRY_LOG_FORMAT)
    for handler in logging.root.handlers:
        handler.setFormatter(formatter)
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
