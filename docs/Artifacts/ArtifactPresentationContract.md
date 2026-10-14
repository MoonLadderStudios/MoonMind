# Artifact Presentation Contract

Owners: MoonMind Platform + UI

## 1. Purpose

This document describes how API and UI clients display and interact with artifact-backed outputs across execution, workflow, session, report, and observability surfaces: how metadata maps to the UI, the conventions for `link_type`, default reads, downloads, previews, and rendering hints, and how presentation works for runtime results, reports, and **workflow-scoped session containers**.

It sits downstream of the artifact storage, execution identity, managed-runtime, report, and observability designs and does not redefine them.

### 1.1 Related docs and ownership boundaries

- `docs/Temporal/WorkflowArtifactSystemDesign.md` — storage backend, artifact identity, lifecycle, linkage, authorization, and activity-boundary design.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — execution identity, list/detail query semantics, and workflow-surface mapping.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` — canonical runtime contracts (`AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`).
- `docs/ManagedAgents/CodexCliManagedSessions.md` — Codex workflow-scoped session identity, control actions, and clear/reset semantics.
- `docs/Observability/LiveLogs.md` — live-log and observability APIs, artifact-backed tails, and diagnostics presentation for managed runs.
- `docs/Artifacts/ReportArtifacts.md` — report artifact classes, report bundles, report metadata, evidence separation, and report-first UI.
- `docs/UI/WorkflowConsoleArchitecture.md` — dashboard route wiring, source resolution, and mixed-source UI integration.

This document covers the **generic consumer-facing presentation layer** on top of those designs.

---

## 2. Scope

In scope: listing artifacts for one execution, metadata and its UI mapping, preview vs raw download, viewer selection, error codes, runtime result integration, and presentation of provider snapshots, diagnostics, reports, and session continuity artifacts.

Out of scope: storage internals, the full authorization model, lifecycle cleanup, report bundle assembly and schemas, execution query semantics, container orchestration, terminal embedding for logs, and exposing runtime home directories or auth volumes as artifacts.

---

## 3. Consumer invariants

1. **Artifacts are execution-centric.** Artifacts attach durably to a Temporal execution identified by `(namespace, workflow_id, run_id)`. Workflow-, session-, and report-oriented views are projections over execution-linked artifacts, not a second durable identity.

2. **Workflow-scoped session containers are continuity caches, not durable truth.** A managed-runtime container may survive across several plan steps, but anything needed for recovery, audit, presentation, or rerun is materialized as artifacts or bounded workflow metadata. Container memory, container-local session databases, and runtime home directories are not a canonical source because they do not survive the container.

3. **Step boundaries stay first-class when one session spans many steps.** Session reuse does not collapse multiple plan steps into one artifact blob; each step still produces step-scoped input, output, and diagnostic evidence linked to the producing execution.

4. **Session resets create explicit epoch boundaries.** `/clear`, hard reset, recovery rebuild, or an equivalent intervention starts a new logical `session_epoch`, and the UI shows that boundary rather than implying one uninterrupted conversation.

5. **`default_read_ref` is authoritative for the default read target.** Clients do not override the server's choice based on `redaction_level`. An `ArtifactRef` is an identifier, not an access grant or URL.

6. **"Latest" is server-defined.** Clients use the server's query parameters and response shape instead of sorting by `created_at` in the browser, so that "latest output" for runtime, provider, session, and report artifacts means the same thing everywhere.

7. **Metadata is display-safe.** `metadata` and session- or report-specific fields carry bounded hints and domain semantics only; they do not contain secrets, auth headers, cookies, session tokens, presigned URLs, private keys, or absolute local filesystem paths.

8. **Clients degrade gracefully.** Unknown `link_type`, `artifact_type`, `render_hint`, `content_type`, `checkpoint_kind`, `report_type`, or group keys fall back to generic presentation rather than failing.

9. **Detail pages use the latest execution identity.** The `run_id` used for artifact listing comes from execution detail, not cached state, because reruns and Continue-As-New change it.

10. **Observability is artifact-first.** Live streaming is optional and secondary; artifact-backed stdout/stderr/diagnostics, event journals, and checkpoints are the record of what happened.

11. **Reports are artifact families, not a separate storage system.** Report artifacts follow the same metadata, preview, raw-download, retention, and renderer rules as other artifacts; report-first surfaces are convenience views.

---

## 4. Glossary

- **Preview artifact / raw artifact**: A redacted or reduced representation generated for safer presentation, and the original (possibly restricted) bytes.
- **Workflow-scoped session container**: A managed-runtime container or long-lived process reused across multiple steps within one workflow.
- **Session ID / session epoch**: Durable identifier for one workflow-scoped session, and one continuity interval within it. A reset or `/clear` starts a new epoch.
- **Step checkpoint / session summary / session control artifact**: A compact step-boundary record for recovery and review, the latest durable session summary, and a record of an explicit control action (`/clear`, approve/reject, operator message, pause, resume, cancel).
- **Session projection**: Server-produced read model grouping execution-linked artifacts by `session_id` and optional `session_epoch`.
- **Report bundle**: Compact result shape referencing the report, summary, structured data, and evidence artifacts.

---

## 5. Consumer-facing data models

Pydantic models live in `moonmind/schemas/temporal_artifact_models.py`; the router is `api_service/api/routers/temporal_artifacts.py`.

### 5.1 ArtifactRef v1

Used in workflow/activity IO and returned by the API.

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

`artifact_id` is the durable handle. The ref carries no URL and is not a download token. Compact projections may use a reduced ref with only `artifact_ref_v` and `artifact_id` when size and security matter more than inline metadata.

### 5.2 ArtifactMetadata

Returned by metadata and list endpoints. Presentation-relevant fields:

- identity and content: `artifact_id`, `created_at`, `created_by_principal`, `content_type`, `size_bytes`, `sha256`
- lifecycle: `status` (`pending_upload | complete | failed | deleted`), `retention_class` (`ephemeral | standard | long | pinned`), `expires_at`, `pinned`
- access: `redaction_level` (`none | preview_only | restricted`), `raw_access_allowed`, `artifact_ref`, `preview_artifact_ref`, `default_read_ref`, and optional `download_url`/`download_expires_at` when explicitly requested and allowed
- `metadata`: bounded JSON for UI hints and domain semantics
- `links[]`: execution links with `namespace`, `workflow_id`, `run_id`, `link_type`, and optional `label`
- debug-oriented: `storage_backend`, `storage_key`, `encryption`

Optional `session_context` carries `session_id`, `session_epoch`, `step_id`, `step_index`, `turn_index`, `checkpoint_kind` (`step_checkpoint | session_summary | recovery_bundle | reset_boundary | none`), `control_event_kind` (`operator_message | clear | approve | reject | pause | resume | cancel | none`), and `source_execution_ref`.

Client behavior:

- `default_read_ref` is the default display target. When it names a different artifact than `artifact_id`, the client resolves it through the normal metadata/download flow.
- When `raw_access_allowed=false` and `preview_artifact_ref=null`, the client shows a pending or unavailable preview state rather than assuming raw bytes are readable.
- `session_context` is used for grouping and badges; `links[]` remains the durable source-execution evidence.
- Report metadata may be displayed when present, but generic clients render a safe fallback without it.

### 5.3 SessionProjection

Returned by session-aware list endpoints.

```json
{
  "agent_run_id": "tr_...",
  "session_id": "sess_...",
  "session_epoch": 3,
  "grouped_artifacts": [
    { "group_key": "runtime", "title": "Runtime", "artifacts": ["ArtifactMetadata", "..."] },
    { "group_key": "continuity", "title": "Continuity", "artifacts": ["ArtifactMetadata", "..."] },
    { "group_key": "control", "title": "Control", "artifacts": ["ArtifactMetadata", "..."] }
  ],
  "latest_summary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_checkpoint_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_control_event_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_reset_boundary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." }
}
```

The projection is a read convenience, not a second identity model: every artifact in it still carries normal execution `links[]`, and it may span multiple executions when a session container is reused across steps. Groups are server-defined; clients render known groups (`runtime`, `continuity`, `control`) and tolerate unknown keys. Missing refs are omitted. The projection does not require a live session container, a live session-controller query, or container-local history, so it remains readable after the session ends.

### 5.4 ReportProjection

Report projection shapes are owned by `docs/Artifacts/ReportArtifacts.md`. Generic clients understand only this baseline:

```json
{
  "has_report": true,
  "latest_report_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "latest_report_summary_ref": { "artifact_ref_v": 1, "artifact_id": "art_..." },
  "report_type": "security_pentest_report",
  "report_status": "final",
  "finding_counts": { "total": 8 },
  "severity_counts": { "critical": 1, "high": 2 }
}
```

Report projections are convenience read models; the underlying artifacts remain individually addressable, and generic artifact UI works without a report projection.

---

## 6. Runtime result integration

### 6.1 Compact runtime results

Agent-runtime activities return compact canonical contracts (`AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`). `AgentRunResult` carries artifact refs for large outputs: `output_refs[]` for primary outputs and `diagnostics_ref` for operational diagnostics. Provider payload dumps, managed-runtime logs, large summaries and diffs, session summaries and checkpoints, and report bodies and evidence all live in artifacts; workflow history sees only small summaries, refs, and bounded metadata, which keeps history small and replay-safe.

### 6.2 Workflow-scoped session containers

A session container may persist across plan steps and keep native runtime state for efficiency. MoonMind still materializes step inputs, outputs, logs, diagnostics, summaries, and checkpoints as artifacts, and detail UIs prefer that evidence over in-container state. For each managed step that uses such a container, the durable surface includes:

- step-scoped inputs (instructions, plan fragments, skill snapshots, operator feedback)
- step-scoped outputs (primary output, patch, summary, agent result)
- runtime stdout, stderr, merged logs, event journals, and diagnostics
- at least one continuity artifact (step checkpoint and/or session summary)
- explicit control or reset artifacts when the operator intervenes or the epoch changes

No user-important state exists only inside the container, because the container is not guaranteed to survive.

### 6.3 Workflow and detail UIs

When an `AgentRunResult` references artifacts, the UI lists artifacts by execution through the standard list endpoint, renders each with the standard presentation rules, and uses session projections only as a grouping and continuity aid. It does not parse raw provider payloads inline or treat container-local session memory as history.

---

## 7. Endpoint summary

All endpoints are server-relative; authentication follows the app auth mode. Request and response models are in `moonmind/schemas/temporal_artifact_models.py`.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/artifacts` | Create an artifact (optional `content_type`, `size_bytes`, `sha256`, `retention_class`, initial `link`, `metadata`, `redaction_level`, `encryption`). Returns `artifact_ref` plus an `upload` descriptor (mode, URL or upload ID, expiry, max size, required headers). |
| `PUT /api/artifacts/{artifact_id}/content` | Upload the raw request body; a `Content-Type` header becomes the artifact `content_type`. |
| `POST /api/artifacts/{artifact_id}/presign-upload-part` | Presign one multipart part (`part_number` → `url`, `expires_at`, `required_headers`). |
| `POST /api/artifacts/{artifact_id}/complete` | Complete a multipart upload from `parts[]` (`part_number`, `etag`); returns `ArtifactRef`. |
| `GET /api/artifacts/{artifact_id}` | Metadata. `include_download=true` adds `download_url`/`download_expires_at` when raw access is allowed; otherwise those fields are absent. |
| `POST /api/artifacts/{artifact_id}/presign-download` | Presigned raw download (`url`, `expires_at`) for raw-download affordances when `raw_access_allowed=true`. |
| `GET /api/artifacts/{artifact_id}/download` | API-proxied download with `Content-Type` from the artifact (or `application/octet-stream`) and `Content-Disposition: attachment; filename="{artifact_id}"`. |
| `POST /api/artifacts/{artifact_id}/links` | Link to an execution (`namespace`, `workflow_id`, `run_id`, `link_type`, optional `label`, optional provenance `created_by_activity_type`/`created_by_worker`); returns updated metadata. |
| `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts` | List artifacts for one execution, optionally filtered by `link_type`, with `latest_only`. Returns `{ "artifacts": [...] }`. |
| `GET /api/agent-runs/{agent_run_id}/artifact-sessions/{session_id}` | `SessionProjection` for a workflow-scoped session. |
| `GET /api/sessions/{session_id}/resources` (and `/resources/files`) | Read-only `SessionResourceList` for a session. |
| `GET /api/sessions/{session_id}/resources/{artifact_id}/content` and `/download` | Content and download for an artifact in that session. |
| `POST` / `DELETE /api/artifacts/{artifact_id}/pin` | Pin (optional `reason`) or unpin. |
| `DELETE /api/artifacts/{artifact_id}` | Delete; returns metadata, typically `status=deleted`. |

Behavior worth knowing:

- An uploaded artifact is not readable until metadata reports `status=complete`.
- The proxy download filename is transport-oriented (the `artifact_id`), so display labels come from `metadata.name` or other UI labels.
- `latest_only=true` returns at most one artifact only when paired with `link_type`; without `link_type`, the server does not collapse results per link type.
- The session projection endpoint reads persisted artifacts and the durable managed-session record, returns only artifacts that still carry execution `links[]`, and defines grouping and latest-ref semantics itself.
- Session resources are read-only projections over authorized artifact refs from the durable managed-session projection. They do not create blob storage, upload flows, or a second resource browser, and the same artifact authorization boundary applies to listing, content, and download. Secret-like metadata keys are dropped from resource summaries. Ended or degraded sessions still return resources from durable artifact state. The content and download aliases serve only artifacts in that session's projection; any other artifact id returns `session_resource_not_found`, even if the artifact exists elsewhere, so the alias cannot be used to reach unrelated artifacts.

---

## 8. Error contract

Errors are `HTTPException.detail` objects with a stable `code` and a human-readable `message`.

| Code | Status | Client behavior |
| --- | --- | --- |
| `authentication_required` | 401 | Auth/session problem; do not retry in a loop. |
| `artifact_not_found` | 404 | Show unavailable. |
| `artifact_forbidden` | 403 | Do not retry raw access; hide or disable raw-download affordances. |
| `artifact_state_error` | 409 | Recoverable; refresh and show uploading/processing state. |
| `invalid_artifact_payload` | 422 | Request problem. |
| `artifact_too_large` | 413 | Request exceeds size limits. |
| `session_projection_not_found` | 404 | No session projection for this run; this is not proof that the execution has no artifacts, so fall back to execution-scoped listing. |
| `session_resource_not_found` | 404 | Artifact is not part of the requested session. |

---

## 9. UI presentation

### 9.1 Primary surfaces

Execution or workflow detail exposes:

- an **Execution Artifacts** panel for the current execution (all artifacts, filtered by `link_type`, or the latest for one execution/link pair), listed with `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts` using the latest `temporalRunId` from execution detail
- a **Session Continuity** panel when the workflow used a workflow-scoped session container, backed by `GET /api/agent-runs/{agent_run_id}/artifact-sessions/{session_id}`
- a **Report** panel or card when a canonical report artifact exists
- observability/log panels that read live streams or artifact-backed histories

`GET /api/sessions/{session_id}/resources` may back compact evidence cards or links, but it does not replace artifact detail, execution artifacts, diagnostics, or log surfaces.

The artifact detail drawer/page fetches `GET /api/artifacts/{artifact_id}`, renders metadata plus the default content target, offers raw download only when `raw_access_allowed=true`, shows session and report badges when that metadata is present, and shows the source execution for session- or report-projected artifacts.

### 9.2 Default read, download, and redaction

Default rendering resolves `default_read_ref.artifact_id`, fetches its metadata if needed, and renders it with the renderer-selection rules in section 10. Raw download calls `presign-download` and navigates to the URL only when `raw_access_allowed=true`; otherwise the UI shows a restricted message. `presign-download` is not used for inline preview by default, because that would bypass the preview/redaction choice.

- `raw_access_allowed=false`: no raw download. With a `preview_artifact_ref`, `default_read_ref` is the preview target; without one, the UI shows "Preview not yet available" with refresh.
- `redaction_level=restricted` with raw access allowed: the UI may de-emphasize raw download or note the restriction, and still uses `default_read_ref` for display.
- `redaction_level=preview_only`: preview is the intended steady-state surface, not an error.

These rules apply equally to every artifact class.

### 9.3 Metadata visibility

Shown to most users: created time, size, content type, checksum when useful, `link_type` and `label`, retention and expiry, pinned and redaction status, session badges from `session_context`, and safe report badges (type, scope, title, producer, bounded counts).

Debug-only: storage backend and key, `created_by_principal`, `encryption`, provenance fields, recovery internals that expose local layout or secret-bearing paths, raw provider payload details, and sensitive report evidence internals.

### 9.4 "Latest output"

"Latest" is server-defined scope plus `link_type`, not a client grouping heuristic. Within one execution the UI specifies `link_type`; within a workflow-scoped session it uses the session endpoint; for the latest report it uses the report projection or `link_type=report.primary`. Cross-link aggregation would be a new explicit server mode rather than client-side collapsing. This applies to every link type.

### 9.5 Session continuity

The Session Continuity panel is artifact-backed. It shows explicit epoch boundaries when `session_epoch` changes; `/clear`, hard reset, and recovery rebuild appear as control or reset artifacts rather than invisible gaps. It shows the latest summary, checkpoint, control event, and reset boundary when available, renders server-defined groups without hiding unknown ones, and links each artifact back to its source execution. Sharing a `session_id` does not imply all artifacts came from one execution. When the projection is unavailable, the UI falls back to execution-scoped artifacts plus workflow step context.

### 9.6 Logs and observability

Managed-run log viewing uses the observability APIs for live tails and artifact-backed retrieval. Artifact panels may still show `runtime.stdout`, `runtime.stderr`, `runtime.merged_logs`, `runtime.diagnostics`, and event-journal artifacts, but do not simulate a terminal. Ended runs stay viewable from artifacts without a live connection, and live streams and artifact-backed logs converge on the same durable evidence where possible.

### 9.7 Reports

Report-first presentation is owned by `docs/Artifacts/ReportArtifacts.md`. When an execution has `report.primary`, the dashboard may show a report-first card. Report artifacts still appear in generic lists unless intentionally filtered, evidence stays separately addressable, generic rendering uses the same default-read, redaction, raw-download, and renderer rules, and report projections do not hide artifact identity or execution linkage.

---

## 10. Rendering by `content_type` and hints

### 10.1 Renderer selection

Priority: `metadata.render_hint`, then `content_type`, then a filename-like hint in `metadata.name`, then binary/download-only. Unknown `render_hint` values are ignored.

### 10.2 Baseline renderers

| Renderer | Content types | Behavior |
| --- | --- | --- |
| Text | `text/plain`, `text/markdown`, `text/x-diff`, `application/x-ndjson` | Inline preview up to `MAX_INLINE_BYTES` (for example 256 KB), then an explicit open/download action instead of eager loading. Diff formatting when content type or hint indicates a patch. NDJSON keeps line boundaries. |
| JSON | `application/json` | Pretty-printed; falls back to text if parsing fails; very large JSON gets a bounded preview plus open/download. |
| Image | `image/png`, `image/jpeg`, `image/webp`, `image/gif` | Inline, subject to redaction/default-read rules; dimensions when available; not inlined when restricted with no preview. |
| PDF / document export | `application/pdf`, `text/html`, other exports | Metadata plus download/open unless a deliberate viewer exists; untrusted HTML is never embedded without sanitization; uses `default_read_ref` if a safe preview exists. |
| Binary / unknown | everything else | Metadata, size, and download if allowed; no inline rendering. |

### 10.3 Standard metadata hints

Reserved keys under `ArtifactMetadata.metadata`:

- `name`: display filename, basename only, with no path separators so it does not leak workspace layout
- `title`, `description`: display title and short description
- `source`: bounded origin such as `worker:codex`, `worker:runner`, `ui:upload`
- `artifact_type`: stable domain classifier
- `render_hint`: `text | json | diff | image | binary`
- `preview_of`: source artifact ID for preview artifacts
- `policy`: preview/redaction policy name

Producer-specific keys live under a producer-owned nested object to avoid collisions; `metadata.session` may elaborate on session details but does not duplicate or conflict with `session_context`. Without these hints the UI still works from `content_type`, `links`, and `session_context`.

Report metadata keys (`report_type`, `report_scope`, `sensitivity`, `producer`, `subject`, `finding_counts`, `severity_counts`, `is_final_report`) are defined in `docs/Artifacts/ReportArtifacts.md`.

---

## 11. Artifact classes

`link_type` values are lowercase, dot-delimited, and stable. Use `link_type` for machine meaning and the optional `label` (for example `Plan v2`, `Epoch 3 reset`, `Executive summary`) for human nuance. Clients display unknown link types generically. Report link types are ordinary link types, not a separate storage namespace.

### 11.1 Inputs and outputs

| `link_type` | Typical `content_type` | Renderer | Notes |
| --- | --- | --- | --- |
| `input.instructions` | `text/plain`, `text/markdown` | Text | Instructions for one step |
| `input.manifest` | `application/json` | JSON | Manifest definition |
| `input.plan` | `application/json` | JSON | Pre-computed plan |
| `input.skill_snapshot` | `application/json` | JSON | Resolved skill set |
| `input.prompt_index` | `application/json` | JSON | Prompt index for runtime |
| `output.primary` | varies | Auto-detect | Primary execution output |
| `output.patch` | `text/x-diff` | Diff | Code patch |
| `output.logs` | `text/plain` | Text | Logs outside managed-run observability |
| `output.summary` | `text/plain`, `text/markdown` | Text | Human-readable summary |
| `output.agent_result` | `application/json` | JSON | Canonical agent result |

### 11.2 Provider and runtime

| `link_type` | Typical `content_type` | Renderer | Notes |
| --- | --- | --- | --- |
| `output.provider_snapshot` | `application/json` | JSON | Raw provider result; may be restricted |
| `runtime.stdout` / `runtime.stderr` | `text/plain` | Text | Managed runtime streams |
| `runtime.merged_logs` | `text/plain` | Text | Combined stdout/stderr |
| `runtime.diagnostics` | `application/json`, `text/plain` | Auto-detect | Operational diagnostics; may be restricted |
| `runtime.skill_materialization` | `application/json` | JSON | Runtime delivery bundle |

Provider snapshots and diagnostics may hold sensitive operational detail, so they are not assumed safe for unrestricted inline display. A live log panel does not make durable runtime artifacts redundant.

### 11.3 Session continuity

| `link_type` | Typical `content_type` | Renderer | Notes |
| --- | --- | --- | --- |
| `session.summary` | `application/json`, `text/markdown` | Auto-detect | Latest durable session summary |
| `session.step_checkpoint` | `application/json` | JSON | Step-boundary checkpoint for recovery and review |
| `session.control_event` | `application/json` | JSON | `/clear`, operator message, approval, rejection, pause, resume, cancel |
| `session.transcript` | `application/x-ndjson`, `text/plain` | Text | Optional ordered session event stream |
| `session.recovery_bundle` | `application/json` | JSON | Sanitized recovery bundle; operator/debug-oriented, may be restricted |
| `session.reset_boundary` | `application/json`, `text/plain` | Auto-detect | Epoch boundary after `/clear`, reset, or rebuild |

Summaries and step checkpoints are the primary continuity artifacts for normal users. Control events are visible enough that resets and interventions are not hidden. Continuity works without `session.transcript`, which may not exist.

Session artifacts carry `session_context` so the UI can group by session and epoch without hiding the source execution. `session_id` is stable for the session's lifetime; `session_epoch` increases on clear, reset, or rebuild; `step_id`/`step_index` identify the producing or consuming step; `turn_index` may identify a turn; and `source_execution_ref` preserves the producing execution. Session metadata does not include container paths, auth volume paths, cookies, tokens, or provider credentials.

### 11.4 Reports

| `link_type` | Typical `content_type` | Renderer | Notes |
| --- | --- | --- | --- |
| `report.primary` | `text/markdown`, `text/plain`, `application/pdf`, `text/html` | Auto-detect | Canonical human-facing report |
| `report.summary` | `text/markdown`, `text/plain`, `application/json` | Auto-detect | Executive summary |
| `report.structured` | `application/json` | JSON | Machine-readable findings |
| `report.evidence` | varies | Auto-detect | Screenshots, excerpts, command results, captures |
| `report.appendix` | varies | Auto-detect | Extended detail |
| `report.findings_index` | `application/json` | JSON | Findings index for grouping/filtering |
| `report.export` | `application/pdf`, `text/html`, varies | Download or auto-detect | Rendered export |

`report.*` marks intentional report deliverables, `output.*` generic outputs, and `runtime.*`/`debug.*` operational evidence. Report metadata stays bounded; report bodies, findings, screenshots, and evidence blobs are not stored inline in metadata. Evidence stays separately addressable when that improves auditability or reuse. PDFs stay download-only unless a viewer is added, and untrusted HTML exports are not injected into the dashboard unsanitized. The UI does not assume every `output.primary` is a report, and report-first surfaces fall back to generic lists when no report exists.

### 11.5 Debug

| `link_type` | Typical `content_type` | Renderer | Notes |
| --- | --- | --- | --- |
| `debug.trace` | `application/json`, `text/plain` | Auto-detect | Execution trace |
| `debug.skill_resolution_trace` | `application/json` | JSON | Skill resolution debug info |

Debug artifacts are hidden in normal views and shown in operator/debug panels.

### 11.6 Saved-result presentation in Workflow Detail

Workflow Detail shows one compact Saved Results section on the Artifacts tab, bound to the server's selected run/result and its committed artifact references from the run artifact listing. It reuses the existing result/artifact projection, the single continuation owner, and the publication-only publisher path rather than adding a second result store or recovery wizard. The frontend lives in `frontend/src/entrypoints/saved-results.ts` and `saved-results-section.tsx`.

- **What counts as saved.** Result-linked artifacts only: `output.*` except `output.logs`, `report.*`, repository/patch/checkpoint links, or `result`. Report-only and non-Git output are useful results.
- **Saved-work manifests.** A committed manifest (`application/vnd.moonmind.saved-work-manifest+json`) is one saved unit whose named parts (snapshot archive, delta, history, file manifest, index patch) each show their own completeness and download. Format claims, exclusions, and limitations come from the server-projected `saved_work_summary` listing metadata; the browser does not parse manifest bytes, and a manifest without a summary shows "format details unavailable".
- **Completeness.** Only a server `COMPLETE` status bound to digest plus size counts as complete; exit codes, local paths, or permissive defaults do not. A required format without a `self_contained`/`requires_dependencies` claim, or with a missing, incomplete, or expired part, makes the unit incomplete, and summary metadata never upgrades it.
- **Distinct outcomes.** Compute, saving, requested publication, and cleanup are reported separately, so a failed run can still show committed saved work and a failed publication can still show a successful save. Publication status comes from `auxiliaryOutcomes.gitPublication`; cleanup reports the recorded `hostCleanup`/`providerProfileRelease`/`janitorRequired` outcome, or unknown. Missing projection data shows a pending/unavailable state; the UI never restarts compute or invents a status.
- **Preview and download.** Previews use the safe-preview path and do not authorize raw restore or publication. Download appears only when `raw_access_allowed === true`. Expired entries (`expires_at` passed or `DELETED`) show no Preview/Download, and hostile generated content never triggers credentialed fetches or workflow controls.
- **Continue working.** Sends operator-authored instructions through `continueInNewWorkflow` (`POST /executions/{workflow_id}/continue`), the same owner as the terminal Chat actions. It creates a fresh admitted execution rather than reviving a session or lease, is available in every terminal state including `no_commit`, and carries only complete saved outputs the captured evidence authorizes.
- **Publish Saved Work.** Offered for a complete, unexpired saved unit with raw access to its manifest and required snapshot/delta closure, when the action projection reports `canPublishSavedWork`. When that capability is off, `disabledReasons.canPublishSavedWork` carries the route's admission reason (for example `publication_recovery_disabled`, `publication_mode_not_allowed`, `publication_recovery_not_in_canary`, or `temporal_submit_disabled`), which the section shows once while keeping Preview, Download, and Continue available; an unreported capability counts as unavailable. `actionEvidence.publishSavedWork` limits the form to its `allowedModes` and any `canaryRepositories`. The form posts `savedWorkRef`, `sourceRunId`, destination repository, objective, branches, strategy, and optional PR title to `POST /executions/{workflow_id}/retry-publication` (see [Publish Saved Work](../Workflows/WorkflowPublishing.md#publish-saved-work)), so no model reruns. It is the only saved-work publication form and is independent of `canRetryPublication`, which gates only body-less recovery of a failed requested publication from the action menu. The server decides admission, and refusals such as `publication_retry_not_admitted` are shown as returned. An accepted publication is followed through `GET /executions/{workflow_id}` until it closes; following never starts, retries, or cancels work.
- **Request binding.** Requests and state are bound to the selected `(workflow_id, run_id)` and the latest request per action, so late responses cannot retarget a historical selection. A double click dispatches once; transport failures and timeouts after submission are reported as possibly accepted. The same continuation intent maps to the same idempotency key and the same publication request to the same server operation, including after reload, and leaving a form is not cancellation.
- **Access.** Cache keys follow the single-user model with no human-user partitions. A `401`/`403` refreshes the protected listing so raw-access affordances follow current authorization.

### 11.7 Remediation evidence

`remediation.context`, approval request/decision, action request/result, verification, audit, target annotation, decision log, and lifecycle summary artifacts keep durable links and remain the deep evidence. Workflow Detail projects only bounded, redacted JSON and metadata: artifact class, status, freshness/expiry, boundedness, and safe lifecycle fields. Per-class availability and degradation come from the canonical context artifact.

Action delivery, repair verification, and prevention verification are reported separately and are not reconstructed from rendered logs, chat text, or assistant prose, because those are not authoritative. When a compact projection is unavailable, the durable artifact link stays visible and the UI reports degradation instead of inventing state.

---

## 12. Uploads

Simple upload: `POST /api/artifacts`; upload bytes to `upload.upload_url` with the required headers when `upload.mode=single_put` (or `PUT /api/artifacts/{artifact_id}/content` in local-dev mode); then refresh metadata until `status=complete`. Multipart upload: `POST /api/artifacts` returns `upload.mode=multipart` and `upload.upload_id`; presign and upload each part; call `complete` with the parts list; refresh until complete.

Worker- and workflow-created artifacts (session continuity, control, and report artifacts) follow the same metadata and linkage rules. Live output may be spooled and persisted later, but the final presentation is artifact-first.

---

## 13. Producers

**Workflows** pass only `ArtifactRef`s, compact summaries, bounded metadata, server-defined selectors, canonical runtime results, and compact report bundle refs. Large bodies, provider payloads, long logs, binary evidence, presigned URLs, secrets, and container-local state stay out because workflow history is size-limited and durable.

**Activities and workers** perform the side effects: writing bytes, finalizing uploads, generating previews, linking artifacts, publishing runtime logs, diagnostics, session continuity artifacts, and report artifacts, and returning compact refs to workflow code. Managed runtime producers sanitize metadata and logs before exposing them and do not rely on container-local state. Report producers follow `docs/Artifacts/ReportArtifacts.md` and keep curated report content separate from raw logs.

Older producers may lack `session_context`, report metadata, previews, report link types, or session projections. Clients still render those artifacts generically from `artifact_id`, `content_type`, `size_bytes`, `links[]`, `metadata`, `raw_access_allowed`, and `default_read_ref`.

---

## 14. Security and access

Access is governed by the artifact system and execution authorization. Clients request metadata before raw access, honor `raw_access_allowed`, use `default_read_ref` for default rendering, avoid retry loops on forbidden access, and keep artifact IDs, labels, and metadata out of unrelated surfaces.

Potentially sensitive classes include `output.provider_snapshot`, `runtime.diagnostics`, `runtime.merged_logs`, `session.recovery_bundle`, `report.structured`, `report.evidence`, `report.export`, `debug.trace`, and `debug.skill_resolution_trace`. The list is not exhaustive; clients follow metadata and access-policy fields rather than assuming any class is safe.

Presigned URLs are access grants, not identity. They are not stored in workflow history, metadata, or durable report bodies, are requested only when the user intentionally opens or downloads raw content, and are not copied into logs or diagnostics.

Metadata stays bounded and display-safe (see invariant 7): beyond credentials and URLs, it also excludes raw payload or report bodies, container auth volume paths, and large nested structures.

---

## 15. Retention and pinning

Retention semantics are owned by the artifact system. The UI shows retention class and expiry where useful, shows pinned state prominently on important outputs and reports, does not imply permanence unless an artifact is pinned or under long retention, and offers pin/unpin where authorized. When the API returns tombstones, deleted artifacts render as unavailable rather than silently disappearing from historical context.
