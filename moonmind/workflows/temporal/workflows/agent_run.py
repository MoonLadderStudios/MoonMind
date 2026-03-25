import asyncio
import logging
import os
import dataclasses
from datetime import timedelta
from temporalio import workflow, activity
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        AgentRunHandle,
        AgentRunResult,
        AgentRunStatus as AgentRunStatusModel,
    )
    from moonmind.workflows.adapters.agent_adapter import AgentAdapter
    from moonmind.workflows.adapters.managed_agent_adapter import (
        ManagedAgentAdapter,
        ProfileResolutionError,
    )
    from moonmind.workflows.adapters.external_adapter_registry import (
        build_default_registry,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

# Map canonical AgentRunState literals to workflow-usable status constants.
# Named RunStatus (not AgentRunStatus) to avoid shadowing the Pydantic model
# imported in activity code.
class RunStatus:
    queued = "queued"
    awaiting_slot = "awaiting_slot"
    launching = "launching"
    running = "running"
    awaiting_callback = "awaiting_callback"
    awaiting_feedback = "awaiting_feedback"
    completed = "completed"
    failed = "failed"
    cancelled = "canceled"
    timed_out = "timed_out"


_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.completed, RunStatus.failed, RunStatus.cancelled, RunStatus.timed_out, "intervention_requested"}
)

_EXTERNAL_STATUS_TO_RUN_STATUS: dict[str, str] = {
    "queued": RunStatus.queued,
    "launching": RunStatus.launching,
    "running": RunStatus.running,
    "in_progress": RunStatus.running,
    "in-progress": RunStatus.running,
    "processing": RunStatus.running,
    "awaiting_callback": RunStatus.awaiting_callback,
    "awaiting_feedback": RunStatus.awaiting_feedback,
    "awaiting_approval": "awaiting_approval",
    "intervention_requested": "intervention_requested",
    "collecting_results": "collecting_results",
    "completed": RunStatus.completed,
    "success": RunStatus.completed,
    "done": RunStatus.completed,
    "resolved": RunStatus.completed,
    "finished": RunStatus.completed,
    "failed": RunStatus.failed,
    "error": RunStatus.failed,
    "errored": RunStatus.failed,
    "cancelled": RunStatus.cancelled,
    "canceled": RunStatus.cancelled,
    "timed_out": RunStatus.timed_out,
    "timeout": RunStatus.timed_out,
    "timed-out": RunStatus.timed_out,
    "unknown": RunStatus.awaiting_callback,
}

PROVIDER_RATE_LIMIT_ERROR_CODE = "429"

# Default workflow-level execution timeouts
DEFAULT_MANAGED_TIMEOUT_SECONDS = 3600      # 1 hour
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 21600    # 6 hours

# Activity catalog constants for agent_runtime fleet routing.
AGENT_RUNTIME_TASK_QUEUE = "mm.activity.agent_runtime"
AGENT_RUNTIME_ACTIVITY_TIMEOUT = timedelta(minutes=2)
AGENT_RUNTIME_CANCEL_TIMEOUT = timedelta(minutes=1)
AGENT_RUNTIME_STATUS_TIMEOUT = timedelta(seconds=60)
INTEGRATIONS_TASK_QUEUE = "mm.activity.integrations"
INTEGRATIONS_ACTIVITY_TIMEOUT = timedelta(minutes=2)
INTEGRATIONS_STATUS_TIMEOUT = timedelta(seconds=60)
WORKFLOW_TASK_QUEUE = "mm.workflow"
STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT = timedelta(seconds=120)
MANAGED_STATUS_ACTIVITY_PATCH_ID = "agent-run-managed-status-activity-v1"


@activity.defn(name="integration.get_activity_route")
async def get_activity_route(activity_name: str) -> dict:
    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity(activity_name)
    return dataclasses.asdict(route)

@activity.defn(name="integration.resolve_external_adapter")
async def resolve_external_adapter(agent_id: str) -> str:
    """Activity: verify that *agent_id* has a registered adapter.

    All non-deterministic work (reading env vars, dynamic imports) runs
    here rather than in the workflow so that replays remain deterministic.

    Returns the validated agent_id on success; raises if no adapter exists.
    """

    registry = build_default_registry()
    # Validate the adapter is registered — this forces the gate check.
    registry.create(agent_id)
    return agent_id


@activity.defn(name="integration.external_adapter_execution_style")
async def external_adapter_execution_style(agent_id: str) -> str:
    """Return ``polling`` or ``streaming_gateway`` for *agent_id*."""

    from moonmind.workflows.adapters.base_external_agent_adapter import (
        BaseExternalAgentAdapter,
    )
    from moonmind.workflows.adapters.external_adapter_registry import (
        build_default_registry,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

    registry = build_default_registry()
    adapter = registry.create(agent_id)
    if isinstance(adapter, BaseExternalAgentAdapter):
        return adapter.provider_capability.execution_style
    return "polling"


@workflow.defn(name="MoonMind.AgentRun")
class MoonMindAgentRun:
    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
            logging.getLogger(__name__).exception("Error getting workflow info in _get_logger")
            return logging.getLogger(__name__)

        extra = {
            "workflow_id": getattr(info, "workflow_id", "unknown"),
            "run_id": getattr(info, "run_id", "unknown"),
            "task_queue": getattr(info, "task_queue", "unknown"),
        }
        owner_id = getattr(self, "_owner_id", None)
        if owner_id:
            extra["owner_id"] = owner_id

        logger_to_use = workflow.logger
        if not hasattr(logger_to_use, "isEnabledFor"):
            logger_to_use = logging.getLogger(__name__)

        try:
            logger_to_use.isEnabledFor(logging.INFO)
            return logging.LoggerAdapter(logger_to_use, extra=extra)
        except Exception:
            logging.getLogger(__name__).exception("Error checking logger capabilities in _get_logger")
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self):
        self.completion_event = asyncio.Event()
        self.slot_assigned_event = asyncio.Event()
        self.run_status = RunStatus.queued
        self.final_result: AgentRunResult | None = None
        self.run_id: str | None = None
        self.agent_kind: str | None = None
        self._assigned_profile_id: str | None = None
        self._external_agent_id: str | None = None
        # Auto-answer state (Jules question auto-answer, spec 094)
        self._answered_activity_ids: set[str] = set()
        self._auto_answer_count: int = 0
        self._route_cache: dict[str, tuple[str, timedelta]] = {}

    async def _get_route_info(self, activity_name: str, fallback_queue: str, fallback_timeout: timedelta) -> tuple[str, timedelta]:
        if activity_name in self._route_cache:
            return self._route_cache[activity_name]
        
        if workflow.patched("agent-run-catalog-timeouts-v1"):
            route_dict = await workflow.execute_activity(
                "integration.get_activity_route",
                activity_name,
                start_to_close_timeout=timedelta(seconds=10),
                task_queue=WORKFLOW_TASK_QUEUE,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            q = route_dict["task_queue"]
            t = timedelta(seconds=route_dict["timeouts"]["start_to_close_seconds"])
            self._route_cache[activity_name] = (q, t)
            return q, t
            
        self._route_cache[activity_name] = (fallback_queue, fallback_timeout)
        return fallback_queue, fallback_timeout

    async def _ensure_manager_and_signal(
        self,
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool = True,
    ) -> workflow.ExternalWorkflowHandle:
        """Signal the auth-profile-manager; auto-start it on first failure.

        Tries the signal. If the manager workflow doesn't exist, starts it
        via the ``auth_profile.ensure_manager`` activity and retries once.
        
        Note: The Temporal Python SDK does not provide a `signal_with_start`
        method on `workflow.ExternalWorkflowHandle` objects for use inside a
        workflow. Therefore, we use this try/catch/activity/retry pattern to
        emulate the behavior.
        """
        manager_handle = workflow.get_external_workflow_handle(manager_id)
        signal_payload = {
            "requester_workflow_id": workflow.info().workflow_id,
            "runtime_id": runtime_id,
        }
        if not request_slot:
            return manager_handle
        try:
            await manager_handle.signal("request_slot", signal_payload)
        except ApplicationError as exc:
            if "ExternalWorkflowExecutionNotFound" not in (
                getattr(exc, "type", None) or str(exc)
            ):
                raise
            self._get_logger().warning(
                "AuthProfileManager %s not found, auto-starting via activity",
                manager_id,
            )
            q, t = await self._get_route_info("auth_profile.ensure_manager", "mm.activity.artifacts", timedelta(seconds=30))
            await workflow.execute_activity(
                "auth_profile.ensure_manager",
                {"runtime_id": runtime_id},
                task_queue=q,
                start_to_close_timeout=t,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            # Re-acquire handle and retry signal once.
            manager_handle = workflow.get_external_workflow_handle(manager_id)
            await manager_handle.signal("request_slot", signal_payload)
        return manager_handle

    async def _sync_manager_profiles(
        self,
        *,
        manager_handle: workflow.ExternalWorkflowHandle,
        runtime_id: str,
    ) -> int:
        """Best-effort manager refresh from DB-backed auth_profile.list snapshot."""
        try:
            q, t = await self._get_route_info("auth_profile.list", "mm.activity.artifacts", timedelta(seconds=30))
            profile_snapshot = await workflow.execute_activity(
                "auth_profile.list",
                {"runtime_id": runtime_id},
                task_queue=q,
                start_to_close_timeout=t,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            profiles = []
            if isinstance(profile_snapshot, dict):
                raw_profiles = profile_snapshot.get("profiles", [])
                if isinstance(raw_profiles, list):
                    profiles = raw_profiles
            await manager_handle.signal("sync_profiles", {"profiles": profiles})
            return len(profiles)
        except Exception:
            self._get_logger().warning(
                "Failed to sync auth profiles for runtime_id=%s; continuing with manager state",
                runtime_id,
                exc_info=True,
            )
            return -1

    @workflow.signal
    def completion_signal(self, result_dict: dict) -> None:
        self.final_result = AgentRunResult(**result_dict)
        self.run_status = RunStatus.completed
        self.completion_event.set()

    @workflow.signal
    def slot_assigned(self, payload: dict) -> None:
        self._assigned_profile_id = payload.get("profile_id")
        self.slot_assigned_event.set()

    @staticmethod
    def _normalize_external_status(
        *,
        normalized_status: str | None,
        raw_status: object,
        provider_status: str | None,
    ) -> str:
        for candidate in (normalized_status, raw_status, provider_status):
            token = str(candidate or "").strip().lower()
            if not token:
                continue
            mapped = _EXTERNAL_STATUS_TO_RUN_STATUS.get(token)
            if mapped is not None:
                return mapped
        return RunStatus.awaiting_callback

    def _coerce_external_status_payload(
        self,
        *,
        status_payload: object,
        fallback_agent_id: str,
    ) -> AgentRunStatusModel:
        if isinstance(status_payload, AgentRunStatusModel):
            return status_payload

        payload: dict
        if isinstance(status_payload, dict):
            payload = status_payload
        else:
            payload = {
                "external_id": getattr(status_payload, "external_id", None),
                "externalId": getattr(status_payload, "externalId", None),
                "run_id": getattr(status_payload, "run_id", None),
                "runId": getattr(status_payload, "runId", None),
                "status": getattr(status_payload, "status", None),
                "normalized_status": getattr(status_payload, "normalized_status", None),
                "normalizedStatus": getattr(status_payload, "normalizedStatus", None),
                "provider_status": getattr(status_payload, "provider_status", None),
                "providerStatus": getattr(status_payload, "providerStatus", None),
                "url": getattr(status_payload, "url", None),
                "external_url": getattr(status_payload, "external_url", None),
                "externalUrl": getattr(status_payload, "externalUrl", None),
                "tracking_ref": getattr(status_payload, "tracking_ref", None),
                "trackingRef": getattr(status_payload, "trackingRef", None),
                "terminal": getattr(status_payload, "terminal", None),
                "agent_id": getattr(status_payload, "agent_id", None),
                "agentId": getattr(status_payload, "agentId", None),
                "metadata": getattr(status_payload, "metadata", None),
            }

        # Preferred path: canonical AgentRunStatus contract.
        try:
            return AgentRunStatusModel(**payload)
        except Exception:
            pass

        metadata = payload.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}

        external_id = str(
            payload.get("external_id")
            or payload.get("externalId")
            or payload.get("run_id")
            or payload.get("runId")
            or self.run_id
            or ""
        ).strip()
        if not external_id:
            raise ValueError("External status payload is missing external_id/run_id")

        normalized_status = str(
            payload.get("normalized_status")
            or payload.get("normalizedStatus")
            or metadata_dict.get("normalizedStatus")
            or metadata_dict.get("normalized_status")
            or ""
        ).strip().lower() or None
        provider_status = str(
            payload.get("provider_status")
            or payload.get("providerStatus")
            or metadata_dict.get("providerStatus")
            or metadata_dict.get("provider_status")
            or payload.get("status")
            or "unknown"
        ).strip()

        run_status = self._normalize_external_status(
            normalized_status=normalized_status,
            raw_status=payload.get("status"),
            provider_status=provider_status,
        )

        external_url = (
            payload.get("url")
            or payload.get("external_url")
            or payload.get("externalUrl")
            or metadata_dict.get("externalUrl")
            or metadata_dict.get("external_url")
        )
        tracking_ref = (
            payload.get("tracking_ref")
            or payload.get("trackingRef")
            or metadata_dict.get("trackingRef")
            or metadata_dict.get("tracking_ref")
        )
        terminal = bool(payload.get("terminal", run_status in _TERMINAL_RUN_STATUSES))

        agent_id = str(
            payload.get("agent_id")
            or payload.get("agentId")
            or self._external_agent_id
            or fallback_agent_id
        ).strip() or fallback_agent_id

        normalized_for_metadata = normalized_status or "unknown"
        result_metadata = {
            "providerStatus": provider_status,
            "normalizedStatus": normalized_for_metadata,
            "terminal": terminal,
        }
        if external_url is not None:
            result_metadata["externalUrl"] = external_url
        if tracking_ref is not None:
            result_metadata["trackingRef"] = tracking_ref

        return AgentRunStatusModel(
            runId=external_id,
            agentKind="external",
            agentId=agent_id,
            status=run_status,
            metadata=result_metadata,
        )

    @staticmethod
    def _coerce_external_start_status(
        handle_payload: dict[str, object],
    ) -> tuple[str, str]:
        """Map integration-start payload status fields into canonical run states."""
        normalized_token = str(
            handle_payload.get("normalized_status")
            or handle_payload.get("normalizedStatus")
            or ""
        ).strip().lower() or None
        run_status = MoonMindAgentRun._normalize_external_status(
            normalized_status=normalized_token,
            raw_status=handle_payload.get("status"),
            provider_status=str(
                handle_payload.get("provider_status")
                or handle_payload.get("providerStatus")
                or ""
            ).strip() or None,
        )
        valid_statuses = {
            "queued",
            "launching",
            "running",
            "awaiting_callback",
            "awaiting_feedback",
            "awaiting_approval",
            "intervention_requested",
            "collecting_results",
            "completed",
            "failed",
            "canceled",
            "timed_out",
        }
        if run_status not in valid_statuses:
            raise ValueError(f"Unsupported run status from integration start: {run_status!r}")
        return run_status, (normalized_token or run_status)

    @staticmethod
    def _coerce_managed_status_payload(
        *,
        status_payload: object,
        run_id: str,
        fallback_agent_id: str,
    ) -> AgentRunStatusModel:
        if isinstance(status_payload, AgentRunStatusModel):
            return status_payload
        if isinstance(status_payload, dict):
            return AgentRunStatusModel(**status_payload)

        payload = {
            "runId": getattr(status_payload, "run_id", None)
            or getattr(status_payload, "runId", None)
            or run_id,
            "agentKind": "managed",
            "agentId": getattr(status_payload, "agent_id", None)
            or getattr(status_payload, "agentId", None)
            or fallback_agent_id,
            "status": getattr(status_payload, "status", None) or "running",
            "metadata": getattr(status_payload, "metadata", None) or {},
        }
        return AgentRunStatusModel(**payload)

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        self.agent_kind = request.agent_kind

        timeout_seconds = (
            DEFAULT_EXTERNAL_TIMEOUT_SECONDS
            if request.agent_kind == "external"
            else DEFAULT_MANAGED_TIMEOUT_SECONDS
        )
        if request.timeout_policy and "timeout_seconds" in request.timeout_policy:
            timeout_seconds = request.timeout_policy["timeout_seconds"]

        # Loop for handling 429 cooldown & profile swaps safely within the timeout boundary
        overall_start = workflow.now()
        use_managed_status_activity = workflow.patched(
            MANAGED_STATUS_ACTIVITY_PATCH_ID
        )

        try:
            while True:
                skip_poll_and_fetch = False
                elapsed = (workflow.now() - overall_start).total_seconds()
                if elapsed >= timeout_seconds:
                    self.run_status = RunStatus.timed_out
                    return AgentRunResult(failure_class="execution_error")

                manager_handle = None
                # Acquire auth slot if managed
                if request.agent_kind == "managed":
                    runtime_mapping = {
                        "gemini_cli": "gemini_cli",
                        "claude": "claude_code",
                        "codex": "codex_cli",
                    }
                    runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                    manager_id = f"auth-profile-manager:{runtime_id}"

                    self.slot_assigned_event.clear()
                    manager_handle = await self._ensure_manager_and_signal(
                        manager_id,
                        runtime_id,
                        request_slot=True,
                    )
                    profile_count = await self._sync_manager_profiles(
                        manager_handle=manager_handle,
                        runtime_id=runtime_id,
                    )
                    if profile_count == 0:
                        raise ApplicationError(
                            f"No enabled auth profiles found for runtime_id='{runtime_id}'",
                            type="ProfileResolutionError",
                            non_retryable=True,
                        )

                    # Wait indefinitely for an auth profile slot.
                    # Awaiting time does not count against the execution timeout;
                    # overall_start is reset once the slot is acquired.
                    self.run_status = RunStatus.awaiting_slot
                    parent_info = workflow.info().parent
                    if parent_info:
                        parent_handle = workflow.get_external_workflow_handle(
                            parent_info.workflow_id, run_id=parent_info.run_id
                        )
                        await parent_handle.signal(
                            "child_state_changed",
                            args=["awaiting_slot", f"Waiting for auth profile slot on {runtime_id}"]
                        )

                    await workflow.wait_condition(
                        lambda: self.slot_assigned_event.is_set(),
                    )

                    # Reset the execution clock so the timeout budget starts
                    # from slot acquisition, not from workflow start.
                    overall_start = workflow.now()
                    
                    self.run_status = RunStatus.launching
                    if parent_info:
                        parent_handle = workflow.get_external_workflow_handle(
                            parent_info.workflow_id, run_id=parent_info.run_id
                        )
                        await parent_handle.signal(
                            "child_state_changed",
                            args=["launching", f"Slot acquired for {runtime_id}"]
                        )
                    request.execution_profile_ref = self._assigned_profile_id

                    # Notify parent of the assigned profile so it can release the slot
                    # if this child exits in a terminal state (fallback for cancelled
                    # workflows that fail to release their own slot).
                    if parent_info and self._assigned_profile_id:
                        parent_handle = workflow.get_external_workflow_handle(
                            parent_info.workflow_id, run_id=parent_info.run_id
                        )
                        await parent_handle.signal(
                            "profile_assigned",
                            {
                                "profile_id": self._assigned_profile_id,
                                "child_workflow_id": workflow.info().workflow_id,
                            },
                        )

                    # Wire ManagedAgentAdapter with real DI callables.
                    # The slot_requester / slot_releaser / cooldown_reporter
                    # are thin wrappers around AuthProfileManager signals.
                    # The profile_fetcher dispatches to the auth_profile.list
                    # activity on the artifacts fleet.
                    wf_id = workflow.info().workflow_id

                    async def _profile_fetcher(**kw):
                        q, t = await self._get_route_info("auth_profile.list", "mm.activity.artifacts", timedelta(seconds=30))
                        return await workflow.execute_activity(
                            "auth_profile.list",
                            {"runtime_id": kw.get("runtime_id", runtime_id)},
                            task_queue=q,
                            start_to_close_timeout=t,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    async def _slot_requester(**kw):
                        await manager_handle.signal("request_slot", {
                            "requester_workflow_id": wf_id,
                            "runtime_id": kw.get("runtime_id", runtime_id),
                        })

                    async def _slot_releaser(**kw):
                        await manager_handle.signal("release_slot", {
                            "requester_workflow_id": wf_id,
                            "profile_id": kw.get("profile_id", request.execution_profile_ref),
                        })

                    async def _cooldown_reporter(**kw):
                        await manager_handle.signal("report_cooldown", {
                            "profile_id": kw.get("profile_id", request.execution_profile_ref),
                            "cooldown_seconds": kw.get("cooldown_seconds", 300),
                        })

                    async def _run_launcher(**kw):
                        q, t = await self._get_route_info("agent_runtime.launch", AGENT_RUNTIME_TASK_QUEUE, timedelta(seconds=30))
                        return await workflow.execute_activity(
                            "agent_runtime.launch",
                            kw.get("payload", {}),
                            task_queue=q,
                            start_to_close_timeout=t,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    store_root = os.path.join(
                        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
                        "managed_runs",
                    )
                    run_store = ManagedRunStore(store_root)

                    adapter: AgentAdapter = ManagedAgentAdapter(
                        profile_fetcher=_profile_fetcher,
                        slot_requester=_slot_requester,
                        slot_releaser=_slot_releaser,
                        cooldown_reporter=_cooldown_reporter,
                        workflow_id=wf_id,
                        runtime_id=runtime_id,
                        run_store=run_store,
                        run_launcher=_run_launcher,
                    )

                    # --- Managed agent: launch via adapter ---
                    try:
                        handle = await adapter.start(request)
                    except ProfileResolutionError as exc:
                        raise ApplicationError(
                            str(exc),
                            type="ProfileResolutionError",
                            non_retryable=True,
                        ) from exc
                    self.run_id = handle.run_id
                    self.run_status = handle.status
                    poll_interval = handle.poll_hint_seconds or 10

                elif request.agent_kind == "external":
                    # Validate adapter availability in an activity (deterministic-safe).
                    q, t = await self._get_route_info("integration.resolve_external_adapter", WORKFLOW_TASK_QUEUE, timedelta(seconds=30))
                    validated_id = await workflow.execute_activity(
                        resolve_external_adapter,
                        request.agent_id,
                        task_queue=q,
                        start_to_close_timeout=t,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )
                    # Store the validated agent_id for activity routing.
                    self._external_agent_id = validated_id

                    q, t = await self._get_route_info("integration.external_adapter_execution_style", WORKFLOW_TASK_QUEUE, timedelta(seconds=30))
                    execution_style = await workflow.execute_activity(
                        external_adapter_execution_style,
                        validated_id,
                        task_queue=q,
                        start_to_close_timeout=t,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )

                    if execution_style == "streaming_gateway":
                        stc_seconds = min(
                            max(int(timeout_seconds), 60),
                            86400,
                        )
                        q, t = await self._get_route_info("integration.openclaw.execute", INTEGRATIONS_TASK_QUEUE, timedelta(seconds=stc_seconds))
                        result_payload = await workflow.execute_activity(
                            "integration.openclaw.execute",
                            request,
                            task_queue=q,
                            start_to_close_timeout=t,
                            heartbeat_timeout=STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )
                        self.final_result = (
                            AgentRunResult(**result_payload)
                            if isinstance(result_payload, dict)
                            else result_payload
                        )
                        self.run_status = RunStatus.completed
                        adapter = None
                        skip_poll_and_fetch = True
                    else:
                        # --- Multi-step Jules support ---
                        # If a jules_session_id is provided in request.parameters,
                        # this is a continuation step: send a follow-up message
                        # to the existing session instead of creating a new one.
                        jules_session_id = (request.parameters or {}).get("jules_session_id")
                        if jules_session_id and validated_id == "jules":
                            prompt = (request.instruction_ref or "").strip()
                            if not prompt:
                                raise ApplicationError(
                                    "Jules continuation step requires a non-empty instruction_ref (prompt)",
                                    non_retryable=True,
                                )
                            q, t = await self._get_route_info("integration.jules.send_message", INTEGRATIONS_TASK_QUEUE, INTEGRATIONS_ACTIVITY_TIMEOUT)
                            await workflow.execute_activity(
                                "integration.jules.send_message",
                                {
                                    "session_id": jules_session_id,
                                    "prompt": prompt,
                                },
                                task_queue=q,
                                start_to_close_timeout=t,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )
                            self.run_id = jules_session_id
                            self.run_status = "running"
                            poll_interval = 15
                        else:
                            # Start via Temporal activity on the integrations fleet
                            # (determinism-safe: no adapter construction in-workflow).
                            act_name = f"integration.{validated_id}.start"
                            q, t = await self._get_route_info(act_name, INTEGRATIONS_TASK_QUEUE, INTEGRATIONS_ACTIVITY_TIMEOUT)
                            handle_dict = await workflow.execute_activity(
                                act_name,
                                request,
                                task_queue=q,
                                start_to_close_timeout=t,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                            if isinstance(handle_dict, dict) and "external_id" in handle_dict:
                                try:
                                    status, normalized_for_metadata = self._coerce_external_start_status(
                                        handle_dict
                                    )
                                except ValueError as exc:
                                    self._get_logger().warning(str(exc))
                                    raise ApplicationError(
                                        str(exc),
                                        type="UnsupportedStatus",
                                        non_retryable=True,
                                    ) from exc
                                handle = AgentRunHandle(
                                    runId=handle_dict["external_id"],
                                    agentKind="external",
                                    agentId=validated_id,
                                    status=status,
                                    startedAt=workflow.now(),
                                    metadata={
                                        "providerStatus": handle_dict.get("provider_status", handle_dict.get("status", "unknown")),
                                        "normalizedStatus": normalized_for_metadata,
                                        "externalUrl": handle_dict.get("url"),
                                        "callbackSupported": handle_dict.get("callback_supported", False),
                                    }
                                )
                            else:
                                handle = AgentRunHandle(**handle_dict) if isinstance(handle_dict, dict) else handle_dict

                            self.run_id = handle.run_id
                            self.run_status = handle.status
                            poll_interval = handle.poll_hint_seconds or 10
                        adapter = None  # External ops route through activities, not adapter
                else:
                    raise ValueError(f"Unknown agent kind: {request.agent_kind}")

                # Wait for completion checking periodically
                if not skip_poll_and_fetch:
                    while True:
                        remaining_timeout = timeout_seconds - (workflow.now() - overall_start).total_seconds()
                        if remaining_timeout <= 0:
                            break

                        try:
                            # Add bounded wait
                            await workflow.wait_condition(
                                lambda: self.completion_event.is_set(),
                                timeout=timedelta(seconds=min(poll_interval, remaining_timeout))
                            )
                            break  # Callback received
                        except asyncio.TimeoutError:
                            if request.agent_kind == "external":
                                # Poll via Temporal activity (determinism-safe).
                                act_name = f"integration.{self._external_agent_id}.status"
                                q, t = await self._get_route_info(act_name, INTEGRATIONS_TASK_QUEUE, INTEGRATIONS_STATUS_TIMEOUT)
                                status_dict = await workflow.execute_activity(
                                    act_name,
                                    {"external_id": self.run_id},
                                    task_queue=q,
                                    start_to_close_timeout=t,
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )
                                status_obj = self._coerce_external_status_payload(
                                    status_payload=status_dict,
                                    fallback_agent_id=request.agent_id,
                                )
                            else:
                                if use_managed_status_activity:
                                    q, t = await self._get_route_info("agent_runtime.status", AGENT_RUNTIME_TASK_QUEUE, AGENT_RUNTIME_STATUS_TIMEOUT)
                                    status_payload = await workflow.execute_activity(
                                        "agent_runtime.status",
                                        {
                                            "run_id": self.run_id,
                                            "agent_id": request.agent_id,
                                        },
                                        task_queue=q,
                                        start_to_close_timeout=t,
                                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                    )
                                    status_obj = self._coerce_managed_status_payload(
                                        status_payload=status_payload,
                                        run_id=str(self.run_id or ""),
                                        fallback_agent_id=request.agent_id,
                                    )
                                else:
                                    # Legacy replay-compatibility path: older histories
                                    # recorded timer-only polling for managed runs.
                                    # Avoid consulting mutable run-store state during
                                    # replay to keep command sequencing stable.
                                    status_obj = AgentRunStatusModel(
                                        runId=str(self.run_id or ""),
                                        agentKind="managed",
                                        agentId=request.agent_id,
                                        status=RunStatus.running,
                                    )

                            self.run_status = status_obj.status

                            # --- Jules auto-answer sub-flow (spec 094) ---
                            if (
                                status_obj.status == RunStatus.awaiting_feedback
                                and request.agent_kind == "external"
                                and self._external_agent_id == "jules"
                            ):
                                # Read config via activity (determinism-safe)
                                q, t = await self._get_route_info("integration.jules.get_auto_answer_config", INTEGRATIONS_TASK_QUEUE, timedelta(seconds=10))
                                auto_answer_config = await workflow.execute_activity(
                                    "integration.jules.get_auto_answer_config",
                                    [],
                                    task_queue=q,
                                    start_to_close_timeout=t,
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )
                                aa_enabled = auto_answer_config.get("enabled", True) if isinstance(auto_answer_config, dict) else True
                                aa_max = auto_answer_config.get("max_answers", 3) if isinstance(auto_answer_config, dict) else 3

                                if not aa_enabled or self._auto_answer_count >= aa_max:
                                    # Opt-out or max cycles exhausted → escalate
                                    self.run_status = "intervention_requested"
                                    self._get_logger().warning(
                                        "Jules auto-answer %s for session %s (count=%d, max=%d)",
                                        "disabled" if not aa_enabled else "exhausted",
                                        self.run_id,
                                        self._auto_answer_count,
                                        aa_max,
                                    )
                                    break

                                # Extract the question
                                q, t = await self._get_route_info("integration.jules.list_activities", INTEGRATIONS_TASK_QUEUE, INTEGRATIONS_STATUS_TIMEOUT)
                                activities_result = await workflow.execute_activity(
                                    "integration.jules.list_activities",
                                    {"session_id": self.run_id},
                                    task_queue=q,
                                    start_to_close_timeout=t,
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )

                                act_id = activities_result.get("activityId") if isinstance(activities_result, dict) else None
                                question = activities_result.get("latestAgentQuestion") if isinstance(activities_result, dict) else None

                                if question and act_id and act_id not in self._answered_activity_ids:
                                    # Dispatch question-answer cycle
                                    task_context = request.instruction_ref or request.agent_id or ""
                                    q, t = await self._get_route_info("integration.jules.answer_question", INTEGRATIONS_TASK_QUEUE, INTEGRATIONS_ACTIVITY_TIMEOUT)
                                    answer_result = await workflow.execute_activity(
                                        "integration.jules.answer_question",
                                        {
                                            "session_id": self.run_id,
                                            "question": question,
                                            "task_context": task_context,
                                        },
                                        task_queue=q,
                                        start_to_close_timeout=t,
                                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                    )
                                    if isinstance(answer_result, dict) and answer_result.get("answered"):
                                        self._answered_activity_ids.add(act_id)
                                        self._auto_answer_count += 1
                                        self._get_logger().info(
                                            "Jules auto-answer #%d sent for session %s (activity %s)",
                                            self._auto_answer_count,
                                            self.run_id,
                                            act_id,
                                        )
                                    else:
                                        self._get_logger().warning(
                                            "Jules auto-answer failed for session %s: %s",
                                            self.run_id,
                                            answer_result.get("error") if isinstance(answer_result, dict) else "unknown",
                                        )
                                else:
                                    # No new question or already answered this one;
                                    # wait for Jules to transition on its own.
                                    pass

                                # Continue polling regardless of answer success
                                self.run_status = RunStatus.running
                                continue
                            # --- end auto-answer sub-flow ---

                            if status_obj.status in _TERMINAL_RUN_STATUSES:
                                break

                elapsed = (workflow.now() - overall_start).total_seconds()

                if elapsed >= timeout_seconds and not self.completion_event.is_set():
                    self.run_status = RunStatus.timed_out
                    if manager_handle and request.execution_profile_ref:
                        await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                    return AgentRunResult(failure_class="execution_error")

                if self.final_result is None:
                    if request.agent_kind == "external":
                        # Fetch result via Temporal activity.
                        act_name = f"integration.{self._external_agent_id}.fetch_result"
                        q, t = await self._get_route_info(act_name, INTEGRATIONS_TASK_QUEUE, INTEGRATIONS_ACTIVITY_TIMEOUT)
                        result_dict = await workflow.execute_activity(
                            act_name,
                            {"external_id": self.run_id},
                            task_queue=q,
                            start_to_close_timeout=t,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )
                        self.final_result = AgentRunResult(**result_dict) if isinstance(result_dict, dict) else result_dict
                    else:
                        if use_managed_status_activity:
                            q, t = await self._get_route_info("agent_runtime.fetch_result", AGENT_RUNTIME_TASK_QUEUE, AGENT_RUNTIME_ACTIVITY_TIMEOUT)
                            result_payload = await workflow.execute_activity(
                                "agent_runtime.fetch_result",
                                {
                                    "run_id": self.run_id,
                                    "agent_id": request.agent_id,
                                },
                                task_queue=q,
                                start_to_close_timeout=t,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )
                            self.final_result = (
                                AgentRunResult(**result_payload)
                                if isinstance(result_payload, dict)
                                else result_payload
                            )
                        else:
                            # Managed agent legacy path.
                            self.final_result = await adapter.fetch_result(self.run_id)

                # Check for 429
                if request.agent_kind == "managed" and manager_handle and self.final_result.provider_error_code == PROVIDER_RATE_LIMIT_ERROR_CODE:
                    await manager_handle.signal("report_cooldown", {"profile_id": request.execution_profile_ref, "cooldown_seconds": 300})
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                    self.completion_event.clear()
                    self.final_result = None
                    continue # Retries loop

                # Not a 429 or external agent
                if manager_handle and request.execution_profile_ref:
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})

                # Post-run artifact publishing via the agent_runtime activity fleet.
                q, t = await self._get_route_info("agent_runtime.publish_artifacts", AGENT_RUNTIME_TASK_QUEUE, AGENT_RUNTIME_ACTIVITY_TIMEOUT)
                enriched_result = await workflow.execute_activity(
                    "agent_runtime.publish_artifacts",
                    self.final_result.model_dump(mode="json", by_alias=True) if hasattr(self.final_result, "model_dump") else self.final_result,
                    task_queue=q,
                    start_to_close_timeout=t,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )

                if isinstance(enriched_result, dict):
                    # Handle duplicate aliases from older history events
                    if "diagnosticsRef" in enriched_result and "diagnostics_ref" in enriched_result:
                        del enriched_result["diagnostics_ref"]
                    self.final_result = AgentRunResult(**enriched_result)

                # Inject run_id into result metadata so the parent
                # workflow (MoonMind.Run) can track it for multi-step
                # Jules session reuse across plan nodes.
                if self.run_id and hasattr(self.final_result, "metadata"):
                    result_meta = dict(self.final_result.metadata or {})
                    result_meta["jules_session_id"] = self.run_id
                    self.final_result = self.final_result.model_copy(
                        update={"metadata": result_meta}
                    )

                return self.final_result

        except asyncio.TimeoutError:
            self.run_status = RunStatus.timed_out
            if request.agent_kind == "managed" and hasattr(request, "execution_profile_ref") and request.execution_profile_ref:
                try:
                    runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
                    runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                    manager_id = f"auth-profile-manager:{runtime_id}"
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    self._get_logger().warning("Failed to release slot on timeout, which may lead to a leak.", exc_info=True)
            return AgentRunResult(failure_class="execution_error")

        except CancelledError:
            tasks = []

            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
                runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                manager_id = f"auth-profile-manager:{runtime_id}"
                
                async def _release_slot():
                    try:
                        manager_handle = workflow.get_external_workflow_handle(manager_id)
                        await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                    except Exception:
                        # Errors are intentionally ignored to avoid masking the original cancellation
                        self._get_logger().warning("Failed to release slot on cancellation, which may lead to a leak.", exc_info=True)
                
                tasks.append(asyncio.shield(_release_slot()))

            if self.run_id is not None and self.agent_kind is not None:
                async def _cancel_agent():
                    try:
                        if self.agent_kind == "external" and self._external_agent_id is not None:
                            # Route external cancel through integration activity.
                            act_name = f"integration.{self._external_agent_id}.cancel"
                            q, t = await self._get_route_info(act_name, INTEGRATIONS_TASK_QUEUE, AGENT_RUNTIME_CANCEL_TIMEOUT)
                            await workflow.execute_activity(
                                act_name,
                                {"external_id": self.run_id},
                                task_queue=q,
                                start_to_close_timeout=t,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )
                        else:
                            q, t = await self._get_route_info("agent_runtime.cancel", AGENT_RUNTIME_TASK_QUEUE, AGENT_RUNTIME_CANCEL_TIMEOUT)
                            await workflow.execute_activity(
                                "agent_runtime.cancel",
                                {"agent_kind": self.agent_kind, "run_id": self.run_id},
                                task_queue=q,
                                start_to_close_timeout=t,
                            )
                    except Exception:
                        self._get_logger().warning("Failed to cancel agent runtime on cancellation.", exc_info=True)
                
                tasks.append(asyncio.shield(_cancel_agent()))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            raise
            
        except Exception:
            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
                runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                manager_id = f"auth-profile-manager:{runtime_id}"
                try:
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    self._get_logger().warning("Failed to release slot on unexpected exception, which may lead to a leak.", exc_info=True)
            raise
