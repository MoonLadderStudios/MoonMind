# Artifact Presentation Contract
Status: Draft
Owners: MoonMind Platform + UI
Last updated: 2026-03-06

## 1. Purpose

This document defines the API and UI consumption contract for displaying and interacting with Temporal artifacts in MoonMind.

It focuses on:
- how UI clients present artifacts in execution/task detail flows
- how API clients interpret artifact metadata fields
- stable conventions for `link_type`, default reads, downloads, previews, and rendering hints

This document is intentionally downstream of the broader Temporal architecture. It does not redefine storage internals, artifact lifecycle implementation, or execution query semantics.

### 1.1 Related docs and ownership boundaries

- `docs/Temporal/WorkflowArtifactSystemDesign.md`
  - Owns storage backend, artifact identity, lifecycle, linkage, and authorization design.
- `docs/Temporal/VisibilityAndUiQueryModel.md`
  - Owns execution identity, list/detail query semantics, and task compatibility rules.
- `docs/Ui/TemporalDashboardIntegration.md`
  - Owns dashboard route wiring, source resolution, and mixed-source UI integration.

This document owns the consumer-facing artifact contract layered on top of those decisions.

---

## 2. Scope / Non-goals

### In scope
- artifact listing for one execution: `namespace`, `workflow_id`, `run_id`
- artifact metadata and its UI mapping
- preview vs raw download behavior
- standard viewer rules by `content_type` and metadata hints
- stable error codes and client behavior for common failure states

### Out of scope
- artifact storage backend internals
- full authorization model design
- artifact lifecycle cleanup workflows
- Temporal Web UI integration
- execution list/query/count semantics

---

## 3. Consumer invariants

1. Artifacts are execution-centric.
   - Artifacts attach to a Temporal execution identified by `(namespace, workflow_id, run_id)`.
   - Task-oriented compatibility surfaces may still say "task", but they must not invent a second durable artifact identity.

2. `default_read_ref` is authoritative for the default read target.
   - Clients must not override the server's choice based only on `redaction_level`.
   - `ArtifactRef` is an identifier, not an access grant or URL.

3. Clients must not derive "latest" semantics locally.
   - Use the server's query parameters and response shape.
   - Do not sort by `created_at` in the browser and call that canonical "latest output."

4. Artifact metadata must be safe for control-plane display.
   - `metadata` is for bounded, display-safe hints and domain semantics.
   - It must not contain secrets, auth headers, cookies, session IDs, presigned URLs, private keys, or absolute local filesystem paths.

5. Clients must degrade gracefully.
   - Unknown `link_type`, `artifact_type`, `render_hint`, or `content_type` values must fall back to a generic artifact presentation, not a hard failure.

---

## 4. Glossary

- **Artifact**: Immutable blob stored outside Temporal history, plus metadata and linkage.
- **ArtifactRef**: Small pointer that workflows/activities can pass around safely.
- **ExecutionRef**: `(namespace, workflow_id, run_id)` identifying one Temporal execution.
- **Link**: Join row connecting an artifact to an execution with semantic `link_type`.
- **Preview artifact**: Redacted or reduced representation generated for safer presentation.
- **Raw artifact**: The original bytes, which may be restricted.

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

Returned but debug-oriented fields:
- `storage_backend`
- `storage_key`
- `encryption`

Client rules:
- Treat `default_read_ref` as the default display target when a display target is available.
- If `raw_access_allowed=false` and `preview_artifact_ref=null`, clients must show a pending/unavailable preview state rather than assume the raw artifact is readable.
- If `default_read_ref.artifact_id != artifact_id`, the client should resolve that referenced artifact through the normal artifact metadata/download flow.

---

## 6. Endpoint contract (summary)

All endpoints below are server-relative. Authentication behavior is controlled by the app auth mode.

### 6.1 Create an artifact

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

### 6.2 Upload bytes

`PUT /api/artifacts/{artifact_id}/content`

- Sends the raw request body as artifact bytes.
- If a `Content-Type` header is present, it becomes the artifact `content_type`.

### 6.3 Multipart part presign

`POST /api/artifacts/{artifact_id}/presign-upload-part`

Request:
- `part_number`

Response:
- `part_number`, `url`, `expires_at`, `required_headers`

### 6.4 Complete multipart upload

`POST /api/artifacts/{artifact_id}/complete`

Request:
- `parts[]` (`part_number`, `etag`)

Response:
- `ArtifactRef`

### 6.5 Get metadata

`GET /api/artifacts/{artifact_id}`

Query:
- `include_download` (bool; default `false`)

Response:
- `ArtifactMetadata`

Notes:
- If `include_download=true` and raw access is allowed, the response includes `download_url` and `download_expires_at`.
- Clients should not assume `download_url` is present unless they explicitly requested it.

### 6.6 Presign download

`POST /api/artifacts/{artifact_id}/presign-download`

Response:
- `url`, `expires_at`

Use this for raw-download affordances when `raw_access_allowed=true`.

### 6.7 Download via API proxy

`GET /api/artifacts/{artifact_id}/download`

Returns:
- `Content-Type: artifact.content_type || application/octet-stream`
- `Content-Disposition: attachment; filename="{artifact_id}"`

Notes:
- The current proxy filename is transport-oriented and uses `artifact_id`.
- Clients should prefer `metadata.name` or other UI labels for display text instead of deriving user-facing names from the download filename.

### 6.8 Link artifact to an execution

`POST /api/artifacts/{artifact_id}/links`

Request:
- `namespace`, `workflow_id`, `run_id`, `link_type`
- optional `label`
- optional provenance: `created_by_activity_type`, `created_by_worker`

Response:
- updated `ArtifactMetadata`

### 6.9 List artifacts for an execution

`GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

Query:
- `link_type` (optional)
- `latest_only` (bool; default `false`)

Response:
- `{ "artifacts": ArtifactMetadata[] }`

v1 behavior:
- `latest_only=true` is guaranteed only when paired with `link_type`; in that case the response contains at most one artifact, the latest for that execution/link pair.
- Without `link_type`, clients must not assume the server collapses results to one artifact per `link_type`.

### 6.10 Pin / unpin

- `POST /api/artifacts/{artifact_id}/pin`
- `DELETE /api/artifacts/{artifact_id}/pin`

Pin request includes optional `reason`.

### 6.11 Delete

`DELETE /api/artifacts/{artifact_id}`

Response:
- updated `ArtifactMetadata` (typically `status=deleted`)

---

## 7. Error contract (stable codes)

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

Client rules:
- Treat `authentication_required` as an auth/session problem, not a retry loop.
- Treat `artifact_state_error` as recoverable. Refresh UI and show uploading/processing state.
- Treat `artifact_forbidden` as "do not retry raw access." Hide or disable raw download affordances.

---

## 8. Presentation rules (UI contract)

### 8.1 Primary UI surfaces

Execution detail or Temporal-backed task detail should expose an artifacts panel.

Common modes:
- all artifacts for the execution
- filtered artifacts by `link_type`
- latest artifact for one known execution/link pair

Default list call:
- `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

Artifact detail drawer/page should:
- fetch `GET /api/artifacts/{artifact_id}`
- render metadata plus the default content target
- expose raw download only when `raw_access_allowed=true`

### 8.2 Default read and download behavior

Default rendering:
- resolve `default_read_ref.artifact_id`
- fetch metadata for that artifact if needed
- render it using the renderer-selection rules below

Raw download:
- if `raw_access_allowed=true`, call `POST /api/artifacts/{artifact_id}/presign-download` and navigate to that URL
- otherwise show a restricted message and do not attempt raw download

Clients must not use `presign-download` for inline preview rendering by default unless the product intentionally wants browser-native rendering of the raw asset.

### 8.3 Redaction / preview behavior

Use these fields as follows:

- If `raw_access_allowed=false`:
  - UI must not attempt raw download.
  - If `preview_artifact_ref` is present, use `default_read_ref` for the default preview target.
  - If `preview_artifact_ref` is absent, show "Preview not yet available" and offer refresh.

- If `redaction_level=restricted` and `raw_access_allowed=true`:
  - UI may visually de-emphasize raw download or call out that the artifact is restricted.
  - UI must still treat `default_read_ref` as authoritative for the default display target.

- If `redaction_level=preview_only`:
  - treat preview presentation as an intended steady-state consumer surface, not as an error condition

### 8.4 What to show/hide from metadata

Show to most users:
- created time
- size
- content type
- checksum when useful
- `link_type` plus `label`
- retention plus `expires_at`
- pinned status
- redaction status

Hide by default or keep debug-only:
- `storage_backend`
- `storage_key`
- `created_by_principal` unless ownership/debug context requires it
- `encryption` unless needed for compliance/operator UI
- provenance fields such as `created_by_activity_type` and `created_by_worker`

### 8.5 "Latest output" semantics

For v1, the canonical latest-output contract is execution plus `link_type`, not a client-derived grouping heuristic.

Rules:
- if the UI wants "latest output" for a known artifact kind, it should specify `link_type`
- if the UI wants cross-link aggregation later, add an explicit server mode instead of collapsing client-side

---

## 9. Rendering rules (by `content_type` + hints)

### 9.1 Renderer selection priority

1. `metadata.render_hint`
2. `content_type`
3. filename-like hint in `metadata.name`
4. fallback: binary / download-only

Unknown `render_hint` values must be ignored, not treated as fatal.

### 9.2 Supported renderers (recommended baseline)

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

### 9.3 Standard metadata hints

These reserved keys live under `ArtifactMetadata.metadata`:
- `name`: display filename, basename only
- `title`: display title
- `description`: short UI description
- `source`: bounded origin string such as `worker:codex`, `worker:runner`, `ui:upload`
- `artifact_type`: stable domain classifier
- `render_hint`: `text | json | diff | image | binary`
- `preview_of`: source artifact ID for preview artifacts
- `policy`: preview/redaction policy name

Rules:
- standardized keys above are reserved for cross-client interpretation
- additional producer-specific keys should live under a producer-owned nested object to avoid future key collisions
- `name` must not include path separators or leak local workspace layout

If these keys are absent, UI should still function using `content_type` and `links`.

---

## 10. Upload UX contract (UI behavior)

### 10.1 Simple upload

1. `POST /api/artifacts`
2. If `upload.mode=single_put` and `upload.upload_url` is present:
   - upload bytes to that URL with required headers
3. If local-dev mode returns a direct API path:
   - `PUT /api/artifacts/{artifact_id}/content`
4. Refresh metadata until `status=complete`

### 10.2 Multipart upload

1. `POST /api/artifacts`
2. Expect `upload.mode=multipart` and `upload.upload_id`
3. For each part:
   - `POST /api/artifacts/{artifact_id}/presign-upload-part`
   - upload the part to the returned URL
4. `POST /api/artifacts/{artifact_id}/complete` with the completed parts list
5. Refresh metadata until `status=complete`

---

## 11. Execution linkage conventions

### 11.1 Required link fields

- `namespace`
- `workflow_id`
- `run_id`
- `link_type`

### 11.2 `link_type` naming

Use lowercase, dot-delimited, stable machine meaning.

Examples:
- `input.manifest`
- `input.instructions`
- `output.primary`
- `output.patch`
- `output.logs`
- `output.summary`
- `debug.trace`

Rules:
- `link_type` should stay small, bounded, and stable
- use `link_type` for machine meaning and `label` for human-facing nuance
- clients must tolerate unknown `link_type` values and display them generically

### 11.3 `label` usage

- optional, human-friendly
- can carry versioning or presentation nuance, for example `Plan v2` or `Final output`
- must not replace `link_type` as the machine key

---

## 12. Examples

### 12.1 List the latest logs artifact for an execution

`GET /api/executions/moonmind/wf-1/run-1/artifacts?link_type=output.logs&latest_only=true`

```json
{
  "artifacts": [
    {
      "artifact_id": "art_...",
      "content_type": "text/plain",
      "size_bytes": 48221,
      "status": "complete",
      "redaction_level": "restricted",
      "raw_access_allowed": false,
      "artifact_ref": { "...": "..." },
      "preview_artifact_ref": { "...": "..." },
      "default_read_ref": { "...": "..." },
      "metadata": {
        "title": "Logs",
        "render_hint": "text"
      },
      "links": [
        {
          "namespace": "moonmind",
          "workflow_id": "wf-1",
          "run_id": "run-1",
          "link_type": "output.logs"
        }
      ]
    }
  ]
}
```

### 12.2 Fetch metadata with download included

`GET /api/artifacts/art_...?include_download=true`

Behavior:
- if allowed, the response contains `download_url`
- if not allowed, `download_url` is null or omitted and the UI should not show a raw-download CTA

---

## 13. Open questions / future extensions

- Should the API add an explicit preview-generation request endpoint, or should preview creation remain worker-driven?
- Should the API add an explicit cross-`link_type` latest mode rather than overloading `latest_only`?
- Should MoonMind standardize `artifact_type` enums for dashboard grouping, or keep it producer-defined?
- Should proxy downloads eventually prefer sanitized `metadata.name` over `artifact_id` for filenames?
- Do we want content-range or partial text fetch support for very large logs?
