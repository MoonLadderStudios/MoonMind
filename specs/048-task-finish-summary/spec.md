# Feature Specification: Task Finish Summary System

**Feature Branch**: `041-task-finish-summary`  
**Created**: 2026-02-24  
**Status**: Draft  
**Input**: User description: "Implement the Task Finish Summary System as described in docs/TaskFinishSummarySystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes plus validation tests (docs/spec-only output is not acceptable).  
**Source Document**: `docs/TaskFinishSummarySystem.md` (Last Updated: 2026-02-24)

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/TaskFinishSummarySystem.md` §3.1 Goals (1,2,3) | Terminal queue runs must expose at-a-glance outcome, structured finish summary in detail view, and deterministic run summary artifact. |
| DOC-REQ-002 | `docs/TaskFinishSummarySystem.md` §3.1 Goal 4, §4.2 | Proposals must be represented as finisher output with requested/generated/submitted/error reporting. |
| DOC-REQ-003 | `docs/TaskFinishSummarySystem.md` §4.1 | A dedicated finalize/finish stage must produce finish summary payload and `reports/run_summary.json`, with optional compact failure artifact. |
| DOC-REQ-004 | `docs/TaskFinishSummarySystem.md` §5.1 | `finishOutcome.code` must use the documented closed set and `finishOutcome.stage` must use the documented stage vocabulary. |
| DOC-REQ-005 | `docs/TaskFinishSummarySystem.md` §5.1 | `finishOutcome.reason` must be persisted as list-friendly outcome reason metadata. |
| DOC-REQ-006 | `docs/TaskFinishSummarySystem.md` §5.2 | Finish summary payload must follow a stable, shared schema with timestamps, stage statuses, publish details, changes summary, and proposals summary. |
| DOC-REQ-007 | `docs/TaskFinishSummarySystem.md` §5.3 | Finish summary content must remain non-secret and text fields must be redacted before persistence/output. |
| DOC-REQ-008 | `docs/TaskFinishSummarySystem.md` §6.1 | Queue job storage must persist finish outcome code/stage/reason plus full finish summary JSON. |
| DOC-REQ-009 | `docs/TaskFinishSummarySystem.md` §6.2 | Queue API schemas must support finish metadata on read models and terminal mutation requests. |
| DOC-REQ-010 | `docs/TaskFinishSummarySystem.md` §6.3 | Job list responses must include finish outcome fields while detail responses include finish summary payload. |
| DOC-REQ-011 | `docs/TaskFinishSummarySystem.md` §7.1 | Worker finalization must track stage timing, build finish summary on success/failure/cancel, and emit `reports/run_summary.json` best effort. |
| DOC-REQ-012 | `docs/TaskFinishSummarySystem.md` §7.2 | Publish outcomes must be classified deterministically across publish disabled, no changes, branch publish, PR publish, and failure cases. |
| DOC-REQ-013 | `docs/TaskFinishSummarySystem.md` §7.3 | Proposal submission reporting must include requested flag, hook skills, generated/submitted counts, and short redacted error reasons. |
| DOC-REQ-014 | `docs/TaskFinishSummarySystem.md` §7.4 | Terminal queue transitions (success/failure/cancel ack) must carry finish outcome metadata and finish summary payload. |
| DOC-REQ-015 | `docs/TaskFinishSummarySystem.md` §8.1 | Queue list and active views must display an outcome badge plus stage/reason context. |
| DOC-REQ-016 | `docs/TaskFinishSummarySystem.md` §8.2 | Queue detail view must show finish summary outcome, publish outcome, and proposals summary with link to proposals filtered by originating job. |
| DOC-REQ-017 | `docs/TaskFinishSummarySystem.md` §8.3 | Proposals API/UI filtering must support `originId` deep links for queue-origin jobs. |
| DOC-REQ-018 | `docs/TaskFinishSummarySystem.md` §10, §11 | Delivery must include runtime validation coverage for worker, API, and dashboard behavior across all documented terminal outcomes. |
| DOC-REQ-019 | Runtime scope guard from task objective | Deliverables must include production runtime code changes and validation tests; docs/spec-only output is out of scope. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Scan Job Outcomes Instantly (Priority: P1)

As a dashboard operator, I need each finished queue job to show an explicit outcome category and reason so I can triage runs quickly without opening artifacts.

**Why this priority**: Operational triage speed is the core business value and depends on clear, reliable terminal outcome labeling.

**Independent Test**: Load queue list and active views with sample jobs representing each terminal outcome and verify badges, stage, and reason are visible directly from API-provided finish metadata.

**Acceptance Scenarios**:

1. **Given** a terminal queue job with finished metadata, **When** the list view renders, **Then** it shows one documented outcome category with stage/reason context.
2. **Given** a failed or cancelled queue job, **When** the operator reviews it in the list, **Then** stage and reason explain where/why the run stopped.

---

### User Story 2 - Understand Full Finish State in Detail View (Priority: P2)

As an operator investigating a specific run, I need a full finish summary in job detail, including publish/proposals outcomes, so I can understand what completed and what did not without downloading artifacts.

**Why this priority**: Detail diagnosis is required for incident follow-up and handoffs.

**Independent Test**: Open detail pages for successful, no-change, publish-disabled, failed, and cancelled runs and verify finish summary panel content is complete and consistent with run artifacts.

**Acceptance Scenarios**:

1. **Given** a finished queue job detail page, **When** finish summary exists, **Then** outcome, stage, reason, publish result, and proposal counts appear in one panel.
2. **Given** a job with proposals generated/submitted, **When** the detail panel renders, **Then** the proposals link opens the proposals list filtered to the originating job.

---

### User Story 3 - Produce Deterministic Machine-Readable Run Summaries (Priority: P3)

As a platform maintainer, I need workers to produce and persist a stable finish summary contract for every terminal run so UI and automation can rely on one canonical source.

**Why this priority**: Consistent contracts reduce ambiguity across worker, API, and UI layers and enable reliable testing.

**Independent Test**: Execute representative runs for each outcome type, confirm finish metadata persistence and `reports/run_summary.json` emission, and run automated tests for worker/API/UI contract behavior.

**Acceptance Scenarios**:

1. **Given** a successful run that publishes a PR, **When** finalization completes, **Then** finish outcome is classified as PR published and includes link metadata.
2. **Given** a successful run with publish skipped for no local changes, **When** finalization completes, **Then** finish outcome is classified as no changes rather than generic success.
3. **Given** a run with publish mode disabled after successful execution, **When** finalization completes, **Then** finish outcome is classified as publish disabled.

### Edge Cases

- Publish mode disabled combined with earlier stage failure: outcome must remain failure with correct failing stage rather than publish disabled.
- Partial proposal submission failures: finish summary must retain generated/submitted counts and redacted error reasons.
- Cancellation after prepare or execute starts: finish summary must still include stage timings for completed stages and cancelled outcome classification.
- Artifact upload interruption during finalize: queue terminal transition must still carry finish metadata best effort while recording artifact-write failure in non-secret form.
- Sensitive strings in error/reason text: persisted summaries and artifacts must redact credential-like values before storage.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST add a dedicated task finalization stage that assembles and persists a finish summary contract for every terminal queue job. (Maps: DOC-REQ-001, DOC-REQ-003, DOC-REQ-011)
- **FR-002**: The finish summary MUST include standardized outcome code, outcome stage, outcome reason, timestamps, stage statuses, publish details, change summary, and proposal summary in one stable schema. (Maps: DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)
- **FR-003**: The system MUST enforce documented outcome classification rules so publish-disabled, no-change, branch-published, PR-published, failure, and cancelled states are distinguishable and deterministic. (Maps: DOC-REQ-004, DOC-REQ-012)
- **FR-004**: The system MUST redact secret-like values from finish summary fields and must not persist credentials or raw sensitive execution text in finish metadata artifacts/storage. (Maps: DOC-REQ-007)
- **FR-005**: Queue persistence models MUST store finish outcome code/stage/reason and finish summary JSON for retrieval by list/detail surfaces. (Maps: DOC-REQ-008, DOC-REQ-010)
- **FR-006**: Queue API schemas and terminal mutation payloads MUST support finish metadata for completion, failure, and cancellation acknowledgements. (Maps: DOC-REQ-009, DOC-REQ-014)
- **FR-007**: Worker finalization MUST produce `reports/run_summary.json` best effort for every terminal queue job and include compact failure reporting when applicable. (Maps: DOC-REQ-003, DOC-REQ-011)
- **FR-008**: Proposal finisher output MUST capture proposal requested state, hook skills, generated count, submitted count, and redacted error reasons in finish summary. (Maps: DOC-REQ-002, DOC-REQ-013)
- **FR-009**: Queue list and active views MUST surface an outcome badge and stage/reason context from finish metadata without requiring artifact downloads. (Maps: DOC-REQ-001, DOC-REQ-015)
- **FR-010**: Queue detail view MUST present finish summary outcome, publish outcome details, and proposal summary with a link filtered to the originating queue job. (Maps: DOC-REQ-001, DOC-REQ-002, DOC-REQ-016)
- **FR-011**: Proposals list filtering MUST support queue origin source + origin id so deep links from queue detail show proposals for that exact run. (Maps: DOC-REQ-017)
- **FR-012**: Delivery MUST include production runtime code changes spanning worker/API/UI behavior; documentation-only or spec-only changes are not sufficient for acceptance. (Maps: DOC-REQ-019)
- **FR-013**: Delivery MUST include validation tests covering worker classification logic, queue API finish metadata behavior, and dashboard rendering/filter behavior for finish summaries. (Maps: DOC-REQ-018, DOC-REQ-019)
- **FR-014**: Existing proposal queue semantics and promotion flow MUST remain unchanged while proposals are surfaced as finisher output metadata. (Maps: DOC-REQ-002)
- **FR-015**: For this document-backed feature, planning artifacts MUST maintain deterministic `DOC-REQ-*` traceability by recording at least one implementation task and one validation task for each requirement plus explicit implementation/validation ownership metadata. (Maps: DOC-REQ-018, DOC-REQ-019)

### Key Entities *(include if feature involves data)*

- **FinishOutcome**: A compact terminal outcome record containing code, stage, and short reason for list-friendly status display.
- **FinishSummary**: Canonical run-finish payload containing outcome, timing, stage results, publish summary, change summary, and proposal summary.
- **StageResult**: Per-stage status and duration tracking for prepare, execute, publish, proposals, and finalize lifecycle phases.
- **PublishOutcome**: Publish mode/status/reason metadata including branch and optional PR reference for published runs.
- **ProposalOutcome**: Proposal generation/submission counts and redacted issue list tied to queue-origin context.

### Assumptions & Dependencies

- Queue terminal transitions already provide hook points where finish metadata can be attached without changing queue semantics.
- Artifact storage for queue runs is available for best-effort `reports/run_summary.json` output.
- Dashboard list/detail views can consume added finish metadata fields without introducing a new front-end framework.
- Proposal records already carry queue-origin metadata fields required for filter extension with origin id.

### Non-Goals

- Reworking proposal promotion workflow semantics.
- Introducing a new dashboard framework or replacing existing vanilla JS UI model.
- Building long-term analytics infrastructure beyond operator-facing run finish summaries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of terminal queue jobs returned by list APIs expose one documented finish outcome code with stage and reason metadata.
- **SC-002**: Queue detail pages show finish summary data without requiring operators to download run artifacts.
- **SC-003**: In validation scenarios for publish disabled, no changes, branch publish, PR publish, failure, and cancellation, each run is classified into the correct terminal outcome code.
- **SC-004**: For representative terminal outcomes, workers emit `reports/run_summary.json` and persist corresponding finish summary data in queue storage surfaces.
- **SC-005**: Deep links from queue detail to proposals filtered by originating queue job return only proposals linked to that run.
- **SC-006**: Validation tests covering worker, API, and dashboard finish-summary behavior pass via `./tools/test_unit.sh`.
- **SC-007**: `DOC-REQ-001` through `DOC-REQ-019` each map to at least one implementation task and one validation task, and traceability rows include explicit implementation and validation owners.
