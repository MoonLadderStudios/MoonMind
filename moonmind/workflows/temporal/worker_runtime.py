"""Temporal worker runtime entrypoint."""

import asyncio
import logging
import os
import random
import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.temporal.activity_runtime import (
    TemporalJulesActivities,
    TemporalPlanActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    build_worker_activity_bindings,
    describe_configured_worker,
    list_registered_workflow_types,
)
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow as MoonMindManifestIngest,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow as MoonMindRun
from moonmind.workflows.automation.workspace import generate_branch_name

logger = logging.getLogger(__name__)

_SUPPORTED_AUTO_SKILL_RUNTIMES = frozenset({"codex", "gemini", "claude", "jules"})
_SUPPORTED_PUBLISH_MODES = frozenset({"none", "branch", "pr"})
_DEFAULT_GEMINI_ALLOWED_TOOLS = (
    "activate_skill",
    "run_shell_command",
    "replace",
    "write_file",
    "web_fetch",
)
_FULL_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
)
_TOOL_ERROR_PATTERNS = (
    re.compile(r'Error executing tool .*?: Tool ".*?" not found', re.IGNORECASE),
    re.compile(r'Tool ".*?" not found', re.IGNORECASE),
)
_GITHUB_PR_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+",
    re.IGNORECASE,
)
_GEMINI_TRANSIENT_CAPACITY_PATTERNS = (
    re.compile(r"\bMODEL_CAPACITY_EXHAUSTED\b", re.IGNORECASE),
    re.compile(r"\bRESOURCE_EXHAUSTED\b", re.IGNORECASE),
    re.compile(r"\brateLimitExceeded\b", re.IGNORECASE),
    re.compile(r"\bNo capacity available for model\b", re.IGNORECASE),
)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _read_positive_float_env(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


_GEMINI_CAPACITY_RETRY_MAX_ATTEMPTS = _read_positive_int_env(
    "MOONMIND_GEMINI_CAPACITY_RETRY_MAX_ATTEMPTS",
    8,
)
_GEMINI_CAPACITY_RETRY_BASE_DELAY_SECONDS = _read_positive_float_env(
    "MOONMIND_GEMINI_CAPACITY_RETRY_BASE_DELAY_SECONDS",
    30.0,
)
_GEMINI_CAPACITY_RETRY_MAX_DELAY_SECONDS = _read_positive_float_env(
    "MOONMIND_GEMINI_CAPACITY_RETRY_MAX_DELAY_SECONDS",
    600.0,
)


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_runtime_mode(raw_mode: Any) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if not normalized:
        raise RuntimeError("auto tool runtime.mode is required")
    if normalized not in _SUPPORTED_AUTO_SKILL_RUNTIMES:
        supported = ", ".join(sorted(_SUPPORTED_AUTO_SKILL_RUNTIMES))
        raise RuntimeError(
            f"auto tool runtime.mode '{normalized}' is unsupported; expected one of: {supported}"
        )
    return normalized


def _build_auto_runtime_command(
    *,
    runtime_mode: str,
    instructions: str,
    model: str | None,
    effort: str | None,
    workspace_path: str | None = None,
    gemini_allowed_tools: tuple[str, ...] = (),
) -> list[str]:
    if runtime_mode == "gemini":
        # Gemini CLI uses --prompt for one-shot non-interactive execution.
        command = ["gemini", "--prompt", instructions]
        include_directories: list[str] = []
        if workspace_path:
            include_directories.append(workspace_path)
            workspace = Path(workspace_path)
            candidate_paths = (
                workspace / ".agents" / "skills",
                workspace / ".gemini" / "skills",
                workspace / "skills_active",
                workspace.parent / "skills_active",
            )
            for candidate in candidate_paths:
                if candidate.exists():
                    include_directories.append(str(candidate))
        for include_dir in dict.fromkeys(include_directories):
            command.extend(["--include-directories", include_dir])
        if gemini_allowed_tools:
            command.extend(["--allowed-tools", ",".join(gemini_allowed_tools)])
        if model:
            command.extend(["--model", model])
        return command
    if runtime_mode == "claude":
        command = ["claude", "--print", instructions]
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        return command
    if runtime_mode == "codex":
        command = ["codex", "exec", instructions]
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        return command

    # Keep legacy passthrough behavior for any remaining allowed runtime mode.
    command = [runtime_mode, "run", "--instructions", instructions]
    if model:
        command.extend(["--model", model])
    if effort:
        command.extend(["--effort", effort])
    return command


def _resolve_gemini_command_env() -> dict[str, str | None]:
    raw_mode = str(os.environ.get("MOONMIND_GEMINI_CLI_AUTH_MODE", "api_key")).strip()
    auth_mode = raw_mode.lower() if raw_mode else "api_key"
    if auth_mode not in {"api_key", "oauth"}:
        raise RuntimeError(
            "MOONMIND_GEMINI_CLI_AUTH_MODE must be one of: api_key, oauth"
        )

    if auth_mode == "oauth":
        gemini_home = str(
            os.environ.get("GEMINI_CLI_HOME") or os.environ.get("GEMINI_HOME") or ""
        ).strip()
        if not gemini_home:
            raise RuntimeError(
                "MOONMIND_GEMINI_CLI_AUTH_MODE=oauth requires GEMINI_CLI_HOME or GEMINI_HOME"
            )
        return {
            "GEMINI_HOME": gemini_home,
            "GEMINI_CLI_HOME": gemini_home,
            "GEMINI_API_KEY": None,
            "GOOGLE_API_KEY": None,
        }

    # api_key mode: Gemini CLI expects GEMINI_API_KEY for API-key auth.
    gemini_api_key = str(os.environ.get("GEMINI_API_KEY", "")).strip()
    google_api_key = str(os.environ.get("GOOGLE_API_KEY", "")).strip()
    if not gemini_api_key and google_api_key:
        return {"GEMINI_API_KEY": google_api_key}
    return {}


def _resolve_task_tool(task_payload: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    tool_payload = _coerce_mapping(task_payload.get("tool"))
    skill_payload = _coerce_mapping(task_payload.get("skill"))

    selected_payload: dict[str, Any]
    if tool_payload:
        tool_type = str(
            tool_payload.get("type") or tool_payload.get("kind") or "skill"
        ).strip()
        if tool_type and tool_type.lower() != "skill":
            raise RuntimeError(
                "task.tool.type must be 'skill' for the current runtime contract"
            )
        selected_payload = tool_payload
    else:
        selected_payload = skill_payload

    tool_name = str(
        selected_payload.get("name") or selected_payload.get("id") or ""
    ).strip()
    tool_version = str(selected_payload.get("version") or "").strip()

    if tool_name and not tool_version:
        raise RuntimeError(
            "task.tool.version is required when task.tool.name is set "
            "(task.skill is a legacy alias)"
        )
    if tool_version and not tool_name:
        raise RuntimeError(
            "task.tool.name is required when task.tool.version is set "
            "(task.skill is a legacy alias)"
        )
    if not tool_name:
        tool_name = "auto"
        tool_version = "1.0"

    inline_inputs = selected_payload.get("inputs")
    if not isinstance(inline_inputs, Mapping):
        inline_inputs = selected_payload.get("args")
    normalized_inputs = dict(inline_inputs) if isinstance(inline_inputs, Mapping) else {}
    return tool_name, tool_version, normalized_inputs


def _detect_cli_tool_error(stdout_tail: Any, stderr_tail: Any) -> str | None:
    candidates: list[str] = []
    if isinstance(stderr_tail, str) and stderr_tail.strip():
        candidates.append(stderr_tail)
    if isinstance(stdout_tail, str) and stdout_tail.strip():
        candidates.append(stdout_tail)
    if not candidates:
        return None
    combined = "\n".join(candidates)
    for pattern in _TOOL_ERROR_PATTERNS:
        match = pattern.search(combined)
        if match:
            return match.group(0)
    return None


def _extract_pull_request_url(stdout_tail: Any, stderr_tail: Any) -> str | None:
    candidates: list[str] = []
    if isinstance(stderr_tail, str) and stderr_tail.strip():
        candidates.append(stderr_tail)
    if isinstance(stdout_tail, str) and stdout_tail.strip():
        candidates.append(stdout_tail)
    if not candidates:
        return None
    combined = "\n".join(candidates)
    match = _GITHUB_PR_URL_PATTERN.search(combined)
    if match is None:
        return None
    return match.group(0)


def _is_transient_gemini_capacity_error(stdout_tail: Any, stderr_tail: Any) -> bool:
    candidates: list[str] = []
    if isinstance(stderr_tail, str) and stderr_tail.strip():
        candidates.append(stderr_tail)
    if isinstance(stdout_tail, str) and stdout_tail.strip():
        candidates.append(stdout_tail)
    if not candidates:
        return False
    combined = "\n".join(candidates)
    return any(pattern.search(combined) for pattern in _GEMINI_TRANSIENT_CAPACITY_PATTERNS)


def _gemini_capacity_retry_delay_seconds(failed_attempt: int) -> float:
    # Exponential backoff with a fixed ceiling to keep retries bounded.
    delay = _GEMINI_CAPACITY_RETRY_BASE_DELAY_SECONDS * (2 ** max(failed_attempt - 1, 0))
    return min(delay, _GEMINI_CAPACITY_RETRY_MAX_DELAY_SECONDS)


def _normalize_publish_mode(raw_mode: Any) -> str:
    if raw_mode is None:
        return ""
    if not isinstance(raw_mode, str):
        raise RuntimeError("publishMode must be a string when provided")
    normalized = raw_mode.strip().lower()
    if not normalized:
        return ""
    if normalized not in _SUPPORTED_PUBLISH_MODES:
        raise RuntimeError("publishMode must be one of: none, branch, pr")
    return normalized


def _normalize_repository_ref(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise RuntimeError("repository must be a string when provided")
    normalized = raw_value.strip()
    if not normalized:
        return None
    if any(character.isspace() for character in normalized):
        raise RuntimeError("repository must not contain whitespace")
    return normalized


def _normalize_branch_value(field_name: str, raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise RuntimeError(f"{field_name} must be a string when provided")
    normalized = raw_value.strip()
    return normalized or None


def _derive_branch_suffix_from_instruction(instructions: str) -> str | None:
    tokens = re.findall(r"[A-Za-z0-9]+", instructions.lower())
    if not tokens:
        return None
    return "-".join(tokens[:4])


def _resolve_gemini_allowed_tools() -> tuple[str, ...]:
    raw = str(os.environ.get("MOONMIND_GEMINI_ALLOWED_TOOLS", "")).strip()
    if not raw:
        return _DEFAULT_GEMINI_ALLOWED_TOOLS
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or _DEFAULT_GEMINI_ALLOWED_TOOLS


def _branch_generation_key(*, workflow_id: str, node_id: str) -> str:
    match = _FULL_UUID_PATTERN.search(workflow_id)
    if match is not None:
        return match.group(0)
    return f"{workflow_id}-{node_id}"


def _compose_auto_runtime_instructions(
    *,
    instructions: str,
    publish_mode: str,
    repository: str | None,
    workspace_path: str | None,
    starting_branch: str | None,
    working_branch: str | None,
) -> str:
    details: list[str] = []
    if repository:
        details.append(f"- Repository: {repository}")
    if workspace_path:
        details.append(f"- Workspace: {workspace_path}")
    if starting_branch:
        details.append(f"- Starting branch: {starting_branch}")
    if working_branch:
        details.append(f"- Working branch: {working_branch}")

    if publish_mode == "pr":
        details.append(
            "- Publish mode: pr. Commit and push changes, open a GitHub pull request, "
            "and print the final PR URL."
        )
    elif publish_mode == "branch":
        details.append(
            "- Publish mode: branch. Commit and push changes to the working branch, "
            "and print the branch name."
        )

    if not details:
        return instructions.strip()
    return f"{instructions.strip()}\n\nRUNTIME CONTEXT:\n" + "\n".join(details)


def _build_runtime_planner():
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
        tool_name, tool_version, inline_tool_inputs = _resolve_task_tool(task_payload)

        explicit_inputs = task_payload.get("inputs")
        node_inputs: dict[str, Any] = (
            dict(explicit_inputs) if isinstance(explicit_inputs, Mapping) else {}
        )
        if not node_inputs and inline_tool_inputs:
            node_inputs = dict(inline_tool_inputs)

        instructions = node_inputs.get("instructions")
        if instructions is None:
            instructions = task_payload.get("instructions")
        if instructions is None:
            instructions = input_payload.get("instructions")
        if instructions is None:
            instructions = parameter_payload.get("instructions")

        if not isinstance(instructions, str) or not instructions.strip():
            if tool_name == "auto":
                raise RuntimeError(
                    "auto tool requires non-empty instructions in task.instructions, "
                    "inputs.instructions, or parameters.instructions"
                )
            instructions = (
                f"Execute the '{tool_name}' skill for this repository and report "
                "the result."
            )

        runtime_payload = _coerce_mapping(task_payload.get("runtime"))
        runtime_node = _coerce_mapping(node_inputs.get("runtime"))
        runtime_mode = _normalize_runtime_mode(
            runtime_node.get("mode")
            or runtime_payload.get("mode")
            or parameter_payload.get("targetRuntime")
            or settings.workflow.default_task_runtime
        )
        runtime_node["mode"] = runtime_mode

        model = runtime_node.get("model")
        if model is None:
            model = runtime_payload.get("model", parameter_payload.get("model"))
        if model is not None:
            if not isinstance(model, str) or not model:
                raise RuntimeError("auto tool runtime.model must be a non-empty string")
            runtime_node["model"] = model

        effort = runtime_node.get("effort")
        if effort is None:
            effort = runtime_payload.get("effort", parameter_payload.get("effort"))
        if effort is not None:
            if not isinstance(effort, str) or not effort:
                raise RuntimeError(
                    "auto tool runtime.effort must be a non-empty string"
                )
            runtime_node["effort"] = effort

        if not node_inputs:
            node_inputs = {
                "instructions": instructions,
                "runtime": runtime_node,
            }
            publish_payload = _coerce_mapping(task_payload.get("publish"))
            publish_mode = publish_payload.get("mode", parameter_payload.get("publishMode"))
            normalized_publish_mode = _normalize_publish_mode(publish_mode)
            if normalized_publish_mode:
                node_inputs["publishMode"] = normalized_publish_mode

            repository = _normalize_repository_ref(
                task_payload.get("repository")
                or input_payload.get("repository")
                or parameter_payload.get("repository")
                or parameter_payload.get("repo")
            )
            if repository:
                node_inputs["repository"] = repository
                node_inputs["repo"] = repository

            repo_ref = task_payload.get("repoRef")
            if isinstance(repo_ref, str) and repo_ref.strip():
                node_inputs["repoRef"] = repo_ref.strip()

            branch = task_payload.get("branch")
            if isinstance(branch, str) and branch.strip():
                node_inputs["branch"] = branch.strip()

            task_git_payload = _coerce_mapping(task_payload.get("git"))
            if not task_git_payload:
                task_git_payload = _coerce_mapping(input_payload.get("git"))
            starting_branch = _normalize_branch_value(
                "task.git.startingBranch",
                task_git_payload.get("startingBranch"),
            )
            new_branch = _normalize_branch_value(
                "task.git.newBranch",
                task_git_payload.get("newBranch"),
            )
            if starting_branch:
                node_inputs["startingBranch"] = starting_branch
            if new_branch:
                node_inputs["newBranch"] = new_branch
        else:
            node_inputs["instructions"] = instructions
            node_inputs["runtime"] = runtime_node

        if not node_inputs and isinstance(input_payload.get("inputs"), Mapping):
            node_inputs = dict(input_payload["inputs"])

        failure_mode = str(parameter_payload.get("failurePolicy") or "FAIL_FAST").strip()
        if failure_mode not in {"FAIL_FAST", "CONTINUE"}:
            failure_mode = "FAIL_FAST"

        title = (
            str(task_payload.get("title") or parameter_payload.get("title") or "").strip()
            or "Generated Plan"
        )
        created_at = (
            datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        node_id = str(task_payload.get("id") or "node-1").strip() or "node-1"

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
            "nodes": [
                {
                    "id": node_id,
                    "tool": {
                        "type": "skill",
                        "name": tool_name,
                        "version": tool_version,
                    },
                    "skill": {"name": tool_name, "version": tool_version},
                    "inputs": node_inputs,
                }
            ],
            "edges": [],
        }

    return _runtime_planner


async def _build_runtime_activities(topology) -> tuple[AsyncExitStack, list[object]]:
    resources = AsyncExitStack()
    try:
        session = await resources.enter_async_context(get_async_session_context())
        artifact_service = TemporalArtifactService(TemporalArtifactRepository(session))
        dispatcher = SkillActivityDispatcher()
        sandbox_activities = TemporalSandboxActivities(artifact_service=artifact_service)

        async def _auto_skill_handler(inputs, context):
            payload = _coerce_mapping(inputs)
            context_payload = _coerce_mapping(context)
            runtime_payload = _coerce_mapping(payload.get("runtime"))
            publish_payload = _coerce_mapping(payload.get("publish"))
            git_payload = _coerce_mapping(payload.get("git"))
            model = runtime_payload.get("model", payload.get("model"))
            effort = runtime_payload.get("effort", payload.get("effort"))
            instructions = payload.get("instructions")
            repo_ref = payload.get("repoRef")
            repository = payload.get("repository", payload.get("repo"))
            checkout_revision = payload.get("branch")
            workspace_ref = payload.get("workspaceRef")
            starting_branch = payload.get("startingBranch", git_payload.get("startingBranch"))
            new_branch = payload.get("newBranch", git_payload.get("newBranch"))
            publish_mode = _normalize_publish_mode(
                publish_payload.get("mode", payload.get("publishMode"))
            )

            try:
                target_runtime = _normalize_runtime_mode(
                    runtime_payload.get("mode", payload.get("targetRuntime"))
                )
                if model is not None and (not isinstance(model, str) or not model):
                    raise RuntimeError("runtime.model must be a non-empty string")
                if effort is not None and (not isinstance(effort, str) or not effort):
                    raise RuntimeError("runtime.effort must be a non-empty string")
                if not isinstance(instructions, str) or not instructions.strip():
                    raise RuntimeError("instructions must be a non-empty string")
                if repo_ref is not None and (
                    not isinstance(repo_ref, str) or not repo_ref.strip()
                ):
                    raise RuntimeError("repoRef must be a non-empty string when provided")
                repository = _normalize_repository_ref(repository)
                if checkout_revision is not None and (
                    not isinstance(checkout_revision, str)
                    or not checkout_revision.strip()
                ):
                    raise RuntimeError("branch must be a non-empty string when provided")
                checkout_revision = _normalize_branch_value("branch", checkout_revision)
                starting_branch = _normalize_branch_value(
                    "startingBranch", starting_branch
                )
                new_branch = _normalize_branch_value("newBranch", new_branch)
                if workspace_ref is not None and (
                    not isinstance(workspace_ref, str) or not workspace_ref.strip()
                ):
                    raise RuntimeError(
                        "workspaceRef must be a non-empty string when provided"
                    )
            except Exception as exc:
                return SkillResult(
                    status="FAILED",
                    outputs={"error": str(exc)},
                    progress={"details": "Invalid auto tool runtime payload"},
                )

            principal = str(context_payload.get("principal") or "system")
            workflow_id = str(context_payload.get("workflow_id") or "unknown")
            node_id = str(context_payload.get("node_id") or "unknown")

            workspace_path = workspace_ref.strip() if isinstance(workspace_ref, str) else None
            checkout_repo_ref = (
                repo_ref.strip()
                if isinstance(repo_ref, str) and repo_ref.strip()
                else repository
            )
            checkout_target = starting_branch or checkout_revision
            if workspace_path is None and checkout_repo_ref:
                try:
                    workspace_path = await sandbox_activities.sandbox_checkout_repo(
                        repo_ref=checkout_repo_ref,
                        idempotency_key=f"auto-{workflow_id}-{node_id}",
                        checkout_revision=checkout_target,
                    )
                except Exception as exc:
                    return SkillResult(
                        status="FAILED",
                        outputs={"error": str(exc)},
                        progress={
                            "details": (
                                "Failed to checkout repository context in auto tool handler"
                            )
                        },
                    )

            if publish_mode in {"branch", "pr"} and new_branch is None:
                branch_suffix = _derive_branch_suffix_from_instruction(instructions)
                new_branch = generate_branch_name(
                    _branch_generation_key(workflow_id=workflow_id, node_id=node_id),
                    prefix="task",
                    suffix=branch_suffix,
                )

            if publish_mode == "pr" and workspace_path is None:
                return SkillResult(
                    status="FAILED",
                    outputs={
                        "error": (
                            "publishMode 'pr' requires workspaceRef, repoRef, or repository to "
                            "point at a writable repository checkout"
                        )
                    },
                    progress={
                        "details": "Missing workspace context for PR publish mode"
                    },
                )

            async def _run_git_command(
                command: list[str],
                *,
                timeout_seconds: int = 120,
            ) -> Any:
                return await sandbox_activities.sandbox_run_command(
                    {
                        "workspace_ref": workspace_path,
                        "cmd": command,
                        "principal": principal,
                        "timeout_seconds": timeout_seconds,
                    }
                )

            if workspace_path and (starting_branch or new_branch):
                if starting_branch:
                    checkout_starting = await _run_git_command(
                        ["git", "checkout", starting_branch]
                    )
                    if checkout_starting.exit_code != 0:
                        checkout_starting = await _run_git_command(
                            [
                                "git",
                                "checkout",
                                "-B",
                                starting_branch,
                                f"origin/{starting_branch}",
                            ]
                        )
                        if checkout_starting.exit_code != 0:
                            return SkillResult(
                                status="FAILED",
                                outputs={
                                    "error": (
                                        "failed to checkout starting branch "
                                        f"'{starting_branch}': "
                                        f"{checkout_starting.stderr_tail or checkout_starting.stdout_tail}"
                                    )
                                },
                                progress={
                                    "details": "Failed to prepare workspace branch context"
                                },
                            )
                if new_branch:
                    branch_base = starting_branch or "HEAD"
                    checkout_working = await _run_git_command(
                        ["git", "checkout", "-B", new_branch, branch_base]
                    )
                    if checkout_working.exit_code != 0:
                        return SkillResult(
                            status="FAILED",
                            outputs={
                                "error": (
                                    f"failed to checkout working branch '{new_branch}': "
                                    f"{checkout_working.stderr_tail or checkout_working.stdout_tail}"
                                )
                            },
                            progress={
                                "details": "Failed to prepare workspace branch context"
                            },
                        )

            gemini_allowed_tools: tuple[str, ...] = ()
            if target_runtime == "gemini":
                gemini_allowed_tools = _resolve_gemini_allowed_tools()

            command_instructions = _compose_auto_runtime_instructions(
                instructions=instructions,
                publish_mode=publish_mode,
                repository=repository,
                workspace_path=workspace_path,
                starting_branch=starting_branch,
                working_branch=new_branch,
            )
            cmd = _build_auto_runtime_command(
                runtime_mode=target_runtime,
                instructions=command_instructions,
                model=model if isinstance(model, str) else None,
                effort=effort if isinstance(effort, str) else None,
                workspace_path=workspace_path,
                gemini_allowed_tools=gemini_allowed_tools,
            )
            command_env: dict[str, str | None] | None = None
            if target_runtime == "gemini":
                try:
                    command_env = _resolve_gemini_command_env()
                except RuntimeError as exc:
                    return SkillResult(
                        status="FAILED",
                        outputs={"error": str(exc)},
                        progress={"details": "Invalid Gemini CLI auth mode configuration"},
                    )

            try:
                request_payload: dict[str, Any] = {
                    "workspace_ref": workspace_path,
                    "cmd": cmd,
                    "principal": principal,
                    "timeout_seconds": 900,
                }
                if command_env:
                    request_payload["env"] = command_env
                max_command_attempts = (
                    _GEMINI_CAPACITY_RETRY_MAX_ATTEMPTS
                    if target_runtime == "gemini"
                    else 1
                )
                command_attempt = 1
                while True:
                    sandbox_result = await sandbox_activities.sandbox_run_command(
                        request_payload
                    )
                    if (
                        target_runtime == "gemini"
                        and sandbox_result.exit_code != 0
                        and command_attempt < max_command_attempts
                        and _is_transient_gemini_capacity_error(
                            sandbox_result.stdout_tail,
                            sandbox_result.stderr_tail,
                        )
                    ):
                        retry_delay_seconds = _gemini_capacity_retry_delay_seconds(
                            command_attempt
                        )
                        # Apply ±25% jitter so concurrent workers don't all
                        # retry at the same instant, which would re-trigger
                        # the rate limit immediately.
                        jitter_range = retry_delay_seconds * 0.25
                        retry_delay_seconds = max(
                            1.0,
                            retry_delay_seconds
                            + random.uniform(-jitter_range, jitter_range),
                        )
                        logger.warning(
                            "Gemini capacity exhausted for workflow_id=%s node_id=%s "
                            "(attempt %d/%d); retrying in %.1fs",
                            workflow_id,
                            node_id,
                            command_attempt,
                            max_command_attempts,
                            retry_delay_seconds,
                        )
                        await asyncio.sleep(retry_delay_seconds)
                        command_attempt += 1
                        continue
                    break
            except Exception as exc:
                return SkillResult(
                    status="FAILED",
                    outputs={"error": str(exc)},
                    progress={
                        "details": f"Failed to execute generic LLM handler for {target_runtime}"
                    },
                )

            outputs = {
                "exit_code": sandbox_result.exit_code,
                "stdout_tail": sandbox_result.stdout_tail,
                "stderr_tail": sandbox_result.stderr_tail,
            }
            if new_branch:
                outputs["working_branch"] = new_branch
            if starting_branch:
                outputs["starting_branch"] = starting_branch
            if repository:
                outputs["repository"] = repository
            pull_request_url = _extract_pull_request_url(
                sandbox_result.stdout_tail, sandbox_result.stderr_tail
            )
            if pull_request_url:
                outputs["pull_request_url"] = pull_request_url

            output_artifacts = []
            if sandbox_result.diagnostics_ref:
                output_artifacts.append(sandbox_result.diagnostics_ref)

            tool_error = _detect_cli_tool_error(
                sandbox_result.stdout_tail,
                sandbox_result.stderr_tail,
            )
            result_status = "SUCCEEDED" if sandbox_result.exit_code == 0 else "FAILED"
            progress_details = f"Executed generic LLM handler via {target_runtime}"
            if result_status == "SUCCEEDED" and tool_error:
                result_status = "FAILED"
                outputs["error"] = (
                    "runtime CLI reported a tool invocation failure despite zero exit code: "
                    f"{tool_error}"
                )
                progress_details = (
                    f"Generic LLM handler via {target_runtime} reported tool failure"
                )
            elif result_status == "SUCCEEDED" and publish_mode == "pr":
                if pull_request_url is None:
                    result_status = "FAILED"
                    outputs["error"] = (
                        "publishMode 'pr' requires command output to include a "
                        "GitHub pull request URL"
                    )
                    progress_details = (
                        f"Generic LLM handler via {target_runtime} did not report a PR URL"
                    )

            return SkillResult(
                status=result_status,
                outputs=outputs,
                output_artifacts=tuple(output_artifacts),
                progress={"details": progress_details},
            )

        dispatcher.register_skill(
            skill_name="auto",
            version="1.0",
            handler=_auto_skill_handler,
        )
        dispatcher.register_default_skill_handler(handler=_auto_skill_handler)
        planner = _build_runtime_planner()
        if not callable(planner):
            raise RuntimeError(
                "Temporal runtime planner wiring is required and must be callable"
            )

        bindings = build_worker_activity_bindings(
            fleet=topology.fleet,
            artifact_activities=TemporalArtifactActivities(artifact_service),
            plan_activities=TemporalPlanActivities(
                artifact_service=artifact_service,
                planner=planner,
            ),
            skill_activities=TemporalSkillActivities(
                dispatcher=dispatcher,
                artifact_service=artifact_service,
            ),
            sandbox_activities=sandbox_activities,
            integration_activities=TemporalJulesActivities(
                artifact_service=artifact_service
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
        return resources, [binding.handler for binding in bindings]
    except Exception:
        await resources.aclose()
        raise


def _worker_concurrency_kwargs(topology) -> dict[str, int]:
    if topology.concurrency_limit is None:
        return {}
    if topology.fleet == WORKFLOW_FLEET:
        return {"max_concurrent_workflow_tasks": topology.concurrency_limit}
    return {"max_concurrent_activities": topology.concurrency_limit}


async def main_async() -> None:
    """Run the Temporal worker."""
    topology = describe_configured_worker()

    logger.info(
        f"Starting {topology.service_name} [{topology.fleet}] "
        f"queues={','.join(topology.task_queues)} "
        f"concurrency={topology.concurrency_limit}"
    )

    client = await Client.connect(
        settings.temporal.address, namespace=settings.temporal.namespace
    )

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [MoonMindRun, MoonMindManifestIngest]
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())
