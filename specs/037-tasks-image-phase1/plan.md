# Implementation Plan: Tasks Image Attachments Phase 1

**Branch**: `037-tasks-image-phase1` | **Date**: 2026-02-23 | **Spec**: `specs/037-tasks-image-phase1/spec.md`  
**Input**: Feature specification from `/specs/037-tasks-image-phase1/spec.md`

## Summary

Implement Phase 1 of `docs/TasksImageSystem.md`: queue job creation must atomically persist PNG/JPEG/WebP attachments under the reserved `inputs/` namespace, expose list/download APIs for job owners and claiming workers, and ensure workers download attachments during prepare to produce `.moonmind/inputs`, `.moonmind/attachments_manifest.json`, and `.moonmind/vision/image_context.md`. Prompt payloads for Codex/Gemini/Claude must prepend an `INPUT ATTACHMENTS` block referencing those artifacts, and the Tasks Dashboard must gain attachment pickers plus a job-detail panel. A reusable `moonmind/vision` module governs caption/OCR generation with Gemini defaults and feature flags. Automated tests cover API validation, worker prepare flows, prompt injection, and dashboard UX contracts.

## Technical Context

**Language/Version**: Python 3.11 for API + workers, FastAPI 0.129, SQLAlchemy 2.x, Celery 5.4; TypeScript/ES2022 bundled to vanilla JS for dashboard; TailwindCSS for styling.  
**Primary Dependencies**: FastAPI upload stack (`UploadFile`, Starlette), SQLAlchemy ORM, Pydantic v2 models, httpx AsyncClient, Docker SDK (worker containers), Node + Tailwind build, StatsD instrumentation.  
**Storage**: PostgreSQL `agent_jobs` + `agent_job_artifacts`, filesystem artifact root `var/artifacts/agent_jobs/<jobId>/inputs/...`, worker-local `.moonmind/` tree inside repo checkout, RabbitMQ for job dispatch.  
**Testing**: `./tools/test_unit.sh` (pytest) for API/service/worker/dashboard view models; optional `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` for end-to-end.  
**Target Platform**: Linux containers (Compose stack) plus local dev shells/WSL.  
**Project Type**: Backend services + Celery worker + web dashboard bundle.  
**Performance Goals**: keep attachment upload validation <1 s for ≤25 MB total, worker download manifest creation before runtime start, dashboard preview render <2 s for ≤10MB images.  
**Constraints**: attachments limited to PNG/JPEG/WebP, enforce sanitized filenames + digest logging, `.moonmind/` excluded from git, prompt injection must precede workspace instructions, instrumentation must emit `task.attachments.*` events.  
**Scale/Scope**: queue jobs typically carry ≤10 images; features must not regress existing artifact APIs or worker throughput.

## Constitution Check

`.specify/memory/constitution.md` remains a placeholder template with unnamed principles. No enforceable gates can be applied; flagging **NEEDS CLARIFICATION** for governance to define concrete standards. Proceeding under MoonMind runtime guardrails (runtime code + automated tests required) and Spec Kit instructions. Gate re-check after design shows no new conflicts.

## Project Structure

### Documentation (this feature)

```text
specs/037-tasks-image-phase1/
├── plan.md                     # This plan
├── research.md                 # Phase 0 decisions (namespace, API, worker, vision, prompt)
├── data-model.md               # Entity + artifact layout
├── quickstart.md               # Validation steps for API, worker, dashboard
├── contracts/
│   ├── attachments.openapi.yaml
│   └── requirements-traceability.md
└── checklists/ (feature-specific QA if requested later)
```

### Source Code + Tests

```text
api_service/
├── api/routers/agent_queue.py          # Multipart create endpoint + list/download APIs
├── api/routers/task_dashboard.py       # Serve dashboard shell (unchanged wiring)
├── static/task_dashboard/dashboard.js  # Queue submit UI + job detail attachments panel
├── templates/task_dashboard.html       # Ensures config injection supports new panel states

moonmind/
├── config/settings.py                  # Attachment + vision env flags
├── schemas/agent_queue_models.py       # JobWithAttachments DTOs
├── workflows/agent_queue/
│   ├── service.py                      # Attachment validation, list/download ACL
│   └── storage.py                      # Artifact root interactions
├── agents/codex_worker/
│   ├── worker.py                       # Prepare stage downloads, manifest + prompt injection
│   ├── handlers.py                     # Event emission hooks if needed
│   └── utils.py                        # Filesystem helpers for `.moonmind`
├── agents/codex_worker/metrics.py      # `task.attachments.*` counters
├── agents/codex_worker/cli.py          # Wire new CLI flags if required
├── vision/                             # NEW module (settings.py, service.py, ocr.py)

docs/TasksImageSystem.md                # Source-of-truth spec referenced throughout

tests/
├── unit/api/routers/test_agent_queue.py
├── unit/workflows/agent_queue/test_service_attachments.py
├── unit/agents/codex_worker/test_worker.py
├── unit/config/test_settings.py        # env flag overrides
└── unit/task_dashboard/test_dashboard_attachments.py (new Jest/Playwright-lite harness if needed)
```

**Structure Decision**: Build on the existing queue/service layering (schemas → repository/service → router) and keep worker-specific logic confined to `moonmind/agents/codex_worker` plus the new `moonmind/vision` package. Dashboard updates stay in the static JS bundle so no backend template changes are required beyond config exposures already in place.

## Phase 0: Research Summary

Phase 0 outputs (`research.md`) resolved:
1. Attachments reuse `AgentJobArtifact` storage under the reserved `inputs/` prefix; no parallel blob store is introduced.
2. Dedicated list/download endpoints exist for user + worker contexts to enforce ACL without leaking non-input artifacts.
3. Worker prepare stage handles download + manifest + context generation before runtime start.
4. `moonmind/vision` module centralizes caption/OCR with Gemini defaults and feature flags.
5. Prompt builders prepend a deterministic `INPUT ATTACHMENTS` block before workspace instructions across runtimes.

These decisions remove `NEEDS CLARIFICATION` markers, so Phase 1 can focus on implementation details.

## Phase 1: Implementation Blueprint

### 1. Queue API + Service Hardening
- `moonmind/config/settings.py`: ensure attachment limit env vars + allowed content types documented; add `MOONMIND_VISION_*` settings (enable flag, provider, model, max tokens, OCR toggle). Extend `SpecWorkflowSettings` validators for tuple parsing + defaults.
- `moonmind/workflows/agent_queue/service.py`: finalize `create_job_with_attachments` (validate `CANONICAL_TASK_JOB_TYPE`, enforce count/size/digest). Confirm `_persist_attachments` emits queue events and `list/get` methods filter to `_ATTACHMENT_NAMESPACE`. Add logging for rejected namespace usage (FR-004) and TOT bytes. Ensure `_normalize_attachment_upload` uses signature sniffing for PNG/JPEG/WebP.
- `moonmind/workflows/agent_queue/storage.py`: verify path sanitization and `AgentQueueArtifactStorage.write_artifact` handles `inputs/` directories; add integration tests if missing.
- `moonmind/schemas/agent_queue_models.py`: expose `JobWithAttachmentsResponse` (already present but ensure aliasing + documentation). Add dataclasses for `AttachmentManifestEntry` when referenced by worker outputs.
- `api_service/api/routers/agent_queue.py`: keep multipart route but add worker/user list/download endpoints with limit query, worker-token enforcement, and HTTP 413/403 responses. Document `captions` optional JSON input.
- `api_service/api/routers/task_dashboard.py`: no API change, but ensure allowable route list includes new attachments detail path segments if needed.

### 2. Worker Prepare Stage + Manifest Artifacts
- `moonmind/agents/codex_worker/worker.py`:
  - Augment `_run_prepare_stage` to call a new `_download_task_attachments` helper once the repo checkout is ready. Helper responsibilities: fetch metadata via `QueueApiClient.list_attachments_worker`, download each artifact with streaming GET, verify SHA-256 digest, write to `repo/.moonmind/inputs/<uuid>-<sanitized>`, update `.git/info/exclude` with `.moonmind/` guard, and return manifest payload.
  - Write `.moonmind/attachments_manifest.json` with job id, downloaded timestamp, entries containing `id`, `filename`, `contentType`, `sizeBytes`, `digest`, `localPath` relative to repo root, and any `userCaptionHint` text derived from the upload-time `captions` payload.
  - Update `artifacts/task_context.json` by merging an `attachments` object (`enabled`, `count`, `totalBytes`, manifest path, `visionContextPath`, `visionContextStatus`).
  - Emit queue events `task.attachments.download.started/finished` and StatsD metrics for counts/bytes.
- `QueueApiClient` class: add `list_attachments_worker(job_id)` + `download_attachment_worker(job_id, attachment_id)` that call `/attachments/worker` endpoints with worker token header; use streaming download to temporary file before rename.
- Handle error cases: digest mismatch, HTTP 404/403, network errors with retries (bounded). On failure, raise `RuntimeError` so job fails early.

### 3. Vision Context Module
- Introduce `moonmind/vision/settings.py` + `moonmind/vision/service.py`:
  - `VisionSettings` reads `MOONMIND_VISION_CONTEXT_ENABLED`, provider/model overrides, max tokens, OCR toggle, provider-specific env (Gemini by default, fallback to OpenAI/Anthropic later).
  - `VisionContextGenerator` accepts attachments manifest entries, orchestrates caption generation (calls Gemini via `google-generativeai` or uses placeholder when disabled/misconfigured), optionally runs OCR via `pytesseract`/`pillow` if flag true.
  - Output `repo/.moonmind/vision/image_context.md` per template: safety notice, enumerated attachments with metadata, `description` (caption), `ocr` (if enabled, else “OCR disabled”). Provide fallback text when provider disabled.
  - Emit event `task.attachments.context.generated` including provider + count + status (disabled, fallback, success).

### 4. Prompt Injection & Runtime Alignment
- Update `moonmind/agents/codex_worker/worker.py::_compose_step_instruction_for_runtime` to prepend:
  - `INPUT ATTACHMENTS` header.
  - Bullets referencing `.moonmind/attachments_manifest.json`, `.moonmind/inputs/` path, and inline the contents of `image_context.md` (truncate >8 KB with link). Ensure block is inserted before the existing `WORKSPACE` section.
- For Gemini/Claude adapters (`_build_non_codex_runtime_command`), ensure prompt string already includes this block (common builder). Add tests verifying final instructions include attachments block regardless of runtime.

### 5. Dashboard UI & UX
- `api_service/static/task_dashboard/dashboard.js`:
  - Queue submit form: add drag/drop zone + file input (accept `image/png,image/jpeg,image/webp`), show previews list (filename, type, size, validation errors). Enforce max count + bytes client-side using settings from `dashboard_config` (extend config to expose `agent_job_attachment_*`). On submit, build `FormData` with `request` JSON + `files[]`. Show progress state while upload occurs.
  - Job detail view: fetch `/api/queue/jobs/{id}/attachments` and render list with preview thumbnails (use `<img src>` from download endpoint) and download buttons linking to user download route.
  - Worker attachments states appear in queue detail table so operators can see counts/bytes; display indicator if attachments exist but manifest missing (should not happen).
- CSS updates (`dashboard.tailwind.css`) to style dropzone, thumbnail grid, validation errors.
- Potential addition to `task_dashboard_view_model.build_runtime_config` to surface attachment limits & allowed MIME types to the client script.

### 6. API Documentation & Contracts
- `specs/037-tasks-image-phase1/contracts/attachments.openapi.yaml`: already drafted; keep in sync with implementation (ensure parameter refs defined once at top). Provide docstrings/responses for worker endpoints.
- Update `docs/TasksImageSystem.md` if minor clarifications discovered during implementation.

### 7. Instrumentation & Logging
- Extend `moonmind/agents/codex_worker/metrics.py` to expose StatsD timers/counters for attachments download bytes/time and vision generation latency; call from new helpers.
- Queue events: ensure `AgentQueueService` appends `Attachment uploaded` per file; worker emits `task.attachments.download.*` and `task.attachments.context.generated`. Tests assert events recorded.
- Add structured logs when attachments disabled (flag false) or provider missing credentials.

### 8. Testing Strategy
- API/service: `tests/unit/api/routers/test_agent_queue.py` (multipart success/validation errors, worker/user download auth), `tests/unit/workflows/agent_queue/test_service_attachments.py` (limits, ACL, namespace guard), `tests/unit/config/test_settings.py` (vision + attachment env overrides).
- Worker: new tests in `tests/unit/agents/codex_worker/test_worker.py` for `_download_task_attachments`, manifest writing, `.git/info/exclude`, prompt injection block contents, vision disabled/enabled flows.
- Dashboard: add JS unit tests (if existing harness) or integration snapshot verifying queue submit view renders attachments UI; use `jest`/`vitest` or add Playwright fixture referencing `/tasks/queue/new` (if automation available). At minimum, use DOM-level tests verifying dropzone state machine.
- Quickstart manual steps (documented in `quickstart.md`) ensure API + worker + dashboard path validated end-to-end.

## Post-Design Constitution Re-check

- Runtime code paths (API + worker + dashboard) plus automated tests are included, satisfying MoonMind runtime guardrail + spec instructions.
- No additional constitution clauses exist; gate remains PASS WITH NOTE pending actual constitution content.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| None | – | – |
