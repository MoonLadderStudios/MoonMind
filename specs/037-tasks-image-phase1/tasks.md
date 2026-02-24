# Tasks: Tasks Image Attachments Phase 1

**Input**: Design artifacts under `/specs/037-tasks-image-phase1/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/attachments.openapi.yaml, quickstart.md
**Tests**: Required via `./tools/test_unit.sh` (per DOC-REQ-011) plus optional `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests`

**Format Reminder**: `- [ ] T### [P?] [US#] Description (DOC-REQ-###, ...)` — include `[US#]` only inside user-story phases and mark `[P]` when tasks do not block others.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare configuration, module scaffolding, and fixtures that every story needs.

- [X] T001 Update `moonmind/config/settings.py` and `config.toml` so `AGENT_JOB_ATTACHMENT_*` and `MOONMIND_VISION_*` defaults plus validation live in one place for downstream services (DOC-REQ-007, DOC-REQ-010).
- [X] T002 [P] Scaffold `moonmind/vision/__init__.py`, `moonmind/vision/settings.py`, and a placeholder `service.py` to reserve the module namespace referenced by workers and prompts (DOC-REQ-007).
- [X] T003 [P] Add PNG/JPEG/WebP fixtures under `tests/fixtures/attachments/` and reference them from `specs/037-tasks-image-phase1/quickstart.md` so automated suites can exercise uploads (DOC-REQ-011).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Harden storage, service helpers, and clients before implementing user stories.

- [ ] T004 Refactor `moonmind/workflows/agent_queue/storage.py` to enforce sanitized `inputs/<uuid>/<filename>` paths, digest accounting, and reserved-namespace guards (DOC-REQ-002, DOC-REQ-004, DOC-REQ-009, DOC-REQ-010).
- [ ] T005 [P] Extend `moonmind/workflows/agent_queue/service.py` with shared helpers (limit checks, `_list_input_artifacts`, `_assert_job_worker_ownership`) that gate both user and worker flows (DOC-REQ-001, DOC-REQ-003, DOC-REQ-004).
- [ ] T006 [P] Update `moonmind/schemas/agent_queue_models.py` plus related DTO conversions so queue/job responses expose attachment metadata, counts, and totals (DOC-REQ-001, DOC-REQ-003).
- [ ] T007 [P] Add attachment list/download stubs with worker-token headers to `moonmind/agents/codex_worker/queue_api_client.py` for future prepare-stage use (DOC-REQ-003, DOC-REQ-005).

---

## Phase 3: User Story 1 - Submit Tasks With Image Attachments (Priority: P1) 🎯 MVP

**Goal**: Dashboard/API users can submit PNG/JPEG/WebP attachments alongside a task, and the job becomes claimable only after every file persists under `inputs/`.
**Independent Test**: Use `POST /api/queue/jobs/with-attachments` with ≤10 files totaling ≤25 MB and confirm the response lists sanitized attachment metadata plus job status queued.

### Tests for User Story 1 (required by DOC-REQ-011)

- [ ] T008 [P] [US1] Add happy-path + limit failure coverage for `POST /api/queue/jobs/with-attachments` in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-010, DOC-REQ-011).
- [ ] T009 [P] [US1] Expand `tests/unit/workflows/agent_queue/test_service_attachments.py` to assert sanitized filenames, namespace guards, and aggregate byte enforcement (DOC-REQ-002, DOC-REQ-004, DOC-REQ-009, DOC-REQ-011).
- [ ] T010 [P] [US1] Add ACL tests for job-owner attachment listing/downloading (and unauthorized callers) in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-010, DOC-REQ-011).

### Implementation for User Story 1

- [ ] T011 [P] [US1] Implement multipart `POST /api/queue/jobs/with-attachments` parsing plus request validation in `api_service/api/routers/agent_queue.py`, including the optional `captions` JSON map keyed by filename (DOC-REQ-001, DOC-REQ-002, DOC-REQ-010).
- [ ] T012 [US1] Complete `AgentQueueService.create_job_with_attachments` to atomically persist jobs + attachments, store caption hints, enforce per-file/total limits, and emit `Attachment uploaded` queue events plus audit logs (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-010).
- [ ] T013 [P] [US1] Update `moonmind/workflows/agent_queue/storage.py` and related artifact writers so attachments land in `var/artifacts/agent_jobs/<jobId>/inputs/<uuid>/<filename>` with digests (DOC-REQ-002, DOC-REQ-009).
- [ ] T014 [P] [US1] Extend DTOs and response builders in `moonmind/schemas/agent_queue_models.py` plus `api_service/api/routers/agent_queue.py` to surface attachment counts/sizes in job payloads (DOC-REQ-001, DOC-REQ-003).
- [ ] T015 [US1] Add job-owner `GET /api/queue/jobs/{jobId}/attachments` + `/download` endpoints with pagination + sanitized filenames in `api_service/api/routers/agent_queue.py` (DOC-REQ-003, DOC-REQ-004).
- [ ] T016 [P] [US1] Emit StatsD counters and queue events for attachment upload/list/download inside `moonmind/workflows/agent_queue/service.py` and `moonmind/agents/codex_worker/metrics.py` (DOC-REQ-004, DOC-REQ-010).

**Checkpoint**: Job creation + owner APIs work end-to-end; attachments never leak outside `inputs/`.

---

## Phase 4: User Story 2 - Worker Prepares Vision Context (Priority: P2)

**Goal**: Codex/Gemini/Claude workers download attachments during prepare, build manifests + vision context, and inject an `INPUT ATTACHMENTS` block before runtime instructions.
**Independent Test**: Claim a job with attachments and verify `.moonmind/inputs`, `.moonmind/attachments_manifest.json`, `.moonmind/vision/image_context.md`, updated `artifacts/task_context.json`, and prompt logs containing the attachment block.

### Tests for User Story 2 (required by DOC-REQ-011)

- [ ] T017 [P] [US2] Extend `tests/unit/agents/codex_worker/test_worker.py` to cover attachment downloads, digest verification, `.moonmind/inputs` paths, and StatsD events (DOC-REQ-005, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011).
- [ ] T018 [P] [US2] Add prompt-builder tests (Codex/Gemini/Claude) ensuring the `INPUT ATTACHMENTS` block precedes `WORKSPACE` text and handles disabled vision states in `tests/unit/agents/codex_worker/test_prompts.py` (DOC-REQ-006, DOC-REQ-005, DOC-REQ-011).
- [ ] T019 [P] [US2] Create `tests/unit/vision/test_service.py` validating `moonmind/vision` enable flags, provider/model overrides, and OCR toggles (DOC-REQ-007, DOC-REQ-011).
- [ ] T020 [P] [US2] Add worker-endpoint auth tests to `tests/unit/api/routers/test_agent_queue.py` covering token enforcement + claim checks (DOC-REQ-003, DOC-REQ-004, DOC-REQ-011).

### Implementation for User Story 2

- [ ] T021 [US2] Implement worker-scoped `GET /api/queue/jobs/{jobId}/attachments/worker` and `/download/worker` routes in `api_service/api/routers/agent_queue.py` using the new ACL helpers (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005).
- [ ] T022 [P] [US2] Extend `moonmind/agents/codex_worker/queue_api_client.py` with streaming list/download calls that send `X-MoonMind-Worker-Token` and handle limit pagination (DOC-REQ-003, DOC-REQ-005).
- [ ] T023 [US2] Update `moonmind/agents/codex_worker/worker.py::_run_prepare_stage` to download attachments via the client, verify digests, and write binaries under `repo/.moonmind/inputs/<uuid>-<filename>` (DOC-REQ-005, DOC-REQ-009).
- [ ] T024 [P] [US2] Write `.moonmind/attachments_manifest.json`, update `.git/info/exclude`, and guard directories via helpers in `moonmind/agents/codex_worker/utils.py` (DOC-REQ-005, DOC-REQ-009).
- [ ] T025 [P] [US2] Implement `moonmind/vision/service.py` to render `vision/image_context.md` using Gemini defaults + OCR toggle, returning fallback text when disabled (DOC-REQ-007, DOC-REQ-005).
- [ ] T026 [US2] Inject the `INPUT ATTACHMENTS` block ahead of workspace instructions for Codex/Gemini/Claude inside `moonmind/agents/codex_worker/worker.py` prompt builders (DOC-REQ-006, DOC-REQ-005).
- [ ] T027 [P] [US2] Update `artifacts/task_context.json` writer and `moonmind/agents/codex_worker/metrics.py` to summarize attachment counts/bytes, context status, and emit `task.attachments.*` events (DOC-REQ-005, DOC-REQ-010).
- [ ] T028 [P] [US2] Wire `MOONMIND_VISION_*` + attachment flags into `moonmind/agents/codex_worker/cli.py` and documentation so operators can toggle providers/OCR (DOC-REQ-007, DOC-REQ-010).

**Checkpoint**: Workers always download attachments, emit manifests/context, and prepend prompts before executing runtimes.

---

## Phase 5: User Story 3 - Review Attachments From Job Detail (Priority: P3)

**Goal**: Dashboard users can add attachments via drag/drop at creation time and review/preview downloads inside the job detail panel with ACL enforcement.
**Independent Test**: Submit a job through the dashboard with ≤10 images, observe validation feedback for invalid files, and preview/download attachments in the job detail drawer while an unauthorized user receives HTTP 403.

### Tests for User Story 3 (required by DOC-REQ-011)

- [ ] T029 [P] [US3] Add dashboard JS unit tests (or DOM harness) covering drag/drop validation, size/type limits, and error messaging in `tests/unit/task_dashboard/test_dashboard_attachments.py` (DOC-REQ-008, DOC-REQ-001, DOC-REQ-011).
- [ ] T030 [P] [US3] Write job-detail preview/download tests (Playwright or equivalent) verifying preview rendering and 403 responses for unauthorized downloads (DOC-REQ-008, DOC-REQ-003, DOC-REQ-011).

### Implementation for User Story 3

- [ ] T031 [US3] Update `api_service/static/task_dashboard/dashboard.js` to add the attachments picker (drag/drop + file input), client-side validation, and multipart FormData submission (DOC-REQ-008, DOC-REQ-001).
- [ ] T032 [P] [US3] Extend dashboard config plumbing (`api_service/api/routers/task_dashboard.py`, `templates/task_dashboard.html`) to expose attachment limits + allowed MIME types to the frontend (DOC-REQ-008, DOC-REQ-003).
- [ ] T033 [P] [US3] Implement the job-detail attachments panel with previews, sanitized filenames, and download buttons wired to the new APIs inside `dashboard.js` (DOC-REQ-008, DOC-REQ-003).
- [ ] T034 [P] [US3] Refresh Tailwind/CSS assets (`api_service/static/task_dashboard/dashboard.css`, `tailwind.config.cjs`) for dropzone + gallery states, then rebuild via `npm run dashboard:css:min` (DOC-REQ-008).

**Checkpoint**: Dashboard users see realtime validation, previews, and secure downloads for every attachment.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, instrumentation visibility, and final validation steps shared across stories.

- [ ] T035 Update `docs/TasksImageSystem.md` and `specs/037-tasks-image-phase1/quickstart.md` with the final API routes, worker prepare expectations, and dashboard UX cues (DOC-REQ-001, DOC-REQ-005, DOC-REQ-008).
- [ ] T036 [P] Add attachment/vision metric references to `moonmind/agents/codex_worker/metrics.py` docs or `docs/observability.md`, ensuring operators know the new `task.attachments.*` counters (DOC-REQ-010).
- [ ] T037 Run `./tools/test_unit.sh tests/unit/api/routers/test_agent_queue.py tests/unit/workflows/agent_queue/test_service_attachments.py tests/unit/agents/codex_worker/test_worker.py tests/unit/task_dashboard/test_dashboard_attachments.py` and capture results under `var/artifacts/workflow_runs/` (DOC-REQ-011).
- [ ] T038 Run `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` to validate the end-to-end queue → worker → dashboard attachment flow (DOC-REQ-011).

---

## Dependencies & Execution Order

- Setup (Phase 1) → Foundational (Phase 2): configuration + scaffolding must exist before service code compiles.
- User Story 1 depends on Phases 1-2 completing so queue/service/storage helpers are stable.
- User Story 2 depends on Phases 1-3; workers cannot download until APIs + storage exist.
- User Story 3 depends on Phase 3 (API responses + metadata) but can run in parallel with Phase 4 once the POST/list/download endpoints are stable.
- Polish tasks run last after all user stories pass their checkpoints.

## Parallel Opportunities

- Within Phase 2, tasks T004–T007 touch disjoint modules and can proceed concurrently.
- After Phase 2, US1 API work (T011–T016) can run while US1 tests (T008–T010) execute on stubs.
- Once US1 endpoints stabilize, US2 worker downloads (T021–T024) and vision/prompt work (T025–T028) can happen in parallel.
- US3 frontend tasks (T031–T034) can start immediately after US1 exposes attachment metadata, independent from worker changes.
- Test tasks marked [P] across all phases can execute concurrently on CI agents.

## Implementation Strategy

1. Deliver MVP by finishing Phases 1–3 and verifying `POST /api/queue/jobs/with-attachments` end-to-end (DOC-REQ-001/002/003/004/009/010).
2. Layer in worker automation (Phase 4) so every runtime consumes manifests + vision context (DOC-REQ-005/006/007/009/010).
3. Ship dashboard UX (Phase 5) to unlock customer-facing previews (DOC-REQ-008).
4. Close with Phase 6 polish + validation to document flows and prove coverage (DOC-REQ-011).
5. At each checkpoint, confirm attachments remain confined to `inputs/` artifacts and prompts always mention manifests before proceeding to the next phase.
