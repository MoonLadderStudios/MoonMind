# Feature Specification: Resubmit Terminal Tasks

**Feature Branch**: `043-resubmit-terminal-tasks`  
**Created**: 2026-03-01  
**Status**: Draft  
**Input**: User description: "Extend MoonMind queued-task editing with a resubmit edited copy flow for failed/cancelled tasks, including API/service/UI/tests/docs updates and preserving all listed constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Document-Derived Requirements

- **DOC-REQ-001** (`docs/TaskEditingSystem.md` §12): Terminal-task resubmit mode must preserve source history, reuse prefill behavior, and clearly communicate the no-attachment-copy v1 policy.
- **DOC-REQ-002** (`docs/TaskQueueSystem.md` §3.1): Queue API surface must include first-class `POST /api/queue/jobs/{jobId}/resubmit` semantics with terminal-task eligibility and audit linkage.
- **DOC-REQ-003** (`docs/TaskUiArchitecture.md` §5.4): Thin dashboard create/edit route reuse must resolve edit vs resubmit mode correctly and apply mode-specific submit behavior and redirects.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resubmit a Failed or Cancelled Task (Priority: P1)

As a queue operator, I need to take a failed or cancelled task, adjust it, and submit it again as a new job without losing the original job history.

**Why this priority**: This is the primary user value and directly reduces time to retry corrected work.

**Independent Test**: Open a failed or cancelled task detail, start resubmit flow, change task fields, submit, and verify a new job is created while source job remains unchanged.

**Acceptance Scenarios**:

1. **Given** a `type="task"` job in `failed` state, **When** I choose Resubmit and submit valid edits, **Then** a new queued job is created and I am redirected to the new job detail.
2. **Given** a `type="task"` job in `cancelled` state, **When** I choose Resubmit, **Then** the create form is prefilled from the source job and the primary action is labeled Resubmit.

---

### User Story 2 - Enforce Safe Eligibility and Ownership (Priority: P2)

As a platform maintainer, I need strict eligibility and authorization checks so resubmit cannot rewrite active jobs or bypass ownership boundaries.

**Why this priority**: Safety and ownership enforcement protect queue correctness and user isolation.

**Independent Test**: Attempt resubmit for queued/running/non-task jobs and non-owner users; verify all are rejected with documented error semantics.

**Acceptance Scenarios**:

1. **Given** a queued or running job, **When** resubmit is attempted, **Then** the request is rejected as ineligible for terminal-job resubmission.
2. **Given** a failed task owned by another user, **When** I attempt resubmit, **Then** the request is rejected as not authorized.

---

### User Story 3 - Preserve Audit History and Communicate Attachment Limits (Priority: P3)

As an operator, I need clear traceability between source and replacement jobs and clear attachment expectations so retries remain understandable and predictable.

**Why this priority**: Audit linkage and explicit limits prevent confusion during incident handling.

**Independent Test**: Resubmit successfully and verify source/new audit linkage exists; verify UI informs users that attachments are not copied in v1.

**Acceptance Scenarios**:

1. **Given** a successful resubmit, **When** job events are inspected, **Then** the source job records a resubmission event that references the new job identifier.
2. **Given** resubmit mode is active, **When** the form is shown, **Then** users see that source attachments are not copied and must be re-uploaded via supported create flows.

### Edge Cases

- Source job transitions between detail load and submit due to concurrent lifecycle changes; submit must fail safely with state conflict behavior.
- Source job is terminal but not `failed` or `cancelled` (for example `dead_letter`); v1 must reject unless explicitly enabled later.
- Source job type is not `task`; resubmit must be rejected even if status is terminal.
- Source job is missing or deleted between navigation and submit; UI must show recoverable error and allow return to source list/detail.
- Resubmit payload fails normalization or runtime gate checks; no new job should be created.
- Resubmit request includes optional note omitted or empty; operation should still succeed without requiring note.
- Resubmit from a source job with attachments must not silently copy inputs; user-facing messaging must state re-upload requirements.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve existing in-place edit behavior only for `type="task"` jobs that are `queued` and never started; the new resubmit flow must not change this edit eligibility contract.
- **FR-002**: The system MUST classify jobs as resubmittable only when `type="task"` and `status` is either `failed` or `cancelled`.
- **FR-003**: The queue job detail experience MUST surface a Resubmit action for resubmittable jobs and MUST NOT show this action for non-resubmittable jobs.
- **FR-004**: Entering resubmit mode MUST reuse the existing create route and prefill by reading the source job via queue job detail API.
- **FR-005**: Resubmit form submit behavior MUST call a dedicated queue resubmit endpoint on the source job and MUST create a new job rather than mutating the source job.
- **FR-006**: The queue API MUST provide an authenticated endpoint `POST /api/queue/jobs/{jobId}/resubmit` that returns the new `JobModel` with `201` on success. (Maps: DOC-REQ-002)
- **FR-007**: Resubmit request payload MUST mirror create/update task envelope fields (`type`, `priority`, `maxAttempts`, `affinityKey`, `payload`) and accept an optional `note`.
- **FR-008**: Resubmit service logic MUST enforce owner authorization parity with queued edit semantics (`created_by_user_id` or `requested_by_user_id` ownership model).
- **FR-009**: Resubmit service logic MUST validate source eligibility (`type="task"` and terminal status in allowed set) and reject queued/running/ineligible source jobs.
- **FR-010**: Resubmit service logic MUST normalize task payload using the same create/update normalization path so runtime gating and validation behavior remain consistent.
- **FR-011**: Resubmit service logic MUST append audit events linking jobs, including source event `Job resubmitted` with actor and new job reference; new job linkage event `Job resubmitted from` SHOULD also be recorded.
- **FR-012**: Dashboard runtime view-model configuration MUST expose a resubmit endpoint template under queue sources so the thin dashboard can resolve request URLs at runtime.
- **FR-013**: The create/edit form implementation MUST support mode-specific submit semantics: update mode uses queue update endpoint; resubmit mode uses queue resubmit endpoint. (Maps: DOC-REQ-003)
- **FR-014**: Resubmit cancel navigation MUST return to source job detail, and successful resubmit MUST redirect to the new job detail with a success message that identifies the source job. (Maps: DOC-REQ-003)
- **FR-015**: Resubmit v1 MUST NOT copy source attachments to the new job and MUST present explicit user guidance that attachments must be re-uploaded or retrieved from the prior run. (Maps: DOC-REQ-001)
- **FR-016**: Validation coverage MUST include service tests (success plus reject cases and audit events), router tests (status/error mappings), and dashboard tests (mode parsing/inference, CTA labeling/gating, endpoint selection).
- **FR-017**: Documentation MUST be updated to reflect the resubmit terminal-task contract in task editing, task queue API surface, and task UI architecture references. (Maps: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)
- **FR-018**: Completion criteria for this feature MUST include production runtime code changes plus passing validation tests; docs-only or spec-only changes do not satisfy the feature objective.

### Key Entities *(include if feature involves data)*

- **SourceJob**: Existing terminal queue task selected for retry, which remains immutable during resubmit.
- **ResubmitRequest**: Create-like envelope plus optional note that defines the new job configuration derived from a source job.
- **ResubmittedJob**: Newly created queued job produced from a terminal source and linked for audit traceability.
- **ResubmitAuditEvent**: Queue event payload capturing actor, source/new job linkage, optional note, and changed field summary.
- **QueueFormModeState**: UI state that distinguishes create, update, and resubmit behavior while reusing one submit form.

### Assumptions & Dependencies

- Existing queue create and queued-update paths already provide normalization, ownership semantics, and queue event append infrastructure that resubmit can reuse.
- Queue detail API remains the source of truth for prefill values in both update and resubmit modes.
- Thin dashboard runtime config injection is the supported pattern for queue endpoint templates and remains available for the new resubmit endpoint template.
- Existing queue status model includes `failed` and `cancelled` terminal states and may add `dead_letter` eligibility in a later phase.

### Non-Goals

- Mutating failed/cancelled source job records in place.
- Expanding resubmit eligibility to non-task job types in v1.
- Automatic source attachment cloning in v1 resubmit flow.
- Replacing existing queued-edit behavior for eligible queued + never-started tasks.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of validated success cases, resubmitting an eligible failed/cancelled task creates a distinct new job identifier while preserving source job payload/history.
- **SC-002**: In 100% of validated ineligible-state/type scenarios (queued, running, wrong type, non-owner), resubmit requests are rejected with documented error outcomes and no new job created.
- **SC-003**: In 100% of validated dashboard resubmit flows, UI shows Resubmit only for eligible source jobs, selects the resubmit endpoint on submit, and redirects to the new job detail on success.
- **SC-004**: In 100% of validated successful resubmits, queue events provide source-to-new linkage metadata sufficient to trace retry lineage.
- **SC-005**: Validation suites for service, router, and dashboard resubmit behavior pass through `./tools/test_unit.sh`.
- **SC-006**: Delivered implementation includes production runtime changes across API/service/UI plus documentation updates, not docs/spec-only output.
