from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.managed_session_models import (
        CODEX_MANAGED_SESSION_CONTROL_ACTIONS,
        CodexManagedSessionAttachRuntimeHandlesSignal,
        CodexManagedSessionArtifactsPublication,
        CodexManagedSessionBinding,
        CodexManagedSessionCancelUpdateRequest,
        CodexManagedSessionClearRequest,
        CodexManagedSessionClearUpdateRequest,
        CodexManagedSessionHandle,
        CodexManagedSessionInterruptRequest,
        CodexManagedSessionLocator,
        CodexManagedSessionPlaneContract,
        CodexManagedSessionRequestTrackingEntry,
        CodexManagedSessionSendFollowUpRequest,
        CodexManagedSessionSnapshot,
        CodexManagedSessionState,
        CodexManagedSessionSteerRequest,
        CodexManagedSessionSummary,
        CodexManagedSessionTerminateUpdateRequest,
        CodexManagedSessionTurnResponse,
        CodexManagedSessionWorkflowInput,
        FetchCodexManagedSessionSummaryRequest,
        InterruptCodexManagedSessionTurnRequest,
        TerminateCodexManagedSessionRequest,
        PublishCodexManagedSessionArtifactsRequest,
        SendCodexManagedSessionTurnRequest,
        SteerCodexManagedSessionTurnRequest,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityRoute,
        build_default_activity_catalog,
    )


AGENT_SESSION_STATUS_INITIALIZING = "initializing"
AGENT_SESSION_STATUS_ACTIVE = "active"
AGENT_SESSION_STATUS_CLEARING = "clearing"
AGENT_SESSION_STATUS_TERMINATING = "terminating"
AGENT_SESSION_STATUS_TERMINATED = "terminated"
MAX_REQUEST_TRACKING_ENTRIES = 100
DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()


@workflow.defn(name="MoonMind.AgentSession")
class MoonMindAgentSessionWorkflow:
    @workflow.init
    def __init__(self, session_input: CodexManagedSessionWorkflowInput) -> None:
        self._contract = CodexManagedSessionPlaneContract()
        self._binding = CodexManagedSessionBinding.from_input(
            workflow_id=workflow.info().workflow_id,
            session_input=session_input,
        )
        self._status = AGENT_SESSION_STATUS_ACTIVE
        self._container_id: str | None = None
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._last_control_action: str | None = None
        self._last_control_reason: str | None = None
        self._latest_summary_ref: str | None = None
        self._latest_checkpoint_ref: str | None = None
        self._latest_control_event_ref: str | None = None
        self._latest_reset_boundary_ref: str | None = None
        self._is_degraded = False
        self._continue_as_new_event_threshold = (
            session_input.continue_as_new_event_threshold
        )
        self._request_tracking_state: dict[
            str, CodexManagedSessionRequestTrackingEntry
        ] = {}
        self._termination_requested = False
        self._mutation_lock = asyncio.Lock()
        self._restore_continue_as_new_state(session_input)

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
            summary=self._activity_summary(activity_name, payload),
            **self._execute_kwargs_for_route(route),
        )

    def _activity_summary(
        self, activity_name: str, payload: Mapping[str, Any] | None
    ) -> str:
        action = {
            "agent_runtime.launch_session": "Launch managed Codex session",
            "agent_runtime.send_turn": "Send managed Codex turn",
            "agent_runtime.steer_turn": "Steer managed Codex turn",
            "agent_runtime.interrupt_turn": "Interrupt managed Codex turn",
            "agent_runtime.clear_session": "Clear managed Codex session",
            "agent_runtime.terminate_session": "Terminate managed Codex session",
            "agent_runtime.fetch_session_summary": "Fetch managed Codex session summary",
            "agent_runtime.publish_session_artifacts": "Publish managed Codex session artifacts",
        }.get(activity_name, activity_name)
        identifiers = []
        payload = payload or {}
        for key in ("sessionId", "sessionEpoch", "containerId", "threadId", "turnId"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                identifiers.append(f"{key}={value}")
        return f"{action}: {', '.join(identifiers)}" if identifiers else action

    def _search_attributes(self) -> dict[str, list[str] | list[int] | list[bool]]:
        binding = self._require_binding()
        return {
            "TaskRunId": [binding.task_run_id],
            "RuntimeId": [binding.runtime_id],
            "SessionId": [binding.session_id],
            "SessionEpoch": [binding.session_epoch],
            "SessionStatus": [self._status],
            "IsDegraded": [self._is_degraded],
        }

    def _telemetry_context(self, transition: str) -> dict[str, str | int | bool]:
        binding = self._require_binding()
        context: dict[str, str | int | bool] = {
            "transition": transition,
            "taskRunId": binding.task_run_id,
            "runtimeId": binding.runtime_id,
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
            "sessionStatus": self._status,
            "isDegraded": self._is_degraded,
        }
        for key, value in (
            ("containerId", self._container_id),
            ("threadId", self._thread_id),
            ("turnId", self._active_turn_id),
        ):
            if value:
                context[key] = value
        return context

    def _current_details(self, transition: str) -> str:
        binding = self._require_binding()
        parts = [
            f"Codex managed session {transition}",
            f"session={binding.session_id}",
            f"runtime={binding.runtime_id}",
            f"epoch={binding.session_epoch}",
            f"status={self._status}",
        ]
        if self._container_id:
            parts.append(f"container={self._container_id}")
        if self._thread_id:
            parts.append(f"thread={self._thread_id}")
        if self._active_turn_id:
            parts.append(f"turn={self._active_turn_id}")
        for label, value in (
            ("summaryRef", self._latest_summary_ref),
            ("checkpointRef", self._latest_checkpoint_ref),
            ("controlRef", self._latest_control_event_ref),
            ("resetRef", self._latest_reset_boundary_ref),
        ):
            if value:
                parts.append(f"{label}={value}")
        if self._is_degraded:
            parts.append("degraded=true")
        return " | ".join(parts)

    def _update_operator_visibility(self, transition: str) -> None:
        try:
            workflow.set_current_details(self._current_details(transition))
            workflow.upsert_search_attributes(self._search_attributes())
            workflow.logger.info(
                "managed session transition",
                extra={"managed_session": self._telemetry_context(transition)},
            )
        except Exception as exc:
            # Unit tests instantiate workflow classes outside a Temporal workflow
            # context. Real workflow execution records the visibility update.
            if "NotInWorkflow" in type(exc).__name__ or "Not in workflow" in str(exc):
                return
            raise

    def _mark_degraded(self, transition: str) -> None:
        self._is_degraded = True
        self._update_operator_visibility(transition)

    def _require_binding(self) -> CodexManagedSessionBinding:
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

    def _runtime_handles_attached(self) -> bool:
        return bool(self._container_id and self._thread_id)

    async def _await_runtime_handles(self) -> None:
        if self._runtime_handles_attached():
            return
        await workflow.wait_condition(self._runtime_handles_attached)

    def _validate_mutation_allowed(self) -> None:
        if self._status in {
            AGENT_SESSION_STATUS_TERMINATING,
            AGENT_SESSION_STATUS_TERMINATED,
        } or self._termination_requested:
            raise ValueError("Managed session is terminating or terminated")

    def _validate_current_epoch(self, session_epoch: int) -> None:
        if session_epoch != self._binding.session_epoch:
            raise ValueError(
                f"stale sessionEpoch {session_epoch}; current epoch is {self._binding.session_epoch}"
            )

    def _require_active_turn(self) -> str:
        if not self._active_turn_id:
            raise ValueError("Managed session has no active turn to control")
        return self._active_turn_id

    def _apply_runtime_snapshot(
        self,
        payload: Mapping[str, Any] | None,
        *,
        last_control_action: str | None = None,
        last_control_reason: str | None = None,
    ) -> dict[str, Any]:
        if not payload:
            return {}
        session_state = CodexManagedSessionState.model_validate(dict(payload))
        if session_state.session_id != self._binding.session_id:
            raise ValueError("Managed session response sessionId does not match the workflow binding")
        self._binding = self._binding.model_copy(
            update={"session_epoch": session_state.session_epoch}
        )
        self._container_id = session_state.container_id
        self._thread_id = session_state.thread_id
        self._active_turn_id = session_state.active_turn_id
        if last_control_action is not None:
            self._last_control_action = last_control_action
        if last_control_reason is not None or last_control_action is not None:
            self._last_control_reason = last_control_reason
        return session_state.model_dump(mode="json", by_alias=True)

    def _apply_continuity_projection(self, projection: Mapping[str, Any]) -> None:
        self._latest_summary_ref = projection.get("latestSummaryRef")
        self._latest_checkpoint_ref = projection.get("latestCheckpointRef")
        self._latest_control_event_ref = projection.get("latestControlEventRef")
        self._latest_reset_boundary_ref = projection.get("latestResetBoundaryRef")

    def _record_request_tracking(
        self,
        *,
        request_id: str | None,
        action: str,
        status: str,
        result_ref: str | None = None,
        session_epoch: int | None = None,
    ) -> None:
        if request_id is None:
            return
        entry = CodexManagedSessionRequestTrackingEntry(
            requestId=request_id,
            action=action,
            sessionEpoch=session_epoch or self._binding.session_epoch,
            status=status,
            resultRef=result_ref,
        )
        self._request_tracking_state[entry.request_id] = entry
        while len(self._request_tracking_state) > MAX_REQUEST_TRACKING_ENTRIES:
            oldest_request_id = next(iter(self._request_tracking_state))
            del self._request_tracking_state[oldest_request_id]

    def _request_tracking_id(self, caller_request_id: str | None) -> str | None:
        update_info = workflow.current_update_info()
        if update_info is not None and update_info.id:
            return update_info.id
        return caller_request_id

    def _validate_request_not_completed(
        self,
        *,
        request_id: str | None,
        action: str,
    ) -> None:
        if request_id is None:
            return
        existing = self._request_tracking_state.get(request_id)
        if existing is None:
            return
        if existing.action != action:
            raise ValueError(
                f"Managed session request {request_id} already used for action {existing.action}"
            )
        if existing.status == "completed":
            raise ValueError(
                f"Managed session request {request_id} already completed as {existing.action}"
            )

    async def _refresh_continuity_projection(
        self,
        *,
        locator: CodexManagedSessionLocator,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = self._require_binding()
        summary = CodexManagedSessionSummary.model_validate(
            await self._execute_routed_activity(
                "agent_runtime.fetch_session_summary",
                FetchCodexManagedSessionSummaryRequest(
                    sessionId=locator.session_id,
                    sessionEpoch=locator.session_epoch,
                    containerId=locator.container_id,
                    threadId=locator.thread_id,
                ).model_dump(by_alias=True),
            )
        )
        publication = CodexManagedSessionArtifactsPublication.model_validate(
            await self._execute_routed_activity(
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
        )
        return {
            "latestSummaryRef": publication.latest_summary_ref
            or summary.latest_summary_ref,
            "latestCheckpointRef": publication.latest_checkpoint_ref
            or summary.latest_checkpoint_ref,
            "latestControlEventRef": publication.latest_control_event_ref
            or summary.latest_control_event_ref,
            "latestResetBoundaryRef": publication.latest_reset_boundary_ref
            or summary.latest_reset_boundary_ref,
        }

    def _restore_continue_as_new_state(
        self,
        session_input: CodexManagedSessionWorkflowInput,
    ) -> None:
        self._container_id = session_input.container_id
        self._thread_id = session_input.thread_id
        self._active_turn_id = session_input.active_turn_id
        self._last_control_action = session_input.last_control_action
        self._last_control_reason = session_input.last_control_reason
        self._latest_summary_ref = session_input.latest_summary_ref
        self._latest_checkpoint_ref = session_input.latest_checkpoint_ref
        self._latest_control_event_ref = session_input.latest_control_event_ref
        self._latest_reset_boundary_ref = session_input.latest_reset_boundary_ref
        self._request_tracking_state = {}
        for entry in session_input.request_tracking_state:
            self._request_tracking_state[entry.request_id] = entry
            while len(self._request_tracking_state) > MAX_REQUEST_TRACKING_ENTRIES:
                oldest_request_id = next(iter(self._request_tracking_state))
                del self._request_tracking_state[oldest_request_id]

    def _build_continue_as_new_input(self) -> CodexManagedSessionWorkflowInput:
        binding = self._require_binding()
        payload: dict[str, Any] = {
            "taskRunId": binding.task_run_id,
            "runtimeId": binding.runtime_id,
            "sessionId": binding.session_id,
            "sessionEpoch": binding.session_epoch,
        }
        optional_fields = {
            "executionProfileRef": binding.execution_profile_ref,
            "containerId": self._container_id,
            "threadId": self._thread_id,
            "activeTurnId": self._active_turn_id,
            "lastControlAction": self._last_control_action,
            "lastControlReason": self._last_control_reason,
            "latestSummaryRef": self._latest_summary_ref,
            "latestCheckpointRef": self._latest_checkpoint_ref,
            "latestControlEventRef": self._latest_control_event_ref,
            "latestResetBoundaryRef": self._latest_reset_boundary_ref,
            "continueAsNewEventThreshold": self._continue_as_new_event_threshold,
            "requestTrackingState": [
                entry.model_dump(mode="json", by_alias=True)
                for entry in self._request_tracking_state.values()
            ]
            or None,
        }
        payload.update(
            {key: value for key, value in optional_fields.items() if value is not None}
        )
        return CodexManagedSessionWorkflowInput.model_validate(payload)

    def _should_continue_as_new(self) -> bool:
        if self._termination_requested:
            return False
        info = workflow.info()
        is_continue_as_new_suggested = getattr(
            info, "is_continue_as_new_suggested", False
        )
        if (
            is_continue_as_new_suggested()
            if callable(is_continue_as_new_suggested)
            else bool(is_continue_as_new_suggested)
        ):
            return True
        threshold = self._continue_as_new_event_threshold
        history_length = getattr(info, "get_current_history_length", None)
        return bool(
            threshold is not None
            and callable(history_length)
            and history_length() >= threshold
        )

    @workflow.run
    async def run(
        self, session_input: CodexManagedSessionWorkflowInput
    ) -> dict[str, Any]:
        del session_input
        self._update_operator_visibility("session started")
        while not self._termination_requested:
            await workflow.wait_condition(
                lambda: self._termination_requested or self._should_continue_as_new()
            )
            if self._should_continue_as_new():
                await workflow.wait_condition(lambda: workflow.all_handlers_finished)
                workflow.continue_as_new(self._build_continue_as_new_input())
        await workflow.wait_condition(lambda: workflow.all_handlers_finished)
        self._status = AGENT_SESSION_STATUS_TERMINATED
        return self.get_status()

    @workflow.signal(name="attach_runtime_handles")
    def attach_runtime_handles(
        self,
        payload: CodexManagedSessionAttachRuntimeHandlesSignal,
    ) -> None:
        payload = CodexManagedSessionAttachRuntimeHandlesSignal.model_validate(payload)
        if payload.container_id is not None:
            self._container_id = payload.container_id
        if payload.thread_id is not None:
            self._thread_id = payload.thread_id
        if payload.active_turn_id is not None:
            self._active_turn_id = payload.active_turn_id
        if payload.session_epoch is not None:
            self._binding = self._binding.model_copy(
                update={"session_epoch": payload.session_epoch}
            )
        if payload.last_control_action is not None:
            self._last_control_action = payload.last_control_action
        if payload.last_control_reason is not None:
            self._last_control_reason = payload.last_control_reason
        self._update_operator_visibility("session started")

    @workflow.signal(name="control_action")
    def apply_control_action(self, payload: dict[str, Any] | None = None) -> None:
        # Preserve replay compatibility for in-flight histories that already contain
        # legacy control_action signal events and for internal child-workflow signals.
        payload = dict(payload or {})
        raw_action = payload.get("action")
        action = str(raw_action).strip() if raw_action is not None else ""
        if not action:
            raise ValueError("control_action requires action")
        if action not in CODEX_MANAGED_SESSION_CONTROL_ACTIONS:
            raise ValueError(f"Unsupported managed-session control action: {action}")

        reason_value = payload.get("reason")
        container_value = payload.get("containerId") or payload.get("container_id")
        thread_value = payload.get("threadId") or payload.get("thread_id")
        active_turn_value = payload.get("activeTurnId") or payload.get("active_turn_id")

        reason = str(reason_value).strip() or None if reason_value is not None else None
        container_id = (
            str(container_value).strip() or None if container_value is not None else None
        )
        thread_id = str(thread_value).strip() or None if thread_value is not None else None
        active_turn_id = (
            str(active_turn_value).strip() or None
            if active_turn_value is not None
            else None
        )

        binding = self._require_binding()
        self._last_control_action = action
        self._last_control_reason = reason

        if action == "clear_session":
            if thread_id is None:
                raise ValueError("clear_session requires threadId")
            self._status = AGENT_SESSION_STATUS_CLEARING
            if self._container_id and self._thread_id:
                cleared = CodexManagedSessionState(
                    sessionId=binding.session_id,
                    sessionEpoch=binding.session_epoch,
                    containerId=self._container_id,
                    threadId=self._thread_id,
                    activeTurnId=self._active_turn_id,
                ).clear_session(new_thread_id=thread_id)
                next_epoch = cleared.session_epoch
                self._thread_id = cleared.thread_id
                self._active_turn_id = cleared.active_turn_id
            else:
                next_epoch = binding.session_epoch + 1
                self._thread_id = thread_id
                self._active_turn_id = None
            if container_id is not None:
                self._container_id = container_id
            self._binding = binding.model_copy(update={"session_epoch": next_epoch})
            self._status = AGENT_SESSION_STATUS_ACTIVE
            self._update_operator_visibility("cleared to new epoch")
            return

        if action in {"cancel_session", "terminate_session"}:
            self._status = AGENT_SESSION_STATUS_TERMINATING
            self._termination_requested = True
            self._update_operator_visibility("terminating")
            return

        if container_id is not None:
            self._container_id = container_id
        if thread_id is not None:
            self._thread_id = thread_id
        if active_turn_id is not None:
            self._active_turn_id = active_turn_id
        self._update_operator_visibility("session updated")

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
            latestSummaryRef=self._latest_summary_ref,
            latestCheckpointRef=self._latest_checkpoint_ref,
            latestControlEventRef=self._latest_control_event_ref,
            latestResetBoundaryRef=self._latest_reset_boundary_ref,
            terminationRequested=self._termination_requested,
            requestTrackingState=tuple(self._request_tracking_state.values()),
        )
        return snapshot.model_dump(mode="json", by_alias=True)

    @workflow.update(name="SendFollowUp")
    async def send_follow_up(
        self,
        payload: CodexManagedSessionSendFollowUpRequest,
    ) -> dict[str, Any]:
        request = CodexManagedSessionSendFollowUpRequest.model_validate(payload)
        await self._await_runtime_handles()
        async with self._mutation_lock:
            self._validate_mutation_allowed()
            request_tracking_id = self._request_tracking_id(request.request_id)
            self._validate_request_not_completed(
                request_id=request_tracking_id,
                action="send_turn",
            )
            request_epoch = self._binding.session_epoch
            self._record_request_tracking(
                request_id=request_tracking_id,
                action="send_turn",
                status="accepted",
                session_epoch=request_epoch,
            )
            try:
                self._status = AGENT_SESSION_STATUS_ACTIVE
                self._update_operator_visibility("active turn running")
                locator = self._require_locator()
                response = CodexManagedSessionTurnResponse.model_validate(
                    await self._execute_routed_activity(
                        "agent_runtime.send_turn",
                        SendCodexManagedSessionTurnRequest(
                            sessionId=locator.session_id,
                            sessionEpoch=locator.session_epoch,
                            containerId=locator.container_id,
                            threadId=locator.thread_id,
                            instructions=request.message,
                            reason=request.reason,
                        ).model_dump(by_alias=True),
                    )
                )
                session_state = self._apply_runtime_snapshot(
                    response.session_state.model_dump(mode="json", by_alias=True),
                    last_control_action="send_turn",
                    last_control_reason=request.reason,
                )
                continuity = await self._refresh_continuity_projection(
                    locator=self._require_locator(),
                    metadata={"action": "send_follow_up", "reason": request.reason},
                )
                self._apply_continuity_projection(continuity)
                self._is_degraded = False
                self._update_operator_visibility("session started")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="send_turn",
                    status="completed",
                    result_ref=continuity.get("latestControlEventRef")
                    or continuity.get("latestSummaryRef"),
                    session_epoch=request_epoch,
                )
                return {
                    "turnId": response.turn_id,
                    "status": response.status,
                    "sessionState": session_state,
                    **continuity,
                }
            except Exception:
                self._mark_degraded("degraded")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="send_turn",
                    status="failed",
                    session_epoch=request_epoch,
                )
                raise

    @send_follow_up.validator
    def validate_send_follow_up(
        self,
        payload: CodexManagedSessionSendFollowUpRequest,
    ) -> None:
        CodexManagedSessionSendFollowUpRequest.model_validate(payload)
        self._validate_mutation_allowed()

    @workflow.update(name="InterruptTurn")
    async def interrupt_turn_update(
        self,
        payload: CodexManagedSessionInterruptRequest,
    ) -> dict[str, Any]:
        request = CodexManagedSessionInterruptRequest.model_validate(payload)
        await self._await_runtime_handles()
        async with self._mutation_lock:
            self._validate_mutation_allowed()
            self._validate_current_epoch(request.session_epoch)
            request_tracking_id = self._request_tracking_id(request.request_id)
            self._validate_request_not_completed(
                request_id=request_tracking_id,
                action="interrupt_turn",
            )
            request_epoch = self._binding.session_epoch
            self._record_request_tracking(
                request_id=request_tracking_id,
                action="interrupt_turn",
                status="accepted",
                session_epoch=request_epoch,
            )
            try:
                locator = self._require_locator()
                turn_id = self._require_active_turn()
                self._update_operator_visibility("active turn running")
                response = CodexManagedSessionTurnResponse.model_validate(
                    await self._execute_routed_activity(
                        "agent_runtime.interrupt_turn",
                        InterruptCodexManagedSessionTurnRequest(
                            sessionId=locator.session_id,
                            sessionEpoch=locator.session_epoch,
                            containerId=locator.container_id,
                            threadId=locator.thread_id,
                            turnId=turn_id,
                            reason=request.reason,
                        ).model_dump(by_alias=True),
                    )
                )
                session_state = self._apply_runtime_snapshot(
                    response.session_state.model_dump(mode="json", by_alias=True),
                    last_control_action="interrupt_turn",
                    last_control_reason=request.reason,
                )
                continuity = await self._refresh_continuity_projection(
                    locator=self._require_locator(),
                    metadata={"action": "interrupt_turn", "reason": request.reason},
                )
                self._apply_continuity_projection(continuity)
                self._is_degraded = False
                self._update_operator_visibility("interrupted")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="interrupt_turn",
                    status="completed",
                    result_ref=continuity.get("latestControlEventRef")
                    or continuity.get("latestSummaryRef"),
                    session_epoch=request_epoch,
                )
                return {
                    "turnId": response.turn_id,
                    "status": response.status,
                    "sessionState": session_state,
                    **continuity,
                }
            except Exception:
                self._mark_degraded("degraded")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="interrupt_turn",
                    status="failed",
                    session_epoch=request_epoch,
                )
                raise

    @interrupt_turn_update.validator
    def validate_interrupt_turn(
        self,
        payload: CodexManagedSessionInterruptRequest,
    ) -> None:
        request = CodexManagedSessionInterruptRequest.model_validate(payload)
        self._validate_mutation_allowed()
        self._validate_current_epoch(request.session_epoch)
        if self._runtime_handles_attached():
            self._require_active_turn()

    @workflow.update(name="SteerTurn")
    async def steer_turn_update(
        self,
        payload: CodexManagedSessionSteerRequest,
    ) -> dict[str, Any]:
        request = CodexManagedSessionSteerRequest.model_validate(payload)
        await self._await_runtime_handles()
        async with self._mutation_lock:
            self._validate_mutation_allowed()
            self._validate_current_epoch(request.session_epoch)
            request_tracking_id = self._request_tracking_id(request.request_id)
            self._validate_request_not_completed(
                request_id=request_tracking_id,
                action="steer_turn",
            )
            request_epoch = self._binding.session_epoch
            self._record_request_tracking(
                request_id=request_tracking_id,
                action="steer_turn",
                status="accepted",
                session_epoch=request_epoch,
            )
            try:
                locator = self._require_locator()
                turn_id = self._require_active_turn()
                self._update_operator_visibility("active turn running")
                response = CodexManagedSessionTurnResponse.model_validate(
                    await self._execute_routed_activity(
                        "agent_runtime.steer_turn",
                        SteerCodexManagedSessionTurnRequest(
                            sessionId=locator.session_id,
                            sessionEpoch=locator.session_epoch,
                            containerId=locator.container_id,
                            threadId=locator.thread_id,
                            turnId=turn_id,
                            instructions=request.message,
                            metadata={
                                **({"reason": request.reason} if request.reason else {}),
                            },
                        ).model_dump(by_alias=True),
                    )
                )
                session_state = self._apply_runtime_snapshot(
                    response.session_state.model_dump(mode="json", by_alias=True),
                    last_control_action="steer_turn",
                    last_control_reason=request.reason,
                )
                continuity = await self._refresh_continuity_projection(
                    locator=self._require_locator(),
                    metadata={"action": "steer_turn", "reason": request.reason},
                )
                self._apply_continuity_projection(continuity)
                self._is_degraded = False
                self._update_operator_visibility("active turn running")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="steer_turn",
                    status="completed",
                    result_ref=continuity.get("latestControlEventRef")
                    or continuity.get("latestSummaryRef"),
                    session_epoch=request_epoch,
                )
                return {
                    "turnId": response.turn_id,
                    "status": response.status,
                    "sessionState": session_state,
                    **continuity,
                }
            except Exception:
                self._mark_degraded("degraded")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="steer_turn",
                    status="failed",
                    session_epoch=request_epoch,
                )
                raise

    @steer_turn_update.validator
    def validate_steer_turn(
        self,
        payload: CodexManagedSessionSteerRequest,
    ) -> None:
        request = CodexManagedSessionSteerRequest.model_validate(payload)
        self._validate_mutation_allowed()
        self._validate_current_epoch(request.session_epoch)
        if self._runtime_handles_attached():
            self._require_active_turn()

    @workflow.update(name="ClearSession")
    async def clear_session_update(
        self, payload: CodexManagedSessionClearUpdateRequest
    ) -> dict[str, Any]:
        request = CodexManagedSessionClearUpdateRequest.model_validate(payload)
        await self._await_runtime_handles()
        async with self._mutation_lock:
            self._validate_mutation_allowed()
            if self._status == AGENT_SESSION_STATUS_CLEARING:
                raise ValueError("Managed session is already clearing")
            request_tracking_id = self._request_tracking_id(request.request_id)
            self._validate_request_not_completed(
                request_id=request_tracking_id,
                action="clear_session",
            )
            request_epoch = self._binding.session_epoch
            self._record_request_tracking(
                request_id=request_tracking_id,
                action="clear_session",
                status="accepted",
                session_epoch=request_epoch,
            )
            binding = self._require_binding()
            locator = self._require_locator()
            self._status = AGENT_SESSION_STATUS_CLEARING
            self._update_operator_visibility("clearing")
            try:
                next_thread_id = f"thread:{binding.session_id}:{binding.session_epoch + 1}"
                handle = CodexManagedSessionHandle.model_validate(
                    await self._execute_routed_activity(
                        "agent_runtime.clear_session",
                        CodexManagedSessionClearRequest(
                            sessionId=locator.session_id,
                            sessionEpoch=locator.session_epoch,
                            containerId=locator.container_id,
                            threadId=locator.thread_id,
                            newThreadId=next_thread_id,
                            reason=request.reason,
                        ).model_dump(by_alias=True),
                    )
                )
                session_state = self._apply_runtime_snapshot(
                    handle.session_state.model_dump(mode="json", by_alias=True),
                    last_control_action="clear_session",
                    last_control_reason=request.reason,
                )
                continuity = await self._refresh_continuity_projection(
                    locator=self._require_locator(),
                    metadata={"action": "clear_session", "reason": request.reason},
                )
                self._apply_continuity_projection(continuity)
                self._is_degraded = False
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="clear_session",
                    status="completed",
                    result_ref=continuity.get("latestResetBoundaryRef")
                    or continuity.get("latestControlEventRef")
                    or continuity.get("latestSummaryRef"),
                    session_epoch=request_epoch,
                )
                return {
                    "sessionState": session_state,
                    **continuity,
                }
            except Exception:
                self._mark_degraded("degraded")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="clear_session",
                    status="failed",
                    session_epoch=request_epoch,
                )
                raise
            finally:
                if self._status == AGENT_SESSION_STATUS_CLEARING:
                    self._status = AGENT_SESSION_STATUS_ACTIVE
                    transition = (
                        "cleared to new epoch"
                        if not self._is_degraded
                        and self._last_control_action == "clear_session"
                        else "session started"
                    )
                    self._update_operator_visibility(transition)

    @clear_session_update.validator
    def validate_clear_session(
        self,
        payload: CodexManagedSessionClearUpdateRequest,
    ) -> None:
        CodexManagedSessionClearUpdateRequest.model_validate(payload)
        self._validate_mutation_allowed()
        if self._status == AGENT_SESSION_STATUS_CLEARING:
            raise ValueError("Managed session is already clearing")

    @workflow.update(name="CancelSession")
    async def cancel_session_update(
        self, payload: CodexManagedSessionCancelUpdateRequest
    ) -> dict[str, Any]:
        request = CodexManagedSessionCancelUpdateRequest.model_validate(payload)
        async with self._mutation_lock:
            request_tracking_id = self._request_tracking_id(request.request_id)
            self._validate_request_not_completed(
                request_id=request_tracking_id,
                action="cancel_session",
            )
            request_epoch = self._binding.session_epoch
            self._record_request_tracking(
                request_id=request_tracking_id,
                action="cancel_session",
                status="accepted",
                session_epoch=request_epoch,
            )
            try:
                locator: CodexManagedSessionLocator | None = None
                turn_id: str | None = None
                if self._container_id and self._thread_id and self._active_turn_id:
                    locator = self._require_locator()
                    turn_id = self._active_turn_id
                if locator is not None and turn_id is not None:
                    response = CodexManagedSessionTurnResponse.model_validate(
                        await self._execute_routed_activity(
                            "agent_runtime.interrupt_turn",
                            InterruptCodexManagedSessionTurnRequest(
                                sessionId=locator.session_id,
                                sessionEpoch=locator.session_epoch,
                                containerId=locator.container_id,
                                threadId=locator.thread_id,
                                turnId=turn_id,
                                reason=request.reason,
                            ).model_dump(by_alias=True),
                        )
                    )
                    self._apply_runtime_snapshot(
                        response.session_state.model_dump(mode="json", by_alias=True)
                    )
                self._status = AGENT_SESSION_STATUS_ACTIVE
                self._last_control_action = "cancel_session"
                self._last_control_reason = request.reason
                self._is_degraded = False
                self._update_operator_visibility("interrupted")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="cancel_session",
                    status="completed",
                    session_epoch=request_epoch,
                )
                return self.get_status()
            except Exception:
                self._mark_degraded("degraded")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="cancel_session",
                    status="failed",
                    session_epoch=request_epoch,
                )
                raise

    @cancel_session_update.validator
    def validate_cancel_session(
        self,
        payload: CodexManagedSessionCancelUpdateRequest,
    ) -> None:
        CodexManagedSessionCancelUpdateRequest.model_validate(payload)
        self._validate_mutation_allowed()

    @workflow.update(name="TerminateSession")
    async def terminate_session_update(
        self, payload: CodexManagedSessionTerminateUpdateRequest
    ) -> dict[str, Any]:
        request = CodexManagedSessionTerminateUpdateRequest.model_validate(payload)
        async with self._mutation_lock:
            if self._termination_requested:
                return self.get_status()
            request_tracking_id = self._request_tracking_id(request.request_id)
            self._validate_request_not_completed(
                request_id=request_tracking_id,
                action="terminate_session",
            )
            request_epoch = self._binding.session_epoch
            self._record_request_tracking(
                request_id=request_tracking_id,
                action="terminate_session",
                status="accepted",
                session_epoch=request_epoch,
            )
            try:
                self._status = AGENT_SESSION_STATUS_TERMINATING
                self._last_control_action = "terminate_session"
                self._last_control_reason = request.reason
                self._update_operator_visibility("terminating")
                if self._container_id and self._thread_id:
                    locator = self._require_locator()
                    handle = CodexManagedSessionHandle.model_validate(
                        await self._execute_routed_activity(
                            "agent_runtime.terminate_session",
                            TerminateCodexManagedSessionRequest(
                                sessionId=locator.session_id,
                                sessionEpoch=locator.session_epoch,
                                containerId=locator.container_id,
                                threadId=locator.thread_id,
                                reason=request.reason,
                            ).model_dump(by_alias=True),
                        )
                    )
                    self._apply_runtime_snapshot(
                        handle.session_state.model_dump(mode="json", by_alias=True),
                        last_control_action="terminate_session",
                        last_control_reason=request.reason,
                    )
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="terminate_session",
                    status="completed",
                    session_epoch=request_epoch,
                )
                self._termination_requested = True
                self._is_degraded = False
                self._update_operator_visibility("terminated")
                return self.get_status()
            except Exception:
                self._mark_degraded("degraded")
                self._record_request_tracking(
                    request_id=request_tracking_id,
                    action="terminate_session",
                    status="failed",
                    session_epoch=request_epoch,
                )
                raise

    @terminate_session_update.validator
    def validate_terminate_session(
        self,
        payload: CodexManagedSessionTerminateUpdateRequest,
    ) -> None:
        CodexManagedSessionTerminateUpdateRequest.model_validate(payload)
        if (
            self._status == AGENT_SESSION_STATUS_TERMINATING
            and self._last_control_action == "terminate_session"
        ):
            return
        self._validate_mutation_allowed()
