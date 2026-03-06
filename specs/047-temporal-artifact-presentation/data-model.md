# Data Model: Temporal Artifact Presentation Contract

## Entity: TemporalTaskDetailView

- **Description**: The task-oriented detail payload rendered for one Temporal-backed record on `/tasks/:taskId`.
- **Fields**:
  - `taskId` (string, required, equals `workflowId` for Temporal-backed work)
  - `workflowId` (string, required)
  - `temporalRunId` (string, required, latest run only for default detail scope)
  - `namespace` (string, required)
  - `dashboardStatus` (enum: `queued`, `running`, `awaiting_action`, `succeeded`, `failed`, `cancelled`)
  - `rawState` (string, required)
  - `temporalStatus` (string, nullable)
  - `closeStatus` (string, nullable)
  - `title` (string, nullable)
  - `summary` (string, required)
  - `workflowType` (string, nullable)
  - `waitingReason` (string, nullable)
  - `attentionRequired` (boolean)
  - `startedAt` (datetime, nullable)
  - `updatedAt` (datetime, nullable)
  - `closedAt` (datetime, nullable)
- **Rules**:
  - `taskId` remains the canonical route handle.
  - `temporalRunId` is detail/debug metadata and the default artifact scope selector, not the route key.
  - Raw Temporal history JSON is not part of the default detail model.

## Entity: LatestRunScope

- **Description**: The execution-run boundary derived from the execution detail response and used to fetch artifacts.
- **Fields**:
  - `namespace` (string, required)
  - `workflowId` (string, required)
  - `temporalRunId` (string, required)
  - `canFetch` (boolean, required)
- **Rules**:
  - The scope is resolved from execution detail, not from stale list-row cache.
  - Default artifact presentation is limited to this run only.
  - Prior-run browsing, if introduced later, must be explicit and separate from the default view.

## Entity: TemporalTimelineEntry

- **Description**: A synthesized timeline row shown in the default Temporal detail page.
- **Fields**:
  - `label` (string, required)
  - `timestamp` (datetime, nullable)
  - `detail` (string, required)
- **Rules**:
  - Timeline rows summarize execution lifecycle and waiting context.
  - Timeline content must not expose raw event-history JSON blobs.

## Entity: ExecutionArtifactEntry

- **Description**: One artifact returned from the latest-run execution-scoped artifact list.
- **Fields**:
  - `artifact_id` (string, required)
  - `status` (string, required)
  - `content_type` (string, nullable)
  - `size_bytes` (integer, nullable)
  - `links` (array of `ExecutionArtifactLink`)
  - `preview_artifact_ref` (`ArtifactReadRef`, nullable)
  - `default_read_ref` (`ArtifactReadRef`, nullable)
  - `raw_access_allowed` (boolean)
- **Rules**:
  - Artifacts are immutable references.
  - Linkage metadata is the preferred source for display labels and link meaning.

## Entity: ExecutionArtifactLink

- **Description**: The execution-link metadata attached to an artifact.
- **Fields**:
  - `link_type` (string, required)
  - `label` (string, nullable)
- **Rules**:
  - The first/primary link may drive the default artifact label in the UI.
  - Link types remain stable machine-readable semantics even when labels are absent.

## Entity: ArtifactReadRef

- **Description**: Read-target metadata used to present or access preview/raw content.
- **Fields**:
  - `artifact_id` (string, required)
  - `content_type` (string, nullable)
  - `size_bytes` (integer, nullable)
- **Rules**:
  - `preview_artifact_ref` represents the safer preview target when present.
  - `default_read_ref` may supply display metadata even when the top-level artifact is restricted or transformed.

## Entity: ArtifactPresentation

- **Description**: The normalized UI-ready representation derived from one `ExecutionArtifactEntry`.
- **Fields**:
  - `artifactId` (string, required)
  - `artifactLabel` (string, required)
  - `linkType` (string, required)
  - `contentType` (string or `"-"`)
  - `size` (integer or `"-"`)
  - `status` (string, required)
  - `accessNotes` (array of string)
  - `actions` (array of `ArtifactPresentationAction`)
- **Rules**:
  - Prefer preview-first actions when a preview artifact exists.
  - Add raw-download action only when raw access is allowed.
  - When raw access is denied and no preview exists, present no access action and show an explicit safety note.

## Entity: ArtifactPresentationAction

- **Description**: A user-triggerable artifact access action exposed in the Temporal detail table.
- **Fields**:
  - `artifactId` (string, required)
  - `label` (enum: `Open preview`, `Download`, `Download raw`)
  - `variant` (enum: `preview`, `download`)
- **Rules**:
  - Actions resolve MoonMind-controlled access URLs before opening content.
  - `preview` may open a new window/tab; `download` may navigate the browser to an authorized URL.

## Entity: ArtifactAccessPolicy

- **Description**: The subset of artifact metadata that governs presentation safety.
- **Fields**:
  - `raw_access_allowed` (boolean, required)
  - `preview_artifact_ref` (`ArtifactReadRef`, nullable)
  - `default_read_ref` (`ArtifactReadRef`, nullable)
- **Rules**:
  - Access-policy metadata must be evaluated before deciding whether inline view, preview, or raw download is safe.
  - Policy metadata, not MIME type guesses, governs whether raw access is allowed.

## Entity: TaskActionSurface

- **Description**: The allowed task-oriented controls for a Temporal-backed detail page.
- **Fields**:
  - `actionsEnabled` (boolean)
  - `submitEnabled` (boolean)
  - `availableActions` (array of enum: `edit_inputs`, `rename`, `rerun`, `approve`, `pause`, `resume`, `cancel`)
  - `labels` (map of action key to task-oriented display copy)
- **Rules**:
  - Available actions depend on current Temporal-backed state.
  - Controls map to Temporal update/signal/cancel endpoints behind the scenes.
  - User-facing labels remain task-oriented.

## State Transitions

- **Temporal detail resolution**:
  - route load -> execution detail fetched -> latest run scope resolved -> latest-run artifacts fetched -> synthesized detail rendered
- **Rerun / Continue-As-New**:
  - `workflowId` remains stable
  - `temporalRunId` changes
  - default artifact scope updates to the latest run after the next detail fetch
- **Artifact access**:
  - action requested -> MoonMind access URL granted -> preview/download opened
  - restricted raw without preview -> no action exposed

## Invariants

- `taskId == workflowId` for Temporal-backed rows.
- The default artifact table shows latest-run artifacts only.
- Preview is preferred when available.
- Raw access is never inferred from file type or renderability alone.
- Artifact edits produce new immutable references rather than in-place mutation.
