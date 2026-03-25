import contextlib

def _build_proposal_service_factory():
    from api_service.db.base import get_async_session_context
    
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
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any, Mapping


from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.base import get_async_session_context
from moonmind.config.settings import settings
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.task_proposals.service import TaskProposalService
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalIntegrationActivities,
    TemporalPlanActivities,
    TemporalProposalActivities,
    TemporalReviewActivities,
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
from moonmind.workflows.temporal.workflows.auth_profile_manager import (
    MoonMindAuthProfileManagerWorkflow as MoonMindAuthProfileManager,
)
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow as MoonMindManifestIngest,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow as MoonMindRun
from moonmind.workflows.temporal.worker_healthcheck import start_healthcheck_server
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    external_adapter_execution_style,
    get_activity_route,
    resolve_external_adapter,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.supervisor import ManagedRunSupervisor

logger = logging.getLogger(__name__)

_SUPPORTED_AGENT_RUNTIMES = frozenset({"codex", "gemini_cli", "claude", "jules"})
# Agent runtimes where PR creation is driven by the provider API (e.g. Jules
# ``automationMode`` / ``AUTO_CREATE_PR``), not by appending ``gh pr create``
# to plan instructions.
_TOOLS_WITH_AUTO_PR_CREATION = frozenset({"jules", "jules_api"})


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_runtime_mode(raw_mode: Any) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if not normalized:
        return str(settings.workflow.default_task_runtime or "gemini_cli").strip().lower()
    return normalized


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

        for git_key in ("startingBranch", "newBranch", "branch"):
            git_val = (
                git_payload.get(git_key)
                or task_payload.get(git_key)
                or selected_skill_inputs.get(git_key)
                or parameter_payload.get(git_key)
                or input_payload.get(git_key)
            )
            if isinstance(git_val, str) and git_val.strip():
                node_inputs[git_key] = git_val.strip()

        if isinstance(publish_mode, str) and publish_mode.strip().lower() == "pr":
            if not node_inputs.get("newBranch") and not node_inputs.get("branch"):
                import uuid
                node_inputs["newBranch"] = f"auto-{str(uuid.uuid4())[:8]}"

        # --- Assemble plan ---
        title = str(
            task_payload.get("title")
            or parameter_payload.get("title")
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

        # --- Expand task.steps[] into multiple plan nodes ---
        raw_steps = task_payload.get("steps")
        has_multi_steps = (
            isinstance(raw_steps, list)
            and len(raw_steps) > 1
            and all(isinstance(s, Mapping) for s in raw_steps)
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
                    "instructions": step_instructions,
                }

                # Per-step tool/skill override
                step_tool = _coerce_mapping(step_entry.get("tool")) or _coerce_mapping(
                    step_entry.get("skill")
                )
                step_tool_name = str(
                    step_tool.get("name") or step_tool.get("id") or ""
                ).strip()
                step_runtime = runtime_mode
                if step_tool_name:
                    step_runtime = step_tool_name

                nodes.append({
                    "id": step_id,
                    "tool": {
                        "type": "agent_runtime",
                        "name": step_runtime,
                        "version": "1.0",
                    },
                    "inputs": step_node_inputs,
                })

                if prev_step_id:
                    edges.append({"from": prev_step_id, "to": step_id})
                prev_step_id = step_id
        else:
            node_id = str(task_payload.get("id") or "node-1").strip() or "node-1"
            nodes.append({
                "id": node_id,
                "tool": {
                    "type": "agent_runtime",
                    "name": runtime_mode,
                    "version": "1.0",
                },
                "inputs": node_inputs,
            })

        # Append PR creation instructions to the last node so CLI-based agents
        # create the PR in the same workspace where the changes were made.
        # Skip Jules: session creation uses Jules API ``automationMode`` =
        # ``AUTO_CREATE_PR`` when ``publishMode`` is ``pr`` or ``branch``
        # (see ``JulesAgentAdapter.do_start``), not shell instructions.
        if isinstance(publish_mode, str) and publish_mode.strip().lower() == "pr":
            last_tool = str(nodes[-1].get("tool", {}).get("name") or "").strip().lower()
            if last_tool not in _TOOLS_WITH_AUTO_PR_CREATION:
                pr_suffix = (
                    "\n\nAfter completing the changes above, commit your work and "
                    "push the current branch to origin (`git push -u origin HEAD`). "
                    "Then create a GitHub pull request with the changes using `gh pr create --fill`."
                )
                last_inputs = nodes[-1]["inputs"]
                last_inputs["instructions"] = last_inputs["instructions"] + pr_suffix
        elif isinstance(publish_mode, str) and publish_mode.strip().lower() == "branch":
            last_tool = str(nodes[-1].get("tool", {}).get("name") or "").strip().lower()
            if last_tool not in _TOOLS_WITH_AUTO_PR_CREATION:
                push_suffix = (
                    "\n\nAfter completing the changes above, commit your work and "
                    "push the current branch to origin (`git push -u origin HEAD`)."
                )
                last_inputs = nodes[-1]["inputs"]
                last_inputs["instructions"] = last_inputs["instructions"] + push_suffix

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


def _build_agent_runtime_deps() -> tuple[ManagedRunStore, ManagedRunSupervisor, ManagedRuntimeLauncher]:
    """Build shared store, supervisor, and launcher for the agent_runtime fleet."""
    import os
    from pathlib import Path

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
    artifact_root = os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_ARTIFACTS", "/work/agent_jobs"),
        "artifacts",
    )
    os.makedirs(store_root, exist_ok=True)
    os.makedirs(artifact_root, exist_ok=True)

    store = ManagedRunStore(store_root)
    artifact_storage = LocalRuntimeArtifactStorage(artifact_root)
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedRunSupervisor(store, log_streamer)
    launcher = ManagedRuntimeLauncher(store)
    return store, supervisor, launcher


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

        # Build agent_runtime dependencies (store + supervisor + launcher)
        run_store, run_supervisor, run_launcher = _build_agent_runtime_deps()
        reconciled = await run_supervisor.reconcile()
        if reconciled:
            logger.info(
                "Reconciled %d stale managed run records during startup",
                len(reconciled),
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
            integration_activities=TemporalIntegrationActivities(
                artifact_service=artifact_service
            ),
            agent_runtime_activities=TemporalAgentRuntimeActivities(
                artifact_service=artifact_service,
                run_store=run_store,
                run_supervisor=run_supervisor,
                run_launcher=run_launcher,
            ),
            proposal_activities=TemporalProposalActivities(
                artifact_service=artifact_service,
                proposal_service_factory=_build_proposal_service_factory(),
            ),
            review_activities=TemporalReviewActivities(),
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
        ] + [resolve_external_adapter, external_adapter_execution_style, get_activity_route]
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

    # Start healthcheck server before connecting to Temporal so probes
    # can confirm the process is alive even during initial connection.
    healthcheck_server = await start_healthcheck_server()

    import os
    interceptors = []
    if os.environ.get("MOONMIND_ENABLE_OPENTELEMETRY", "0") == "1":
        try:
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry import trace
            from temporalio.contrib.opentelemetry import TracingInterceptor

            if not isinstance(trace.get_tracer_provider(), TracerProvider):
                trace.set_tracer_provider(TracerProvider())
            interceptors.append(TracingInterceptor())
            logger.info("OpenTelemetry tracing enabled for Temporal worker.")
        except ImportError as e:
            logger.warning(f"OpenTelemetry tracing requested but failed to initialize: {e}")

    client = await Client.connect(
        settings.temporal.address, 
        namespace=settings.temporal.namespace,
        interceptors=interceptors,
    )

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [MoonMindRun, MoonMindManifestIngest, MoonMindAuthProfileManager, MoonMindAgentRun]
        activities = [resolve_external_adapter, external_adapter_execution_style, get_activity_route]
        logger.info(
            "Temporal workflow fleet registrations: %s",
            ", ".join(list_registered_workflow_types()),
        )
    else:
        runtime_resources, activities = await _build_runtime_activities(topology)

    try:
        use_versioning = os.environ.get("MOONMIND_ENABLE_WORKER_VERSIONING", "false").lower() in ("true", "1", "yes")
        build_id = os.environ.get("MOONMIND_BUILD_ID")
        if not build_id:
            import subprocess
            try:
                build_id = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
                ).strip()
            except Exception as e:
                if use_versioning:
                    logger.error(
                        "Failed to determine Temporal worker build ID from "
                        "MOONMIND_BUILD_ID or git. "
                        "Set the MOONMIND_BUILD_ID environment variable to a "
                        "stable, unique identifier for this build when "
                        "use_worker_versioning is enabled.",
                        exc_info=e,
                    )
                    raise RuntimeError(
                        "Unable to determine Temporal worker build ID. "
                        "Set MOONMIND_BUILD_ID to a unique identifier for this build."
                    ) from e
                build_id = "unknown"

        worker = Worker(
            client,
            task_queue=topology.task_queues[0],
            workflows=workflows,
            activities=activities,
            workflow_runner=UnsandboxedWorkflowRunner(),
            build_id=build_id,
            use_worker_versioning=use_versioning,
            **_worker_concurrency_kwargs(topology),
        )

        logger.info(f"Worker started with build_id={build_id}, polling task queues...")
        await worker.run()
    finally:
        if runtime_resources is not None:
            await runtime_resources.aclose()
        if healthcheck_server is not None:
            healthcheck_server.close()
            await healthcheck_server.wait_closed()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())
