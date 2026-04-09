from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.managed_session_models import (
        CODEX_MANAGED_SESSION_CONTROL_ACTIONS,
        CodexManagedSessionArtifactsPublication,
        CodexManagedSessionBinding,
        CodexManagedSessionClearRequest,
        CodexManagedSessionHandle,
        CodexManagedSessionInterruptRequest,
        CodexManagedSessionLocator,
        CodexManagedSessionPlaneContract,
        CodexManagedSessionSendFollowUpRequest,
        CodexManagedSessionSnapshot,
        CodexManagedSessionState,
        CodexManagedSessionSteerRequest,
        CodexManagedSessionSummary,
        CodexManagedSessionTurnResponse,
        CodexManagedSessionWorkflowControlRequest,
        CodexManagedSessionWorkflowInput,
        FetchCodexManagedSessionSummaryRequest,
        InterruptCodexManagedSessionTurnRequest,
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

    @workflow.run
    async def run(
        self, session_input: CodexManagedSessionWorkflowInput
    ) -> dict[str, Any]:
        del session_input
        await workflow.wait_condition(lambda: self._termination_requested)
        self._status = AGENT_SESSION_STATUS_TERMINATED
        return self.get_status()

    @workflow.signal(name="attach_runtime_handles")
    def attach_runtime_handles(self, payload: dict[str, Any] | None = None) -> None:
        payload = dict(payload or {})
        container_id = payload.get("containerId") or payload.get("container_id")
        thread_id = payload.get("threadId") or payload.get("thread_id")
        active_turn_id = payload.get("activeTurnId") or payload.get("active_turn_id")
        session_epoch = payload.get("sessionEpoch") or payload.get("session_epoch")
        last_control_action = payload.get("lastControlAction") or payload.get(
            "last_control_action"
        )
        last_control_reason = payload.get("lastControlReason") or payload.get(
            "last_control_reason"
        )

        if container_id is not None:
            self._container_id = str(container_id).strip() or None
        if thread_id is not None:
            self._thread_id = str(thread_id).strip() or None
        if active_turn_id is not None:
            normalized_turn_id = str(active_turn_id).strip()
            self._active_turn_id = normalized_turn_id or None
        if session_epoch is not None:
            self._binding = self._binding.model_copy(
                update={"session_epoch": int(session_epoch)}
            )
        if last_control_action is not None:
            normalized_action = str(last_control_action).strip()
            if normalized_action and normalized_action not in CODEX_MANAGED_SESSION_CONTROL_ACTIONS:
                raise ValueError(
                    f"Unsupported managed-session control action: {normalized_action}"
                )
            self._last_control_action = normalized_action or None
        if last_control_reason is not None:
            normalized_reason = str(last_control_reason).strip()
            self._last_control_reason = normalized_reason or None

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
            return

        if action in {"cancel_session", "terminate_session"}:
            self._status = AGENT_SESSION_STATUS_TERMINATING
            self._termination_requested = True
            return

        if container_id is not None:
            self._container_id = container_id
        if thread_id is not None:
            self._thread_id = thread_id
        if active_turn_id is not None:
            self._active_turn_id = active_turn_id

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
        request = CodexManagedSessionSendFollowUpRequest.model_validate(payload or {})
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
        return {
            "turnId": response.turn_id,
            "status": response.status,
            "sessionState": session_state,
            **continuity,
        }

    @send_follow_up.validator
    def validate_send_follow_up(self, payload: dict[str, Any] | None = None) -> None:
        CodexManagedSessionSendFollowUpRequest.model_validate(payload or {})
        self._validate_mutation_allowed()
        self._require_locator()

    @workflow.update(name="InterruptTurn")
    async def interrupt_turn_update(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = CodexManagedSessionInterruptRequest.model_validate(payload or {})
        locator = self._require_locator()
        turn_id = self._require_active_turn()
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
        return {
            "turnId": response.turn_id,
            "status": response.status,
            "sessionState": session_state,
            **continuity,
        }

    @interrupt_turn_update.validator
    def validate_interrupt_turn(self, payload: dict[str, Any] | None = None) -> None:
        request = CodexManagedSessionInterruptRequest.model_validate(payload or {})
        self._validate_mutation_allowed()
        self._require_locator()
        self._validate_current_epoch(request.session_epoch)
        self._require_active_turn()

    @workflow.update(name="SteerTurn")
    async def steer_turn_update(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = CodexManagedSessionSteerRequest.model_validate(payload or {})
        locator = self._require_locator()
        turn_id = self._require_active_turn()
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
        return {
            "turnId": response.turn_id,
            "status": response.status,
            "sessionState": session_state,
            **continuity,
        }

    @steer_turn_update.validator
    def validate_steer_turn(self, payload: dict[str, Any] | None = None) -> None:
        request = CodexManagedSessionSteerRequest.model_validate(payload or {})
        self._validate_mutation_allowed()
        self._require_locator()
        self._validate_current_epoch(request.session_epoch)
        self._require_active_turn()

    @workflow.update(name="ClearSession")
    async def clear_session_update(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = CodexManagedSessionWorkflowControlRequest.model_validate(payload or {})
        binding = self._require_binding()
        locator = self._require_locator()
        self._status = AGENT_SESSION_STATUS_CLEARING
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
            return {
                "sessionState": session_state,
                **continuity,
            }
        finally:
            if self._status == AGENT_SESSION_STATUS_CLEARING:
                self._status = AGENT_SESSION_STATUS_ACTIVE

    @clear_session_update.validator
    def validate_clear_session(self, payload: dict[str, Any] | None = None) -> None:
        CodexManagedSessionWorkflowControlRequest.model_validate(payload or {})
        self._validate_mutation_allowed()
        self._require_locator()
        if self._status == AGENT_SESSION_STATUS_CLEARING:
            raise ValueError("Managed session is already clearing")

    @workflow.update(name="CancelSession")
    async def cancel_session_update(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = CodexManagedSessionWorkflowControlRequest.model_validate(payload or {})
        self._status = AGENT_SESSION_STATUS_TERMINATING
        self._termination_requested = True
        self._last_control_action = "cancel_session"
        self._last_control_reason = request.reason
        return self.get_status()

    @cancel_session_update.validator
    def validate_cancel_session(self, payload: dict[str, Any] | None = None) -> None:
        CodexManagedSessionWorkflowControlRequest.model_validate(payload or {})
        self._validate_mutation_allowed()

    @workflow.update(name="TerminateSession")
    async def terminate_session_update(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = CodexManagedSessionWorkflowControlRequest.model_validate(payload or {})
        self._status = AGENT_SESSION_STATUS_TERMINATING
        self._termination_requested = True
        self._last_control_action = "terminate_session"
        self._last_control_reason = request.reason
        return self.get_status()

    @terminate_session_update.validator
    def validate_terminate_session(self, payload: dict[str, Any] | None = None) -> None:
        CodexManagedSessionWorkflowControlRequest.model_validate(payload or {})
        self._validate_mutation_allowed()
