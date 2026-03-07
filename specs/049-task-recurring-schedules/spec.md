# Feature Specification: Task Recurring Schedules System

**Feature Branch**: `[041-task-recurring-schedules]`  
**Created**: 2026-02-24  
**Status**: Draft  
**Input**: User description: "Implement the Task Recurring Schedules System as described in docs/TaskRecurringSchedulesSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage Recurring Schedules (Priority: P1)

As an operator or schedule owner, I can create, update, enable, disable, and manually trigger recurring schedules so routine work runs without manual re-entry.

**Why this priority**: Without schedule management and trigger controls, there is no usable recurring scheduling feature.

**Independent Test**: Can be fully tested by creating schedules for each supported target type, toggling enabled state, invoking run-now, and confirming runs are created and visible with correct status.

**Acceptance Scenarios**:

1. **Given** a user with schedule-management permission, **When** they create a recurring schedule with cron, timezone, target, and policy, **Then** the schedule is persisted and shows the next run time.
2. **Given** an enabled schedule, **When** the user disables it, **Then** future automated dispatches stop until re-enabled.
3. **Given** an existing schedule, **When** the user selects run now, **Then** a manual run record is created and queued for dispatch.

---

### User Story 2 - Reliable Due-Run Dispatch (Priority: P2)

As the platform, I dispatch due schedule occurrences into normal queue jobs (task, manifest, housekeeping) with idempotent behavior so retries, restarts, and multi-instance scheduler operation do not create duplicate work.

**Why this priority**: Reliability and duplicate prevention are mandatory for safe automation.

**Independent Test**: Can be fully tested by running concurrent scheduler instances against the same due schedule set and asserting one run record per occurrence and one resulting queued job per occurrence.

**Acceptance Scenarios**:

1. **Given** a due enabled schedule, **When** scheduler polling runs, **Then** due occurrences are recorded and advanced to the next scheduled time.
2. **Given** a retry after uncertain dispatch outcome, **When** dispatch reruns with the same recurrence key, **Then** no duplicate queue job is created for that occurrence.
3. **Given** policy overlap mode `skip`, **When** a prior occurrence is still active, **Then** the new occurrence is recorded as skipped with a reason.

---

### User Story 3 - Observe Schedule History and Provenance (Priority: P3)

As an operator, I can review schedule run history, dispatch outcomes, and linked queue jobs to understand what ran, when, and why.

**Why this priority**: Operational trust requires history, status, and provenance.

**Independent Test**: Can be fully tested by executing schedule and manual runs, then verifying list/detail views show status history and links to queue job detail where available.

**Acceptance Scenarios**:

1. **Given** schedule runs with mixed outcomes, **When** a user opens schedule detail, **Then** run history shows scheduled time, trigger type, outcome, and associated queue job reference when present.
2. **Given** a queued run, **When** the user opens its linked queue job detail, **Then** recurrence metadata identifies definition, run, and scheduled occurrence.

---

### Edge Cases

- Cron occurrence lands in DST transitions (missing or repeated local clock times).
- Scheduler crashes after enqueue succeeds but before run row is marked enqueued.
- Multiple scheduler instances process due schedules simultaneously.
- A schedule becomes disabled while due rows are being created.
- Target references become invalid (for example, a manifest name no longer exists).
- Misfire exceeds grace window, requiring skip behavior based on policy.
- Catchup `all` requests more historical occurrences than the maximum backfill limit.
- Housekeeping target is configured while housekeeping worker is unavailable.

## Requirements *(mandatory)*

### Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary | Mapping |
| --- | --- | --- | --- |
| DOC-REQ-001 | `docs/TaskRecurringSchedulesSystem.md` §1 Purpose | System must provide dashboard-managed recurring schedules backed by persistent schedule definitions and a scheduler daemon. | FR-001, FR-003, FR-019 |
| DOC-REQ-002 | `docs/TaskRecurringSchedulesSystem.md` §1 Purpose | Scheduled executions must reuse the Agent Queue lifecycle model for task and manifest jobs and include support for housekeeping. | FR-007, FR-009, FR-010 |
| DOC-REQ-003 | `docs/TaskRecurringSchedulesSystem.md` §2 Goals | Users must be able to create, update, enable, disable, run now, and view history/next run for schedules. | FR-001, FR-018, FR-019 |
| DOC-REQ-004 | `docs/TaskRecurringSchedulesSystem.md` §2 Goals | Scheduler operation must be DB-backed and not require a separate broker-beat dependency for cadence execution. | FR-003 |
| DOC-REQ-005 | `docs/TaskRecurringSchedulesSystem.md` §2 Goals | Dispatch must be HA-safe and idempotent so retries and crashes do not duplicate scheduled queue jobs. | FR-005, FR-025 |
| DOC-REQ-006 | `docs/TaskRecurringSchedulesSystem.md` §2 Goals + §6.1 | Cron scheduling must be timezone-aware and DST-correct. | FR-016 |
| DOC-REQ-007 | `docs/TaskRecurringSchedulesSystem.md` §2 Non-Goals | Sub-minute precision and seconds-field cron are out of scope for v1. | FR-023 |
| DOC-REQ-008 | `docs/TaskRecurringSchedulesSystem.md` §2 Non-Goals | System should target effectively-once enqueue per occurrence, not exactly-once worker execution. | FR-024 |
| DOC-REQ-009 | `docs/TaskRecurringSchedulesSystem.md` §4.1 | Schedule definitions must persist core schedule metadata, target config, policy config, scope, ownership, and state fields. | FR-011 |
| DOC-REQ-010 | `docs/TaskRecurringSchedulesSystem.md` §4.1 | Due-scan and ownership listing access patterns require schedule indexes supporting enabled + next-run and owner + enabled queries. | FR-011 |
| DOC-REQ-011 | `docs/TaskRecurringSchedulesSystem.md` §4.2 | Scheduled/manual run rows must track occurrence identity, trigger type, dispatch outcome, attempts, backoff, queue linkage, and messages. | FR-012, FR-025 |
| DOC-REQ-012 | `docs/TaskRecurringSchedulesSystem.md` §4.2 Constraints | A unique definition+occurrence constraint must prevent duplicate run rows for the same scheduled occurrence. | FR-012, FR-005 |
| DOC-REQ-013 | `docs/TaskRecurringSchedulesSystem.md` §5.1 | Task target must support inline queue payload and template-backed expansion before enqueue. | FR-007, FR-008 |
| DOC-REQ-014 | `docs/TaskRecurringSchedulesSystem.md` §5.2 | Manifest target must submit manifest runs through existing manifest run behavior and preserve manifest run tracking updates. | FR-007, FR-009 |
| DOC-REQ-015 | `docs/TaskRecurringSchedulesSystem.md` §5.3 | Housekeeping must be supported as a first-class recurring target, with queue-backed processing as the preferred v1 approach. | FR-007, FR-010 |
| DOC-REQ-016 | `docs/TaskRecurringSchedulesSystem.md` §6.2 | Overlap, catchup, misfire grace, and jitter policy behavior must be supported and enforced during run generation/dispatch. | FR-014, FR-015 |
| DOC-REQ-017 | `docs/TaskRecurringSchedulesSystem.md` §7.3 | Scheduler must use a two-stage loop (schedule-to-runs, runs-to-jobs) with lock-safe concurrent processing and quick schedule commits. | FR-004 |
| DOC-REQ-018 | `docs/TaskRecurringSchedulesSystem.md` §7.3 + §7.5 | Dispatch failures require retry with backoff and idempotent reconciliation before issuing new enqueue attempts. | FR-025 |
| DOC-REQ-019 | `docs/TaskRecurringSchedulesSystem.md` §7.4 | Queue jobs created by recurrence must include recurrence provenance metadata (definition, run, scheduled occurrence). | FR-006 |
| DOC-REQ-020 | `docs/TaskRecurringSchedulesSystem.md` §8 | API must expose list/create/get/update/run-now/history endpoints with personal/global authorization constraints. | FR-001, FR-002, FR-018 |
| DOC-REQ-021 | `docs/TaskRecurringSchedulesSystem.md` §9 | Dashboard must provide schedules list/detail/create routes, run-now controls, target summaries, and history links to queue jobs. | FR-019 |
| DOC-REQ-022 | `docs/TaskRecurringSchedulesSystem.md` §10 | Schedule definitions must not store raw secrets; global schedule management requires operator-level authorization. | FR-002, FR-020 |
| DOC-REQ-023 | `docs/TaskRecurringSchedulesSystem.md` §11 | Delivery must include unit, integration, and dashboard contract validation coverage for recurring scheduling behavior. | FR-022 |
| DOC-REQ-024 | `docs/TaskRecurringSchedulesSystem.md` §12 Phase 5 (optional) | Optional import of manifest YAML `dataSources[].schedule` into recurring definitions may be deferred from core v1 runtime delivery. | FR-026 |

### Functional Requirements

- **FR-001**: System MUST provide recurring schedule CRUD, enable/disable controls, manual run-now trigger, and run history with next-run visibility.
- **FR-002**: System MUST enforce schedule scope authorization so personal schedules are owner-managed and global schedules require elevated operator permissions.
- **FR-003**: System MUST execute recurring scheduling from persisted database state via a dedicated scheduler process without requiring a separate broker-beat cadence subsystem.
- **FR-004**: System MUST separate schedule occurrence creation from external dispatch side effects using a two-stage loop that supports concurrent instances through lock-safe due-row and pending-run processing.
- **FR-005**: System MUST ensure one logical dispatch per schedule occurrence by combining unique occurrence identity with idempotent dispatch semantics.
- **FR-006**: System MUST attach recurrence provenance metadata to queue jobs so downstream consumers and UI can attribute each run to its definition and scheduled occurrence.
- **FR-007**: System MUST support recurring targets for queue task jobs, manifest runs, and housekeeping actions.
- **FR-008**: System MUST support template-backed recurring task targets by expanding template inputs into a concrete task payload before queue submission.
- **FR-009**: System MUST dispatch recurring manifest targets through the existing manifest run pathway so manifest run history remains consistent.
- **FR-010**: System MUST support queue-backed housekeeping dispatch as a first-class recurring target type.
- **FR-011**: System MUST persist schedule definitions with schedule expression, timezone, next-run state, last scheduling state, target configuration, policy configuration, ownership/scope, and versioned update state, with query performance support for due scans and owner listings.
- **FR-012**: System MUST persist recurring run records with occurrence time, trigger type, outcome state, attempts, retry timing, optional queue linkage, and unique definition+occurrence constraints.
- **FR-013**: System MUST expose schedule run outcome states that distinguish pending dispatch, queued dispatch, skipped occurrences, and dispatch failures.
- **FR-014**: System MUST enforce overlap policy modes that allow bounded concurrency or skip new occurrences when prior runs are still active.
- **FR-015**: System MUST enforce catchup, misfire-grace, jitter, and backfill-limit policies for overdue occurrences.
- **FR-016**: System MUST compute recurrence occurrences from cron + IANA timezone semantics with correct daylight-saving handling.
- **FR-017**: System MUST support operational scheduler configuration for poll interval, global batch size, and global backfill ceilings.
- **FR-018**: System MUST expose recurring schedule API operations for listing by scope, creating schedules, reading schedule detail, patching schedules, triggering manual runs, and listing recent runs.
- **FR-019**: System MUST expose dashboard schedule list/detail/create experiences with enable toggle, target summaries, run-now action, last/next run indicators, and run-history linkage to queue job details.
- **FR-020**: System MUST reject raw secret material in recurring schedule definitions and require references/indirection for secret-backed values.
- **FR-021**: Delivery MUST include production runtime code changes implementing recurring scheduling behavior (not docs/spec-only output).
- **FR-022**: Delivery MUST include automated validation tests covering recurrence computation/policy, dispatch/idempotency behavior, API contracts, and dashboard route/source integration.
- **FR-023**: v1 MUST constrain cron support to standard minute-level cadence (no required seconds-field scheduling).
- **FR-024**: System MUST document and preserve effectively-once enqueue behavior per occurrence while allowing at-least-once worker execution semantics.
- **FR-025**: System MUST retry dispatch failures with bounded backoff and reconcile uncertain outcomes using recurrence identity before creating a new external dispatch artifact.
- **FR-026**: v1 MUST treat import of manifest YAML `dataSources[].schedule` into recurring definitions as deferred optional scope and MUST NOT require it for core runtime recurring scheduling acceptance.

### Key Entities *(include if feature involves data)*

- **Recurring Task Definition**: A persisted schedule contract containing name, enablement, recurrence expression, timezone, scope/owner, target definition, policy controls, and next/last scheduling state.
- **Recurring Task Run**: A persisted occurrence record keyed by schedule definition and nominal schedule time, with trigger source, dispatch status, retry metadata, and queue linkage.
- **Recurring Target**: A typed execution destination for a recurring definition (`queue_task`, `queue_task_template`, `manifest_run`, `housekeeping`) plus required target-specific parameters.
- **Recurring Policy**: Behavioral controls for overlap, catchup, misfire grace, jitter, and bounded backfill.
- **Recurrence Metadata**: Provenance payload attached to dispatched jobs identifying the originating recurring definition, run record, and scheduled occurrence.

### Assumptions

- Existing queue job lifecycle, manifests run submission behavior, and dashboard shell patterns are retained and extended rather than replaced.
- Optional manifest YAML schedule import from source manifests is deferred unless explicitly prioritized later.
- Operator-role capability checks for global schedule management follow existing queue/operator authorization patterns.

### Runtime Semantics Commitments

- Recurring scheduling targets effectively-once enqueue semantics per `definition_id + scheduled_for` occurrence through unique run identity and idempotent reconciliation; downstream workers remain at-least-once execution.
- Manifest YAML `dataSources[].schedule` import remains deferred optional scope for v1 and is not required for recurring schedule runtime acceptance.

### Dependencies

- Queue job submission, claiming, and status infrastructure is available in runtime environments.
- Manifest run submission path remains available for recurring manifest targets.
- Persistent relational storage is available for schedule definitions and run history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of schedules created via API or dashboard show a computed next run and can be enabled/disabled without manual database intervention.
- **SC-002**: In concurrent scheduler tests, each definition+occurrence pair produces at most one queued dispatch artifact while preserving one run-history row.
- **SC-003**: At least 95% of due occurrences in test workloads are dispatched within one polling interval plus configured jitter window under normal load.
- **SC-004**: 100% of recurring run history entries in dashboard detail views expose outcome state and queue linkage when available.
- **SC-005**: Automated tests cover cron/timezone edge behavior, policy semantics, dispatch idempotency, and recurring API/dashboard contracts with passing results in CI/local unit test runs.
