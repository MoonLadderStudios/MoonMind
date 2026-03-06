# Data Model: Temporal Dashboard Integration

## Entity: TemporalDashboardFeatureFlagSet

- **Description**: Runtime-configured rollout state for Temporal dashboard behavior.
- **Fields**:
  - `enabled` (boolean)
  - `list_enabled` (boolean)
  - `detail_enabled` (boolean)
  - `actions_enabled` (boolean)
  - `submit_enabled` (boolean)
  - `debug_fields_enabled` (boolean)
- **Rules**:
  - `enabled=false` disables all Temporal dashboard behavior regardless of subordinate flags.
  - `actions_enabled` and `submit_enabled` must default off until those phases are released.
  - Flags are operator-controlled runtime settings exported through `build_runtime_config()`.

## Entity: TemporalDashboardSourceConfig

- **Description**: Runtime config block that teaches the dashboard how to reach Temporal-backed list, detail, action, and artifact APIs.
- **Fields**:
  - `list_endpoint` (`/api/executions`)
  - `create_endpoint` (`/api/executions`)
  - `detail_endpoint` (`/api/executions/{workflowId}`)
  - `update_endpoint` (`/api/executions/{workflowId}/update`)
  - `signal_endpoint` (`/api/executions/{workflowId}/signal`)
  - `cancel_endpoint` (`/api/executions/{workflowId}/cancel`)
  - `artifacts_endpoint` (`/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`)
  - `artifact_create_endpoint` (`/api/artifacts`)
  - `artifact_metadata_endpoint` (`/api/artifacts/{artifactId}`)
  - `artifact_presign_download_endpoint` (`/api/artifacts/{artifactId}/presign-download`)
  - `artifact_download_endpoint` (`/api/artifacts/{artifactId}/download`)
- **Rules**:
  - The browser talks only to MoonMind-owned REST endpoints.
  - Endpoint templates are exported by the server and must be treated as authoritative by the client.

## Entity: DashboardTaskSourceRecord

- **Description**: Canonical server-side source-resolution response for a dashboard `taskId`.
- **Fields**:
  - `task_id` (string)
  - `source` (enum: `queue`, `orchestrator`, `temporal`)
  - `source_label` (string)
  - `detail_path` (string, canonical dashboard path)
- **Rules**:
  - For Temporal-backed records, `task_id` equals `workflow_id`.
  - Resolution is ownership-aware; users may only resolve Temporal executions they are permitted to view.
  - `?source=temporal` remains an explicit override/fallback, not the primary compatibility contract.

## Entity: TemporalDashboardQuery

- **Description**: URL/query state for Temporal-backed list and detail views.
- **Fields**:
  - `source` (string, `temporal` when pinned)
  - `workflow_type` (string nullable)
  - `state` (string nullable)
  - `entry` (enum nullable: `run`, `manifest`)
  - `owner_type` (string nullable; operator/admin only)
  - `owner_id` (string nullable; operator/admin only)
  - `repo` (string nullable)
  - `integration` (string nullable)
  - `page_size` (integer, 1-200)
  - `next_page_token` (opaque string nullable)
- **Rules**:
  - Temporal-only list mode preserves backend pagination/count semantics exactly.
  - Mixed-source mode may reuse some filters but must not claim globally authoritative pagination.
  - Owner filters are hidden from standard end-user UI unless policy allows operator/admin use.

## Entity: TemporalDashboardRow

- **Description**: Task-oriented list row representing one Temporal-backed workflow execution.
- **Fields**:
  - `id` (string; same as `task_id`)
  - `task_id` (string; equals `workflow_id`)
  - `workflow_id` (string)
  - `temporal_run_id` (string nullable; latest run)
  - `source` (constant `temporal`)
  - `source_label` (constant `Temporal`)
  - `title` (string)
  - `summary` (string nullable)
  - `workflow_type` (string)
  - `entry` (string nullable)
  - `status` (enum: `queued`, `running`, `awaiting_action`, `succeeded`, `failed`, `cancelled`)
  - `raw_state` (string)
  - `temporal_status` (string)
  - `close_status` (string nullable)
  - `owner_type` (string nullable)
  - `owner_id` (string nullable)
  - `repository` (string nullable)
  - `integration` (string nullable)
  - `waiting_reason` (string nullable)
  - `attention_required` (boolean)
  - `started_at` (datetime nullable)
  - `updated_at` (datetime nullable)
  - `closed_at` (datetime nullable)
  - `link` (string; canonical detail URL)
- **Rules**:
  - `task_id` and `workflow_id` are the same durable identity for Temporal-backed rows.
  - `temporal_run_id` is latest-run metadata and must not become the canonical route key.
  - Sorting preference is `mm_updated_at`, then `updated_at`, then `workflow_id DESC`, then `started_at`.
  - `attention_required=true` requires matching explanatory metadata such as `waiting_reason` when available.

## Entity: TemporalDashboardPage

- **Description**: Authoritative response envelope for `source=temporal` list views.
- **Fields**:
  - `items` (array of `TemporalDashboardRow`)
  - `next_page_token` (string nullable)
  - `count` (integer nullable)
  - `count_mode` (enum: `exact`, `estimated_or_unknown`)
  - `authoritative` (boolean; true when source is pinned to Temporal)
- **Rules**:
  - When `authoritative=true`, pagination and counts mirror the Temporal execution API exactly.
  - Mixed-source list views derive bounded slices and should not reuse this envelope to imply a global source of truth.

## Entity: MixedSourceSlice

- **Description**: Dashboard convenience aggregation of rows from queue, orchestrator, and Temporal sources.
- **Fields**:
  - `items` (array of normalized rows across sources)
  - `source_totals` (per-source counts or informational totals)
  - `bounded` (boolean, always true)
  - `authoritative` (boolean, always false)
- **Rules**:
  - Mixed-source mode is merge-sorted client-side over bounded slices.
  - Totals are informational and may not support exact cross-source pagination guarantees.

## Entity: TemporalDetailView

- **Description**: Unified task detail model for one Temporal-backed task route.
- **Fields**:
  - `task_id` (string)
  - `workflow_id` (string)
  - `temporal_run_id` (string; latest run from detail response)
  - `namespace` (string)
  - `title` (string)
  - `summary` (string nullable)
  - `workflow_type` (string)
  - `status` (normalized dashboard status)
  - `raw_state` (string)
  - `temporal_status` (string)
  - `close_status` (string nullable)
  - `waiting_reason` (string nullable)
  - `attention_required` (boolean)
  - `started_at` (datetime nullable)
  - `updated_at` (datetime nullable)
  - `closed_at` (datetime nullable)
  - `timeline_entries` (array of synthesized timeline entries)
  - `artifacts` (array of `TemporalArtifactPresentationRecord`)
  - `actions` (one `TemporalActionCapabilitySet`)
  - `debug_fields` (map nullable)
- **Rules**:
  - Detail must fetch the execution first and derive `temporal_run_id` from that response before loading artifacts.
  - Timeline entries are synthesized from known execution fields in v1; raw Temporal history is out of scope.
  - Debug fields are shown only when the debug feature flag is enabled.

## Entity: TemporalActionCapabilitySet

- **Description**: State-aware description of which Temporal dashboard actions are currently valid and visible.
- **Fields**:
  - `can_set_title` (boolean)
  - `can_update_inputs` (boolean)
  - `can_rerun` (boolean)
  - `can_approve` (boolean)
  - `can_pause` (boolean)
  - `can_resume` (boolean)
  - `can_cancel` (boolean)
  - `disabled_reason` (string nullable map by action)
- **Rules**:
  - Capability calculation depends on both feature flags and current execution state.
  - Terminal states may expose `rerun` but not mutation/signal controls.
  - `awaiting_external` may expose `approve` only when the backing workflow supports it.

## Entity: TemporalArtifactPresentationRecord

- **Description**: Latest-run artifact metadata rendered in Temporal detail.
- **Fields**:
  - `artifact_id` (string)
  - `label` (string nullable)
  - `link_type` (string nullable)
  - `name` (string)
  - `content_type` (string nullable)
  - `size_bytes` (integer nullable)
  - `preview_artifact_ref` (string nullable)
  - `default_read_ref` (string nullable)
  - `raw_access_allowed` (boolean)
  - `download_action` (endpoint template or link metadata)
- **Rules**:
  - Artifact listing is scoped to `(namespace, workflow_id, temporal_run_id)`.
  - UI should prefer preview/default-read references when present.
  - Editing inputs creates new artifact refs rather than mutating existing artifact bytes.

## Entity: TemporalSubmitIntent

- **Description**: Dashboard-side task-shaped submit intent that may be routed by the backend to a Temporal execution start.
- **Fields**:
  - `task_shape` (existing dashboard submit payload)
  - `artifact_refs` (zero or more input refs)
  - `target_engine` (backend-resolved; not user-facing)
  - `created_task_id` (string nullable after success)
  - `redirect_path` (string nullable)
- **Rules**:
  - The standard runtime picker must not expose `temporal`.
  - Successful Temporal-backed creates redirect to `/tasks/{taskId}?source=temporal`.
  - Backend routing decides whether a submit becomes queue-backed, orchestrator-backed, or Temporal-backed.

## State and Flow Rules

- **Status normalization**:
  - `initializing`, `planning` -> `queued`
  - `executing`, `finalizing` -> `running`
  - `awaiting_external` -> `awaiting_action`
  - `succeeded` -> `succeeded`
  - `failed` -> `failed`
  - `canceled` -> `cancelled`
- **Action-state matrix**:
  - `initializing` / `planning`: `set_title`, `cancel`
  - `executing`: `set_title`, `pause`, `cancel`
  - `awaiting_external`: `approve`, `pause`, `resume`, `cancel`
  - `finalizing`: `cancel` only when backend policy permits
  - terminal states: `rerun`, artifact inspection/download
- **Detail fetch sequence**:
  - resolve source -> fetch execution detail -> derive latest run -> fetch artifacts -> render view
- **Submit redirect rule**:
  - on successful Temporal-backed create, canonicalize to `/tasks/{task_id}?source=temporal`
