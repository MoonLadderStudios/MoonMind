from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.managed_session_models import (
        CodexManagedSessionBinding,
        CodexManagedSessionControlRequest,
        CodexManagedSessionPlaneContract,
        CodexManagedSessionSnapshot,
        CodexManagedSessionState,
        CodexManagedSessionWorkflowInput,
        FetchCodexManagedSessionSummaryRequest,
        PublishCodexManagedSessionArtifactsRequest,
        SendCodexManagedSessionTurnRequest,
        CodexManagedSessionClearRequest,
        CodexManagedSessionLocator,
    )
    from moonmind.schemas._validation import require_non_blank
    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityRoute,
        build_default_activity_catalog,
    )


AGENT_SESSION_STATUS_INITIALIZING = "initializing"
AGENT_SESSION_STATUS_ACTIVE = "active"
AGENT_SESSION_STATUS_CLEARING = "clearing"
AGENT_SESSION_STATUS_TERMINATING = "terminating"
AGENT_SESSION_STATUS_TERMINATED = "terminated"
DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()


@workflow.defn(name="MoonMind.AgentSession")
class MoonMindAgentSessionWorkflow:
    def __init__(self) -> None:
        self._contract = CodexManagedSessionPlaneContract()
        self._binding: CodexManagedSessionBinding | None = None
        self._status = AGENT_SESSION_STATUS_INITIALIZING
        self._container_id: str | None = None
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._last_control_action: str | None = None
        self._last_control_reason: str | None = None
        self._termination_requested = False

    @staticmethod
    def _retry_policy_for_route(route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    @classmethod
    def _execute_kwargs_for_route(cls, route: TemporalActivityRoute) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": cls._retry_policy_for_route(route),
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        return kwargs

    async def _execute_routed_activity(
        self,
        activity_name: str,
        payload: dict[str, Any],
    ) -> object:
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(activity_name)
        return await workflow.execute_activity(
            activity_name,
            payload,
            **self._execute_kwargs_for_route(route),
        )

    def _initialize_session(self, session_input: CodexManagedSessionWorkflowInput) -> None:
        self._binding = CodexManagedSessionBinding.from_input(
            workflow_id=workflow.info().workflow_id,
            session_input=session_input,
        )
        self._status = AGENT_SESSION_STATUS_ACTIVE

    def _require_binding(self) -> CodexManagedSessionBinding:
        if self._binding is None:
            raise ValueError("Agent session has not been initialized")
        return self._binding

    def _require_locator(self) -> CodexManagedSessionLocator:
        binding = self._require_binding()
        if not self._container_id or not self._thread_id:
            raise ValueError("Managed session runtime handles are not attached yet")
        return CodexManagedSessionLocator(
            sessionId=binding.session_id,
            sessionEpoch=binding.session_epoch,
            containerId=self._container_id,
            threadId=self._thread_id,
        )

    async def _refresh_continuity_projection(
        self,
        *,
        locator: CodexManagedSessionLocator,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = self._require_binding()
        summary = await self._execute_routed_activity(
            "agent_runtime.fetch_session_summary",
            FetchCodexManagedSessionSummaryRequest(
                sessionId=locator.session_id,
                sessionEpoch=locator.session_epoch,
                containerId=locator.container_id,
                threadId=locator.thread_id,
            ).model_dump(by_alias=True),
        )
        publication = await self._execute_routed_activity(
            "agent_runtime.publish_session_artifacts",
            PublishCodexManagedSessionArtifactsRequest(
                sessionId=locator.session_id,
                sessionEpoch=locator.session_epoch,
                containerId=locator.container_id,
                threadId=locator.thread_id,
                taskRunId=binding.task_run_id,
                metadata=metadata or {},
            ).model_dump(by_alias=True),
        )
        summary_payload = dict(summary if isinstance(summary, dict) else {})
        publication_payload = dict(publication if isinstance(publication, dict) else {})
        return {
            "latestSummaryRef": publication_payload.get("latestSummaryRef")
            or summary_payload.get("latestSummaryRef"),
            "latestCheckpointRef": publication_payload.get("latestCheckpointRef")
            or summary_payload.get("latestCheckpointRef"),
            "latestControlEventRef": publication_payload.get("latestControlEventRef")
            or summary_payload.get("latestControlEventRef"),
            "latestResetBoundaryRef": publication_payload.get("latestResetBoundaryRef")
            or summary_payload.get("latestResetBoundaryRef"),
        }

    @workflow.run
    async def run(
        self, session_input: CodexManagedSessionWorkflowInput
    ) -> dict[str, Any]:
        self._initialize_session(session_input)
        await workflow.wait_condition(lambda: self._termination_requested)
        self._status = AGENT_SESSION_STATUS_TERMINATED
        return self.get_status()

    @workflow.signal(name="attach_runtime_handles")
    def attach_runtime_handles(self, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        container_id = payload.get("containerId") or payload.get("container_id")
        thread_id = payload.get("threadId") or payload.get("thread_id")
        active_turn_id = payload.get("activeTurnId") or payload.get("active_turn_id")
        if container_id is not None:
            self._container_id = str(container_id).strip() or None
        if thread_id is not None:
            self._thread_id = str(thread_id).strip() or None
        if active_turn_id is not None:
            self._active_turn_id = str(active_turn_id).strip() or None

    @workflow.signal(name="control_action")
    def apply_control_action(self, payload: dict[str, Any] | None = None) -> None:
        request = CodexManagedSessionControlRequest.model_validate(payload or {})
        binding = self._require_binding()
        self._last_control_action = request.action
        self._last_control_reason = request.reason

        if request.action == "clear_session":
            if request.thread_id is None:
                raise ValueError("clear_session requires threadId")
            self._status = AGENT_SESSION_STATUS_CLEARING
            if self._container_id and self._thread_id:
                cleared = CodexManagedSessionState(
                    sessionId=binding.session_id,
                    sessionEpoch=binding.session_epoch,
                    containerId=self._container_id,
                    threadId=self._thread_id,
                    activeTurnId=self._active_turn_id,
                ).clear_session(new_thread_id=request.thread_id)
                next_epoch = cleared.session_epoch
                self._thread_id = cleared.thread_id
                self._active_turn_id = cleared.active_turn_id
            else:
                next_epoch = binding.session_epoch + 1
                self._thread_id = request.thread_id
                self._active_turn_id = None
            if request.container_id is not None:
                self._container_id = request.container_id
            self._binding = binding.model_copy(update={"session_epoch": next_epoch})
            self._status = AGENT_SESSION_STATUS_ACTIVE
            return

        if request.action in {"cancel_session", "terminate_session"}:
            self._status = AGENT_SESSION_STATUS_TERMINATING
            self._termination_requested = True
            return

        if request.container_id is not None:
            self._container_id = request.container_id
        if request.thread_id is not None:
            self._thread_id = request.thread_id
        if request.active_turn_id is not None:
            self._active_turn_id = request.active_turn_id

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        snapshot = CodexManagedSessionSnapshot(
            binding=self._require_binding(),
            status=self._status,
            containerId=self._container_id,
            threadId=self._thread_id,
            activeTurnId=self._active_turn_id,
            lastControlAction=self._last_control_action,
            lastControlReason=self._last_control_reason,
            terminationRequested=self._termination_requested,
        )
        return snapshot.model_dump(mode="json", by_alias=True)

    @workflow.update(name="SendFollowUp")
    async def send_follow_up(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        message = require_non_blank(
            str(payload.get("message") or ""),
            field_name="message",
        )
        reason = payload.get("reason")
        reason_text = (
            require_non_blank(str(reason), field_name="reason")
            if reason is not None
            else None
        )
        locator = self._require_locator()
        turn_response = await self._execute_routed_activity(
            "agent_runtime.send_turn",
            SendCodexManagedSessionTurnRequest(
                sessionId=locator.session_id,
                sessionEpoch=locator.session_epoch,
                containerId=locator.container_id,
                threadId=locator.thread_id,
                instructions=message,
                reason=reason_text,
            ).model_dump(by_alias=True),
        )
        turn_payload = dict(turn_response if isinstance(turn_response, dict) else {})
        session_state = dict(turn_payload.get("sessionState") or {})
        if session_state:
            self._container_id = str(session_state.get("containerId") or self._container_id)
            self._thread_id = str(session_state.get("threadId") or self._thread_id)
            active_turn_id = session_state.get("activeTurnId")
            self._active_turn_id = (
                str(active_turn_id).strip() if active_turn_id else None
            )
        self._last_control_action = "send_turn"
        self._last_control_reason = reason_text
        continuity = await self._refresh_continuity_projection(
            locator=self._require_locator(),
            metadata={"action": "send_follow_up", "reason": reason_text},
        )
        return {
            "turnId": turn_payload.get("turnId"),
            "sessionState": session_state,
            **continuity,
        }

    @workflow.update(name="ClearSession")
    async def clear_session_update(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = payload or {}
        reason = payload.get("reason")
        reason_text = (
            require_non_blank(str(reason), field_name="reason")
            if reason is not None
            else None
        )
        binding = self._require_binding()
        locator = self._require_locator()
        self._status = AGENT_SESSION_STATUS_CLEARING
        try:
            next_thread_id = f"thread:{binding.session_id}:{binding.session_epoch + 1}"
            handle = await self._execute_routed_activity(
                "agent_runtime.clear_session",
                CodexManagedSessionClearRequest(
                    sessionId=locator.session_id,
                    sessionEpoch=locator.session_epoch,
                    containerId=locator.container_id,
                    threadId=locator.thread_id,
                    newThreadId=next_thread_id,
                    reason=reason_text,
                ).model_dump(by_alias=True),
            )
            handle_payload = dict(handle if isinstance(handle, dict) else {})
            session_state = dict(handle_payload.get("sessionState") or {})
            self._binding = binding.model_copy(
                update={"session_epoch": session_state.get("sessionEpoch", binding.session_epoch)}
            )
            self._container_id = str(session_state.get("containerId") or self._container_id)
            self._thread_id = str(session_state.get("threadId") or self._thread_id)
            self._active_turn_id = None
            self._last_control_action = "clear_session"
            self._last_control_reason = reason_text
            continuity = await self._refresh_continuity_projection(
                locator=self._require_locator(),
                metadata={"action": "clear_session", "reason": reason_text},
            )
            return {
                "sessionState": session_state,
                **continuity,
            }
        finally:
            self._status = AGENT_SESSION_STATUS_ACTIVE
