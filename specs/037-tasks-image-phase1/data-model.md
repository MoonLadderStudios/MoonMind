# Data Model – Tasks Image Attachments Phase 1

## Server-side Entities

### AgentJob
- Existing queue job row; attachments do not add columns but depend on `id`, `claimed_by`, `status`, and `artifacts_path` to enforce auth.
- Relationship: `AgentJob` **has many** `AgentJobArtifact` rows filtered by `name LIKE 'inputs/%'`.

### AgentJobArtifact (`inputs/` namespace)
| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | Primary key referenced by download endpoints. |
| `job_id` | UUID | FK to `AgentJob.id`. |
| `name` | text | Enforced format `inputs/<attachmentUuid>/<sanitized_filename>`; reserved so worker uploads cannot collide. |
| `content_type` | text | Must be one of `image/png`, `image/jpeg`, `image/webp` (sniffed). |
| `size_bytes` | bigint | Validated against per-file (10MB default) and aggregate (25MB default) caps. |
| `digest` | text | `sha256:<hex>` computed on upload; used to validate worker downloads. |
| `storage_path` | text | Relative path within `settings.spec_workflow.agent_job_artifact_root`. |
| `created_at` | timestamptz | Audit log for uploads (mirrors queue events). |

### Attachment Events
- `Attachment uploaded`: emitted once per persisted file during `create_job_with_attachments`.
- `task.attachments.download.started|finished`: emitted by workers to record counts/bytes and match queue job timeline.
- `task.attachments.context.generated`: emitted after writing `image_context.md`.

## API DTOs
- `JobWithAttachmentsResponse`: wraps `JobModel` plus `attachments: list[ArtifactModel]` returned by `POST /api/queue/jobs/with-attachments`.
- `AttachmentModel` (reuse `ArtifactModel`) is shared by list/download endpoints for user + worker flows.
- New OpenAPI components describe multipart form payloads for job creation and worker-auth headers (`X-MoonMind-Worker-Token`).

## Worker-side Artifacts (per job workspace)
- `.moonmind/inputs/`: contains `<attachmentUuid>-<sanitized_filename>` files; filenames stay unique even if user uploaded duplicates.
- `.moonmind/attachments_manifest.json`:
  ```json
  {
    "jobId": "<uuid>",
    "downloadedAt": "2026-02-23T18:05:12Z",
    "attachments": [
      {
        "id": "<artifact uuid>",
        "filename": "wireframe.png",
        "contentType": "image/png",
        "sizeBytes": 183520,
        "digest": "sha256:...",
        "localPath": ".moonmind/inputs/<uuid>-wireframe.png"
      }
    ]
  }
  ```
- `.moonmind/vision/image_context.md`: Markdown emitted by `moonmind/vision` module summarizing each attachment plus generated captions/OCR text; includes a safety notice sentinel.
- `artifacts/task_context.json`: gains a top-level `"attachments"` object capturing counts, bytes, manifest path, and whether vision context was generated or skipped.
- `.git/info/exclude`: append `.moonmind/` entry on first prepare so attachments never appear in `git status`.

## Module Relationships
- `api_service/api/routers/agent_queue.py` ⇄ `moonmind.workflows.agent_queue.service.AgentQueueService` for queue endpoints, plus streaming downloads via `AgentQueueArtifactStorage`.
- `moonmind/agents/codex_worker/worker.py` consumes `QueueApiClient` to fetch attachments, writes manifests, and injects prompt text.
- `moonmind/vision/*` provides caption/OCR generation used by workers + future runtimes.
- Dashboard JS persists metadata client side then calls `/api/queue/jobs/with-attachments` multipart endpoint; job detail requests call new `/attachments` APIs to render previews.

## Configuration Surface
| Setting | Default | Purpose |
|---------|---------|---------|
| `AGENT_JOB_ATTACHMENT_ENABLED` | `True` | Global feature flag for attachment ingest. |
| `AGENT_JOB_ATTACHMENT_MAX_COUNT` | `10` | Enforced at service layer and UI for user guidance. |
| `AGENT_JOB_ATTACHMENT_MAX_BYTES` | `10 MiB` | Max per-file size; used by router preflight and service validation. |
| `AGENT_JOB_ATTACHMENT_TOTAL_BYTES` | `25 MiB` | Aggregate size guard for a single job. |
| `AGENT_JOB_ATTACHMENT_ALLOWED_TYPES` | `image/png,image/jpeg,image/webp` | Whitelist used by magic-byte sniffer. |
| `MOONMIND_VISION_CONTEXT_ENABLED` | `true` | Worker flag to enable caption/OCR. |
| `MOONMIND_VISION_PROVIDER` | `gemini` | Provider key consumed by `moonmind/vision`. |
| `MOONMIND_VISION_MODEL` | `models/gemini-2.5-flash` | Default caption/OCR model. |
| `MOONMIND_VISION_OCR_ENABLED` | `true` | When false, skip OCR subsection but still emit placeholder text. |

## Traceability Hooks
- Requirements with prefix `DOC-REQ-*` map to FR-001..FR-012 (see `contracts/requirements-traceability.md`).
- Research + plan reference ensures Phase 2 (tasks) can link to the same manifest/context artifacts.
