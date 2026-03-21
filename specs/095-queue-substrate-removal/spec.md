# Feature Specification: Queue Substrate Removal (Phase 1)

**Feature Branch**: `095-queue-substrate-removal`
**Created**: 2026-03-21
**Status**: Draft
**Input**: User description: "Implement Phase 1 of docs/tmp/SingleSubstrateMigration.md — ensure every queue-backed submission and feature has a Temporal equivalent so the legacy queue execution substrate can be deprecated"

## Source Document Requirements

| Requirement ID | Source | Requirement Summary |
|---------------|--------|---------------------|
| DOC-REQ-001 | SingleSubstrateMigration.md §Phase 1, item 1.1 | System must audit all queue-only features (attachments, live sessions, operator messages, task control, events/SSE, skills list) |
| DOC-REQ-002 | SingleSubstrateMigration.md §Phase 1, item 1.2 | Each queue feature must be mapped to its Temporal equivalent or explicitly deferred |
| DOC-REQ-003 | SingleSubstrateMigration.md §Phase 1, item 1.3 | Temporal submit must support all current submit form fields: runtime, model, effort, repository, publish mode, attachments |
| DOC-REQ-004 | SingleSubstrateMigration.md §Phase 1, item 1.4 | Manifest submission via `/api/queue/jobs?type=manifest` must route to `MoonMind.ManifestIngest` Temporal workflow |
| DOC-REQ-005 | SingleSubstrateMigration.md §Phase 1, item 1.5 | Recurring tasks (`/api/recurring-tasks`) must use Temporal Schedules, not the queue |
| DOC-REQ-006 | SingleSubstrateMigration.md §Phase 1, item 1.6 | Step templates must work against the Temporal execution path |
| DOC-REQ-007 | SingleSubstrateMigration.md §Phase 1, Gate | No user-facing action requires the queue path; queue router can be deprecated without feature loss |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator Submits a Task via Mission Control (Priority: P1)

An operator submits a new task through the Mission Control dashboard using any runtime (Codex, Gemini CLI, Claude, or Jules) with optional model, effort, repository, and publish mode settings. The task creates a Temporal workflow execution and appears in the task list immediately.

**Why this priority**: This is the primary user action. Every task submission must go through Temporal to satisfy the "one execution model" goal.

**Independent Test**: Submit a task from the dashboard and verify it appears in the Temporal-backed task list with correct metadata.

**Acceptance Scenarios**:

1. **Given** an operator on the Mission Control dashboard, **When** they submit a task with runtime=codex, model=o3, effort=high, repository=org/repo, publishMode=pr, **Then** a `MoonMind.Run` Temporal workflow execution is created with all parameters preserved in the execution payload.
2. **Given** the Temporal dashboard submit is enabled (default), **When** a task is submitted via the `/api/queue/jobs` compatibility endpoint, **Then** the system routes it to the Temporal execution path and returns a compatibility response.
3. **Given** an operator submits a manifest-type job, **When** the job type is `manifest`, **Then** the system routes to `MoonMind.ManifestIngest` workflow via `_create_execution_from_manifest_request`.

---

### User Story 2 - Operator Monitors Task Progress and Takes Actions (Priority: P1)

An operator views their running tasks, sees real-time status updates, and performs actions (cancel, edit, rerun, approve) — all through Temporal-backed APIs.

**Why this priority**: Monitoring and control are the core Mission Control features. Actions must work without the queue backend.

**Independent Test**: Start a task, observe status updates, cancel it, and verify the terminal state is reflected.

**Acceptance Scenarios**:

1. **Given** a running Temporal-backed task, **When** the operator views the task list, **Then** it shows the correct normalized status derived from `mm_state` (e.g., `executing` → `running`).
2. **Given** a running task, **When** the operator clicks Cancel, **Then** the cancellation goes through the Temporal cancel path and the task transitions to `cancelled`.
3. **Given** a completed task, **When** the operator clicks Rerun, **Then** a Continue-As-New rerun is triggered through the Temporal update path and the same `workflowId` receives a new `runId`.

---

### User Story 3 - Recurring Task Scheduling via Temporal (Priority: P1)

An operator creates, views, and manages recurring task schedules. All schedule operations use Temporal Schedules as the execution backend.

**Why this priority**: Recurring tasks must not depend on the queue backend.

**Independent Test**: Create a recurring task, verify it appears in the schedules list, run it manually, and confirm execution creates a Temporal workflow.

**Acceptance Scenarios**:

1. **Given** an operator on the Schedules page, **When** they create a new recurring task with a cron expression, **Then** a Temporal Schedule is created and the schedule appears in the list.
2. **Given** an existing recurring schedule, **When** the operator clicks "Run Now", **Then** a `MoonMind.Run` Temporal workflow execution is started immediately.

---

### User Story 4 - Attachment Upload on Task Submission (Priority: P2)

An operator can upload file attachments (images) when submitting a task. Attachments are stored and accessible through the artifact system.

**Why this priority**: Attachments are an important but secondary feature that bridges the queue attachment system to the Temporal artifact system.

**Independent Test**: Submit a task with an image attachment and verify the attachment is accessible from the task detail page.

**Acceptance Scenarios**:

1. **Given** an operator submitting a new task, **When** they attach a PNG image, **Then** the attachment is stored via the artifact system and linked to the workflow execution.
2. **Given** a completed task with attachments, **When** the operator views task details, **Then** the attachments are downloadable through the Temporal artifact APIs.

---

### User Story 5 - Step Templates Applied to Temporal Tasks (Priority: P2)

An operator can apply saved step templates when creating a new task. The template expands into task parameters that are passed to the Temporal execution path.

**Why this priority**: Templates are a convenience feature that must work with Temporal, but aren't blocking for queue deprecation.

**Independent Test**: Apply a step template and verify the expanded parameters are correctly passed to the Temporal workflow.

**Acceptance Scenarios**:

1. **Given** an operator using a saved step template, **When** they select the template and submit, **Then** the template parameters expand into a Temporal execution request with the correct runtime, instructions, and publish settings.

---

### Edge Cases

- What happens when `temporal_dashboard.submit_enabled` is false? System should fail fast with a clear error rather than silently routing to the legacy queue.
- How does the system handle in-flight queue jobs during migration? Existing queue jobs must be allowed to complete but new jobs should not be created.
- What happens to queue-specific features with no Temporal equivalent (worker tokens, claim/heartbeat/complete lifecycle)? These are worker-facing internals replaced by Temporal task queue polling and should be documented as deprecated.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST route all new task submissions to Temporal workflow executions, regardless of whether they arrive through `/api/queue/jobs` or `/api/executions`. [DOC-REQ-003, DOC-REQ-007]
- **FR-002**: System MUST route manifest-type submissions to `MoonMind.ManifestIngest` Temporal workflow. [DOC-REQ-004]
- **FR-003**: System MUST support all current submit form fields (runtime, model, effort, repository, publish mode) in the Temporal execution path. [DOC-REQ-003]
- **FR-004**: System MUST support file attachment uploads on the Temporal execution path, storing them as Temporal artifacts. [DOC-REQ-001, DOC-REQ-002]
- **FR-005**: Recurring tasks MUST create Temporal Schedules and start Temporal workflow executions, not queue jobs. [DOC-REQ-005]
- **FR-006**: Step templates MUST expand correctly when used with the Temporal execution path. [DOC-REQ-006]
- **FR-007**: The queue-backed worker lifecycle (claim, heartbeat, complete, fail, recover) is NOT required on the Temporal path, as Temporal workers poll native task queues. Document as deprecated. [DOC-REQ-002]
- **FR-008**: Queue SSE events (`/api/queue/jobs/{id}/events/stream`) are NOT required on the Temporal path, as Temporal provides its own execution state and the dashboard polls the Temporal APIs. Document as deprecated. [DOC-REQ-001, DOC-REQ-002]
- **FR-009**: Live session support (`/api/queue/jobs/{id}/live-session`) is NOT required on the Temporal path in Phase 1 — the Temporal path already provides live session support through `/api/task-runs/{id}/live-session`. Document queue endpoint as deprecated. [DOC-REQ-001, DOC-REQ-002]
- **FR-010**: Operator messages (`/api/queue/jobs/{id}/operator-messages`) have no Temporal equivalent yet. Defer to future implementation. [DOC-REQ-002]
- **FR-011**: Worker tokens and runtime capabilities are internal queue infrastructure. Document as deprecated. [DOC-REQ-001, DOC-REQ-002]
- **FR-012**: System MUST produce a feature audit report documenting the Temporal equivalent (or deferral status) for every queue API endpoint. [DOC-REQ-001, DOC-REQ-002]
- **FR-013**: When `temporal_dashboard.submit_enabled` would be false, the system SHOULD fail fast with an explicit error rather than falling back to queue submission. [DOC-REQ-007]

### Key Entities

- **Temporal Workflow Execution**: The unified task execution primitive replacing queue jobs. Identified by `workflowId`.
- **Temporal Artifact**: Replaces queue-based attachment storage. Managed through the artifact system with presigned upload/download.
- **Temporal Schedule**: Replaces queue-backed recurring task execution. Managed through the workflow scheduling guide.
- **Queue API Endpoint (legacy)**: Any `/api/queue/*` endpoint that serves the old queue polling model. Candidates for deprecation.

## Assumptions

- Temporal dashboard submit is enabled by default in production (validated: `submit_enabled` = True).
- Task routing already defaults to `"temporal"` for both runs and manifests (validated: `routing.py` confirms this).
- The queue `create_job` endpoint already delegates to Temporal for both run and manifest types (validated: `agent_queue.py` line 779-795).
- Recurring tasks router already creates Temporal Schedules (needs validation but is expected based on spec 049).
- No production systems are running queue-based workers that poll `/api/queue/jobs/claim`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of task submissions through Mission Control create Temporal workflow executions, with zero queue job creation.
- **SC-002**: All submit form fields (runtime, model, effort, repository, publish mode) are preserved in Temporal execution payloads with no data loss.
- **SC-003**: Manifest submissions create `MoonMind.ManifestIngest` workflow executions, verified by existence of `entry=manifest` in the execution metadata.
- **SC-004**: A complete feature audit report exists documenting the Temporal equivalent or deferral status for every queue API endpoint.
- **SC-005**: Recurring tasks create Temporal Schedules that produce Temporal workflow executions, not queue jobs.
- **SC-006**: Step templates produce correct Temporal execution parameters when applied.
- **SC-007**: Attachment uploads on task submission result in Temporal artifact records linked to the workflow execution.
