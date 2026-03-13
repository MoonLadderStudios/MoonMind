"""Temporal worker runtime entrypoint."""

import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime
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

logger = logging.getLogger(__name__)

_SUPPORTED_AUTO_SKILL_RUNTIMES = frozenset({"codex", "gemini", "claude", "jules"})


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

        if not node_inputs and tool_name == "auto":
            instructions = task_payload.get("instructions")
            if instructions is None:
                instructions = input_payload.get("instructions")
            if instructions is None:
                instructions = parameter_payload.get("instructions")
            if not isinstance(instructions, str) or not instructions.strip():
                raise RuntimeError(
                    "auto tool requires non-empty instructions in task.instructions, "
                    "inputs.instructions, or parameters.instructions"
                )

            runtime_payload = _coerce_mapping(task_payload.get("runtime"))
            runtime_mode = _normalize_runtime_mode(
                runtime_payload.get("mode", parameter_payload.get("targetRuntime"))
            )
            runtime_node: dict[str, Any] = {"mode": runtime_mode}

            model = runtime_payload.get("model", parameter_payload.get("model"))
            if model is not None:
                if not isinstance(model, str) or not model:
                    raise RuntimeError(
                        "auto tool runtime.model must be a non-empty string"
                    )
                runtime_node["model"] = model

            effort = runtime_payload.get("effort", parameter_payload.get("effort"))
            if effort is not None:
                if not isinstance(effort, str) or not effort:
                    raise RuntimeError(
                        "auto tool runtime.effort must be a non-empty string"
                    )
                runtime_node["effort"] = effort

            node_inputs = {
                "instructions": instructions,
                "runtime": runtime_node,
            }

            repository = parameter_payload.get("repository")
            if isinstance(repository, str) and repository.strip():
                node_inputs["repo"] = repository.strip()

            repo_ref = task_payload.get("repoRef")
            if isinstance(repo_ref, str) and repo_ref.strip():
                node_inputs["repoRef"] = repo_ref.strip()

            branch = task_payload.get("branch")
            if isinstance(branch, str) and branch.strip():
                node_inputs["branch"] = branch.strip()

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
            model = runtime_payload.get("model", payload.get("model"))
            effort = runtime_payload.get("effort", payload.get("effort"))
            instructions = payload.get("instructions")
            repo_ref = payload.get("repoRef")
            checkout_revision = payload.get("branch")
            workspace_ref = payload.get("workspaceRef")

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
                if checkout_revision is not None and (
                    not isinstance(checkout_revision, str)
                    or not checkout_revision.strip()
                ):
                    raise RuntimeError("branch must be a non-empty string when provided")
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
            if workspace_path is None and isinstance(repo_ref, str):
                try:
                    workspace_path = await sandbox_activities.sandbox_checkout_repo(
                        repo_ref=repo_ref.strip(),
                        idempotency_key=f"auto-{workflow_id}-{node_id}",
                        checkout_revision=checkout_revision,
                    )
                except Exception as exc:
                    return SkillResult(
                        status="FAILED",
                        outputs={"error": str(exc)},
                        progress={
                            "details": "Failed to checkout repoRef in auto tool handler"
                        },
                    )

            cmd = [target_runtime, "run", "--instructions", instructions]
            if model is not None:
                cmd.extend(["--model", model])
            if effort is not None:
                cmd.extend(["--effort", effort])

            try:
                sandbox_result = await sandbox_activities.sandbox_run_command(
                    workspace_ref=workspace_path,
                    cmd=cmd,
                    principal=principal,
                    timeout_seconds=900,
                )
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

            output_artifacts = []
            if sandbox_result.diagnostics_ref:
                output_artifacts.append(sandbox_result.diagnostics_ref)

            return SkillResult(
                status="SUCCEEDED" if sandbox_result.exit_code == 0 else "FAILED",
                outputs=outputs,
                output_artifacts=tuple(output_artifacts),
                progress={
                    "details": f"Executed generic LLM handler via {target_runtime}"
                },
            )

        dispatcher.register_skill(
            skill_name="auto",
            version="1.0",
            handler=_auto_skill_handler,
        )
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
            skill_activities=TemporalSkillActivities(dispatcher=dispatcher),
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
