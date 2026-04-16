# Data Model: Claude Checkpoints Rewind

## Claude Checkpoint

Represents bounded metadata for one checkpoint in a Claude managed session.

Fields:
- `checkpointId`: stable non-blank checkpoint identifier.
- `sessionId`: canonical Claude managed-session identifier.
- `turnId`: optional turn identifier when the checkpoint is turn-scoped.
- `trigger`: documented checkpoint trigger.
- `captureMode`: documented capture mode.
- `status`: checkpoint lifecycle status.
- `storageRef`: runtime-local checkpoint payload reference.
- `isActive`: whether this checkpoint is the current active cursor.
- `retentionState`: addressability and retention state.
- `createdAt`: timestamp when the checkpoint metadata was created.
- `expiresAt`: optional expiry timestamp.
- `rewoundFromCheckpointId`: optional lineage source for a checkpoint created by rewind.
- `eventLogRef`: optional preserved event-log reference.
- `metadata`: compact, bounded metadata for adapter-specific diagnostics.

Validation:
- Identifiers and storage references must be non-blank.
- Unknown triggers, capture modes, statuses, or retention states fail validation.
- Metadata must remain compact and must not embed payload bodies.
- Bash side effects cannot use code-state capture mode by default.
- External manual edits must use best-effort capture mode.

## Checkpoint Trigger

Allowed values:
- `user_prompt`
- `tracked_file_edit`
- `bash_side_effect`
- `external_manual_edit`

Default capture rules:
- `user_prompt`: captures conversation checkpoint metadata.
- `tracked_file_edit`: captures code and conversation checkpoint metadata.
- `bash_side_effect`: skips code-state checkpoint capture by default.
- `external_manual_edit`: captures best-effort metadata only.

## Checkpoint Capture Mode

Allowed values:
- `conversation`
- `code_and_conversation`
- `code`
- `best_effort`
- `skipped`

## Checkpoint Retention State

Allowed values:
- `addressable`
- `expires_at`
- `expired`
- `garbage_collected`

State rules:
- Addressable and expiring checkpoints can appear in checkpoint index output.
- Expired or garbage-collected checkpoints cannot be used as successful rewind sources.

## Checkpoint Index

Represents operator-visible checkpoint metadata for one session.

Fields:
- `sessionId`: canonical Claude managed-session identifier.
- `activeCheckpointId`: optional active checkpoint cursor.
- `checkpoints`: ordered checkpoint metadata records.
- `generatedAt`: timestamp when the index was produced.
- `metadata`: compact, bounded metadata.

Validation:
- The active checkpoint, when present, must refer to a checkpoint in the index.
- Checkpoint metadata remains pointer-based.

## Rewind Request

Represents a validated request to rewind or summarize from a checkpoint.

Fields:
- `requestId`: stable non-blank request identifier.
- `sessionId`: canonical Claude managed-session identifier.
- `checkpointId`: checkpoint to restore or summarize from.
- `mode`: documented rewind mode.
- `instructions`: optional bounded instructions for summarize-from-here.
- `requestedAt`: request timestamp.
- `metadata`: compact, bounded metadata.

Allowed modes:
- `restore_code_and_conversation`
- `restore_conversation_only`
- `restore_code_only`
- `summarize_from_here`

## Rewind Result

Represents provenance-preserving rewind output.

Fields:
- `resultId`: stable non-blank result identifier.
- `sessionId`: canonical Claude managed-session identifier.
- `requestId`: related rewind request identifier.
- `sourceCheckpointId`: checkpoint used as rewind source.
- `previousActiveCheckpointId`: active checkpoint before rewind.
- `activeCheckpointId`: active checkpoint after rewind.
- `mode`: documented rewind mode.
- `status`: result status.
- `rewoundFromCheckpointId`: lineage pointer to the checkpoint that caused the new active cursor.
- `preservedEventLogRef`: pointer to pre-rewind event history.
- `summaryRef`: optional summary artifact reference for summarize-from-here.
- `codeStateRestored`: whether code state was restored.
- `conversationStateRestored`: whether conversation state was restored.
- `createdAt`: result timestamp.
- `metadata`: compact, bounded metadata.

Validation:
- Summary-from-here requires `summaryRef`.
- Summary-from-here cannot claim code state was restored.
- Restore-code modes must report code restoration consistently.
- Metadata remains compact and payload-light.

## Work Evidence

Checkpoint and rewind helpers produce `ClaudeManagedWorkItem` records with allowed event names:
- `work.checkpoint.created`
- `work.rewind.started`
- `work.rewind.completed`

State transitions:
- Capture evaluation either creates checkpoint metadata and checkpoint-created work evidence or returns skipped capture evidence.
- Rewind result preserves old event-log references and records the new active cursor without deleting previous checkpoint metadata.
