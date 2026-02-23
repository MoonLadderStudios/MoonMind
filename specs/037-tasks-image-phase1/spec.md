# Feature Specification: Tasks Image Attachments Phase 1

**Feature Branch**: `[037-tasks-image-phase1]`  
**Created**: 2026-02-23  
**Status**: Draft  
**Input**: User description: "Implement Phase 1 of the Tasks Image System from docs/TasksImageSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| ID | Source | Requirement |
|----|--------|-------------|
| DOC-REQ-001 | docs/TasksImageSystem.md - Summary & API Design | Task creation must accept PNG/JPEG/WebP attachments via `POST /api/queue/jobs/with-attachments`, allow optional `captions` JSON hints keyed by filename, emit `Attachment uploaded` queue events per file, and only surface the job as claimable after every image persists inside the transaction. |
| DOC-REQ-002 | docs/TasksImageSystem.md - Data Model & Validation Rules | Attachments must be validated (type whitelist, per-file and total byte limits, count limits, signature sniffing, sanitized filenames) and stored as `AgentJobArtifact` rows under `inputs/<attachmentUuid>/<sanitized_filename>` with digests. |
| DOC-REQ-003 | docs/TasksImageSystem.md - API Design (List/Download) | Dashboard users and workers require list/download endpoints scoped to `inputs/` artifacts; worker endpoints demand worker-token auth plus an active claim enforced by job status and `claimed_by`. |
| DOC-REQ-004 | docs/TasksImageSystem.md - Authorization & Storage | The `inputs/` namespace is reserved for user attachments: worker uploads into it are rejected, filenames must prevent traversal, and read access is limited to the job owner or the claiming worker. |
| DOC-REQ-005 | docs/TasksImageSystem.md - Worker Changes | Workers must extend `QueueApiClient`, download attachments into `repo/.moonmind/inputs`, emit manifest + vision context files, update `artifacts/task_context.json`, and fire `task.attachments.*` events with counts and bytes. |
| DOC-REQ-006 | docs/TasksImageSystem.md - Prompt Injection | Runtime prompts must inject an `INPUT ATTACHMENTS` block pointing to `.moonmind/attachments_manifest.json` and inlining `image_context.md` before workspace instructions so every runtime consumes derived context. |
| DOC-REQ-007 | docs/TasksImageSystem.md - Vision Context Generation | A reusable `moonmind/vision` module must expose caption/OCR services governed by `MOONMIND_VISION_*` env vars, default to Gemini (`models/gemini-2.5-flash`), and support an enable flag plus provider/model overrides. |
| DOC-REQ-008 | docs/TasksImageSystem.md - Tasks Dashboard UI Changes | The dashboard must expose an attachments picker (drag/drop + file list) with validation feedback at creation time and a job-detail attachments panel with preview/download controls. |
| DOC-REQ-009 | docs/TasksImageSystem.md - Storage Layout | Server artifacts live under `var/artifacts/agent_jobs/<jobId>/inputs/...` and workers store downloads under `.moonmind/inputs` with `.git/info/exclude` defenses so attachments never enter commits. |
| DOC-REQ-010 | docs/TasksImageSystem.md - Validation & Authorization | Security guardrails must enforce allowed image types, sanitized filenames, size/count caps, prohibition on post-creation uploads, and ownership/worker-token checks while logging upload/download/context events. |
| DOC-REQ-011 | docs/TasksImageSystem.md - Testing Strategy | Phase 1 requires automated tests covering API validation/auth, integration flows confirming workers receive attachments before execution, and worker tests verifying downloads, `.git/info/exclude`, and prompt context injection. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit Tasks With Image Attachments (Priority: P1)

A dashboard user attaches one or more screenshots or design images to a new task so the downstream worker has visual context when the job is claimed.

**Why this priority**: Without reliable attachment ingest, workers cannot see the visual cues that the customer is trying to convey, so this is the core value driver for the system.

**Independent Test**: Use the dashboard (or a mocked API client) to upload 1-10 PNG/JPEG/WebP files totaling ≤25MB via `POST /api/queue/jobs/with-attachments` and confirm the job appears as queued only after attachments metadata is returned.

**Acceptance Scenarios**:

1. **Given** a user selects supported image files within the limit, **When** they submit the task, **Then** the API persists the job plus attachment metadata atomically and the job becomes claimable with attachment counts/sizes visible in the response.
2. **Given** a user drags in an SVG or oversized file, **When** they attempt to submit, **Then** the UI surfaces a validation error (type or size) without creating or queuing the job.
3. **Given** a user supplies optional caption hints in the `captions` JSON payload, **When** the request succeeds, **Then** those hints are stored alongside attachment metadata so workers can reference the human-provided descriptions.

---

### User Story 2 - Worker Prepares Vision Context (Priority: P2)

A Codex/Gemini/Claude worker automatically downloads attachments, summarizes their contents, and injects that context into the runtime prompt before reasoning about the task.

**Why this priority**: Deterministic download + context generation ensures every worker run has the same visual information, which directly influences output quality and mitigates hallucinations.

**Independent Test**: Claim a task with attachments in a staging worker, capture prepare-stage logs, and verify `.moonmind/inputs`, `.moonmind/attachments_manifest.json`, `.moonmind/vision/image_context.md`, and updated `artifacts/task_context.json` are produced before execution starts.

**Acceptance Scenarios**:

1. **Given** a worker claims a job that has attachments, **When** prepare runs, **Then** each attachment downloads to `.moonmind/inputs/<id>-<filename>`, the manifest lists ids/digests/local paths, and context generation populates captions (or an explicit disabled message).
2. **Given** `MOONMIND_VISION_CONTEXT_ENABLED` is set to `false`, **When** the prepare stage completes, **Then** the manifest still exists, the prompt injection states that vision context is disabled, and the worker continues without failure.

---

### User Story 3 - Review Attachments From Job Detail (Priority: P3)

A task owner can review or download the images they uploaded from the dashboard job detail view to confirm the system preserved their intent.

**Why this priority**: Visibility and auditability reassure users that sensitive images remain intact and accessible only to authorized viewers.

**Independent Test**: Open a job detail page that includes attachments, preview the thumbnail modal for at least one image, and download another image while verifying access controls enforce job ownership.

**Acceptance Scenarios**:

1. **Given** a job owner opens the attachments panel, **When** they click a thumbnail, **Then** a preview modal renders the sanitized filename plus image and offers a download action.
2. **Given** a different authenticated user without job ownership attempts to load the attachment URL, **When** the request reaches the API, **Then** it is rejected with an authorization error and no bytes are streamed.

### Edge Cases

- Multiple attachments share the same original filename; sanitization must keep unique on-disk names while preserving a user-visible label so downloads are disambiguated.
- Users attempt to upload more than the configured maximum attachment count (e.g., >10); the API returns a precise error indicating the allowed count and no partial uploads are stored.
- Attachments exceed the total-byte budget; the system calculates the aggregate payload before persistence and rejects the request with guidance on size limits.
- Worker downloads fail mid-stream; the worker must verify digests, retry idempotently, and emit a failure metric if integrity cannot be re-established before execution.
- Vision provider credentials are unavailable; prepare completes with a logged warning and instructs the runtime prompt that image context could not be generated instead of silently omitting attachments.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** (Maps to DOC-REQ-001): The queue API MUST implement `POST /api/queue/jobs/with-attachments`, processing the JSON job payload, optional `captions` hints keyed by filename, and file uploads in a single transaction so no job enters the queue or becomes claimable until each attachment is validated, persisted, and an `Attachment uploaded` queue event fires per file.
- **FR-002** (Maps to DOC-REQ-003): The system MUST expose attachment list and download endpoints for both dashboard users and workers; worker endpoints MUST require worker-token authentication plus an active claim (`job.status = RUNNING` and `claimed_by` matches the token) and the QueueApiClient MUST mirror these capabilities for all runtimes.
- **FR-003** (Maps to DOC-REQ-002): Attachment ingestion MUST enforce PNG/JPEG/WebP content types via signature sniffing, sanitize filenames, cap the per-file, per-job, and count limits, compute a SHA-256 digest, and persist the file under `inputs/<attachmentUuid>/<sanitized_filename>` in `AgentJobArtifact` records.
- **FR-004** (Maps to DOC-REQ-004, DOC-REQ-010): The `inputs/` namespace MUST remain read-only for workers—server-side upload APIs reject any artifact whose name starts with `inputs/`, filenames MUST eliminate traversal characters, and read access MUST be limited to the job owner or the currently claiming worker.
- **FR-005** (Maps to DOC-REQ-005, DOC-REQ-009): During the worker prepare stage the system MUST download attachment binaries into `repo/.moonmind/inputs`, ensure `.git/info/exclude` ignores `.moonmind/`, and write `repo/.moonmind/attachments_manifest.json` that enumerates ids, filenames, digests, sizes, and local paths.
- **FR-006** (Maps to DOC-REQ-005, DOC-REQ-011): Workers MUST emit `task.attachments.download.started`, `task.attachments.download.finished`, and `task.attachments.context.generated` events with attachment counts/bytes and provider names, and update `artifacts/task_context.json` under a new `attachments` key summarizing what was downloaded.
- **FR-007** (Maps to DOC-REQ-007): The platform MUST implement a shared `moonmind/vision` module governed by `MOONMIND_VISION_CONTEXT_ENABLED`, `MOONMIND_VISION_PROVIDER`, `MOONMIND_VISION_MODEL`, `MOONMIND_VISION_MAX_TOKENS`, and `MOONMIND_VISION_OCR_ENABLED`, defaulting to Gemini models while allowing operators to disable or swap providers without code changes.
- **FR-008** (Maps to DOC-REQ-006): `_compose_step_instruction_for_runtime` (and any equivalent entry point) MUST prepend an `INPUT ATTACHMENTS` block that points to `.moonmind/attachments_manifest.json`, explains where binaries live, and inlines or references `repo/.moonmind/vision/image_context.md` before workspace instructions for Codex, Gemini, and Claude runtimes.
- **FR-009** (Maps to DOC-REQ-001, DOC-REQ-008): The Tasks Dashboard MUST add an attachments section to the task creation form with drag/drop UX, validation feedback, and progress indicators plus a job-detail panel that lists sanitized filenames, types, sizes, and offers preview/download actions backed by the new endpoints.
- **FR-010** (Maps to DOC-REQ-009): Attachment storage MUST adhere to the documented layout: artifacts on the server under `var/artifacts/agent_jobs/<jobId>/inputs/...` and worker-local copies under `.moonmind/inputs` and `.moonmind/vision`, ensuring no attachment paths appear in git status diffs.
- **FR-011** (Maps to DOC-REQ-010): Security controls MUST block uploads after job creation, enforce content-type and size guardrails, reject SVG or other disallowed formats, sanitize filenames deterministically, and audit attachment upload/download/context actions via queue events or logs for later review.
- **FR-012** (Maps to DOC-REQ-011 + runtime guard): Delivery MUST include production runtime code plus automated validation (unit tests via `./tools/test_unit.sh` and integration/worker tests) that exercise attachment validation, worker downloads, `.moonmind` manifest creation, prompt injection, and access controls, satisfying the runtime scope guard.

### Key Entities *(include if feature involves data)*

- **Task Attachment Input Artifact**: Represents each sanitized user image with metadata (id, original filename, sanitized name, content type, digest, size, storage path) stored under the reserved `inputs/` namespace.
- **Attachment Manifest**: A job-scoped JSON file (`repo/.moonmind/attachments_manifest.json`) capturing attachment metadata, optional `userCaptionHint` strings sourced from the `captions` payload, and local paths that runtimes and diagnostics can consume without hitting the API again.
- **Image Context Document**: A deterministic markdown file (`repo/.moonmind/vision/image_context.md`) that records safety notices, attachment order, generated captions, and optional OCR text for prompt injection and artifact review.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of validation test runs that submit up to 10 attachments totaling ≤25MB observe the job becoming claimable only after the attachments metadata is present in the response and downloadable by a worker.
- **SC-002**: 100% of unsupported file types or limit violations are rejected within 1 second at the API boundary with actionable error messages and no partially stored artifacts.
- **SC-003**: For 100% of worker executions that include attachments, prepare-stage logs show matching download/context events, manifest + context files exist, and prompt payloads include the attachment block before workspace instructions.
- **SC-004**: Dashboard usability testing demonstrates that attachment previews render and downloads initiate in under 2 seconds for files up to 10MB, and no unauthorized user can retrieve the same assets.

## Scope Boundaries

### In Scope

- API, storage, dashboard UI, and worker changes described in docs/TasksImageSystem.md Phase 1.
- Vision module configuration, feature flags, and manifest/context artifact creation.
- Instrumentation and queue events related to attachment download/context generation.
- Automated validation (unit + integration + worker tests) that prove runtime code paths function end-to-end.

### Out of Scope

- Allowing attachment uploads or edits after job creation; users must resubmit if they forget an image.
- Supporting non-image file types, animated formats beyond GIF (explicitly excluded), or SVG ingestion.
- Adding new artifact retention policies beyond piggybacking on existing `AgentJobArtifact` cleanup jobs.
- Native multimodal runtime messaging (Phase 3) or database schema changes for attachment metadata roles.

## Assumptions & Dependencies

- Existing artifact retention jobs continue to clean up both outputs and the new `inputs/` namespace on the same schedule.
- Workers already export the necessary API keys (Gemini/OpenAI/Anthropic) so the vision module can reuse them without new credential distribution.
- Dashboard front-end builds can leverage existing file-upload infrastructure (multipart form-data + progress events) to add the attachments section.
- Worker sandboxes allow outbound HTTPS so attachments can be downloaded from the API artifact store before execution.
- Queue event consumers and observability pipelines can ingest the new `task.attachments.*` metrics without additional schema changes.
