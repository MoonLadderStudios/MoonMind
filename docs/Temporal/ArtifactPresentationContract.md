# Artifact Presentation Contract

Status: Desired state
Owners: MoonMind Platform + UI
Last updated: 2026-04-06

## 1. Purpose

This document defines the API and UI consumption contract for displaying and interacting with Temporal-linked artifacts in MoonMind.

It focuses on:

- how UI clients present artifacts in execution and task detail flows
- how API clients interpret artifact metadata fields
- stable conventions for `link_type`, default reads, downloads, previews, and rendering hints
- how artifact presentation connects to canonical runtime result contracts
- how artifact presentation works after MoonMind adopts **task-scoped session containers** for managed runtimes

This document is intentionally downstream of the broader Temporal architecture. It does not redefine storage internals, artifact lifecycle implementation, managed-runtime supervision internals, or execution query semantics.

### 1.1 Related docs and ownership boundaries

- `docs/Temporal/WorkflowArtifactSystemDesign.md`
  - Owns storage backend, artifact identity, lifecycle, linkage, and authorization design.
- `docs/Temporal/VisibilityAndUiQueryModel.md`
  - Owns execution identity, list/detail query semantics, and task compatibility rules.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
  - Owns canonical runtime contracts (`AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`) and execution-model boundaries.
- `docs/ManagedAgents/CodexManagedSessionPlane.md`
  - Owns the desired-state contract for Codex task-scoped session identity, control actions, and clear/reset semantics.
- `docs/ManagedAgents/LiveLogs.md`
  - Owns live-log and observability APIs, artifact-backed tails, live streaming, and diagnostics presentation for managed runs.
- `docs/UI/MissionControlArchitecture.md`
  - Owns dashboard route wiring, source resolution, and mixed-source UI integration.

This document owns the **consumer-facing artifact contract** layered on top of those decisions.

---

## 2. Scope / Non-goals

### 2.1 In scope

- artifact listing for one execution: `namespace`, `workflow_id`, `run_id`
- artifact metadata and its UI mapping
- preview vs raw download behavior
- standard viewer rules by `content_type` and metadata hints
- stable error codes and client behavior for common failure states
- runtime result integration and artifact-backed large outputs
- presentation rules for provider snapshots, managed-runtime diagnostics, and task-scoped session continuity artifacts
- session-aware aggregation views for task-scoped session containers

### 2.2 Out of scope

- artifact storage backend internals
- full authorization model design
- artifact lifecycle cleanup workflows
- Temporal Web UI integration
- execution list/query/count semantics
- container-launching details, Docker orchestration internals, or managed-runtime PID/container reconciliation
- browser-native terminal embedding for managed-run logs
- persistence of full runtime home directories or auth volumes as user-facing artifacts

---

## 3. Consumer invariants

1. **Artifacts remain execution-centric.**
   - Artifacts attach durably to a Temporal execution identified by `(namespace, workflow_id, run_id)`.
   - Task-oriented and session-oriented views are compatibility projections over execution-linked artifacts; they do not create a second durable artifact identity.

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
   - “Latest output” for runtime/provider/session artifacts must be server-driven, not client-grouped.

7. **Artifact metadata must be safe for control-plane display.**
   - `metadata` and any session-specific fields are for bounded, display-safe hints and domain semantics.
   - They must not contain secrets, auth headers, cookies, session tokens, presigned URLs, private keys, or absolute local filesystem paths.

8. **Clients must degrade gracefully.**
   - Unknown `link_type`, `artifact_type`, `render_hint`, `content_type`, or `checkpoint_kind` values must fall back to generic artifact presentation, not a hard failure.

9. **Detail pages must use the latest execution identity before listing artifacts.**
   - The `run_id` used for artifact listing must come from execution detail, not from stale cached state.
   - Reruns or Continue-As-New change the `run_id`, so artifact listing must refresh accordingly.

10. **Observability remains artifact-first even when live streaming exists.**
   - Live streaming is optional and secondary.
   - Artifact-backed stdout/stderr/diagnostics and checkpoint artifacts remain authoritative for what happened.

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

---

## 5. Canonical data models (consumer-facing)

### 5.1 ArtifactRef (v1)

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

### 5.2 ArtifactMetadata (expanded)

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

### 5.3 SessionProjection (consumer-facing read model)

Returned by session-aware list endpoints.

```json
{
  "task_run_id": "tr_...",
  "session_id": "sess_...",
  "session_epoch": 3,
  "artifacts": ["ArtifactMetadata", "..."],
  "latest_summary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_checkpoint_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_control_event_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." }
}
```

Rules:

- `SessionProjection` is a read convenience, not a second artifact identity model.
- Every artifact returned in a session projection must still include normal execution `links[]`.
- The session projection may span multiple executions within the same task when a session container is reused across steps.

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
- workflow history should only see small summaries, refs, and bounded metadata

### 6.2 Task-scoped session container rule

After adoption of task-scoped session containers, the artifact contract must behave as follows:

- the container may persist across multiple plan steps
- the container may keep native runtime state for efficiency and continuity
- MoonMind must still materialize step inputs, step outputs, logs, diagnostics, summaries, and checkpoints as artifacts
- task and detail UIs must prefer artifact evidence over in-container session assumptions
- the existence of one long-lived session must not remove the requirement for per-step artifact evidence

### 6.3 Step and session durability rule

For each managed step that uses a task-scoped session container, the durable presentation surface should include:

- step-scoped input artifacts such as instructions, plan fragments, skill snapshots, and operator feedback
- step-scoped output artifacts such as primary output, patch, summary, and agent result
- runtime stdout, stderr, and diagnostics artifacts for the producing execution
- at least one session continuity artifact: a step checkpoint, session summary, or both

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

## 7. Endpoint contract (summary)

All endpoints below are server-relative. Authentication behavior is controlled by the app auth mode.

### 7.1 Create an artifact

`POST /api/artifacts`

Request: `CreateArtifactRequest`

- `content_type` (optional)
- `size_bytes` (optional; recommended)
- `sha256` (optional; recommended)
- `retention_class` (optional)
- `link` (optional initial execution link)
- `metadata` (optional JSON)
- `redaction_level` (optional; default `none`)
- `encryption` (optional; default `none`)

Response:

- `artifact_ref`
- `upload` (`mode` plus URL or upload ID, expiry, max size, and required headers)

### 7.2 Upload bytes

`PUT /api/artifacts/{artifact_id}/content`

- Sends the raw request body as artifact bytes.
- If a `Content-Type` header is present, it becomes the artifact `content_type`.

### 7.3 Multipart part presign

`POST /api/artifacts/{artifact_id}/presign-upload-part`

Request:

- `part_number`

Response:

- `part_number`, `url`, `expires_at`, `required_headers`

### 7.4 Complete multipart upload

`POST /api/artifacts/{artifact_id}/complete`

Request:

- `parts[]` (`part_number`, `etag`)

Response:

- `ArtifactRef`

### 7.5 Get metadata

`GET /api/artifacts/{artifact_id}`

Query:

- `include_download` (bool; default `false`)

Response:

- `ArtifactMetadata`

Notes:

- If `include_download=true` and raw access is allowed, the response includes `download_url` and `download_expires_at`.
- Clients should not assume `download_url` is present unless they explicitly requested it.

### 7.6 Presign download

`POST /api/artifacts/{artifact_id}/presign-download`

Response:

- `url`, `expires_at`

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

- `namespace`, `workflow_id`, `run_id`, `link_type`
- optional `label`
- optional provenance: `created_by_activity_type`, `created_by_worker`

Response:

- updated `ArtifactMetadata`

### 7.9 List artifacts for an execution

`GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

Query:

- `link_type` (optional)
- `latest_only` (bool; default `false`)

Response:

- `{ "artifacts": ArtifactMetadata[] }`

Rules:

- `latest_only=true` is guaranteed only when paired with `link_type`; in that case the response contains at most one artifact, the latest for that execution/link pair.
- Without `link_type`, clients must not assume the server collapses results to one artifact per `link_type`.

### 7.10 List artifacts for a task-scoped session

`GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`

Query:

- `session_epoch` (optional)
- `link_type` (optional)
- `latest_only` (bool; default `false`)

Response:

- `SessionProjection`

Rules:

- This endpoint is a server-side projection over execution-linked artifacts.
- It must return only artifacts that still carry ordinary execution `links[]`.
- If `session_epoch` is omitted, the server may return artifacts across all epochs for that session, but it must preserve per-artifact epoch metadata so the UI can render boundaries.
- If `latest_only=true`, the server must define “latest” within the requested session scope and `link_type`; clients must not re-derive it locally.

### 7.11 Pin / unpin

- `POST /api/artifacts/{artifact_id}/pin`
- `DELETE /api/artifacts/{artifact_id}/pin`

Pin request includes optional `reason`.

### 7.12 Delete

`DELETE /api/artifacts/{artifact_id}`

Response:

- updated `ArtifactMetadata` (typically `status=deleted`)

---

## 8. Error contract (stable codes)

Errors are returned as `HTTPException.detail` objects with:

- `code`: stable machine code
- `message`: human-readable string

Common codes:

- `authentication_required` (401)
- `artifact_not_found` (404)
- `artifact_forbidden` (403)
- `artifact_state_error` (409)
- `invalid_artifact_payload` (422)
- `artifact_too_large` (413)
- `session_projection_not_found` (404)
- `session_projection_unavailable` (409)

Client rules:

- Treat `authentication_required` as an auth/session problem, not a retry loop.
- Treat `artifact_state_error` as recoverable. Refresh UI and show uploading/processing state.
- Treat `artifact_forbidden` as “do not retry raw access.” Hide or disable raw-download affordances.
- Treat `session_projection_unavailable` as a projection/read-model issue. Fall back to execution-scoped artifact listing instead of leaving the UI blank.

---

## 9. Presentation rules (UI contract)

### 9.1 Primary UI surfaces

Execution detail or Temporal-backed task detail should expose:

- an **Execution Artifacts** panel for the current execution
- a **Session Continuity** panel when the task used a task-scoped session container

Common execution-artifact modes:

- all artifacts for the execution
- filtered artifacts by `link_type`
- latest artifact for one known execution/link pair

Default execution list call:

- `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

The `run_id` must be the latest `temporalRunId` from the execution detail response.

Common session-continuity modes:

- all artifacts for the active session
- one session epoch
- latest session summary
- latest step checkpoint
- latest control event

Default session view call:

- `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`

Artifact detail drawer/page should:

- fetch `GET /api/artifacts/{artifact_id}`
- render metadata plus the default content target
- expose raw download only when `raw_access_allowed=true`
- show session badges when `session_context` is present
- show the source execution reference for session-projected artifacts

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

Preview/raw-access behavior applies equally to provider snapshots, managed-runtime diagnostics, session summaries, session checkpoints, and session control artifacts.

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

Hide by default or keep debug-only:

- `storage_backend`
- `storage_key`
- `created_by_principal` unless ownership/debug context requires it
- `encryption` unless needed for compliance/operator UI
- provenance fields such as `created_by_activity_type` and `created_by_worker`
- raw recovery internals that expose local runtime layout or secret-bearing paths

### 9.5 “Latest output” semantics

The canonical latest-output contract is server-defined scope plus `link_type`, not a client-derived grouping heuristic.

Rules:

- if the UI wants “latest output” for a known artifact kind within one execution, it should specify `link_type`
- if the UI wants “latest output” within one task-scoped session, it should use the session endpoint with `link_type`
- if the UI wants cross-link aggregation later, add an explicit server mode instead of collapsing client-side
- this applies equally to `output.primary`, `output.provider_snapshot`, `runtime.diagnostics`, `session.summary`, `session.step_checkpoint`, and all other link types

### 9.6 Session continuity presentation

When task-scoped session containers are in use, the UI must present continuity without making the container itself the source of truth.

Rules:

- the Session Continuity panel must be artifact-backed
- the panel must show explicit epoch boundaries when `session_epoch` changes
- `/clear`, hard reset, or recovery rebuild must appear as an explicit control or reset artifact, not as an invisible gap
- the panel must show the latest session summary and latest checkpoint when available
- the panel must preserve links back to the source execution that produced each artifact
- the panel must not imply that all artifacts came from one execution simply because they share one `session_id`
- if the session projection is unavailable, the UI should fall back to execution-scoped artifacts plus standard task step context

### 9.7 Logs and observability

Managed-run log viewing remains governed by the observability APIs.

Rules:

- task detail log panels should use the observability APIs for live tails and artifact-backed log retrieval
- artifact panels may still show `runtime.stdout`, `runtime.stderr`, `runtime.merged_logs`, and `runtime.diagnostics` when those artifacts exist
- the artifact panel must not attempt to simulate a terminal session
- ended runs should always remain viewable from artifacts even if no live connection exists

---

## 10. Rendering rules (by `content_type` + hints)

### 10.1 Renderer selection priority

1. `metadata.render_hint`
2. `content_type`
3. filename-like hint in `metadata.name`
4. fallback: binary / download-only

Unknown `render_hint` values must be ignored, not treated as fatal.

### 10.2 Supported renderers (recommended baseline)

#### Text viewer

Content types:

- `text/plain`
- `text/markdown`
- `text/x-diff`
- `application/x-ndjson`

UI behavior:

- inline preview up to `MAX_INLINE_BYTES` (for example 256 KB)
- if larger, show an explicit open/download action rather than eagerly loading the full body
- show diff formatting when `content_type` or `render_hint` indicates patch/diff content
- when rendering `application/x-ndjson`, preserve line boundaries and avoid silently coalescing records

#### JSON viewer

Content types:

- `application/json`

UI behavior:

- pretty-print JSON for preview
- if parsing fails, fall back to text viewer

#### Image viewer

Content types:

- `image/png`
- `image/jpeg`
- `image/webp`
- `image/gif`

UI behavior:

- inline preview, subject to redaction/default-read rules

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
- `session.recovery_bundle` is usually debug/operator oriented and may be restricted
- clients must not assume that `session.transcript` exists; session continuity must still work from summary/checkpoint/control artifacts
- session artifacts should carry `session_context` so the UI can group them by session and epoch without hiding the source execution

### 11.4 Debug artifact classes

| `link_type` | Typical `content_type` | Recommended renderer | Notes |
| --- | --- | --- | --- |
| `debug.trace` | `application/json`, `text/plain` | Auto-detect | Execution trace |
| `debug.skill_resolution_trace` | `application/json` | JSON viewer | Skill resolution debug info |

Debug artifacts should be hidden by default in normal user views and shown in operator/debug panels.

---

## 12. Upload UX contract (UI behavior)

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
- session summaries, checkpoints, and control artifacts may be worker-created rather than browser-uploaded, but they must still obey the same metadata and linkage rules

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
- `debug.skill_resolution_trace`
- `debug.trace`

Rules:

- `link_type` should stay small, bounded, and stable
- use `link_type` for machine meaning and `label` for human-facing nuance
- clients must tolerate unknown `link_type` values and display them generically

### 13.3 `label` usage

- optional, human-friendly
- can carry versioning or presentation nuance, for example `Plan v2`, `Final output`, or `Epoch 3 reset`
- must not replace `link_type` as the machine key

### 13.4 Session context rules

When artifacts participate in a task-scoped session view:

- `session_context.session_id` should be stable for the lifetime of the task-scoped session
- `session_context.session_epoch` must increase when a reset or recovery rebuild establishes a new logical continuity interval
- `session_context.source_execution_ref` must point to the producing execution
- session fields must support grouping and auditing; they must not leak local runtime secrets or filesystem layout

---

## 14. Examples

### 14.1 List the latest diagnostics artifact for an execution

`GET /api/executions/moonmind/wf-1/run-1/artifacts?link_type=runtime.diagnostics&latest_only=true`

This returns the latest diagnostics artifact for one execution.

### 14.2 List the current session projection for a task-scoped session

`GET /api/task-runs/tr_1/artifact-sessions/sess_123`

Example shape:

```json
{
  "task_run_id": "tr_1",
  "session_id": "sess_123",
  "session_epoch": 2,
  "latest_summary_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_summary"
  },
  "latest_checkpoint_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_checkpoint"
  },
  "latest_control_event_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_clear"
  },
  "artifacts": [
    {
      "artifact_id": "art_summary",
      "content_type": "application/json",
      "status": "complete",
      "metadata": {
        "title": "Session summary",
        "render_hint": "json"
      },
      "session_context": {
        "session_id": "sess_123",
        "session_epoch": 2,
        "step_id": "step_4",
        "step_index": 4,
        "turn_index": 11,
        "checkpoint_kind": "session_summary",
        "control_event_kind": "none",
        "source_execution_ref": {
          "namespace": "moonmind",
          "workflow_id": "wf-step-4",
          "run_id": "run-step-4"
        }
      },
      "links": [
        {
          "namespace": "moonmind",
          "workflow_id": "wf-step-4",
          "run_id": "run-step-4",
          "link_type": "session.summary"
        }
      ]
    }
  ]
}
```

The UI should:

- show the session and epoch badges
- show the source execution link
- render the session summary using normal artifact rules
- avoid implying that the session summary replaces step-local artifacts

### 14.3 Show a `/clear` boundary

`GET /api/task-runs/tr_1/artifact-sessions/sess_123?link_type=session.control_event`

If the latest control artifact has `control_event_kind=clear`, the UI should:

- render a visible epoch boundary in the session timeline
- show the event as a reset, not as ordinary instructions
- show post-clear artifacts under the new epoch

### 14.4 Fetch metadata with download included

`GET /api/artifacts/art_...?include_download=true`

Behavior:

- if allowed, the response contains `download_url`
- if not allowed, `download_url` is null or omitted and the UI should not show a raw-download CTA

---

## 15. Open questions / future extensions

- Should the API later add step-scoped task projections that group execution artifacts by plan node as well as by session?
- Should MoonMind standardize `artifact_type` enums for dashboard grouping, or keep them producer-defined?
- Should proxy downloads eventually prefer sanitized `metadata.name` over `artifact_id` for filenames?
- Do we want content-range or partial text fetch support for very large logs and transcripts?
- Should session projections expose an explicit timeline cursor for very long tasks?
- Should the server expose a “latest continuity bundle” endpoint that resolves latest summary + latest checkpoint + latest reset boundary in one response?
