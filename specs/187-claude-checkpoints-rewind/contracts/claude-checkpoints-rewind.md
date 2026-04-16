# Contract: Claude Checkpoints Rewind

Source story: MM-346 / STORY-005.

## Python Schema Surface

The managed-session schema boundary must export the following names from `moonmind.schemas.managed_session_models` and `moonmind.schemas`:

- `CLAUDE_CHECKPOINT_TRIGGERS`
- `CLAUDE_CHECKPOINT_CAPTURE_MODES`
- `CLAUDE_CHECKPOINT_RETENTION_STATES`
- `CLAUDE_REWIND_MODES`
- `CLAUDE_CHECKPOINT_WORK_EVENT_NAMES`
- `ClaudeCheckpoint`
- `ClaudeCheckpointCaptureDecision`
- `ClaudeCheckpointCaptureMode`
- `ClaudeCheckpointIndex`
- `ClaudeCheckpointRetentionState`
- `ClaudeCheckpointStatus`
- `ClaudeCheckpointTrigger`
- `ClaudeRewindMode`
- `ClaudeRewindRequest`
- `ClaudeRewindResult`
- `ClaudeRewindStatus`
- `claude_checkpoint_capture_decision`
- `create_claude_checkpoint_work_item`
- `create_claude_rewind_work_items`

## Checkpoint Wire Shape

```json
{
  "checkpointId": "checkpoint-1",
  "sessionId": "claude-session-1",
  "turnId": "turn-1",
  "trigger": "tracked_file_edit",
  "captureMode": "code_and_conversation",
  "status": "captured",
  "storageRef": "runtime-local://checkpoints/checkpoint-1",
  "isActive": true,
  "retentionState": "addressable",
  "createdAt": "2026-04-16T00:00:00Z",
  "eventLogRef": "artifact://events/pre-rewind",
  "metadata": {
    "changedPathCount": 2
  }
}
```

Contract rules:
- `checkpointId`, `sessionId`, and `storageRef` are required and non-blank.
- `trigger`, `captureMode`, `status`, and `retentionState` reject unknown values.
- `metadata` must remain compact and must not carry checkpoint payloads.
- Bash side effects reject code-state capture modes by default.
- External manual edits require `best_effort` capture mode.

## Capture Decision Contract

`claude_checkpoint_capture_decision(trigger)` returns:
- `shouldCreateCheckpoint = true` for `user_prompt`, `tracked_file_edit`, and `external_manual_edit`.
- `shouldCreateCheckpoint = false` for `bash_side_effect`.
- `captureMode = best_effort` for `external_manual_edit`.
- A compact reason string suitable for logs or metadata.

## Checkpoint Index Contract

```json
{
  "sessionId": "claude-session-1",
  "activeCheckpointId": null,
  "generatedAt": "2026-04-16T00:00:00Z",
  "checkpoints": []
}
```

Contract rules:
- The active checkpoint id, when present, must match a checkpoint in `checkpoints`.
- Expired and garbage-collected checkpoints may remain visible as metadata but cannot be successful rewind sources.

## Rewind Request Wire Shape

```json
{
  "requestId": "rewind-request-1",
  "sessionId": "claude-session-1",
  "checkpointId": "checkpoint-1",
  "mode": "restore_code_and_conversation",
  "requestedAt": "2026-04-16T00:00:00Z",
  "metadata": {}
}
```

Allowed modes:
- `restore_code_and_conversation`
- `restore_conversation_only`
- `restore_code_only`
- `summarize_from_here`

## Rewind Result Wire Shape

```json
{
  "resultId": "rewind-result-1",
  "sessionId": "claude-session-1",
  "requestId": "rewind-request-1",
  "sourceCheckpointId": "checkpoint-1",
  "previousActiveCheckpointId": "checkpoint-2",
  "activeCheckpointId": "checkpoint-1",
  "mode": "restore_code_and_conversation",
  "status": "completed",
  "rewoundFromCheckpointId": "checkpoint-2",
  "preservedEventLogRef": "artifact://events/pre-rewind",
  "codeStateRestored": true,
  "conversationStateRestored": true,
  "createdAt": "2026-04-16T00:00:00Z",
  "metadata": {}
}
```

Contract rules:
- Restore-state invariants apply when `status = completed`; `started` and `failed` results may report unrestored state.
- Completed `summarize_from_here` requires `summaryRef`, must set `codeStateRestored = false`, and must restore conversation state.
- Completed `restore_code_only` must restore code state and must set `conversationStateRestored = false`.
- Completed `restore_conversation_only` must restore conversation state and must set `codeStateRestored = false`.
- Completed `restore_code_and_conversation` must restore both code and conversation state.
- `preservedEventLogRef` is required so rewind does not erase pre-rewind history.

## Work Item Event Contract

Checkpoint and rewind work items may use:
- `work.checkpoint.created`
- `work.rewind.started`
- `work.rewind.completed`

All work-item payloads must pass compact Temporal mapping validation and use `sessionId`, not Codex thread aliases.
