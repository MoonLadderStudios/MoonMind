"""Canonical ManifestIngest registration and its persisted entry contracts."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow
from temporalio.common import (
    RetryPolicy,
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)

from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityRoute,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.scheduled_start import (
    MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE,
    temporal_scheduled_start_time,
)

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.manifest_ingest_models import (
        ManifestNodeMutationRequestModel,
        ManifestUpdateManifestRequestModel,
        RequestedByModel,
    )
    from moonmind.workflows.temporal.manifest_ingest import (
        _apply_manifest_node_update,
        _execution_policy_from_parameters,
        _requested_by_principal,
        _resolve_workflow_requested_by,
        _runtime_manifest_nodes,
        _workflow_owner_id,
    )

WORKFLOW_NAME = "MoonMind.ManifestIngest"
DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()
MANIFEST_RECURRING_SCHEDULED_START_PATCH = "manifest-recurring-scheduled-start-v1"
MANIFEST_CATALOG_ACTIVITIES_PATCH = "manifest-catalog-activities-v1"
MANIFEST_TERMINAL_EVIDENCE_PATCH = "manifest-terminal-evidence-v1"


@workflow.defn(name="MoonMind.ManifestIngest")
class MoonMindManifestIngestWorkflow:
    """Real Temporal workflow for manifest ingest."""

    def __init__(self) -> None:
        self._manifest_ref: str | None = None
        self._concurrency: int = 50
        self._paused: bool = False
        self._nodes: dict[str, dict[str, Any]] = {}
        self._status: str = "initializing"
        self._plan_ref: str | None = None
        self._summary_ref: str | None = None
        self._run_index_ref: str | None = None
        self._execution_policy: dict[str, Any] = {}
        self._requested_by: dict[str, Any] = {}
        self._owner_id: str | None = None
        self._activity_principal: str = "system"
        self._action: str = "run"
        self._pending_manifest_update: dict[str, str] | None = None
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._workflow_id: str = ""
        self._run_id: str = ""

    def _retry_policy_for_route(self, route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    def _execute_kwargs_for_route(self, route: TemporalActivityRoute) -> dict[str, Any]:
        return {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": self._retry_policy_for_route(route),
        }

    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
            logging.getLogger(__name__).exception(
                "Error getting workflow info in _get_logger"
            )
            return logging.getLogger(__name__)

        extra = {
            "workflow_id": getattr(info, "workflow_id", "unknown"),
            "run_id": getattr(info, "run_id", "unknown"),
            "task_queue": getattr(info, "task_queue", "unknown"),
        }

        logger_to_use = workflow.logger
        if not hasattr(logger_to_use, "isEnabledFor"):
            logger_to_use = logging.getLogger(__name__)

        try:
            logger_to_use.isEnabledFor(logging.INFO)
            return logging.LoggerAdapter(logger_to_use, extra=extra)
        except Exception:
            logging.getLogger(__name__).exception(
                "Error checking logger capabilities in _get_logger"
            )
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        # These are distinct persisted entry contracts, not interchangeable
        # aliases: manifest_ref compiles; manifestArtifactRef orchestrates nodes.
        if "manifest_ref" in input_payload and "manifestArtifactRef" in input_payload:
            raise exceptions.ApplicationError(
                "ambiguous manifest entry contract", non_retryable=True
            )
        if "manifestArtifactRef" in input_payload:
            return await self._run_nodes(input_payload)
        return await self._run_compilation(input_payload)

    async def _run_compilation(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        self._get_logger().info("Starting MoonMind.ManifestIngest workflow")
        self._manifest_ref = input_payload.get("manifest_ref")

        if not self._manifest_ref:
            raise exceptions.ApplicationError(
                "manifest_ref is required", non_retryable=True
            )

        # 1. Compile Manifest
        compile_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("manifest.compile")
        compile_result = await workflow.execute_activity(
            "manifest.compile",
            {
                "principal": "system",
                "manifest_ref": self._manifest_ref,
                "action": (
                    input_payload["action"]
                    if "action" in input_payload
                    else (
                        "run"
                        if workflow.patched(MANIFEST_CATALOG_ACTIVITIES_PATCH)
                        else "apply"
                    )
                ),
                "options": input_payload.get("options", {}),
                "requested_by": {"type": "system", "id": "temporal"},
                "execution_policy": {},
            },
            **self._execute_kwargs_for_route(compile_route),
        )

        plan_ref = (
            compile_result.get("plan_ref")
            if isinstance(compile_result, dict)
            else getattr(compile_result, "plan_ref", None)
        )
        manifest_digest = (
            compile_result.get("manifest_digest")
            if isinstance(compile_result, dict)
            else getattr(compile_result, "manifest_digest", None)
        )
        if plan_ref:
            self._plan_ref = plan_ref

        # 2. Write Summary
        summary_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "manifest.write_summary"
        )
        summary_result = await workflow.execute_activity(
            "manifest.write_summary",
            {
                "principal": "system",
                "workflow_id": workflow.info().workflow_id,
                "state": "executing",
                "phase": "compiled",
                "manifest_ref": self._manifest_ref,
                "plan_ref": self._plan_ref,
            },
            **self._execute_kwargs_for_route(summary_route),
        )

        # summary_result is a tuple of (summary_ref, run_index_ref)
        if (
            summary_result
            and isinstance(summary_result, (list, tuple))
            and len(summary_result) > 0
        ):
            self._summary_ref = summary_result[0]
        elif isinstance(summary_result, dict) and "summary_ref" in summary_result:
            self._summary_ref = summary_result["summary_ref"]

        return {
            "status": "success",
            "manifest_digest": manifest_digest,
            "plan_ref": self._plan_ref,
            "summary_ref": self._summary_ref,
        }

    def _upsert_scheduled_for_search_attribute(self) -> None:
        if not self._scheduled_start_patch_enabled():
            return
        scheduled_start = temporal_scheduled_start_time(workflow.info())
        if scheduled_start is None:
            return
        try:
            workflow.upsert_search_attributes(
                [
                    SearchAttributePair(
                        SearchAttributeKey.for_datetime(
                            MM_SCHEDULED_FOR_SEARCH_ATTRIBUTE
                        ),
                        scheduled_start,
                    )
                ]
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to upsert manifest scheduled_for search attribute",
                extra={"error": str(exc)},
            )

    def _scheduled_start_patch_enabled(self) -> bool:
        try:
            return workflow.patched(MANIFEST_RECURRING_SCHEDULED_START_PATCH)
        except Exception:
            return False

    async def _compile_manifest(
        self,
        *,
        manifest_ref: str,
    ) -> dict[str, Any]:
        if workflow.patched(MANIFEST_CATALOG_ACTIVITIES_PATCH):
            route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("manifest.compile")
            compiled = await workflow.execute_activity(
                "manifest.compile",
                {
                    "principal": self._activity_principal,
                    "manifest_ref": manifest_ref,
                    "action": self._action,
                    "options": None,
                    "requested_by": self._requested_by,
                    "execution_policy": self._execution_policy,
                },
                **self._execute_kwargs_for_route(route),
            )
            plan_ref = compiled["plan_ref"]
            if isinstance(plan_ref, dict):
                plan_ref = plan_ref["artifact_id"]
            route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.read")
            plan_bytes = await workflow.execute_activity(
                "artifact.read",
                {"principal": self._activity_principal, "artifact_ref": plan_ref},
                **self._execute_kwargs_for_route(route),
            )
            plan = json.loads(plan_bytes)
            return {"plan_ref": plan_ref, "nodes": plan["nodes"]}
        # The alternate historical entry emitted these exact names/payloads.
        # Retain its commands for replay; new work uses catalogued Activities.
        manifest_payload = await workflow.execute_activity(
            "manifest_read",
            args=[
                {
                    "principal": self._activity_principal,
                    "manifest_ref": manifest_ref,
                }
            ],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return await workflow.execute_activity(
            "manifest_compile",
            args=[
                {
                    "principal": self._activity_principal,
                    "manifest_ref": manifest_ref,
                    "manifest_payload": manifest_payload,
                    "action": self._action,
                    "options": None,
                    "requested_by": self._requested_by,
                    "execution_policy": self._execution_policy,
                }
            ],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _apply_pending_manifest_update(self) -> None:
        if self._pending_manifest_update is None:
            return

        update_request = ManifestUpdateManifestRequestModel.model_validate(
            self._pending_manifest_update
        )
        compile_result = await self._compile_manifest(
            manifest_ref=update_request.new_manifest_artifact_ref
        )
        compiled_nodes = _runtime_manifest_nodes(
            compile_result.get("nodes", []),
            requested_by=RequestedByModel.model_validate(self._requested_by),
        )
        self._nodes = _apply_manifest_node_update(
            self._nodes,
            updated_nodes=compiled_nodes,
            mode=update_request.mode,
        )
        self._manifest_ref = update_request.new_manifest_artifact_ref
        self._plan_ref = compile_result.get("plan_ref") or self._plan_ref
        self._pending_manifest_update = None

    async def _run_nodes(self, parameters: dict[str, Any]) -> dict[str, Any]:
        info = workflow.info()
        self._workflow_id = info.workflow_id
        self._run_id = info.run_id
        self._owner_id = _workflow_owner_id(info)
        self._manifest_ref = parameters.get("manifestArtifactRef")
        if not self._manifest_ref:
            raise ValueError("manifestArtifactRef is required")
        self._upsert_scheduled_for_search_attribute()

        requested_by = _resolve_workflow_requested_by(
            parameters,
            owner_id=self._owner_id,
        )
        execution_policy = _execution_policy_from_parameters(parameters)
        self._requested_by = requested_by.model_dump(by_alias=True)
        self._execution_policy = execution_policy.model_dump(by_alias=True)
        self._activity_principal = _requested_by_principal(requested_by)
        self._action = str(parameters.get("action") or "run")
        self._concurrency = execution_policy.max_concurrency
        self._status = "executing"

        self._plan_ref = parameters.get("planArtifactRef")
        nodes_input = parameters.get("manifestNodes", [])

        if self._plan_ref and nodes_input:
            for n in nodes_input:
                node_id = n.get("nodeId") or n.get("node_id")
                if node_id:
                    self._nodes[node_id] = dict(n)
        else:
            # 1. Compile Plan
            compile_result = await self._compile_manifest(
                manifest_ref=self._manifest_ref
            )
            self._plan_ref = compile_result.get("plan_ref")
            for n in _runtime_manifest_nodes(
                compile_result.get("nodes", []),
                requested_by=requested_by,
            ):
                node_id = n.get("nodeId") or n.get("node_id")
                if node_id:
                    self._nodes[node_id] = dict(n)

        # 2. Execute nodes logic
        failure_policy = self._execution_policy.get("failurePolicy", "fail_fast")
        terminal_evidence = workflow.patched(MANIFEST_TERMINAL_EVIDENCE_PATCH)

        self._running_tasks = {}

        def can_start(node_id: str) -> bool:
            node = self._nodes[node_id]
            if node["state"] not in {"pending", "ready"}:
                return False
            for d in set(node.get("dependencies", [])):
                dep_node = self._nodes.get(d)
                if not dep_node or dep_node["state"] != "completed":
                    return False
            return True

        async def run_node(node_id: str):
            node = self._nodes[node_id]
            node["state"] = "running"
            try:
                # Execute MoonMind.UserWorkflow as a child workflow
                run_params = {
                    "manifestIngestWorkflowId": self._workflow_id,
                    "manifestIngestRunId": self._run_id,
                    "manifestArtifactRef": self._manifest_ref,
                    "nodeId": node_id,
                    "requestedBy": self._requested_by,
                    "runtimeHints": {
                        "manifestNodeState": "running",
                        "workflowType": "MoonMind.UserWorkflow",
                    },
                    "parentClosePolicy": "REQUEST_CANCEL",
                }

                child_id = f"{self._workflow_id}:{self._run_id}:{node_id}"
                if terminal_evidence:
                    # Use the model's canonical keys: compiled nodes already carry
                    # these aliases, which take precedence over snake-case inputs.
                    node["childWorkflowId"] = child_id
                    node["childRunId"] = None
                    node["resultArtifactRef"] = None
                    node.pop("error", None)
                else:
                    node["child_workflow_id"] = child_id

                start_child = (
                    workflow.start_child_workflow
                    if terminal_evidence
                    else workflow.execute_child_workflow
                )
                child_result = await start_child(
                    "MoonMind.UserWorkflow",
                    args=[
                        {
                            "workflow_type": "MoonMind.UserWorkflow",
                            "owner_id": (
                                requested_by.id if requested_by.type == "user" else None
                            ),
                            "title": node.get("title", f"Manifest node {node_id}"),
                            "input_artifact_ref": self._manifest_ref,
                            "plan_artifact_ref": self._plan_ref,
                            "manifest_artifact_ref": None,
                            "initial_parameters": run_params,
                        }
                    ],
                    id=child_id,
                    parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                    **(
                        {
                            "search_attributes": TypedSearchAttributes(
                                [
                                    SearchAttributePair(
                                        SearchAttributeKey.for_keyword("mm_owner_type"),
                                        requested_by.type,
                                    ),
                                    SearchAttributePair(
                                        SearchAttributeKey.for_keyword("mm_owner_id"),
                                        self._activity_principal,
                                    ),
                                ]
                            )
                        }
                        if workflow.patched(MANIFEST_CATALOG_ACTIVITIES_PATCH)
                        else {}
                    ),
                )
                if terminal_evidence:
                    child_handle = child_result
                    node["childRunId"] = child_handle.first_execution_run_id
                    child_result = await child_handle
                    outcome = child_result.get("executionOutcome") or {}
                    result_ref = outcome.get("resultRef")
                    if not isinstance(result_ref, str) or not result_ref.strip():
                        result_ref = next(
                            (
                                ref
                                for ref in outcome.get("outputRefs", []) or []
                                if isinstance(ref, str) and ref.strip()
                            ),
                            None,
                        )
                    node["resultArtifactRef"] = result_ref
                else:
                    # Persisted histories used this old output shape.
                    node["result_artifact_ref"] = child_result.get("output_artifact_ref")
                node["state"] = "completed"
            except asyncio.CancelledError as exc:
                node["state"] = "canceled"
                node["error"] = str(exc) or "cancelled"
                raise
            except Exception as e:
                node["state"] = "failed"
                node["error"] = str(e)

        while True:
            await workflow.wait_condition(lambda: not self._paused)

            if self._pending_manifest_update is not None and not self._running_tasks:
                await self._apply_pending_manifest_update()

            # Check for terminal failure in fail_fast mode
            any_failed = any(n["state"] == "failed" for n in self._nodes.values())
            if any_failed and failure_policy == "fail_fast":
                for n in self._nodes.values():
                    if n["state"] in {"pending", "ready"}:
                        n["state"] = "canceled"
                for task in self._running_tasks.values():
                    task.cancel()

            # Find ready nodes
            ready_nodes = [n_id for n_id in self._nodes if can_start(n_id)]

            # Start up to concurrency limit
            available_slots = self._concurrency - len(self._running_tasks)
            for n_id in ready_nodes[:available_slots]:
                task = asyncio.create_task(run_node(n_id))
                self._running_tasks[n_id] = task

            if not self._running_tasks:
                # No running tasks and no ready nodes means we're done
                break

            # Wait for at least one task to complete
            done, _ = await asyncio.wait(
                list(self._running_tasks.values()), return_when=asyncio.FIRST_COMPLETED
            )

            for d in done:
                # Remove completed tasks from tracking
                for n_id, t in list(self._running_tasks.items()):
                    if t == d:
                        del self._running_tasks[n_id]
                        break
                if d.cancelled():
                    continue
                exc = d.exception()
                if exc is not None:
                    if failure_policy == "fail_fast":
                        raise exc
                    # best_effort / continue_and_report: absorb failure, continue

        self._status = "finalizing"

        # 3. Create summary and index artifacts
        nodes_list = list(self._nodes.values())
        catalog_activities = workflow.patched(MANIFEST_CATALOG_ACTIVITIES_PATCH)
        summary_result = await workflow.execute_activity(
            (
                "manifest.write_summary"
                if catalog_activities
                else "manifest_write_summary"
            ),
            args=[
                {
                    "principal": self._activity_principal,
                    "workflow_id": self._workflow_id,
                    "state": (
                        "completed"
                        if all(n["state"] == "completed" for n in nodes_list)
                        else "failed"
                    ),
                    "phase": "completed",
                    "manifest_ref": self._manifest_ref,
                    "plan_ref": self._plan_ref,
                    "nodes": nodes_list,
                }
            ],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
            **(
                {
                    "task_queue": DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                        "manifest.write_summary"
                    ).task_queue
                }
                if catalog_activities
                else {}
            ),
        )

        if summary_result and len(summary_result) == 2:
            self._summary_ref = summary_result[0]
            self._run_index_ref = summary_result[1]
            if catalog_activities:
                if isinstance(self._summary_ref, dict):
                    self._summary_ref = self._summary_ref["artifact_id"]
                if isinstance(self._run_index_ref, dict):
                    self._run_index_ref = self._run_index_ref["artifact_id"]

        if terminal_evidence and any(n["state"] != "completed" for n in nodes_list):
            self._status = "failed"
            # Persist summary/index evidence before failing the authoritative
            # Temporal execution, including cancellation and blocked dependencies.
            raise exceptions.ApplicationError(
                "Manifest ingest ended with incomplete nodes",
                {"summaryRef": self._summary_ref, "runIndexRef": self._run_index_ref},
                type="ManifestNodesIncomplete",
                non_retryable=True,
            )

        final_status = "completed"
        if any(n["state"] == "failed" for n in nodes_list):
            final_status = "failed"
        self._status = final_status

        return {
            "status": final_status,
            "summaryRef": self._summary_ref,
            "runIndexRef": self._run_index_ref,
        }

    @workflow.update(name="UpdateManifest")
    async def update_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = ManifestUpdateManifestRequestModel.model_validate(payload or {})
        self._pending_manifest_update = request.model_dump(by_alias=True)
        return {
            "accepted": True,
            "applied": "next_safe_point",
            "message": (
                "Manifest update accepted and will be applied at the next safe point."
            ),
        }

    @workflow.update(name="SetConcurrency")
    async def set_concurrency(self, payload: dict[str, Any]) -> dict[str, Any]:
        max_concurrency = payload.get("maxConcurrency")
        if max_concurrency is None:
            return {
                "accepted": False,
                "message": "maxConcurrency is required",
            }
        try:
            value = int(max_concurrency)
        except (TypeError, ValueError):
            return {
                "accepted": False,
                "message": "maxConcurrency must be an integer",
            }
        if not 1 <= value <= 500:
            return {
                "accepted": False,
                "message": "maxConcurrency must be between 1 and 500",
            }
        self._concurrency = value
        return {"accepted": True, "applied": "immediate"}

    @workflow.update(name="Pause")
    async def pause(self, payload: dict[str, Any] = None) -> dict[str, Any]:
        self._paused = True
        return {"accepted": True, "applied": "immediate"}

    @workflow.update(name="Resume")
    async def resume(self, payload: dict[str, Any] = None) -> dict[str, Any]:
        self._paused = False
        return {"accepted": True, "applied": "immediate"}

    @workflow.update(name="CancelNodes")
    async def cancel_nodes(self, payload: dict[str, Any]) -> dict[str, Any]:
        mutation = ManifestNodeMutationRequestModel.model_validate(payload or {})
        accepted_node_ids: list[str] = []
        rejected_node_ids: list[str] = []
        node_ids = mutation.node_ids
        for nid in node_ids:
            node = self._nodes.get(nid)
            if node is None:
                rejected_node_ids.append(nid)
                continue
            if node["state"] in {"pending", "ready"}:
                node["state"] = "canceled"
                accepted_node_ids.append(nid)
                continue
            if node["state"] == "running":
                task = self._running_tasks.get(nid)
                if task is None:
                    rejected_node_ids.append(nid)
                    continue
                task.cancel()
                accepted_node_ids.append(nid)
                continue
            rejected_node_ids.append(nid)
        return {
            "accepted": True,
            "applied": "immediate",
            "result": {
                "acceptedNodeIds": accepted_node_ids,
                "rejectedNodeIds": rejected_node_ids,
            },
        }

    @workflow.update(name="RetryNodes")
    async def retry_nodes(self, payload: dict[str, Any]) -> dict[str, Any]:
        node_ids = payload.get("nodeIds", [])
        for nid in node_ids:
            if nid in self._nodes and self._nodes[nid]["state"] in [
                "failed",
                "canceled",
            ]:
                self._nodes[nid]["state"] = "pending"
        return {"accepted": True, "applied": "immediate"}
