# Artifact Presentation Contract

Status: Desired state
Owners: MoonMind Platform + UI
Last updated: 2026-05-06

## 1. Purpose

This document defines the API and UI consumption contract for displaying and interacting with artifact-backed outputs in MoonMind across execution, task, session, report, and observability surfaces.

It focuses on:

- how UI clients present artifacts in execution, task detail, report, and observability flows
- how API clients interpret artifact metadata fields
- stable conventions for `link_type`, default reads, downloads, previews, and rendering hints
- how artifact presentation connects to canonical runtime result and report bundle contracts
- how artifact presentation works for **task-scoped session containers** and managed-runtime continuity

This document is intentionally downstream of the artifact storage, execution identity, managed-runtime, report, and observability contracts. It does not redefine storage internals, artifact lifecycle implementation, managed-runtime supervision internals, report bundle semantics, or execution query semantics.

### 1.1 Related docs and ownership boundaries

- `docs/Temporal/WorkflowArtifactSystemDesign.md`
  - Owns storage backend, artifact identity, lifecycle, linkage, authorization, and activity-boundary design.
- `docs/Temporal/VisibilityAndUiQueryModel.md`
  - Owns execution identity, list/detail query semantics, and task compatibility rules.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
  - Owns canonical runtime contracts (`AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`) and execution-model boundaries.
- `docs/ManagedAgents/CodexCliManagedSessions.md`
  - Owns the desired-state contract for Codex task-scoped session identity, control actions, and clear/reset semantics.
- `docs/Observability/LiveLogs.md`
  - Owns live-log and observability APIs, artifact-backed tails, live streaming, and diagnostics presentation for managed runs.
- `docs/Artifacts/ReportArtifacts.md`
  - Owns report-specific artifact classes, report bundle conventions, report metadata, evidence separation, and report-first UI behavior.
- `docs/UI/MissionControlArchitecture.md`
  - Owns dashboard route wiring, source resolution, and mixed-source UI integration.

This document owns the **generic consumer-facing artifact presentation contract** layered on top of those decisions.

---

## 2. Scope / Non-goals

### 2.1 In scope

- artifact listing for one execution: `namespace`, `workflow_id`, `run_id`
- artifact metadata and its UI mapping
- preview vs raw download behavior
- standard viewer rules by `content_type` and metadata hints
- stable error codes and client behavior for common failure states
- runtime result integration and artifact-backed large outputs
- presentation rules for provider snapshots, managed-runtime diagnostics, reports, and task-scoped session continuity artifacts
- session-aware aggregation views for task-scoped session containers
- generic presentation rules that report-specific artifact contracts build on

### 2.2 Out of scope

- artifact storage backend internals
- full authorization model design
- artifact lifecycle cleanup workflows
- report-specific bundle assembly, report content schemas, or evidence taxonomy
- Temporal Web UI integration
- execution list/query/count semantics
- container-launching details, Docker orchestration internals, or managed-runtime PID/container reconciliation
- browser-native terminal embedding for managed-run logs
- persistence of full runtime home directories or auth volumes as user-facing artifacts

---

## 3. Consumer invariants

1. **Artifacts remain execution-centric.**
   - Artifacts attach durably to a Temporal execution identified by `(namespace, workflow_id, run_id)`.
   - Task-oriented, session-oriented, and report-oriented views are compatibility projections over execution-linked artifacts; they do not create a second durable artifact identity.

2. **Task-scoped session containers are continuity caches, not durable truth.**
   - A persistent managed-runtime container may survive across multiple plan steps within one task.
   - Any state needed for recovery, audit, presentation, or rerun must be materialized as artifacts or bounded workflow metadata.
   - Clients must never assume that container memory, container-local session databases, or runtime home directories are the canonical source of truth.

3. **Step boundaries remain first-class even when one session spans many steps.**
   - Session reuse must not collapse multiple plan steps into one undifferentiated artifact blob.
   - Each step must still produce step-scoped input/output/diagnostic evidence linked to the producing execution.

4. **Session resets create explicit epoch boundaries.**
   - `/clear`, hard reset, recovery rebuild, or equivalent intervention must create a new logical `session_epoch`.
   - UI clients must present epoch boundaries explicitly and must not imply one uninterrupted conversation across resets.

5. **`default_read_ref` is authoritative for the default read target.**
   - Clients must not override the server's choice based only on `redaction_level`.
   - `ArtifactRef` is an identifier, not an access grant or URL.

6. **Clients must not derive “latest” semantics locally.**
   - Use the server's query parameters and response shape.
   - Do not sort by `created_at` in the browser and call that canonical “latest output.”
   - “Latest output” for runtime, provider, session, and report artifacts must be server-driven, not client-grouped.

7. **Artifact metadata must be safe for control-plane display.**
   - `metadata` and any session-specific or report-specific fields are for bounded, display-safe hints and domain semantics.
   - They must not contain secrets, auth headers, cookies, session tokens, presigned URLs, private keys, or absolute local filesystem paths.

8. **Clients must degrade gracefully.**
   - Unknown `link_type`, `artifact_type`, `render_hint`, `content_type`, `checkpoint_kind`, `report_type`, or group keys must fall back to generic artifact presentation, not a hard failure.

9. **Detail pages must use the latest execution identity before listing artifacts.**
   - The `run_id` used for artifact listing must come from execution detail, not from stale cached state.
   - Reruns or Continue-As-New change the `run_id`, so artifact listing must refresh accordingly.

10. **Observability remains artifact-first even when live streaming exists.**
    - Live streaming is optional and secondary.
    - Artifact-backed stdout/stderr/diagnostics, event journals, and checkpoint artifacts remain authoritative for what happened.

11. **Reports are artifact families, not a separate storage system.**
    - Report artifacts use the same metadata, preview, raw-download, retention, and renderer rules as other artifacts.
    - Report-specific projections and report-first UI surfaces are convenience views over ordinary linked artifacts.

---

## 4. Glossary

- **Artifact**: Immutable blob stored outside Temporal history, plus metadata and linkage.
- **ArtifactRef**: Small pointer that workflows/activities can pass around safely.
- **ExecutionRef**: `(namespace, workflow_id, run_id)` identifying one Temporal execution.
- **Link**: Join row connecting an artifact to an execution with semantic `link_type`.
- **Preview artifact**: Redacted or reduced representation generated for safer presentation.
- **Raw artifact**: The original bytes, which may be restricted.
- **Task-scoped session container**: A managed-runtime container or long-lived process reused across multiple steps within one task.
- **Session ID**: Durable MoonMind identifier for one task-scoped runtime session.
- **Session epoch**: One logical continuity interval within a session. A reset or `/clear` starts a new epoch.
- **Step checkpoint artifact**: Compact MoonMind-owned artifact written at a step boundary so the task can be recovered or reviewed without depending on in-container state.
- **Session summary artifact**: Latest durable summary of the active session state used for presentation, handoff, and recovery.
- **Session control artifact**: Artifact describing an explicit session control action such as `/clear`, approve/reject, operator message, pause, resume, or cancel.
- **Session projection**: A server-produced read model that groups execution-linked artifacts by `session_id` and optional `session_epoch` for UI consumption.
- **Report artifact**: An artifact whose primary purpose is to communicate the outcome of a workflow, step, evaluation, or investigation.
- **Report bundle**: A compact result shape that references the human-facing report, summary, structured data, and supporting evidence artifacts.

---

## 5. Canonical data models: consumer-facing

### 5.1 ArtifactRef v1

Used in workflow/activity IO and returned by API.

```json
{
  "artifact_ref_v": 1,
  "artifact_id": "art_...",
  "sha256": "optional",
  "size_bytes": 1234,
  "content_type": "text/plain",
  "encryption": "none"
}
```

Rules:

- `artifact_ref_v` is the version marker for the ref shape.
- `artifact_id` is the durable handle.
- `ArtifactRef` does not include a URL and must not be treated as a permanent download token.
- Compact projections may use a reduced ref shape with only `artifact_ref_v` and `artifact_id` when size and safety matter more than inline metadata.

### 5.2 ArtifactMetadata

Returned by metadata and list endpoints.

Core presentation fields:

- `artifact_id`
- `created_at`
- `created_by_principal`
- `content_type`
- `size_bytes`
- `sha256`
- `status`: `pending_upload | complete | failed | deleted`
- `retention_class`: `ephemeral | standard | long | pinned`
- `expires_at`
- `redaction_level`: `none | preview_only | restricted`
- `metadata`: bounded JSON for UI hints and domain semantics
- `links[]`: execution links, each with `namespace`, `workflow_id`, `run_id`, `link_type`, and optional `label`
- `pinned`
- `artifact_ref`: ref for the raw artifact
- `preview_artifact_ref`: ref for the preview artifact, if available
- `raw_access_allowed`: whether the caller may access raw bytes
- `default_read_ref`: the artifact ref to use for default rendering when renderable
- `download_url` / `download_expires_at`: optional presigned raw download fields when explicitly requested and allowed

Optional session-continuity fields:

- `session_context`: nullable object with:
  - `session_id`
  - `session_epoch`
  - `step_id`
  - `step_index`
  - `turn_index`
  - `checkpoint_kind`: `step_checkpoint | session_summary | recovery_bundle | reset_boundary | none`
  - `control_event_kind`: `operator_message | clear | approve | reject | pause | resume | cancel | none`
  - `source_execution_ref`: `{ namespace, workflow_id, run_id }`

Returned but debug-oriented fields:

- `storage_backend`
- `storage_key`
- `encryption`

Client rules:

- Treat `default_read_ref` as the default display target when a display target is available.
- If `raw_access_allowed=false` and `preview_artifact_ref=null`, clients must show a pending or unavailable preview state rather than assume the raw artifact is readable.
- If `default_read_ref.artifact_id != artifact_id`, the client should resolve that referenced artifact through the normal artifact metadata/download flow.
- If `session_context` is present, the client may use it for grouping and badges, but must still treat `links[]` as the durable source execution evidence.
- If report metadata is present, the client may display it, but generic artifact clients must not depend on report-specific fields to render a safe fallback.

### 5.3 SessionProjection

Returned by session-aware list endpoints.

```json
{
  "task_run_id": "tr_...",
  "session_id": "sess_...",
  "session_epoch": 3,
  "grouped_artifacts": [
    {
      "group_key": "runtime",
      "title": "Runtime",
      "artifacts": ["ArtifactMetadata", "..."]
    },
    {
      "group_key": "continuity",
      "title": "Continuity",
      "artifacts": ["ArtifactMetadata", "..."]
    },
    {
      "group_key": "control",
      "title": "Control",
      "artifacts": ["ArtifactMetadata", "..."]
    }
  ],
  "latest_summary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_checkpoint_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_control_event_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_reset_boundary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." }
}
```

Rules:

- `SessionProjection` is a read convenience, not a second artifact identity model.
- Every artifact returned in a session projection must still include normal execution `links[]`.
- The session projection may span multiple executions within the same task when a session container is reused across steps.
- `grouped_artifacts` is server-defined. Clients may render known groups such as `runtime`, `continuity`, and `control`, but must tolerate unknown group keys.
- Missing refs may be omitted from both latest-ref fields and grouped-artifact lists.
- The projection must not require a live session container, live session-controller query, or container-local history.

### 5.4 ReportProjection

Report-specific projection shapes are owned by `docs/Artifacts/ReportArtifacts.md`.

Generic clients should understand only the following baseline:

```json
{
  "has_report": true,
  "latest_report_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_report_summary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "report_type": "security_pentest_report",
  "report_status": "final",
  "finding_counts": {
    "total": 8
  },
  "severity_counts": {
    "critical": 1,
    "high": 2
  }
}
```

Rules:

- Report projections are convenience read models over ordinary artifacts.
- Underlying report artifacts remain individually addressable through the standard artifact APIs.
- Generic artifact UI should still work if a report projection is absent.

---

## 6. Runtime result integration

### 6.1 Canonical runtime result discipline

True agent-runtime activities return compact canonical contracts. Large outputs stay in artifacts.

The canonical contracts are:

- `AgentRunHandle`
- `AgentRunStatus`
- `AgentRunResult`

`AgentRunResult` normally carries artifact refs for large outputs:

- `output_refs[]` — primary output artifacts
- `diagnostics_ref` — operational diagnostics artifact

This means:

- provider raw payload dumps belong in artifacts, not workflow history
- managed runtime logs belong in artifacts
- large summaries or diffs belong in artifacts
- session summaries and step checkpoints belong in artifacts
- report bodies, report evidence, and structured findings belong in artifacts
- workflow history should only see small summaries, refs, and bounded metadata

### 6.2 Task-scoped session container rule

For task-scoped session containers, the artifact contract must behave as follows:

- the container may persist across multiple plan steps
- the container may keep native runtime state for efficiency and continuity
- MoonMind must still materialize step inputs, step outputs, logs, diagnostics, summaries, and checkpoints as artifacts
- task and detail UIs must prefer artifact evidence over in-container session assumptions
- the existence of one long-lived session must not remove the requirement for per-step artifact evidence

### 6.3 Step and session durability rule

For each managed step that uses a task-scoped session container, the durable presentation surface should include:

- step-scoped input artifacts such as instructions, plan fragments, skill snapshots, and operator feedback
- step-scoped output artifacts such as primary output, patch, summary, and agent result
- runtime stdout, stderr, merged logs, event journals, and diagnostics artifacts for the producing execution
- at least one session continuity artifact: a step checkpoint, session summary, or both
- explicit control or reset artifacts when the operator intervenes or the session epoch changes

A task-scoped session container may be reused, but no user-important state should exist only inside that container.

### 6.4 Rule for task/detail UIs

Task and detail UIs should prefer execution-linked artifact evidence over provider-native inline payloads and over ephemeral container state.

When an `AgentRunResult` references artifacts, the UI should:

1. list artifacts by execution using the standard list endpoint
2. render each artifact using the standard presentation rules
3. use session projections only as a grouping and continuity aid
4. not attempt to reconstruct or parse raw provider payloads inline
5. not treat container-local session memory as canonical history

---

## 7. Endpoint contract summary

All endpoints below are server-relative. Authentication behavior is controlled by the app auth mode.

### 7.1 Create an artifact

`POST /api/artifacts`

Request: `CreateArtifactRequest`

- `content_type` optional
- `size_bytes` optional, recommended
- `sha256` optional, recommended
- `retention_class` optional
- `link` optional initial execution link
- `metadata` optional JSON
- `redaction_level` optional, default `none`
- `encryption` optional, default `none`

Response:

- `artifact_ref`
- `upload`: mode plus URL or upload ID, expiry, max size, and required headers

### 7.2 Upload bytes

`PUT /api/artifacts/{artifact_id}/content`

Rules:

- Sends the raw request body as artifact bytes.
- If a `Content-Type` header is present, it becomes the artifact `content_type`.
- Clients should not assume the artifact is readable until metadata reports `status=complete`.

### 7.3 Multipart part presign

`POST /api/artifacts/{artifact_id}/presign-upload-part`

Request:

- `part_number`

Response:

- `part_number`
- `url`
- `expires_at`
- `required_headers`

### 7.4 Complete multipart upload

`POST /api/artifacts/{artifact_id}/complete`

Request:

- `parts[]`: `part_number`, `etag`

Response:

- `ArtifactRef`

### 7.5 Get metadata

`GET /api/artifacts/{artifact_id}`

Query:

- `include_download` boolean, default `false`

Response:

- `ArtifactMetadata`

Notes:

- If `include_download=true` and raw access is allowed, the response includes `download_url` and `download_expires_at`.
- Clients should not assume `download_url` is present unless they explicitly requested it.

### 7.6 Presign download

`POST /api/artifacts/{artifact_id}/presign-download`

Response:

- `url`
- `expires_at`

Use this for raw-download affordances when `raw_access_allowed=true`.

### 7.7 Download via API proxy

`GET /api/artifacts/{artifact_id}/download`

Returns:

- `Content-Type: artifact.content_type || application/octet-stream`
- `Content-Disposition: attachment; filename="{artifact_id}"`

Notes:

- The proxy filename is transport-oriented and uses `artifact_id` unless a sanitized product-level override is later added.
- Clients should prefer `metadata.name` or other UI labels for display text instead of deriving user-facing names from the download filename.

### 7.8 Link artifact to an execution

`POST /api/artifacts/{artifact_id}/links`

Request:

- `namespace`
- `workflow_id`
- `run_id`
- `link_type`
- optional `label`
- optional provenance:
  - `created_by_activity_type`
  - `created_by_worker`

Response:

- updated `ArtifactMetadata`

### 7.9 List artifacts for an execution

`GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

Query:

- `link_type` optional
- `latest_only` boolean, default `false`

Response:

```json
{
  "artifacts": ["ArtifactMetadata", "..."]
}
```

Rules:

- `latest_only=true` is guaranteed only when paired with `link_type`; in that case the response contains at most one artifact, the latest for that execution/link pair.
- Without `link_type`, clients must not assume the server collapses results to one artifact per `link_type`.

### 7.10 Get artifact projection for a task-scoped session

`GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`

Response:

- `SessionProjection`

Current rules:

- This endpoint is a server-side read model over persisted artifacts and the durable managed-session record.
- It must not require a live session container, live session-controller query, or container-local history.
- It must return only artifacts that still carry ordinary execution `links[]`.
- Missing artifact refs may be omitted from both latest-ref fields and grouped-artifact lists.
- The server defines grouping and latest-ref semantics for the session projection.

Future extension rule:

- If query filters such as `session_epoch`, `link_type`, or `latest_only` are added later, the server must define their scope. Clients must not re-derive canonical “latest” locally.

### 7.11 Pin / unpin

- `POST /api/artifacts/{artifact_id}/pin`
- `DELETE /api/artifacts/{artifact_id}/pin`

Pin request includes optional `reason`.

### 7.12 Delete

`DELETE /api/artifacts/{artifact_id}`

Response:

- updated `ArtifactMetadata`, typically `status=deleted`

---

## 8. Error contract

Errors are returned as `HTTPException.detail` objects with:

- `code`: stable machine code
- `message`: human-readable string

Common codes:

- `authentication_required` 401
- `artifact_not_found` 404
- `artifact_forbidden` 403
- `artifact_state_error` 409
- `invalid_artifact_payload` 422
- `artifact_too_large` 413
- `session_projection_not_found` 404
- `session_projection_unavailable` 409

Client rules:

- Treat `authentication_required` as an auth/session problem, not a retry loop.
- Treat `artifact_state_error` as recoverable. Refresh UI and show uploading/processing state.
- Treat `artifact_forbidden` as “do not retry raw access.” Hide or disable raw-download affordances.
- Treat `session_projection_not_found` as “session projection unavailable for this task run,” not as proof that the task has no artifacts.
- Treat `session_projection_unavailable` as a projection/read-model issue. Fall back to execution-scoped artifact listing instead of leaving the UI blank.

---

## 9. Presentation rules: UI contract

### 9.1 Primary UI surfaces

Execution detail or task detail should expose:

- an **Execution Artifacts** panel for the current execution
- a **Session Continuity** panel when the task used a task-scoped session container
- a **Report** panel or report card when the execution or task has a canonical report artifact
- observability/log panels that can read live streams or artifact-backed histories

Common execution-artifact modes:

- all artifacts for the execution
- filtered artifacts by `link_type`
- latest artifact for one known execution/link pair

Default execution list call:

- `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

The `run_id` must be the latest `temporalRunId` from the execution detail response.

Common session-continuity modes:

- server-defined grouped artifacts for the active session
- runtime group for stdout, stderr, diagnostics, and merged-log artifacts
- continuity group for latest summary and step checkpoint artifacts
- control group for latest control-event and reset-boundary artifacts
- explicit epoch boundaries when `session_epoch` changes

Default session view call:

- `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`

Artifact detail drawer/page should:

- fetch `GET /api/artifacts/{artifact_id}`
- render metadata plus the default content target
- expose raw download only when `raw_access_allowed=true`
- show session badges when `session_context` is present
- show report badges when report metadata is present
- show the source execution reference for session-projected or report-projected artifacts

### 9.2 Default read and download behavior

Default rendering:

- resolve `default_read_ref.artifact_id`
- fetch metadata for that artifact if needed
- render it using the renderer-selection rules below

Raw download:

- if `raw_access_allowed=true`, call `POST /api/artifacts/{artifact_id}/presign-download` and navigate to that URL
- otherwise show a restricted message and do not attempt raw download

Clients must not use `presign-download` for inline preview rendering by default unless the product intentionally wants browser-native rendering of the raw asset.

### 9.3 Redaction / preview behavior

Use these fields as follows:

- If `raw_access_allowed=false`:
  - UI must not attempt raw download.
  - If `preview_artifact_ref` is present, use `default_read_ref` for the default preview target.
  - If `preview_artifact_ref` is absent, show “Preview not yet available” and offer refresh.
- If `redaction_level=restricted` and `raw_access_allowed=true`:
  - UI may visually de-emphasize raw download or call out that the artifact is restricted.
  - UI must still treat `default_read_ref` as authoritative for the default display target.
- If `redaction_level=preview_only`:
  - treat preview presentation as an intended steady-state consumer surface, not as an error condition.

Preview/raw-access behavior applies equally to:

- provider snapshots
- managed-runtime diagnostics
- session summaries
- session checkpoints
- session control artifacts
- report artifacts
- evidence artifacts

### 9.4 What to show/hide from metadata

Show to most users:

- created time
- size
- content type
- checksum when useful
- `link_type` plus `label`
- retention plus `expires_at`
- pinned status
- redaction status
- session badge information from `session_context` when present:
  - `session_id`
  - `session_epoch`
  - `step_id` / `step_index`
  - `checkpoint_kind`
  - `control_event_kind`
- report badge information when present and safe:
  - `report_type`
  - `report_scope`
  - `title`
  - `producer`
  - bounded counts such as `finding_counts` or `severity_counts`

Hide by default or keep debug-only:

- `storage_backend`
- `storage_key`
- `created_by_principal` unless ownership/debug context requires it
- `encryption` unless needed for compliance/operator UI
- provenance fields such as `created_by_activity_type` and `created_by_worker`
- raw recovery internals that expose local runtime layout or secret-bearing paths
- raw provider payload details unless intentionally opened
- report evidence internals that may contain sensitive target, exploit, PII, or credential data

### 9.5 “Latest output” semantics

The canonical latest-output contract is server-defined scope plus `link_type`, not a client-derived grouping heuristic.

Rules:

- if the UI wants “latest output” for a known artifact kind within one execution, it should specify `link_type`
- if the UI wants “latest output” within one task-scoped session, it should use the session endpoint
- if the UI wants “latest report” within one execution or task, it should use report-specific server projection or `link_type=report.primary`
- if the UI wants cross-link aggregation later, add an explicit server mode instead of collapsing client-side
- this applies equally to `output.primary`, `output.provider_snapshot`, `runtime.diagnostics`, `session.summary`, `session.step_checkpoint`, `report.primary`, `report.summary`, and all other link types

### 9.6 Session continuity presentation

When task-scoped session containers are in use, the UI must present continuity without making the container itself the source of truth.

Rules:

- the Session Continuity panel must be artifact-backed
- the panel must show explicit epoch boundaries when `session_epoch` changes
- `/clear`, hard reset, or recovery rebuild must appear as an explicit control or reset artifact, not as an invisible gap
- the panel must show the latest session summary, latest checkpoint, latest control event, and latest reset boundary when available
- the panel should render server-defined groups such as `runtime`, `continuity`, and `control` without hiding unknown future groups
- the panel must preserve links back to the source execution that produced each artifact
- the panel must not imply that all artifacts came from one execution simply because they share one `session_id`
- if the session projection is unavailable, the UI should fall back to execution-scoped artifacts plus standard task step context

### 9.7 Logs and observability

Managed-run log viewing remains governed by the observability APIs.

Rules:

- task detail log panels should use the observability APIs for live tails and artifact-backed log retrieval
- artifact panels may still show `runtime.stdout`, `runtime.stderr`, `runtime.merged_logs`, `runtime.diagnostics`, and event-journal artifacts when those artifacts exist
- the artifact panel must not attempt to simulate a terminal session
- ended runs should always remain viewable from artifacts even if no live connection exists
- live streams and artifact-backed logs should converge on the same durable evidence model where possible

### 9.8 Report presentation

Report-first presentation is owned by `docs/Artifacts/ReportArtifacts.md`, but generic artifact UI must still handle report artifacts.

Rules:

- if an execution has `report.primary`, Mission Control may show a report-first card or panel
- report artifacts must still appear in generic artifact lists unless intentionally filtered
- report evidence should remain separately addressable
- generic rendering must use the same `default_read_ref`, redaction, raw-download, and renderer-selection rules as any other artifact
- report projections must not hide the underlying artifact identity or execution linkage

---

## 10. Rendering rules by `content_type` and hints

### 10.1 Renderer selection priority

1. `metadata.render_hint`
2. `content_type`
3. filename-like hint in `metadata.name`
4. fallback: binary / download-only

Unknown `render_hint` values must be ignored, not treated as fatal.

### 10.2 Supported renderers: recommended baseline

#### Text viewer

Content types:

- `text/plain`
- `text/markdown`
- `text/x-diff`
- `application/x-ndjson`

UI behavior:

- inline preview up to `MAX_INLINE_BYTES`, for example 256 KB
- if larger, show an explicit open/download action rather than eagerly loading the full body
- show diff formatting when `content_type` or `render_hint` indicates patch/diff content
- when rendering `application/x-ndjson`, preserve line boundaries and avoid silently coalescing records

#### JSON viewer

Content types:

- `application/json`

UI behavior:

- pretty-print JSON for preview
- if parsing fails, fall back to text viewer
- for very large JSON, show a bounded preview plus open/download action

#### Image viewer

Content types:

- `image/png`
- `image/jpeg`
- `image/webp`
- `image/gif`

UI behavior:

- inline preview, subject to redaction/default-read rules
- show dimensions when available
- do not inline images when access is restricted and no preview is available

#### PDF and document exports

Content types:

- `application/pdf`
- `text/html`
- other document/export formats

UI behavior:

- default to metadata + download/open action unless a deliberate viewer is implemented
- do not embed untrusted HTML inline without sanitization
- use `default_read_ref` if a safe preview exists

#### Binary / unknown

Everything else:

- show metadata, size, and download action if allowed
- no inline rendering by default

### 10.3 Standard metadata hints

These reserved keys live under `ArtifactMetadata.metadata`:

- `name`: display filename, basename only
- `title`: display title
- `description`: short UI description
- `source`: bounded origin string such as `worker:codex`, `worker:runner`, `ui:upload`
- `artifact_type`: stable domain classifier
- `render_hint`: `text | json | diff | image | binary`
- `preview_of`: source artifact ID for preview artifacts
- `policy`: preview/redaction policy name

Reserved nested session keys under `metadata.session` are allowed for producer-specific elaboration, but they must not duplicate or conflict with `session_context`.

Rules:

- standardized keys above are reserved for cross-client interpretation
- additional producer-specific keys should live under a producer-owned nested object to avoid future key collisions
- `name` must not include path separators or leak local workspace layout
- if these keys are absent, UI should still function using `content_type`, `links`, and `session_context`

Report-specific metadata keys such as `report_type`, `report_scope`, `sensitivity`, `producer`, `subject`, `finding_counts`, `severity_counts`, and `is_final_report` are defined by `docs/Artifacts/ReportArtifacts.md`. Generic clients may display them when present, but this document does not own their schema.

---

## 11. Presentation guidance for artifact classes

### 11.1 Standard artifact classes

These artifact classes have well-defined presentation behavior:

| `link_type` | Typical `content_type` | Recommended renderer | Notes |
| --- | --- | --- | --- |
| `input.instructions` | `text/plain`, `text/markdown` | Text viewer | User-provided or system-provided instructions for one step |
| `input.manifest` | `application/json` | JSON viewer | Manifest definition |
| `input.plan` | `application/json` | JSON viewer | Pre-computed plan |
| `input.skill_snapshot` | `application/json` | JSON viewer | Resolved skill set |
| `input.prompt_index` | `application/json` | JSON viewer | Prompt index for runtime |
| `output.primary` | varies | Auto-detect | Primary execution output |
| `output.patch` | `text/x-diff` | Diff viewer | Code patch |
| `output.logs` | `text/plain` | Text viewer | Execution logs outside managed-run observability surfaces |
| `output.summary` | `text/plain`, `text/markdown` | Text viewer | Human-readable summary |
| `output.agent_result` | `application/json` | JSON viewer | Canonical agent result |

### 11.2 Provider and runtime artifact classes

These artifact classes represent large operational outputs from agent execution:

| `link_type` | Typical `content_type` | Recommended renderer | Notes |
| --- | --- | --- | --- |
| `output.provider_snapshot` | `application/json` | JSON viewer | Raw provider result snapshot; may be restricted |
| `runtime.stdout` | `text/plain` | Text viewer | Managed runtime stdout |
| `runtime.stderr` | `text/plain` | Text viewer | Managed runtime stderr |
| `runtime.merged_logs` | `text/plain` | Text viewer | Combined stdout/stderr when persisted as an artifact |
| `runtime.diagnostics` | `application/json`, `text/plain` | Auto-detect | Operational diagnostics; may be restricted |
| `runtime.skill_materialization` | `application/json` | JSON viewer | Runtime delivery bundle |

Rules:

- provider snapshots and managed-runtime diagnostics may contain sensitive operational detail
- preview/raw-access behavior applies equally to these classes
- UI should not assume these artifacts are safe for unrestricted inline display
- large runtime logs should use the standard size-threshold behavior: inline preview up to `MAX_INLINE_BYTES`, then explicit open/download
- the presence of a live log panel does not remove the value of durable runtime artifacts

### 11.3 Session continuity artifact classes

These artifact classes exist specifically to keep task-scoped session containers artifact-first:

| `link_type` | Typical `content_type` | Recommended renderer | Notes |
| --- | --- | --- | --- |
| `session.summary` | `application/json`, `text/markdown` | Auto-detect | Latest durable summary of session state; may be updated each step |
| `session.step_checkpoint` | `application/json` | JSON viewer | Step-boundary checkpoint used for recovery and review |
| `session.control_event` | `application/json` | JSON viewer | `/clear`, operator message, approval, rejection, pause, resume, cancel |
| `session.transcript` | `application/x-ndjson`, `text/plain` | Text viewer | Optional ordered session event stream |
| `session.recovery_bundle` | `application/json` | JSON viewer | Sanitized recovery bundle; operator/debug-oriented |
| `session.reset_boundary` | `application/json`, `text/plain` | Auto-detect | Explicit epoch boundary after reset or rebuild |

Rules:

- `session.summary` and `session.step_checkpoint` are the preferred continuity artifacts for normal users
- `session.control_event` must be visible enough that resets and operator interventions are not invisible
- `session.reset_boundary` should appear whenever a session epoch boundary changes because of `/clear`, hard reset, or recovery rebuild
- `session.recovery_bundle` is usually debug/operator oriented and may be restricted
- clients must not assume that `session.transcript` exists; session continuity must still work from summary/checkpoint/control/reset artifacts
- session artifacts should carry `session_context` so the UI can group them by session and epoch without hiding the source execution

### 11.4 Report artifact classes

Report-specific semantics are owned by `docs/Artifacts/ReportArtifacts.md`, but generic artifact clients should recognize these classes enough to render them consistently.

| `link_type` | Typical `content_type` | Recommended renderer | Notes |
| --- | --- | --- | --- |
| `report.primary` | `text/markdown`, `text/plain`, `application/pdf`, `text/html` | Auto-detect | Canonical human-facing report for the current scope |
| `report.summary` | `text/markdown`, `text/plain`, `application/json` | Auto-detect | Short executive summary or abstract |
| `report.structured` | `application/json` | JSON viewer | Machine-readable findings or results |
| `report.evidence` | varies | Auto-detect | Supporting evidence such as screenshots, excerpts, command results, or captures |
| `report.appendix` | varies | Auto-detect | Extended detail intentionally separate from the main report |
| `report.findings_index` | `application/json` | JSON viewer | Structured findings index optimized for grouping/filtering |
| `report.export` | `application/pdf`, `text/html`, varies | Binary/download or auto-detect | Alternative rendered export such as PDF or HTML |

Rules:

- generic artifact clients should use the same preview, default-read, raw-download, and renderer-selection rules for report artifacts as for other artifacts
- if a report-specific projection exists, Mission Control may show report artifacts in a report-first surface while keeping them accessible through generic artifact views
- report evidence remains separately addressable and must not be hidden inside one opaque report blob when separate access improves auditability
- `application/pdf` should remain metadata + download-only unless a PDF viewer is intentionally added
- untrusted `text/html` report exports must not be injected into Mission Control without sanitization

### 11.5 Debug artifact classes

| `link_type` | Typical `content_type` | Recommended renderer | Notes |
| --- | --- | --- | --- |
| `debug.trace` | `application/json`, `text/plain` | Auto-detect | Execution trace |
| `debug.skill_resolution_trace` | `application/json` | JSON viewer | Skill resolution debug info |

Debug artifacts should be hidden by default in normal user views and shown in operator/debug panels.

---

## 12. Upload UX contract

### 12.1 Simple upload

1. `POST /api/artifacts`
2. If `upload.mode=single_put` and `upload.upload_url` is present:
   - upload bytes to that URL with required headers
3. If local-dev mode returns a direct API path:
   - `PUT /api/artifacts/{artifact_id}/content`
4. Refresh metadata until `status=complete`

### 12.2 Multipart upload

1. `POST /api/artifacts`
2. Expect `upload.mode=multipart` and `upload.upload_id`
3. For each part:
   - `POST /api/artifacts/{artifact_id}/presign-upload-part`
   - upload the part to the returned URL
4. `POST /api/artifacts/{artifact_id}/complete` with the completed parts list
5. Refresh metadata until `status=complete`

### 12.3 Managed runtime artifact discipline

The upload UX rules also apply to task-scoped session container outputs.

Rules:

- step inputs may be artifact-created before the container receives them
- live runtime output may be spooled and later persisted, but the final presentation contract remains artifact-first
- session summaries, checkpoints, control artifacts, and reset-boundary artifacts may be worker-created rather than browser-uploaded, but they must still obey the same metadata and linkage rules
- report artifacts may be workflow-created, worker-created, or assembled from runtime evidence, but their final presentation still uses the ordinary artifact contract

---

## 13. Execution linkage conventions

### 13.1 Required link fields

- `namespace`
- `workflow_id`
- `run_id`
- `link_type`

### 13.2 `link_type` naming

Use lowercase, dot-delimited, stable machine meaning.

Complete `link_type` registry:

- `input.instructions`
- `input.manifest`
- `input.plan`
- `input.skill_snapshot`
- `input.prompt_index`
- `output.primary`
- `output.patch`
- `output.logs`
- `output.summary`
- `output.provider_snapshot`
- `output.agent_result`
- `runtime.skill_materialization`
- `runtime.stdout`
- `runtime.stderr`
- `runtime.merged_logs`
- `runtime.diagnostics`
- `session.summary`
- `session.step_checkpoint`
- `session.control_event`
- `session.transcript`
- `session.recovery_bundle`
- `session.reset_boundary`
- `report.primary`
- `report.summary`
- `report.structured`
- `report.evidence`
- `report.appendix`
- `report.findings_index`
- `report.export`
- `debug.skill_resolution_trace`
- `debug.trace`

Rules:

- `link_type` should stay small, bounded, and stable
- use `link_type` for machine meaning and `label` for human-facing nuance
- clients must tolerate unknown `link_type` values and display them generically
- report-specific link types are valid ordinary artifact link types, not a separate report-storage namespace

### 13.3 `label` usage

- optional, human-friendly
- can carry versioning or presentation nuance, for example `Plan v2`, `Final output`, `Epoch 3 reset`, or `Executive summary`
- must not replace `link_type` as the machine key

### 13.4 Session context rules

When artifacts participate in a task-scoped session view:

- `session_context.session_id` should be stable for the lifetime of the task-scoped session
- `session_context.session_epoch` must increase when the session is cleared, reset, or rebuilt
- `session_context.step_id` and `session_context.step_index` should identify the step that produced or consumed the artifact when applicable
- `session_context.turn_index` may identify an interaction turn inside the session
- `session_context.source_execution_ref` must preserve the execution that produced the artifact
- session projection grouping must not erase execution linkage
- session-specific metadata must not include container paths, auth volume paths, cookies, tokens, or provider credentials

### 13.5 Report linkage rules

When artifacts participate in a report view:

- use `report.*` link types for artifacts that are intentionally part of a report deliverable
- use `output.*` link types for generic outputs that are not report deliverables
- use `runtime.*` and `debug.*` link types for operational evidence, not curated report content
- use bounded report metadata only for presentation and filtering
- do not store report bodies, finding details, screenshots, or evidence blobs inline in metadata
- keep report evidence separately addressable when separate evidence improves auditability or reuse

---

## 14. Producer rules

### 14.1 Workflows

Workflows should pass only:

- `ArtifactRef`
- compact summaries
- bounded metadata
- server-defined selectors
- canonical runtime result contracts
- compact report bundle refs when applicable

Workflows must not pass:

- large artifact bodies
- provider-native raw payloads
- long logs
- screenshots or binary evidence
- presigned URLs
- secrets or credentials
- full container-local state

### 14.2 Activities and workers

Activities and workers are responsible for side effects such as:

- writing artifact bytes
- finalizing uploads
- generating previews
- linking artifacts to executions
- writing runtime logs and diagnostics
- publishing session summaries and checkpoints
- publishing report artifacts and report evidence
- returning compact refs and bounded metadata to workflow code

### 14.3 Managed runtime producers

Managed runtime producers should:

- write stdout/stderr/diagnostics as runtime artifacts
- write final user-facing output as `output.primary` or report artifacts as appropriate
- write session continuity evidence when a task-scoped session is used
- avoid relying on container-local state for recovery or presentation
- sanitize metadata and logs before exposing them in the control plane

### 14.4 Report producers

Report producers should:

- publish `report.primary` when a report is the canonical deliverable
- publish `report.summary`, `report.structured`, and `report.evidence` when useful
- keep curated report content separate from raw logs and diagnostics
- use report bundle refs rather than large report payloads in workflow history
- follow `docs/Artifacts/ReportArtifacts.md` for report-specific metadata and bundle semantics

---

## 15. Security and access behavior

### 15.1 Default access posture

Artifact access is governed by the artifact system and execution authorization rules.

Clients must:

- request metadata before raw access
- honor `raw_access_allowed`
- use `default_read_ref` for safe preview/default rendering
- avoid retry loops when access is forbidden
- avoid leaking artifact IDs, labels, or metadata into unrelated user surfaces

### 15.2 Sensitive artifacts

These artifact classes should be treated as potentially sensitive:

- `output.provider_snapshot`
- `runtime.diagnostics`
- `runtime.merged_logs`
- `session.recovery_bundle`
- `report.structured`
- `report.evidence`
- `report.export`
- `debug.trace`
- `debug.skill_resolution_trace`

This list is not exhaustive. Clients must follow metadata and access-policy fields rather than assuming a class is safe.

### 15.3 Presigned URLs

Rules:

- presigned URLs are access grants, not artifact identity
- presigned URLs must not be stored in workflow history, metadata, or durable report bodies
- clients should request presigns only when the user intentionally opens or downloads raw content
- clients should not copy presigned URLs into logs or diagnostics

### 15.4 Metadata safety

Artifact metadata must remain bounded and display-safe.

Metadata must not include:

- secrets
- API keys
- OAuth tokens
- provider auth material
- cookies
- presigned URLs
- private keys
- raw payload bodies
- raw report bodies
- absolute local filesystem paths
- container-local auth volume paths
- large nested data structures

---

## 16. Retention and pinning presentation

Retention semantics are owned by the artifact system, but UI clients should present retention clearly.

Rules:

- show retention class and expiry where useful
- show pinned state prominently on important outputs and reports
- do not imply that an artifact is permanent unless it is pinned or governed by a long-retention policy
- expose pin/unpin affordances where authorization allows
- when a report is the primary deliverable, Mission Control may make pinning especially visible
- deleted artifacts should render as unavailable/tombstoned rather than silently disappearing from historical context when the API returns tombstones

---

## 17. Compatibility and migration guidance

### 17.1 Move from Temporal folder to Artifacts folder

The canonical path for this document is:

- `docs/Artifacts/ArtifactPresentationContract.md`

The old path:

- `docs/Temporal/ArtifactPresentationContract.md`

should be treated as stale after the move.

References should be updated across docs, specs, and code comments.

### 17.2 Compatibility with older artifact producers

Older producers may lack:

- `session_context`
- report-specific metadata
- preview artifacts
- explicit report link types
- server-side session projections

Clients must still render artifacts generically using:

- `artifact_id`
- `content_type`
- `size_bytes`
- `links[]`
- `metadata`
- `raw_access_allowed`
- `default_read_ref`

### 17.3 Report rollout compatibility

During report rollout:

- generic outputs may still use `output.primary`
- report-producing workflows should prefer `report.primary`
- UI should not guess that every `output.primary` artifact is a report
- report-first surfaces should fall back to generic artifact lists when no report projection or `report.primary` artifact exists

### 17.4 Session projection compatibility

During managed-session rollout:

- session projections may initially include only latest refs and grouped artifacts from the durable managed-session record
- older task runs may not have a session projection
- clients should fall back to execution-scoped artifacts and observability views
- unknown future group keys should be rendered as generic groups, not hidden

---

## 18. Testing expectations

Consumer-facing artifact changes should include tests that cover:

- default rendering uses `default_read_ref`
- raw download is hidden or disabled when `raw_access_allowed=false`
- unknown `link_type` falls back to generic rendering
- unknown `render_hint` falls back to content-type-based rendering
- large text artifacts do not eagerly load beyond the configured inline preview threshold
- session projections render grouped artifacts and latest refs
- missing session projection falls back to execution artifacts
- report artifacts render through generic artifact rules
- `report.primary` can be surfaced without hiding generic artifact access
- sensitive report and runtime artifacts preserve preview/raw-access behavior
- stale `docs/Temporal/ArtifactPresentationContract.md` references are removed after the move

---

## 19. Open questions

1. Should `ArtifactMetadata` grow a first-class `session_context` column-backed field, or should session presentation remain metadata/projection-driven for now?
2. Should report-aware execution projections live under execution endpoints, task endpoints, or both?
3. Should `application/pdf` get a first-party viewer, or should PDFs remain download/open-only for safety and simplicity?
4. Should latest-artifact queries support a generic multi-link server aggregation mode, or should each projection define its own latest refs?
5. Should `report.evidence` receive additional grouping metadata such as `finding_id`, `section_id`, or `evidence_kind` in the report-specific contract?
6. Should session projections eventually support historical epoch browsing through query filters, or should they remain latest-session surfaces with links back to execution-scoped artifacts?
7. How much debug-only metadata should Mission Control expose to local single-user installs by default?

---

## 20. Bottom line

MoonMind should keep artifact presentation as a generic, artifact-first contract under `docs/Artifacts`.

The artifact system is still execution-linked and Temporal-compatible, but the presentation contract now spans more than Temporal:

- runtime outputs
- managed-session continuity
- observability logs and diagnostics
- report deliverables
- report evidence
- task detail projections
- future UI convenience surfaces

This document should therefore move to `docs/Artifacts/ArtifactPresentationContract.md` and remain the generic presentation layer that more specific documents, especially `docs/Artifacts/ReportArtifacts.md`, build on.