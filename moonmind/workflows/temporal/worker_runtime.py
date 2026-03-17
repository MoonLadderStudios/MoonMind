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
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalJulesActivities,
    TemporalPlanActivities,
    TemporalProposalActivities,
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
    publish_artifacts_activity,
    invoke_adapter_cancel,
)

logger = logging.getLogger(__name__)

_SUPPORTED_AGENT_RUNTIMES = frozenset({"codex", "gemini", "claude", "jules"})


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_runtime_mode(raw_mode: Any) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if not normalized:
        return str(settings.workflow.default_task_runtime or "gemini").strip().lower()
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

        # --- Resolve instructions ---
        instructions = (
            task_payload.get("instructions")
            or input_payload.get("instructions")
            or parameter_payload.get("instructions")
        )
        if not isinstance(instructions, str) or not instructions.strip():
            raise RuntimeError(
                "agent_runtime plan requires non-empty instructions in "
                "task.instructions, inputs.instructions, or parameters.instructions"
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
        )
        if isinstance(repository, str) and repository.strip():
            node_inputs["repository"] = repository.strip()
            node_inputs["repo"] = repository.strip()

        for git_key in ("startingBranch", "newBranch", "branch"):
            git_val = (
                _coerce_mapping(task_payload.get("git")).get(git_key)
                or task_payload.get(git_key)
            )
            if isinstance(git_val, str) and git_val.strip():
                node_inputs[git_key] = git_val.strip()

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
        node_id = str(task_payload.get("id") or "node-1").strip() or "node-1"
        failure_mode = str(
            parameter_payload.get("failurePolicy") or "FAIL_FAST"
        ).strip()
        if failure_mode not in {"FAIL_FAST", "CONTINUE"}:
            failure_mode = "FAIL_FAST"

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
                        "type": "agent_runtime",
                        "name": runtime_mode,
                        "version": "1.0",
                    },
                    "inputs": node_inputs,
                }
            ],
            "edges": [],
        }

    return _runtime_planner


async def _build_runtime_activities(topology) -> tuple[AsyncExitStack, list[object]]:
    """Build activity handlers for the configured non-workflow fleet.

    Agent execution is handled by MoonMind.AgentRun (a child workflow on the
    workflow fleet).  Plan generation and skill dispatch are handled by
    activity fleets (llm, sandbox, etc.).
    """
    resources = AsyncExitStack()
    try:
        session = await resources.enter_async_context(get_async_session_context())
        artifact_service = TemporalArtifactService(TemporalArtifactRepository(session))
        sandbox_activities = TemporalSandboxActivities(artifact_service=artifact_service)
        planner = _build_runtime_planner()

        dispatcher = SkillActivityDispatcher()

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
            agent_runtime_activities=TemporalAgentRuntimeActivities(
                artifact_service=artifact_service,
            ),
            # TODO: wire proposal_service_factory once full proposal
            # generation is implemented.  While the generator stub returns
            # an empty candidate list, proposal_submit is never invoked
            # with real data and the factory is not required.
            proposal_activities=TemporalProposalActivities(
                artifact_service=artifact_service,
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

    # Start healthcheck server before connecting to Temporal so probes
    # can confirm the process is alive even during initial connection.
    healthcheck_server = await start_healthcheck_server()

    client = await Client.connect(
        settings.temporal.address, namespace=settings.temporal.namespace
    )

    workflows = []
    activities = []
    runtime_resources: AsyncExitStack | None = None

    if topology.fleet == WORKFLOW_FLEET:
        workflows = [MoonMindRun, MoonMindManifestIngest, MoonMindAuthProfileManager, MoonMindAgentRun]
        activities = [publish_artifacts_activity, invoke_adapter_cancel]
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())

