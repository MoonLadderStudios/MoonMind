# Feature Specification: Set Jira Issue MM-601 to Status "In PR"

**Feature Branch**: `331-set-jira-issue-in-pr`
**Created**: 2026-05-09
**Status**: Draft
**Input**: User description: "Change Jira issue MM-601 to status In PR"
**Source Jira Issue**: MM-601

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Transition MM-601 to "In PR"

**Summary**: As an operator running this Jira preset task, I want Jira issue MM-601 transitioned to status "In PR" so that the workflow tracker reflects that a pull request is open and downstream review or merge automation can react to that state.

**Goal**: After this task completes, Jira issue MM-601 is observed in status "In PR" in the connected Jira project, and the change is recorded in the issue's history.

**Independent Test**: Submit this preset against the configured Jira tenant and verify, by reading MM-601 directly in Jira (UI or API), that its current status is "In PR" and that an audit entry exists for the transition. The test is independent because it requires only access to Jira and the issue key MM-601, with no other features or downstream consumers.

**Acceptance Scenarios**:

1. **Given** Jira issue MM-601 exists in the connected project and is currently in a status from which "In PR" is reachable via an available workflow transition, **When** the preset task runs, **Then** the system transitions MM-601 to status "In PR" and reports successful completion to the operator.
2. **Given** Jira issue MM-601 already has status "In PR", **When** the preset task runs, **Then** the system treats the request as already satisfied, leaves the issue status unchanged, and reports successful completion without creating a duplicate transition.
3. **Given** Jira issue MM-601 cannot be transitioned to "In PR" because the configured target status is unreachable from its current status or because the operator's Jira credentials lack permission to transition it, **When** the preset task runs, **Then** the system stops without changing MM-601's status, reports an actionable error that names the issue, the current status, and the reason the transition was rejected, and does not retry silently.

### Edge Cases

- What happens when issue MM-601 does not exist or is not visible to the configured Jira credentials? The system MUST stop with an actionable error that names MM-601 and indicates the issue could not be located, and MUST NOT modify any other issue.
- What happens when the Jira project's workflow has no transition leading to a status named "In PR" (or the target status name does not match)? The system MUST stop with an actionable error that names the missing target status and lists the available transitions for the issue, and MUST NOT modify the issue.
- What happens when the Jira API is temporarily unavailable or returns a transient failure? The system MUST surface the failure to the operator with enough detail to retry, and MUST NOT report success unless the transition has been confirmed by reading back the issue's current status.
- What happens when multiple statuses match "In PR" (case or whitespace differences) in the project workflow? The system MUST resolve the target deterministically (case-insensitive, trimmed match) and MUST stop with an actionable error if more than one distinct workflow status still matches after normalization.

## Assumptions

- The connected Jira project for MM-601 defines a workflow status named "In PR" (case-insensitive) that is reachable by an authorized transition from the current status of MM-601 at the time the task runs.
- The credentials available to the running task are entitled to view and transition MM-601 in the Jira project the issue belongs to.
- The operator wants exactly the issue identified by the literal key `MM-601`; no fuzzy matching, search-by-title, or alternative key resolution is in scope.
- "Successful completion" is defined as Jira reporting the transition succeeded AND a follow-up read of MM-601 confirming it is in status "In PR" before the task reports success.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept the preset brief "Change Jira issue MM-601 to status In PR" and treat the literal key `MM-601` as the single target issue without inferring or substituting other issue keys.
- **FR-002**: The system MUST resolve the target workflow status by matching the name "In PR" against MM-601's available workflow transitions using a case-insensitive, whitespace-trimmed comparison.
- **FR-003**: The system MUST execute exactly one transition that lands MM-601 in status "In PR"; it MUST NOT chain through unrelated intermediate statuses unless that is the only path the project's workflow exposes, in which case it MUST record each intermediate transition it performed.
- **FR-004**: If MM-601 is already in status "In PR" before the task runs, the system MUST treat the request as satisfied (idempotent no-op) and MUST report success without performing a transition.
- **FR-005**: After requesting the transition, the system MUST verify success by reading MM-601's current status from Jira and MUST report success only if the read-back confirms status "In PR".
- **FR-006**: On any failure (issue not found, no matching transition, ambiguous target status, permission denied, transient Jira error, read-back mismatch), the system MUST stop, MUST NOT report success, and MUST emit an operator-actionable error that names the issue key, the observed current status (when available), and the specific reason the transition did not complete.
- **FR-007**: The system MUST NOT modify any Jira issue other than MM-601 as part of executing this preset.
- **FR-008**: The system MUST preserve the original preset brief and the target Jira issue key (`MM-601`) in the task's recorded inputs so that downstream verification can compare the executed action against the originating brief.

### Key Entities *(include if feature involves data)*

- **Jira Issue MM-601**: The single target work item in the connected Jira project. Relevant attributes for this feature are its current workflow status, its set of available outbound transitions, and its change history. No other attributes are read or modified.
- **Target Status "In PR"**: The desired terminal status name for MM-601 after this task runs. Resolved by case-insensitive, trimmed match against the names of MM-601's available transitions.
- **Preset Brief**: The original natural-language request ("Change Jira issue MM-601 to status In PR") preserved verbatim in the task input record for verification.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a successful run, an operator inspecting Jira observes that MM-601 has status "In PR" within 30 seconds of the task reporting success, in 100% of runs that report success.
- **SC-002**: When MM-601 is already in status "In PR" at the start of a run, 100% of runs complete successfully without performing any workflow transition and without creating duplicate change-history entries on the issue.
- **SC-003**: 100% of runs that fail to land MM-601 in status "In PR" (for any reason) report failure to the operator and include the issue key, the observed current status, and the failure reason; 0% of failed runs report success.
- **SC-004**: 0% of runs modify any Jira issue other than MM-601 while executing this preset.
- **SC-005**: An auditor reading the task's recorded inputs can reconstruct the literal preset brief ("Change Jira issue MM-601 to status In PR") and the target issue key (`MM-601`) verbatim, in 100% of runs.
