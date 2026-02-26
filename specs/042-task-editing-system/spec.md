# Feature Specification: Task Editing System

**Feature Branch**: `042-task-editing-system`  
**Created**: 2026-02-26  
**Status**: Draft  
**Input**: User description: "Implement the Task Editing System described in docs/TaskEditingSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.  
**Source Document**: `docs/TaskEditingSystem.md` (Last Updated: 2026-02-25)

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/TaskEditingSystem.md` §1, §2 Goals 1 | Queued task jobs can be edited only before execution starts. |
| DOC-REQ-002 | `docs/TaskEditingSystem.md` §2 Goal 2 | Editing must update the existing queued job in place and preserve the same job ID/correlation. |
| DOC-REQ-003 | `docs/TaskEditingSystem.md` §2 Goal 3, §3.1 | Queue detail must expose an Edit entry point for eligible jobs, with list-level edit action optional for v1. |
| DOC-REQ-004 | `docs/TaskEditingSystem.md` §3.2 | Edit flow must reuse `/tasks/queue/new` via `editJobId`, prefill from job detail, show Update CTA, and return to detail on cancel. |
| DOC-REQ-005 | `docs/TaskEditingSystem.md` §3.2 step 5, §4.2, §6.2 | Update submit must use the same create envelope plus optimistic concurrency token `expectedUpdatedAt`. |
| DOC-REQ-006 | `docs/TaskEditingSystem.md` §4.1 | Provide `PUT /api/queue/jobs/{jobId}` under user auth; only queued and never-started jobs are editable. |
| DOC-REQ-007 | `docs/TaskEditingSystem.md` §4.2 | Update request contract mirrors create fields and supports optional `expectedUpdatedAt` plus optional `note` for audit event payload. |
| DOC-REQ-008 | `docs/TaskEditingSystem.md` §4.3 | Successful update returns the updated `JobModel` response. |
| DOC-REQ-009 | `docs/TaskEditingSystem.md` §4.4 | Errors must normalize to documented semantics for missing job, authorization, state conflict, payload validation, and runtime gate failures. |
| DOC-REQ-010 | `docs/TaskEditingSystem.md` §5.1 | Service update flow must lock row, authorize owner, enforce editability/type invariants, normalize payload, update mutable fields, append audit event, and commit. |
| DOC-REQ-011 | `docs/TaskEditingSystem.md` §5.2 | No new persistence schema is required; existing mutable columns and queue event mechanisms are reused. |
| DOC-REQ-012 | `docs/TaskEditingSystem.md` §6.1 | Worker claim vs update race must remain safe through row locking and state validation. |
| DOC-REQ-013 | `docs/TaskEditingSystem.md` §6.2 | Optimistic concurrency must prevent silent multi-tab overwrites when `expectedUpdatedAt` is supplied. |
| DOC-REQ-014 | `docs/TaskEditingSystem.md` §7, §8 | Dashboard runtime config and UI must support queue update endpoint and edit-mode behavior on the existing create route. |
| DOC-REQ-015 | `docs/TaskEditingSystem.md` §9 | Validation coverage must include service, router, and dashboard behavior for update flows and conflicts. |
| DOC-REQ-016 | `docs/TaskEditingSystem.md` §10 | v1 explicitly excludes attachment edits; attachment mutation is future scope only. |
| DOC-REQ-017 | `docs/TaskEditingSystem.md` §11 | Rollout is additive (no feature flag) and documentation surface must include the new queue update endpoint. |
| DOC-REQ-018 | Runtime scope guard from task objective | Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Edit an Eligible Queued Task (Priority: P1)

As a queue operator, I need to edit a queued task before it starts so I can correct instructions without canceling and recreating a new job.

**Why this priority**: Safe in-place correction of queued work is the core user value.

**Independent Test**: Open an eligible queued task detail page, launch Edit, update task fields, and confirm the same job ID reflects updated values.

**Acceptance Scenarios**:

1. **Given** a task job in `queued` state with no start timestamp, **When** I select Edit from job detail, **Then** the system opens edit mode on `/tasks/queue/new?editJobId=<jobId>` with prefilled values.
2. **Given** the form is opened in edit mode, **When** I submit valid changes, **Then** the system updates the existing queued job and returns to the updated job context.

---

### User Story 2 - Prevent Unsafe or Stale Updates (Priority: P2)

As an operator, I need update conflicts to be explicit so I never overwrite a job that already started or was modified elsewhere.

**Why this priority**: Correctness and race safety are required for trustworthy queue operations.

**Independent Test**: Execute concurrent claim/update and multi-tab edit scenarios; verify state and optimistic-concurrency conflicts are rejected with clear error outcomes.

**Acceptance Scenarios**:

1. **Given** a worker claims the job before my update commits, **When** I submit edit changes, **Then** the system rejects the update with a state conflict response.
2. **Given** another user action changes the job after I loaded edit mode, **When** I submit using stale `expectedUpdatedAt`, **Then** the system rejects with conflict instead of silently overwriting.

---

### User Story 3 - Preserve Auditability and Scope Boundaries (Priority: P3)

As a platform maintainer, I need each update recorded and non-goal scopes enforced so queue history remains explainable and v1 scope stays controlled.

**Why this priority**: Observability and bounded scope reduce operational ambiguity and regression risk.

**Independent Test**: Update an eligible job and verify an audit event is appended; attempt running-job/orchestrator/attachment edits and verify they remain unsupported in v1.

**Acceptance Scenarios**:

1. **Given** a successful job update, **When** I inspect queue events, **Then** a "Job updated" event includes actor context and change summary.
2. **Given** a running or already-started job, **When** an update is attempted, **Then** the system rejects the operation as out of editable state.

### Edge Cases

- Job transitions from queued to running between edit-page load and submit; update must fail safely.
- `expectedUpdatedAt` is provided but does not match current `updated_at`; update must fail with conflict semantics.
- Update request changes immutable attributes (such as job type); system must reject the request.
- Non-owner user attempts update on a user-owned job; authorization failure must be returned.
- Update request includes attachment mutation intent in v1; attachment content must remain unchanged.
- Runtime normalization rejects submitted payload (for example runtime disabled); update must return normalized client-facing error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow editing only for queue jobs where job type is task, status is queued, and the job has not started. (Maps: DOC-REQ-001)
- **FR-002**: The system MUST apply edits in place to the existing queued job and preserve the original job identifier. (Maps: DOC-REQ-002)
- **FR-003**: The dashboard MUST expose an Edit entry point on eligible queue job detail views, with optional queue-list edit affordance remaining non-blocking for v1. (Maps: DOC-REQ-003)
- **FR-004**: Entering edit mode MUST reuse `/tasks/queue/new` with `editJobId` (including the existing `/tasks/new` alias behavior), prefill current job values, show Update as primary action, and provide cancel navigation back to job detail. (Maps: DOC-REQ-004)
- **FR-005**: Update submissions MUST send the same logical envelope used by create plus optional `expectedUpdatedAt` concurrency input. (Maps: DOC-REQ-005, DOC-REQ-013)
- **FR-006**: The queue API MUST provide an authenticated update endpoint that permits updates only for queued, never-started jobs and returns the updated job model on success. (Maps: DOC-REQ-006, DOC-REQ-008)
- **FR-007**: The update request contract MUST mirror create fields and support optional `expectedUpdatedAt` and `note`, where `note` is retained in audit event context rather than persisted as a job column. (Maps: DOC-REQ-007)
- **FR-008**: Error behavior MUST expose normalized responses for not-found, not-authorized, state conflict, validation failure, and runtime gate failures. (Maps: DOC-REQ-009)
- **FR-009**: Update processing MUST perform row-lock retrieval, owner authorization, editability/type validation, payload normalization, mutable-field updates, timestamp refresh, audit-event append, and transactional commit. (Maps: DOC-REQ-010)
- **FR-010**: Delivery MUST reuse existing queue persistence schema and event append infrastructure without introducing new database tables for v1 task edits. (Maps: DOC-REQ-011)
- **FR-011**: Update behavior MUST remain race-safe with worker claim operations so only one terminal state transition wins and stale updates are rejected. (Maps: DOC-REQ-012)
- **FR-012**: Dashboard runtime configuration and front-end behavior MUST include queue-update endpoint templating and edit-mode request handling on the existing create route. (Maps: DOC-REQ-014)
- **FR-013**: v1 scope MUST exclude attachment mutation during edit flows, with attachment-edit support deferred to later phases. (Maps: DOC-REQ-016)
- **FR-014**: Rollout MUST be additive without feature-flag dependency and include API-surface documentation updates for the queue update endpoint. (Maps: DOC-REQ-017)
- **FR-015**: Required deliverables MUST include production runtime code changes across API/service/UI behavior; docs-only or spec-only changes do not satisfy completion. (Maps: DOC-REQ-018)
- **FR-016**: Required deliverables MUST include validation tests that cover service update rules, router status/error mapping, and dashboard edit-mode behavior. (Maps: DOC-REQ-015, DOC-REQ-018)

### Key Entities *(include if feature involves data)*

- **QueueJob**: Existing queued task record whose mutable fields (priority, payload, affinity key, max attempts, updated timestamp) are updated in place.
- **QueuedJobUpdateRequest**: Update payload carrying create-like job content plus optional optimistic-concurrency timestamp and optional audit note.
- **JobUpdateAuditEvent**: Queue event appended after successful edit, capturing actor identity, optional note, and changed-field summary.
- **EditSessionState**: UI-level state derived from fetched job detail, including editability flags and cached `updatedAt` token.

### Assumptions & Dependencies

- Existing queue owner-authorization semantics used by create/cancel flows are available for update flows.
- Queue repository row-lock capabilities and worker claim locking behavior remain unchanged and enforce mutual exclusion.
- Task payload normalization and runtime-gate validation used by create flows can be reused for update requests.
- Dashboard view model runtime config can safely expose one additional queue source endpoint template.

### Non-Goals

- Editing running or already-started jobs.
- Editing orchestrator runs.
- Editing or replacing attachments as part of v1 task edits.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For eligible task jobs, updates complete successfully while preserving the original job ID in 100% of passing test scenarios.
- **SC-002**: In ineligible-state scenarios (not queued or started), update attempts return documented conflict semantics in 100% of validation cases.
- **SC-003**: In stale-concurrency scenarios using mismatched `expectedUpdatedAt`, updates are rejected with conflict outcomes and no silent overwrite.
- **SC-004**: Dashboard edit mode prefill/update behavior executes successfully for eligible jobs and displays user-facing error handling for conflict, authorization, and validation failures.
- **SC-005**: Successful updates append an auditable queue event recording actor context and changed-field summary.
- **SC-006**: Validation tests for service, router, and dashboard update behavior pass via `./tools/test_unit.sh`.
