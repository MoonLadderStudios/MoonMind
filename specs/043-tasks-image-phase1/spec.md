# Feature Specification: Tasks Image Attachments Phase 1 (Runtime Alignment)

**Feature Branch**: `037-tasks-image-phase1`  
**Created**: 2026-02-23  
**Updated**: 2026-03-01  
**Status**: Draft  
**Input**: User description: "Update `specs/037-tasks-image-phase1` to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit Task With Image Attachments (Priority: P1)

As a dashboard user, I can submit a queue task with supported image attachments and trust that the job is not available for claim until attachments are persisted.

**Why this priority**: Reliable attachment ingestion is the entry point for all downstream image-aware task execution.

**Independent Test**: Submit queue tasks with supported image files and verify validation, storage, and claimability gating behave correctly.

**Acceptance Scenarios**:

1. **Given** a valid queue task payload with supported image files, **When** the task is created, **Then** attachments are persisted and indexed before the task becomes claimable.
2. **Given** unsupported or invalid attachment payloads, **When** create is attempted, **Then** the API rejects the request with validation errors and no task is exposed for claim.

---

### User Story 2 - Worker Consumes Attachments During Prepare (Priority: P1)

As a worker runtime, I need attachment files and generated context artifacts prepared before execution so runtime instructions can use them deterministically.

**Why this priority**: Attachment-aware execution requires deterministic local files, metadata, and prompt context.

**Independent Test**: Claim a task with attachments and verify prepare downloads, artifact generation, event emission, and runtime instruction composition.

**Acceptance Scenarios**:

1. **Given** a claimed task with persisted attachments, **When** prepare runs, **Then** the worker downloads files into `.moonmind/inputs`, writes a manifest, and renders image context.
2. **Given** prepare succeeds, **When** execution instructions are composed, **Then** an `INPUT ATTACHMENTS` section is injected before `WORKSPACE` and references generated artifacts.

---

### User Story 3 - Review Attachments in Queue Detail (Priority: P2)

As a task owner, I can inspect and download submitted attachments from the queue detail view, including image previews for supported content types.

**Why this priority**: Visibility and retrieval of submitted inputs are needed for debugging, trust, and operator workflows.

**Independent Test**: Open queue detail for an attachment-enabled task and verify attachment listing, previews, and download behavior.

**Acceptance Scenarios**:

1. **Given** a queue task owned by the current user, **When** queue detail loads, **Then** attachment metadata and preview/download affordances are shown.
2. **Given** an unauthorized or non-owner context, **When** attachment endpoints are called, **Then** access is denied by ownership/claim checks.

---

### Edge Cases

- Attachment payload exceeds max file size, count, or total size limits.
- Content-type header and sniffed signature do not match supported image formats.
- Worker prepare download fails mid-stream; partial local artifacts must not produce successful context generation.
- Worker or user endpoints are requested for artifacts outside the reserved attachment namespace.
- Request includes `captions` hints before caption persistence support exists; API must fail-fast with explicit error.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/TaskQueueSystem.md`, attachment API section; `docs/TasksImageSystem.md`, API Design): Queue task creation supports multipart image attachments and keeps jobs non-claimable until attachment persistence completes.
- **DOC-REQ-002** (Source: `docs/TaskQueueSystem.md`, attachment validation/storage section; `docs/TasksImageSystem.md`, Validation Rules): Attachment ingestion enforces supported image types, signature sniffing, and configured size/count limits.
- **DOC-REQ-003** (Source: `docs/TaskQueueSystem.md`, reserved `inputs/` namespace section; `docs/TasksImageSystem.md`, Data Model/Storage): Attachment artifacts persist under the reserved `inputs/` namespace with deterministic metadata, and worker uploads cannot write into that namespace.
- **DOC-REQ-004** (Source: `docs/TaskQueueSystem.md`, attachment list/download section; `docs/TasksImageSystem.md`, API Design + Authorization): User and worker attachment list/download APIs enforce owner/active-claim authorization boundaries.
- **DOC-REQ-005** (Source: `docs/TasksImageSystem.md`, Worker Changes): Worker prepare downloads attachments, writes manifest/context artifacts, records task context attachment summary data, and emits lifecycle events.
- **DOC-REQ-006** (Source: `docs/TasksImageSystem.md`, Prompt Injection): Runtime instruction composition injects an `INPUT ATTACHMENTS` block before `WORKSPACE`.
- **DOC-REQ-007** (Source: `docs/TasksImageSystem.md`, Image Context Generation): Vision context generation remains toggleable via runtime settings while preserving deterministic artifact output in prepare flows.
- **DOC-REQ-008** (Source: `docs/TaskQueueSystem.md`, dashboard attachment references; `docs/TasksImageSystem.md`, Mission Control UI Changes): Dashboard create/detail experiences support attachment upload plus preview/download visibility.
- **DOC-REQ-009** (Source: `docs/TasksImageSystem.md`, resolved Phase 1 scope): `captions` input remains explicitly deferred in Phase 1 and unsupported payloads fail-fast.
- **DOC-REQ-010** (Source: task objective provided in this feature request): Completion deliverables include production runtime code changes; docs/spec-only output is insufficient.
- **DOC-REQ-011** (Source: task objective provided in this feature request): Completion deliverables include validation tests executed via `./tools/test_unit.sh`.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`): Queue task creation MUST support multipart image attachments and MUST keep tasks non-claimable until attachment persistence completes.
- **FR-002** (`DOC-REQ-002`): Attachment ingestion MUST enforce supported image type checks, signature sniffing, per-file limits, total-size limits, and max-count limits.
- **FR-003** (`DOC-REQ-003`): Persisted attachment artifacts MUST remain under reserved `inputs/<attachment-id>/<sanitized-filename>` paths with digest, byte-size, and media-type metadata.
- **FR-004** (`DOC-REQ-003`): Worker artifact upload APIs MUST reject attempts to write into the reserved `inputs/` attachment namespace.
- **FR-005** (`DOC-REQ-004`): User attachment list/download APIs MUST expose only attachment artifacts and MUST enforce task ownership authorization.
- **FR-006** (`DOC-REQ-004`): Worker attachment list/download APIs MUST expose only attachment artifacts and MUST enforce active claim ownership for the claiming worker.
- **FR-007** (`DOC-REQ-005`, `DOC-REQ-007`): Worker prepare MUST download attachments to `.moonmind/inputs`, generate `.moonmind/attachments_manifest.json`, generate `.moonmind/vision/image_context.md`, and record attachment summary data in `task_context.json`.
- **FR-008** (`DOC-REQ-005`): Worker prepare MUST emit attachment lifecycle events for download start, download finish, and context generation completion.
- **FR-009** (`DOC-REQ-006`): Runtime instruction composition MUST inject an `INPUT ATTACHMENTS` block before `WORKSPACE`, including references to manifest path, local attachment directory, and generated context.
- **FR-010** (`DOC-REQ-008`): Dashboard runtime config and UI MUST support attachment upload at task create time plus attachment preview/download in queue detail.
- **FR-011** (`DOC-REQ-009`): The API MUST fail-fast on unsupported `captions` attachment input until caption persistence is explicitly implemented in a later phase.
- **FR-012** (`DOC-REQ-010`): Completion deliverables MUST include production runtime code changes across API, worker, and dashboard surfaces; docs-only or spec-only updates are insufficient.
- **FR-013** (`DOC-REQ-011`): Completion deliverables MUST include validation tests run via `./tools/test_unit.sh` that cover attachment ingestion/authorization, worker prepare artifact generation + prompt ordering, and dashboard attachment visibility flows.

### Key Entities *(include if feature involves data)*

- **Attachment Artifact**: Job artifact persisted in the reserved `inputs/` namespace with sanitized filename, digest, size, and content type.
- **Attachment Manifest**: `.moonmind/attachments_manifest.json` generated during worker prepare with one entry per downloaded attachment.
- **Vision Context Document**: `.moonmind/vision/image_context.md` generated during worker prepare for runtime prompt guidance.
- **Attachment-Aware Task Context**: `task_context.json` enrichment that summarizes attachment count and generated artifact paths.

### Assumptions & Dependencies

- Existing queue attachment endpoints and namespace reservation rules remain the canonical integration points.
- Worker prepare has access to attachment list/download endpoints for claimed jobs.
- Dashboard queue create/detail screens remain the Phase 1 UX surfaces for attachment workflows.
- MoonMind compatibility policy remains in force: no hidden compatibility transforms affecting runtime model/effort/publish semantics.

### Non-Goals

- Persisting user-provided `captions` hints from create payloads.
- Native multimodal provider message APIs for direct image message transport.
- Provider-backed OCR/caption generation beyond deterministic file/context scaffolding in this phase.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Attachment-enabled task creation enforces validation and persistence gating with zero claimable tasks emitted before attachments persist in validation coverage.
- **SC-002**: Worker prepare deterministically produces local downloads, manifest, and vision context for attachment-enabled tasks in automated tests.
- **SC-003**: Runtime instructions include `INPUT ATTACHMENTS` before `WORKSPACE` for attachment-enabled tasks in automated tests.
- **SC-004**: Dashboard create/detail flows expose attachment upload and preview/download behaviors for authorized users while preventing unauthorized access.
- **SC-005**: Required validation coverage passes through `./tools/test_unit.sh` after runtime implementation updates.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode coverage is explicit in `tasks.md` with production runtime implementation tasks and required validation tasks listed under Prompt B scope controls.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for `DOC-REQ-001` through `DOC-REQ-011` in `contracts/requirements-traceability.md`.
- Cross-artifact alignment is explicit: runtime implementation intent in this spec, runtime constraints in `plan.md`, and execution/validation sequencing in `tasks.md` are consistent.

### MEDIUM/LOW remediation status

- Wording has been normalized across artifacts to keep runtime-first constraints deterministic and avoid docs-only interpretation drift.

### Residual risks

- The feature spans API, queue service/storage, worker prepare, and dashboard surfaces, so integration defects can still appear until implementation tasks and unit validation tasks are executed.
- Final validation evidence remains open until `./tools/test_unit.sh` execution is recorded in `quickstart.md`.

## Security & Compatibility Guardrails

- Preserve reserved attachment namespace rules and authorization boundaries; do not widen access scope.
- Do not introduce compatibility transforms that alter runtime model identifiers, effort values, queue semantics, or publish behavior.
- Keep `.moonmind/` artifacts local to runtime execution and excluded from repository tracking.
