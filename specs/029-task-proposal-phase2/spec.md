# Feature Specification: Task Proposal Queue Phase 2

**Feature Branch**: `025-task-proposal-phase2`  
**Created**: 2026-02-18  
**Status**: Draft  
**Input**: Implement Phase 2 of the Task Proposal Queue design described in `docs/TaskProposalQueue`. Include deduped proposal detection, edit-before-promote flow, snooze + priority triage controls, and category-triggered notifications. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect Duplicates Before Promotion (Priority: P1)

As a reviewer triaging new worker proposals, I need the dashboard and API to highlight similar proposals so I can merge or dismiss duplicates before promoting redundant work.

**Why this priority**: Duplicate follow-up tasks waste reviewer time and may enqueue repeated work if not caught early; this is the highest-leverage Phase 2 capability.

**Independent Test**: Seed multiple proposals with similar repositories/titles, load list/detail views, and confirm similar proposals plus dedup hash appear with links and API support without creating queue jobs.

**Acceptance Scenarios**:

1. **Given** proposals with the same repository and near-identical titles, **When** a reviewer opens the detail page, **Then** the UI renders a "Similar Proposals" card showing other open proposals ranked by similarity and docs show dedup hash fields via the API.
2. **Given** a new proposal is created with a normalized title that collides with an existing open item, **When** the backend stores it, **Then** it records a dedup key/hash and surfaces similarity metadata in list/detail APIs while still allowing submission.

---

### User Story 2 - Edit Payload Before Promotion (Priority: P1)

As a reviewer, I want an optional edit workflow that lets me tweak the stored `taskCreateRequest` (instructions, priority, attempts, publish mode) before creating a queue job, while retaining the single-click "use as-is" path.

**Why this priority**: Reviewers often need small adjustments before enqueuing; today they must re-type the job manually. Inline edits reduce friction while keeping auditability.

**Independent Test**: Use the new UI modal, change instructions/publish mode/priority, promote the proposal, and confirm the job reflects the edited payload and audit fields capture the editor.

**Acceptance Scenarios**:

1. **Given** a reviewer clicks "Edit before Promote", **When** they modify instructions and hit Promote, **Then** the backend validates the modified payload using canonical rules and records both the edits and resulting queue job.
2. **Given** no changes are needed, **When** the reviewer presses the default "Promote" button, **Then** the existing one-click flow works unchanged.

---

### User Story 3 - Snooze and Priority Triage (Priority: P2)

As a reviewer managing backlog health, I need to snooze proposals (hide until a later date) and set reviewer-visible priority chips so the list view reflects triage order.

**Why this priority**: Snoozing prevents clutter without losing proposals; explicit priority lets teams focus on urgent follow-ups and prepare for notifications.

**Independent Test**: Snooze a proposal via API/UI, confirm it disappears from default open list until the snooze expires or is cancelled, and adjust priority to see the list sort indicator and badge updates.

**Acceptance Scenarios**:

1. **Given** a reviewer snoozes a proposal for 7 days, **When** other reviewers load the open list immediately, **Then** the snoozed proposal is omitted but reappears once the snooze expires or when filtering `status=snoozed`.
2. **Given** a reviewer updates the priority field to `high`, **When** the list renders, **Then** a badge/ordering indicates the assigned priority without affecting stored worker priority fields.

---

### User Story 4 - Notifications for Security/Tests (Priority: P2)

As a platform owner, I want Slack/webhook alerts whenever new security or tests proposals arrive so teams respond quickly.

**Why this priority**: These categories often require urgent fixes; automated notifications keep humans in the loop without polling the dashboard.

**Independent Test**: Create proposals in `security` and `tests` categories, verify notification dispatch occurs once with dedup suppression, and ensure other categories do not trigger alerts.

---

### Edge Cases

- Multiple similar proposals arrive simultaneously; dedup logic must avoid race conditions and show all matches even when more than 10 exist.
- Snooze expiration occurs while a reviewer is viewing the list; UI should refresh gracefully without stale state errors.
- Editing payload removes required fields; validation should block promotion and surface inline errors rather than enqueueing a broken job.
- Notification target unreachable; failures should be retried/backed off without blocking proposal creation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The backend MUST compute and persist a deterministic deduplication key (`dedup_key`) and opaque hash (`dedup_hash`) derived from repository + normalized title for every proposal.
- **FR-002**: The API MUST expose a `similar` collection per proposal listing up to 10 other open proposals sharing the same dedup key ordered by recency.
- **FR-003**: The dashboard list and detail views MUST surface the dedup hash and similar proposal links without trusting raw user input.
- **FR-004**: Reviewers MUST be able to launch an "Edit & Promote" UI affordance that loads the stored task payload, allows edits to instructions, publish settings, priority, attempts, affinity key, and tags, and submits the modified payload to the backend promote endpoint.
- **FR-005**: Promotion API MUST accept an optional `taskCreateRequestOverride` object (parallel to stored envelope) and validate it using the canonical Task contract before enqueuing.
- **FR-006**: The system MUST track reviewer-defined triage priority (`low`, `normal`, `high`, `urgent`) per proposal and display it in API/UI responses.
- **FR-007**: Reviewers MUST be able to snooze a proposal until a timestamp (`snoozed_until`), during which it is excluded from default `status=open` listings but retrievable via `status=snoozed` or `includeSnoozed=true` filter.
- **FR-008**: Snoozed proposals MUST automatically revert to `open` status when the snooze timestamp passes, without manual intervention.
- **FR-009**: Notification service MUST emit Slack/webhook payloads for newly created proposals whose category equals `security` or `tests`, with per-proposal deduplication to avoid duplicate alerts.
- **FR-010**: Metrics/logging MUST capture dedup collisions, snooze actions, edits before promote, and notification outcomes to maintain observability parity with Phase 1 counters.

### Key Entities *(include if feature involves data)*

- **TaskProposal (extended)**: Adds `dedup_key`, `dedup_hash`, `review_priority`, `snoozed_until`, and `snooze_history`. References existing origin/promote fields for audit. Similar proposals derived from dedup key.
- **TaskProposalNotification**: Logical event (not necessarily a table) capturing notification target, category, timestamp, success/failure to ensure at-least-once delivery without blocking writes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Duplicate proposal promotions decrease by 80% (baseline: >5 duplicates/month) due to reviewers seeing similar proposals before promotion.
- **SC-002**: 90% of reviewers report the edit-before-promote flow eliminates manual queue job creation for follow-up tasks (via rollout survey/logs).
- **SC-003**: Snoozed proposals automatically reopen at the configured time with <1 minute delay in 95% of cases (monitored via scheduled job metrics).
- **SC-004**: Slack/webhook notifications for `security`/`tests` proposals are delivered within 60 seconds of proposal creation with success logging, and zero incidents of double notifications per proposal.
