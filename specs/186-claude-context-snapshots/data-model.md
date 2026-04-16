# Data Model: Claude Context Snapshots

## Claude ContextSnapshot

Represents immutable metadata for the context known to a Claude managed session at one compaction epoch.

Fields:
- `snapshotId`: stable non-blank snapshot identifier.
- `sessionId`: canonical Claude managed-session identifier.
- `turnId`: optional turn identifier when the snapshot is turn-scoped.
- `compactionEpoch`: integer greater than or equal to zero.
- `segments`: one or more `ClaudeContextSegment` records.
- `createdAt`: timestamp when the snapshot was created.
- `metadata`: compact, bounded metadata for adapter-specific diagnostics.

Validation:
- `snapshotId` and `sessionId` must be non-blank.
- `compactionEpoch` must be monotonic in helper-created compaction snapshots.
- `segments` must not be empty.
- `metadata` must remain compact and Temporal-safe.

## Claude ContextSegment

Represents bounded metadata for one source of context.

Fields:
- `segmentId`: stable non-blank segment identifier.
- `kind`: documented context source kind.
- `sourceRef`: pointer to a runtime-local artifact, source path, manifest reference, or summary reference.
- `loadedAt`: documented load timing.
- `reinjectionPolicy`: explicit compaction reinjection policy.
- `guidanceRole`: whether the segment is guidance, enforcement, or neutral context.
- `tokenBudgetHint`: optional integer budget hint.
- `metadata`: compact, bounded metadata.

Validation:
- Unknown `kind`, `loadedAt`, `reinjectionPolicy`, or `guidanceRole` values fail validation.
- Guidance sources such as CLAUDE files and memory cannot be represented as enforcement.
- Segment metadata must not embed large payload-like values.

## Context Source Kind

Documented startup kinds:
- `system_prompt`
- `output_style`
- `managed_claude_md`
- `project_claude_md`
- `local_claude_md`
- `auto_memory`
- `mcp_tool_manifest`
- `skill_description`
- `hook_injected_context`

Documented on-demand kinds:
- `file_read`
- `nested_claude_md`
- `path_rule`
- `invoked_skill_body`
- `runtime_summary`

Post-compaction kind:
- `transcript_summary`

## Reinjection Policy

Allowed values:
- `always`
- `on_demand`
- `budgeted`
- `never`
- `startup_refresh`
- `configurable`

Default policy mapping:
- `system_prompt`, `output_style`, `managed_claude_md`, `project_claude_md`, `local_claude_md`, `auto_memory`, and `transcript_summary`: `always`.
- `path_rule` and `nested_claude_md`: `on_demand`.
- `skill_description`: `startup_refresh`.
- `invoked_skill_body`: `budgeted`.
- `file_read`: `never`.
- `hook_injected_context`: `configurable`.
- `mcp_tool_manifest`: `startup_refresh`.

## Claude ContextEvent

Represents a normalized event for context loading or compaction.

Fields:
- `eventId`: stable non-blank event identifier.
- `sessionId`: canonical Claude managed-session identifier.
- `turnId`: optional turn identifier.
- `snapshotId`: optional related snapshot.
- `workItemId`: optional related work item.
- `eventName`: documented context event name.
- `occurredAt`: event timestamp.
- `metadata`: compact, bounded event metadata.

Allowed event names:
- `work.context.loaded`
- `work.compaction.started`
- `work.compaction.completed`

## Compaction Result

Helper-produced result that carries:
- `snapshot`: new post-compaction ContextSnapshot with incremented epoch.
- `workItem`: compaction work item.
- `events`: started and completed compaction/context events.

State transitions:
- Existing snapshot remains unchanged.
- New snapshot epoch equals previous epoch plus one.
- Segments with `always`, `startup_refresh`, `budgeted`, and `configurable` policies can be retained for the new snapshot.
- Segments with `on_demand` and `never` policies are omitted until their trigger recurs.
