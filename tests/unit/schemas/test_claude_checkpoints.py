"""Unit tests for Claude checkpoint and rewind contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas import CLAUDE_REWIND_MODES as EXPORTED_CLAUDE_REWIND_MODES
from moonmind.schemas.managed_session_models import (
    CLAUDE_CHECKPOINT_CAPTURE_MODES,
    CLAUDE_CHECKPOINT_RETENTION_STATES,
    CLAUDE_CHECKPOINT_TRIGGERS,
    CLAUDE_CHECKPOINT_WORK_EVENT_NAMES,
    CLAUDE_REWIND_MODES,
    ClaudeCheckpoint,
    ClaudeCheckpointIndex,
    ClaudeManagedWorkItem,
    ClaudeRewindRequest,
    ClaudeRewindResult,
    claude_checkpoint_capture_decision,
    create_claude_checkpoint_work_item,
    create_claude_rewind_work_items,
)


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def _checkpoint(**overrides: object) -> ClaudeCheckpoint:
    payload: dict[str, object] = {
        "checkpointId": "checkpoint-1",
        "sessionId": "claude-session-1",
        "turnId": "turn-1",
        "trigger": "tracked_file_edit",
        "captureMode": "code_and_conversation",
        "status": "captured",
        "storageRef": "runtime-local://checkpoints/checkpoint-1",
        "isActive": True,
        "retentionState": "addressable",
        "createdAt": NOW,
        "eventLogRef": "artifact://events/pre-rewind",
        "metadata": {"changedPathCount": 2},
    }
    payload.update(overrides)
    return ClaudeCheckpoint(**payload)


def test_documented_checkpoint_triggers_and_capture_modes_are_exported() -> None:
    assert CLAUDE_CHECKPOINT_TRIGGERS == (
        "user_prompt",
        "tracked_file_edit",
        "bash_side_effect",
        "external_manual_edit",
    )
    assert CLAUDE_CHECKPOINT_CAPTURE_MODES == (
        "conversation",
        "code_and_conversation",
        "code",
        "best_effort",
        "skipped",
    )
    assert CLAUDE_CHECKPOINT_RETENTION_STATES == (
        "addressable",
        "expires_at",
        "expired",
        "garbage_collected",
    )
    assert CLAUDE_REWIND_MODES == (
        "restore_code_and_conversation",
        "restore_conversation_only",
        "restore_code_only",
        "summarize_from_here",
    )
    assert EXPORTED_CLAUDE_REWIND_MODES == CLAUDE_REWIND_MODES


@pytest.mark.parametrize(
    ("trigger", "should_create", "capture_mode"),
    [
        ("user_prompt", True, "conversation"),
        ("tracked_file_edit", True, "code_and_conversation"),
        ("bash_side_effect", False, "skipped"),
        ("external_manual_edit", True, "best_effort"),
    ],
)
def test_checkpoint_capture_decision_follows_documented_defaults(
    trigger: str,
    should_create: bool,
    capture_mode: str,
) -> None:
    decision = claude_checkpoint_capture_decision(trigger)

    assert decision.trigger == trigger
    assert decision.should_create_checkpoint is should_create
    assert decision.capture_mode == capture_mode
    assert decision.reason


def test_checkpoint_rejects_bash_side_effect_code_state_capture() -> None:
    with pytest.raises(ValidationError, match="Bash side effects"):
        _checkpoint(
            trigger="bash_side_effect",
            captureMode="code_and_conversation",
        )


def test_checkpoint_rejects_manual_edit_without_best_effort_mode() -> None:
    with pytest.raises(ValidationError, match="Manual external edits"):
        _checkpoint(
            trigger="external_manual_edit",
            captureMode="code_and_conversation",
        )


def test_checkpoint_index_requires_active_cursor_to_be_listed() -> None:
    checkpoint = _checkpoint()

    index = ClaudeCheckpointIndex(
        sessionId="claude-session-1",
        activeCheckpointId=checkpoint.checkpoint_id,
        checkpoints=[checkpoint],
        generatedAt=NOW,
    )

    assert index.active_checkpoint_id == "checkpoint-1"

    with pytest.raises(ValidationError, match="activeCheckpointId"):
        ClaudeCheckpointIndex(
            sessionId="claude-session-1",
            activeCheckpointId="missing-checkpoint",
            checkpoints=[checkpoint],
            generatedAt=NOW,
        )


def test_checkpoint_index_allows_empty_fresh_session_results() -> None:
    index = ClaudeCheckpointIndex(
        sessionId="claude-session-1",
        checkpoints=[],
        generatedAt=NOW,
    )

    assert index.checkpoints == ()
    assert index.active_checkpoint_id is None


def test_checkpoint_metadata_rejects_large_payloads() -> None:
    with pytest.raises(ValidationError):
        _checkpoint(metadata={"payload": "x" * 9000})


def test_rewind_request_accepts_only_documented_modes() -> None:
    request = ClaudeRewindRequest(
        requestId="rewind-request-1",
        sessionId="claude-session-1",
        checkpointId="checkpoint-1",
        mode="restore_code_and_conversation",
        requestedAt=NOW,
    )

    assert request.mode == "restore_code_and_conversation"

    with pytest.raises(ValidationError):
        ClaudeRewindRequest(
            requestId="rewind-request-2",
            sessionId="claude-session-1",
            checkpointId="checkpoint-1",
            mode="restore_everything",
            requestedAt=NOW,
        )


def test_rewind_result_preserves_lineage_and_event_log_reference() -> None:
    result = ClaudeRewindResult(
        resultId="rewind-result-1",
        sessionId="claude-session-1",
        requestId="rewind-request-1",
        sourceCheckpointId="checkpoint-1",
        previousActiveCheckpointId="checkpoint-2",
        activeCheckpointId="checkpoint-1",
        mode="restore_code_and_conversation",
        status="completed",
        rewoundFromCheckpointId="checkpoint-2",
        preservedEventLogRef="artifact://events/pre-rewind",
        codeStateRestored=True,
        conversationStateRestored=True,
        createdAt=NOW,
    )

    assert result.rewound_from_checkpoint_id == "checkpoint-2"
    assert result.preserved_event_log_ref == "artifact://events/pre-rewind"


def test_rewind_result_defers_restore_invariants_until_completed() -> None:
    started = ClaudeRewindResult(
        resultId="rewind-result-started",
        sessionId="claude-session-1",
        requestId="rewind-request-1",
        sourceCheckpointId="checkpoint-1",
        previousActiveCheckpointId="checkpoint-2",
        activeCheckpointId="checkpoint-1",
        mode="restore_code_and_conversation",
        status="started",
        rewoundFromCheckpointId="checkpoint-2",
        preservedEventLogRef="artifact://events/pre-rewind",
        codeStateRestored=False,
        conversationStateRestored=False,
        createdAt=NOW,
    )

    assert started.status == "started"


@pytest.mark.parametrize(
    ("mode", "code_restored", "conversation_restored", "message"),
    [
        (
            "summarize_from_here",
            False,
            False,
            "must restore conversation state",
        ),
        ("restore_code_only", False, False, "must restore code state"),
        (
            "restore_conversation_only",
            False,
            False,
            "must restore conversation state",
        ),
        (
            "restore_code_and_conversation",
            True,
            False,
            "must restore code and conversation state",
        ),
    ],
)
def test_completed_rewind_result_requires_requested_state_to_be_restored(
    mode: str,
    code_restored: bool,
    conversation_restored: bool,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ClaudeRewindResult(
            resultId=f"rewind-result-{mode}",
            sessionId="claude-session-1",
            requestId="rewind-request-1",
            sourceCheckpointId="checkpoint-1",
            previousActiveCheckpointId="checkpoint-2",
            activeCheckpointId="checkpoint-1",
            mode=mode,
            status="completed",
            rewoundFromCheckpointId="checkpoint-2",
            preservedEventLogRef="artifact://events/pre-rewind",
            summaryRef=(
                "artifact://summaries/from-checkpoint-1"
                if mode == "summarize_from_here"
                else None
            ),
            codeStateRestored=code_restored,
            conversationStateRestored=conversation_restored,
            createdAt=NOW,
        )


def test_summarize_from_here_requires_summary_ref_and_never_restores_code() -> None:
    with pytest.raises(ValidationError, match="summaryRef"):
        ClaudeRewindResult(
            resultId="rewind-result-1",
            sessionId="claude-session-1",
            requestId="rewind-request-1",
            sourceCheckpointId="checkpoint-1",
            previousActiveCheckpointId="checkpoint-2",
            activeCheckpointId="checkpoint-1",
            mode="summarize_from_here",
            status="completed",
            rewoundFromCheckpointId="checkpoint-2",
            preservedEventLogRef="artifact://events/pre-rewind",
            codeStateRestored=False,
            conversationStateRestored=True,
            createdAt=NOW,
        )

    with pytest.raises(ValidationError, match="cannot restore code"):
        ClaudeRewindResult(
            resultId="rewind-result-2",
            sessionId="claude-session-1",
            requestId="rewind-request-1",
            sourceCheckpointId="checkpoint-1",
            previousActiveCheckpointId="checkpoint-2",
            activeCheckpointId="checkpoint-1",
            mode="summarize_from_here",
            status="completed",
            rewoundFromCheckpointId="checkpoint-2",
            preservedEventLogRef="artifact://events/pre-rewind",
            summaryRef="artifact://summaries/from-checkpoint-1",
            codeStateRestored=True,
            conversationStateRestored=True,
            createdAt=NOW,
        )

    result = ClaudeRewindResult(
        resultId="rewind-result-3",
        sessionId="claude-session-1",
        requestId="rewind-request-1",
        sourceCheckpointId="checkpoint-1",
        previousActiveCheckpointId="checkpoint-2",
        activeCheckpointId="checkpoint-1",
        mode="summarize_from_here",
        status="completed",
        rewoundFromCheckpointId="checkpoint-2",
        preservedEventLogRef="artifact://events/pre-rewind",
        summaryRef="artifact://summaries/from-checkpoint-1",
        codeStateRestored=False,
        conversationStateRestored=True,
        createdAt=NOW,
    )

    assert result.summary_ref == "artifact://summaries/from-checkpoint-1"


def test_checkpoint_and_rewind_work_item_events_are_validated() -> None:
    assert CLAUDE_CHECKPOINT_WORK_EVENT_NAMES == (
        "work.checkpoint.created",
        "work.rewind.started",
        "work.rewind.completed",
    )

    checkpoint_work = create_claude_checkpoint_work_item(
        item_id="work-checkpoint-1",
        turn_id="turn-1",
        session_id="claude-session-1",
        checkpoint_id="checkpoint-1",
        created_at=NOW,
    )
    rewind_started, rewind_completed = create_claude_rewind_work_items(
        started_item_id="work-rewind-started-1",
        completed_item_id="work-rewind-completed-1",
        turn_id="turn-1",
        session_id="claude-session-1",
        request_id="rewind-request-1",
        result_id="rewind-result-1",
        source_checkpoint_id="checkpoint-1",
        active_checkpoint_id="checkpoint-1",
        created_at=NOW,
    )

    assert checkpoint_work.kind == "checkpoint"
    assert checkpoint_work.event_name == "work.checkpoint.created"
    assert rewind_started.kind == "rewind"
    assert rewind_started.event_name == "work.rewind.started"
    assert rewind_completed.event_name == "work.rewind.completed"

    with pytest.raises(ValidationError, match="Checkpoint and rewind event names"):
        ClaudeManagedWorkItem(
            itemId="bad-work",
            turnId="turn-1",
            sessionId="claude-session-1",
            kind="tool_call",
            status="completed",
            eventName="work.checkpoint.created",
            startedAt=NOW,
            endedAt=NOW,
        )
