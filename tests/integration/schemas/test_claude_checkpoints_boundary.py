"""Integration-style boundary tests for Claude checkpoints and rewinds."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas.managed_session_models import (
    ClaudeCheckpoint,
    ClaudeCheckpointIndex,
    ClaudeRewindRequest,
    ClaudeRewindResult,
    claude_checkpoint_capture_decision,
    create_claude_checkpoint_work_item,
    create_claude_rewind_work_items,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def test_claude_checkpoint_boundary_preserves_rewind_lineage() -> None:
    user_prompt_decision = claude_checkpoint_capture_decision("user_prompt")
    file_edit_decision = claude_checkpoint_capture_decision("tracked_file_edit")
    bash_decision = claude_checkpoint_capture_decision("bash_side_effect")
    manual_decision = claude_checkpoint_capture_decision("external_manual_edit")

    assert user_prompt_decision.should_create_checkpoint is True
    assert file_edit_decision.capture_mode == "code_and_conversation"
    assert bash_decision.should_create_checkpoint is False
    assert bash_decision.capture_mode == "skipped"
    assert manual_decision.capture_mode == "best_effort"

    prompt_checkpoint = ClaudeCheckpoint(
        checkpointId="checkpoint-prompt",
        sessionId="claude-session-1",
        turnId="turn-1",
        trigger="user_prompt",
        captureMode=user_prompt_decision.capture_mode,
        status="captured",
        storageRef="runtime-local://checkpoints/prompt",
        isActive=False,
        retentionState="addressable",
        createdAt=NOW,
        eventLogRef="artifact://events/before-prompt",
    )
    file_checkpoint = ClaudeCheckpoint(
        checkpointId="checkpoint-file",
        sessionId="claude-session-1",
        turnId="turn-1",
        trigger="tracked_file_edit",
        captureMode=file_edit_decision.capture_mode,
        status="captured",
        storageRef="runtime-local://checkpoints/file",
        isActive=True,
        retentionState="addressable",
        createdAt=NOW,
        eventLogRef="artifact://events/before-file",
        metadata={"changedPathCount": 1},
    )
    manual_checkpoint = ClaudeCheckpoint(
        checkpointId="checkpoint-manual",
        sessionId="claude-session-1",
        turnId="turn-1",
        trigger="external_manual_edit",
        captureMode=manual_decision.capture_mode,
        status="captured",
        storageRef="runtime-local://checkpoints/manual",
        isActive=False,
        retentionState="addressable",
        createdAt=NOW,
        eventLogRef="artifact://events/before-manual",
        metadata={"authoritative": False},
    )
    index = ClaudeCheckpointIndex(
        sessionId="claude-session-1",
        activeCheckpointId="checkpoint-file",
        checkpoints=[prompt_checkpoint, file_checkpoint, manual_checkpoint],
        generatedAt=NOW,
    )
    checkpoint_work = create_claude_checkpoint_work_item(
        item_id="work-checkpoint-file",
        turn_id="turn-1",
        session_id="claude-session-1",
        checkpoint_id=file_checkpoint.checkpoint_id,
        created_at=NOW,
    )

    request = ClaudeRewindRequest(
        requestId="rewind-request-1",
        sessionId="claude-session-1",
        checkpointId=prompt_checkpoint.checkpoint_id,
        mode="restore_code_and_conversation",
        requestedAt=NOW,
    )
    result = ClaudeRewindResult(
        resultId="rewind-result-1",
        sessionId="claude-session-1",
        requestId=request.request_id,
        sourceCheckpointId=prompt_checkpoint.checkpoint_id,
        previousActiveCheckpointId=index.active_checkpoint_id,
        activeCheckpointId=prompt_checkpoint.checkpoint_id,
        mode=request.mode,
        status="completed",
        rewoundFromCheckpointId=index.active_checkpoint_id,
        preservedEventLogRef="artifact://events/pre-rewind",
        codeStateRestored=True,
        conversationStateRestored=True,
        createdAt=NOW,
    )
    rewind_started, rewind_completed = create_claude_rewind_work_items(
        started_item_id="work-rewind-started",
        completed_item_id="work-rewind-completed",
        turn_id="turn-1",
        session_id="claude-session-1",
        request_id=request.request_id,
        result_id=result.result_id,
        source_checkpoint_id=result.source_checkpoint_id,
        active_checkpoint_id=result.active_checkpoint_id,
        created_at=NOW,
    )
    summary_result = ClaudeRewindResult(
        resultId="rewind-result-summary",
        sessionId="claude-session-1",
        requestId="rewind-request-summary",
        sourceCheckpointId=file_checkpoint.checkpoint_id,
        previousActiveCheckpointId=result.active_checkpoint_id,
        activeCheckpointId=file_checkpoint.checkpoint_id,
        mode="summarize_from_here",
        status="completed",
        rewoundFromCheckpointId=result.active_checkpoint_id,
        preservedEventLogRef="artifact://events/pre-summary",
        summaryRef="artifact://summaries/from-file-checkpoint",
        codeStateRestored=False,
        conversationStateRestored=True,
        createdAt=NOW,
    )

    assert index.active_checkpoint_id == "checkpoint-file"
    assert result.active_checkpoint_id == "checkpoint-prompt"
    assert result.rewound_from_checkpoint_id == "checkpoint-file"
    assert result.preserved_event_log_ref == "artifact://events/pre-rewind"
    assert checkpoint_work.event_name == "work.checkpoint.created"
    assert (rewind_started.event_name, rewind_completed.event_name) == (
        "work.rewind.started",
        "work.rewind.completed",
    )
    assert summary_result.summary_ref == "artifact://summaries/from-file-checkpoint"
    assert summary_result.code_state_restored is False

    for record in (
        prompt_checkpoint,
        file_checkpoint,
        manual_checkpoint,
        index,
        checkpoint_work,
        request,
        result,
        rewind_started,
        rewind_completed,
        summary_result,
    ):
        wire = record.model_dump(by_alias=True)
        assert "threadId" not in wire
        assert "childThread" not in wire
