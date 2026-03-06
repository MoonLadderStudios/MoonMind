# Data Model: Task Execution Compatibility

## Entity: TaskSourceMapping

- **Description**: Persisted global task index used to resolve a `taskId` to its canonical execution source and durable backing identifier.
- **Fields**:
  - `task_id` (string, primary key)
  - `source` (enum: `queue` | `orchestrator` | `temporal`)
  - `entry` (string | null)
  - `source_record_id` (string)
  - `workflow_id` (string | null)
  - `owner_type` (string | null)
  - `owner_id` (string | null)
  - `created_at` (UTC datetime)
  - `updated_at` (UTC datetime)
- **Rules**:
  - For Temporal-backed rows, `task_id == workflow_id`.
  - `source_record_id` stores the durable record handle used by that source.
  - Resolution reads this entity first; source probing is fallback-only for unmigrated legacy rows.

## Entity: TaskCompatibilityRow

- **Description**: Normalized task list row returned by `GET /api/tasks/list` regardless of backing source.
- **Fields**:
  - `taskId` (string)
  - `source` (enum)
  - `entry` (string | null)
  - `title` (string)
  - `summary` (string | null)
  - `status` (enum: `queued` | `running` | `awaiting_action` | `succeeded` | `failed` | `cancelled`)
  - `rawState` (string | null)
  - `temporalStatus` (string | null)
  - `closeStatus` (string | null)
  - `workflowId` (string | null)
  - `workflowType` (string | null)
  - `ownerType` (string | null)
  - `ownerId` (string | null)
  - `createdAt` (UTC datetime)
  - `updatedAt` (UTC datetime)
  - `closedAt` (UTC datetime | null)
  - `artifactsCount` (integer)
  - `detailHref` (string)
- **Rules**:
  - Temporal rows always use `taskId == workflowId`.
  - `createdAt` for Temporal rows is sourced from `startedAt`.
  - `status` is normalized for filtering/display; raw lifecycle fields remain separate.

## Entity: TemporalTaskDetail

- **Description**: Expanded normalized detail payload for Temporal-backed tasks rendered inside `/tasks/{taskId}`.
- **Fields**:
  - All `TaskCompatibilityRow` fields
  - `temporalRunId` (string)
  - `namespace` (string)
  - `artifactRefs` (string[])
  - `searchAttributes` (object)
  - `memo` (object)
  - `inputArtifactRef` (string | null)
  - `planArtifactRef` (string | null)
  - `manifestArtifactRef` (string | null)
  - `actions` (`TaskActionAvailability`)
  - `debug` (`TaskDebugContext`)
- **Rules**:
  - Route identity stays anchored to `taskId == workflowId` even when `temporalRunId` changes.
  - `searchAttributes` and `memo` are allowlisted, bounded, and secret-safe.
  - Raw `parameters` are not returned verbatim; only reviewed task-safe previews may appear.

## Entity: TaskActionAvailability

- **Description**: Task-facing action availability block attached to detail responses.
- **Fields**:
  - `rename` (boolean)
  - `editInputs` (boolean)
  - `rerun` (boolean)
  - `approve` (boolean)
  - `pause` (boolean)
  - `resume` (boolean)
  - `deliverCallback` (boolean)
  - `cancel` (boolean)
  - `forceTerminate` (boolean)
- **Rules**:
  - Terminal tasks disable normal mutate actions.
  - `forceTerminate` is operator/admin-only and not enabled for standard user flows.
  - Availability is derived from normalized state plus authorization context.

## Entity: TaskDebugContext

- **Description**: Optional operator/debug block attached to detail responses.
- **Fields**:
  - `namespace` (string | null)
  - `workflowType` (string | null)
  - `workflowId` (string | null)
  - `temporalRunId` (string | null)
  - `temporalStatus` (string | null)
  - `closeStatus` (string | null)
  - `waitingReason` (string | null)
  - `attentionRequired` (boolean)
- **Rules**:
  - Debug fields augment the detail payload and never replace normalized task fields.
  - `ContinuedAsNew` appears here/run history semantics, not as a user-facing stable-task status.

## Entity: MixedSourceCursor

- **Description**: Opaque compatibility-owned cursor used by `GET /api/tasks/list` when multiple sources participate.
- **Fields**:
  - `sort_anchor` (UTC datetime)
  - `queue_cursor` (string | null)
  - `orchestrator_cursor` (string | null)
  - `temporal_next_page_token` (string | null)
  - `source_filter` (string)
  - `page_size` (integer)
- **Encoding**:
  - JSON payload encoded as opaque base64url text.
- **Rules**:
  - Raw backend tokens are nested inside this compatibility cursor and are never exposed as the universal mixed-source cursor contract.
  - Cursor payload must be rejected when filters or page-size no longer match.

## Entity: TaskCompatibilityListResponse

- **Description**: Paginated response envelope for `GET /api/tasks/list`.
- **Fields**:
  - `items` (`TaskCompatibilityRow[]`)
  - `nextCursor` (string | null)
  - `count` (integer | null)
  - `countMode` (enum: `exact` | `estimated_or_unknown`)
- **Rules**:
  - Temporal-only responses may return `countMode=exact`.
  - Mixed-source responses default to `estimated_or_unknown` unless exact aggregation is proven cheap and correct.

## Entity: TaskMetadataEnvelope

- **Description**: Allowlisted metadata contract for Temporal-backed compatibility records.
- **Search Attributes**:
  - Required: `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`
  - Optional bounded keys: `mm_repo`, `mm_integration`
- **Memo**:
  - Required/supported: `title`, `summary`
  - Optional safe refs: `input_ref`, `plan_ref`, `manifest_ref`, `waiting_reason`, `attention_required`
- **Rules**:
  - Secrets, credentials, raw prompts, and other large free-form payloads must not be stored in compatibility metadata.
  - Oversized metadata is redirected to artifacts or rejected.

## Status Mapping Rules

- `initializing -> queued`
- `planning -> running`
- `executing -> running`
- `awaiting_external -> awaiting_action`
- `finalizing -> running`
- `succeeded -> succeeded`
- `failed -> failed`
- `canceled -> cancelled`
- `TimedOut` and `Terminated` close outcomes normalize to `failed` while preserving raw close state.
- `ContinuedAsNew` remains debug/run-history information for the stable task identity.

## State Transitions

- **Create Temporal task**: new `TemporalExecutionRecord` -> new `TaskSourceMapping` -> `TaskCompatibilityRow(status=queued)`.
- **Continue-As-New rerun**: same `task_id` and `workflow_id`, new `temporalRunId`, refreshed `updatedAt`, preserved mapping row.
- **Pause/await external**: `rawState=awaiting_external` -> normalized `status=awaiting_action`; `waitingReason` refines operator context.
- **Terminal close**: normalized status becomes `succeeded`, `failed`, or `cancelled`; action availability updates to no-op/unavailable for mutating actions.

## Validation Constraints

- Any detail lookup that resolves a `taskId` without a mapping row must fall back deterministically and backfill the mapping when safe.
- Mixed-source cursors must be opaque and must not expose raw Temporal page tokens directly.
- Temporal-backed payloads must preserve `taskId == workflowId` and must never overload legacy `runId`.
- Queue-backed manifest jobs must remain `source=queue`; only Temporal-backed manifest executions use `source=temporal` + `entry=manifest`.
