from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.managed_session_models import (
        CodexManagedSessionBinding,
        CodexManagedSessionControlRequest,
        CodexManagedSessionPlaneContract,
        CodexManagedSessionSnapshot,
        CodexManagedSessionState,
        CodexManagedSessionWorkflowInput,
    )


AGENT_SESSION_STATUS_INITIALIZING = "initializing"
AGENT_SESSION_STATUS_ACTIVE = "active"
AGENT_SESSION_STATUS_CLEARING = "clearing"
AGENT_SESSION_STATUS_TERMINATING = "terminating"
AGENT_SESSION_STATUS_TERMINATED = "terminated"


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
