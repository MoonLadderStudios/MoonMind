"""Unit tests for the managed session-plane contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.managed_session_models import (
    CODEX_MANAGED_SESSION_CONTROL_ACTIONS,
    CodexManagedSessionAttachRuntimeHandlesSignal,
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionCancelUpdateRequest,
    CodexManagedSessionClearRequest,
    CodexManagedSessionClearUpdateRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionPlaneContract,
    CodexManagedSessionState,
    CodexManagedSessionSummary,
    CodexManagedSessionTerminateUpdateRequest,
    CodexManagedSessionTurnResponse,
    LaunchCodexManagedSessionRequest,
    SendCodexManagedSessionTurnRequest,
)


def test_codex_managed_session_plane_contract_freezes_phase1_mvp_scope() -> None:
    contract = CodexManagedSessionPlaneContract()

    assert contract.runtime_family == "codex"
    assert contract.protocol == "codex_app_server"
    assert contract.container_backend == "docker"
    assert contract.session_scope == "task"
    assert contract.session_container_policy == "one_container_per_task"
    assert contract.cross_task_reuse is False
    assert contract.log_authority == "artifact_first"
    assert contract.continuity_authority == "artifact_first"
    assert (
        contract.durable_state_rule
        == "artifacts_and_bounded_workflow_metadata_are_authoritative"
    )
    assert contract.clear_behavior == "new_thread_same_container_new_epoch"


def test_codex_managed_session_plane_contract_exposes_canonical_control_actions() -> None:
    contract = CodexManagedSessionPlaneContract()

    assert contract.control_actions == CODEX_MANAGED_SESSION_CONTROL_ACTIONS
    assert contract.control_actions == (
        "start_session",
        "resume_session",
        "send_turn",
        "steer_turn",
        "interrupt_turn",
        "clear_session",
        "cancel_session",
        "terminate_session",
    )


def test_codex_managed_session_plane_contract_rejects_non_canonical_overrides() -> None:
    with pytest.raises(ValidationError, match="Input should be False"):
        CodexManagedSessionPlaneContract(cross_task_reuse=True)

    with pytest.raises(
        ValidationError, match="control_actions must match the canonical"
    ):
        CodexManagedSessionPlaneContract(
            control_actions=("start_session", "send_turn")
        )


def test_codex_managed_session_state_clear_session_bumps_epoch_and_rotates_thread() -> None:
    state = CodexManagedSessionState(
        sessionId="sess-123",
        sessionEpoch=1,
        containerId="ctr-123",
        threadId="thread-1",
        activeTurnId="turn-1",
    )

    cleared = state.clear_session(new_thread_id="thread-2")

    assert cleared.session_id == "sess-123"
    assert cleared.container_id == "ctr-123"
    assert cleared.thread_id == "thread-2"
    assert cleared.session_epoch == 2
    assert cleared.active_turn_id is None


def test_codex_managed_session_state_normalizes_identifier_whitespace() -> None:
    state = CodexManagedSessionState(
        sessionId="  sess-123  ",
        sessionEpoch=1,
        containerId="  ctr-123  ",
        threadId="  thread-1  ",
        activeTurnId="  turn-1  ",
    )

    assert state.session_id == "sess-123"
    assert state.container_id == "ctr-123"
    assert state.thread_id == "thread-1"
    assert state.active_turn_id == "turn-1"


def test_codex_managed_session_state_clear_session_requires_a_new_non_blank_thread() -> None:
    state = CodexManagedSessionState(
        sessionId="sess-123",
        sessionEpoch=1,
        containerId="ctr-123",
        threadId="thread-1",
    )

    with pytest.raises(ValueError, match="new threadId"):
        state.clear_session(new_thread_id="thread-1")

    with pytest.raises(ValueError, match="threadId must not be blank"):
        state.clear_session(new_thread_id="   ")


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("sessionId", "   "),
        ("containerId", "   "),
        ("threadId", "   "),
        ("activeTurnId", "   "),
    ],
)
def test_codex_managed_session_state_rejects_blank_identifiers(
    field_name: str, field_value: str
) -> None:
    kwargs = {
        "sessionId": "sess-123",
        "sessionEpoch": 1,
        "containerId": "ctr-123",
        "threadId": "thread-1",
        field_name: field_value,
    }

    with pytest.raises(ValidationError):
        CodexManagedSessionState(**kwargs)


def test_codex_managed_session_state_requires_epoch_at_least_one() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        CodexManagedSessionState(
            sessionId="sess-123",
            sessionEpoch=0,
            containerId="ctr-123",
            threadId="thread-1",
        )


def test_launch_codex_managed_session_request_freezes_remote_container_defaults() -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-123",
        sessionId="sess-123",
        threadId="thread-1",
        workspacePath="/work/task/repo",
        sessionWorkspacePath="/work/task/session",
        artifactSpoolPath="/work/task/artifacts",
        codexHomePath="/work/task/codex-home",
        imageRef="moonmind:latest",
    )

    assert request.runtime_family == "codex"
    assert request.container_backend == "docker"
    assert request.control_mode == "remote_container"
    assert request.protocol == "codex_app_server"
    assert request.session_epoch == 1


def test_launch_codex_managed_session_request_rejects_local_control_mode() -> None:
    with pytest.raises(ValidationError, match="Input should be 'remote_container'"):
        LaunchCodexManagedSessionRequest(
            taskRunId="task-123",
            sessionId="sess-123",
            threadId="thread-1",
            workspacePath="/work/task/repo",
            sessionWorkspacePath="/work/task/session",
            artifactSpoolPath="/work/task/artifacts",
            codexHomePath="/work/task/codex-home",
            imageRef="moonmind:latest",
            controlMode="local_process",
        )


def test_codex_managed_session_clear_request_requires_new_thread() -> None:
    with pytest.raises(ValidationError, match="must differ from threadId"):
        CodexManagedSessionClearRequest(
            sessionId="sess-123",
            sessionEpoch=1,
            containerId="ctr-123",
            threadId="thread-1",
            newThreadId="thread-1",
        )


def test_codex_managed_session_locator_requires_bounded_identity() -> None:
    locator = CodexManagedSessionLocator(
        sessionId="sess-123",
        sessionEpoch=1,
        containerId="ctr-123",
        threadId="thread-1",
    )

    assert locator.session_id == "sess-123"
    assert locator.session_epoch == 1
    assert locator.container_id == "ctr-123"
    assert locator.thread_id == "thread-1"


@pytest.mark.parametrize("missing_field", ["sessionEpoch", "containerId", "threadId"])
def test_send_turn_request_requires_full_session_locator(
    missing_field: str,
) -> None:
    payload = {
        "sessionId": "sess-123",
        "sessionEpoch": 1,
        "containerId": "ctr-123",
        "threadId": "thread-1",
        "instructions": "Investigate the failing test",
    }
    payload.pop(missing_field)

    with pytest.raises(ValidationError, match=missing_field):
        SendCodexManagedSessionTurnRequest(**payload)


def test_codex_managed_session_handle_exposes_remote_container_contract() -> None:
    handle = CodexManagedSessionHandle(
        sessionState={
            "sessionId": "sess-123",
            "sessionEpoch": 1,
            "containerId": "ctr-123",
            "threadId": "thread-1",
        },
        status="ready",
        imageRef="moonmind:latest",
    )

    assert handle.control_mode == "remote_container"
    assert handle.container_backend == "docker"
    assert handle.session_state.container_id == "ctr-123"


def test_codex_managed_session_turn_response_requires_remote_session_state() -> None:
    response = CodexManagedSessionTurnResponse(
        sessionState={
            "sessionId": "sess-123",
            "sessionEpoch": 1,
            "containerId": "ctr-123",
            "threadId": "thread-1",
            "activeTurnId": "turn-1",
        },
        turnId="turn-1",
        status="running",
    )

    assert response.turn_id == "turn-1"
    assert response.session_state.active_turn_id == "turn-1"


def test_codex_managed_session_summary_and_publication_allow_artifact_refs() -> None:
    summary = CodexManagedSessionSummary(
        sessionState={
            "sessionId": "sess-123",
            "sessionEpoch": 2,
            "containerId": "ctr-123",
            "threadId": "thread-2",
        },
        latestSummaryRef="art-summary",
        latestCheckpointRef="art-checkpoint",
        latestControlEventRef="art-control",
    )
    publication = CodexManagedSessionArtifactsPublication(
        sessionState=summary.session_state,
        publishedArtifactRefs=("art-summary", "art-checkpoint"),
        latestSummaryRef="art-summary",
    )

    assert summary.latest_summary_ref == "art-summary"
    assert publication.published_artifact_refs == ("art-summary", "art-checkpoint")


def test_send_codex_managed_session_turn_request_trims_instruction_and_reason() -> None:
    request = SendCodexManagedSessionTurnRequest(
        sessionId="sess-123",
        sessionEpoch=1,
        containerId="ctr-123",
        threadId="thread-1",
        instructions="  Investigate the failing test  ",
        reason="  Operator follow-up  ",
    )

    assert request.instructions == "Investigate the failing test"
    assert request.reason == "Operator follow-up"


def test_attach_runtime_handles_signal_is_explicit_typed_contract() -> None:
    signal = CodexManagedSessionAttachRuntimeHandlesSignal.model_validate(
        {
            "sessionEpoch": 2,
            "containerId": "  ctr-123  ",
            "threadId": "  thread-2  ",
            "activeTurnId": "  turn-2  ",
            "lastControlAction": "send_turn",
            "lastControlReason": "  operator follow-up  ",
        }
    )

    assert signal.session_epoch == 2
    assert signal.container_id == "ctr-123"
    assert signal.thread_id == "thread-2"
    assert signal.active_turn_id == "turn-2"
    assert signal.last_control_action == "send_turn"
    assert signal.last_control_reason == "operator follow-up"


@pytest.mark.parametrize(
    "model_type",
    [
        CodexManagedSessionClearUpdateRequest,
        CodexManagedSessionCancelUpdateRequest,
        CodexManagedSessionTerminateUpdateRequest,
    ],
)
def test_each_session_control_update_has_its_own_request_model(
    model_type: type[CodexManagedSessionClearUpdateRequest],
) -> None:
    request = model_type.model_validate(
        {"reason": "  operator control  ", "requestId": "  request-1  "}
    )

    assert request.reason == "operator control"
    assert request.request_id == "request-1"


def test_session_control_update_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CodexManagedSessionCancelUpdateRequest.model_validate(
            {"reason": "stop", "action": "terminate_session"}
        )
