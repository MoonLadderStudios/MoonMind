# Feature Specification: Transition Jira Issue MM-640 to "In Progress"

**Feature Branch**: `change-jira-issue-mm-640-to-status-in-pr-802b3fb2`
**Created**: 2026-05-11
**Status**: Draft
**Input**: User description: "Change Jira issue MM-640 to status 'In Progress'."

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Move MM-640 into "In Progress"

**Summary**: As a delivery operator, I want the system to move Jira issue MM-640 into the "In Progress" workflow status so that the Jira tracker reflects that implementation work on MM-640 has started.

**Goal**: After the workflow runs, Jira issue MM-640's workflow status is "In Progress", with no other fields on MM-640 mutated and no other Jira issues modified.

**Independent Test**: Can be fully tested by running the workflow against issue key `MM-640` and then re-fetching the issue from the Jira tracker to confirm its current workflow status reads "In Progress" (or, if it was already "In Progress" before the run, that the workflow recorded a no-op rather than performing a redundant transition).

**Acceptance Scenarios**:

1. **Given** `MM-640` is in a workflow status from which a transition leading to "In Progress" is currently available, **When** the workflow runs against `MM-640`, **Then** `MM-640`'s workflow status becomes "In Progress" and the run report names the issue key, the prior status, the chosen transition, and the verified post-transition status.
2. **Given** MM-640 is already in workflow status "In Progress", **When** the workflow runs against `MM-640`, **Then** no transition is performed, no other fields on MM-640 are changed, and the run report describes the outcome as a no-op with the current status `In Progress`.
3. **Given** MM-640 is in a workflow status from which no available transition leads to "In Progress", **When** the workflow runs against `MM-640`, **Then** the workflow stops with an explicit "no matching transition" error, MM-640 is not modified, and the report names the available transition options for operator inspection.
4. **Given** MM-640 has more than one available transition whose target status name matches "In Progress" (case-insensitive), **When** the workflow runs against `MM-640`, **Then** the workflow stops with an explicit ambiguity error, MM-640 is not modified, and the report lists the candidate transitions for operator selection.

### Edge Cases

- The trusted Jira tool surface is unavailable or not registered in the runtime: the workflow stops with an explicit "Jira tools unavailable" error and performs no mutation.
- MM-640 cannot be located by the configured Jira credentials (does not exist, or is not visible to the active credential): the workflow stops with an explicit "issue not found / not visible" error that names `MM-640`, performs no mutation, and does not silently fall back to any other issue key.
- Authentication or authorization to view or modify MM-640 fails: the workflow stops with a sanitized auth/permission error and performs no mutation; no credentials, tokens, cookies, or auth headers appear in any user-visible output, log, or artifact.
- Jira returns a transient failure (rate limit or 5xx) during transition discovery or transition execution: the workflow surfaces the failure as an explicit error without retrying through ad-hoc HTTP and without claiming success.
- The post-transition verification re-read shows a workflow status other than "In Progress" (for example, the workflow advanced through an intermediate state): the workflow reports the actual final status and flags the result as not matching the requested target.
- Jira's transition for "In Progress" requires additional fields (such as resolution or comment) before it will execute: the workflow stops with the named missing fields and performs no mutation rather than guessing values.

## Assumptions

- "In Progress" is a discoverable Jira workflow status reachable from MM-640's current status, on the Jira project that owns MM-640.
- The runtime executing this workflow has access to MoonMind's trusted Jira tool surface for retrieving issue details, discovering available transitions, and executing status changes; raw Jira REST credentials are not handed to the agent runtime.
- The brief targets a workflow status transition only; no description, label, assignee, summary, or other field updates are implied by "Change ... to status 'In Progress'".
- Status name matching is case-insensitive and ignores surrounding whitespace, but otherwise compares against the literal target name `In Progress`.

## Source Design Requirements

- **DESIGN-REQ-001**: The system MUST update only the Jira issue identified by the literal issue key `MM-640`.
  - Source: User brief — `"Change Jira issue MM-640 to status 'In Progress'."`
  - Scope: in scope
  - Mapped FR: FR-001, FR-008
- **DESIGN-REQ-002**: The system MUST drive MM-640 to the workflow status named `In Progress`.
  - Source: User brief — `"... to status 'In Progress'."`
  - Scope: in scope
  - Mapped FR: FR-003, FR-005, FR-006
- **DESIGN-REQ-003**: The system MUST treat the request as a workflow status change only, not as a description, comment, or other field edit.
  - Source: User brief verb — `"Change ... status"`
  - Scope: in scope
  - Mapped FR: FR-008

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST resolve the update target to the literal Jira issue key `MM-640` and reject any attempt to operate on a different issue key inferred from surrounding text.
- **FR-002**: System MUST discover MM-640's currently available workflow transitions before performing any mutation.
- **FR-003**: System MUST select a transition whose target workflow status name equals `In Progress` (case-insensitive, trimmed) and whose identity among the available transitions is unambiguous.
- **FR-004**: System MUST execute the selected transition exclusively through MoonMind's trusted Jira tool surface, never through ad-hoc HTTP calls or raw Jira credentials in the agent runtime.
- **FR-005**: System MUST verify the post-transition status by re-reading MM-640 and report the verified final status.
- **FR-006**: System MUST treat the case where MM-640 is already in workflow status `In Progress` as a successful no-op, reporting the current status without invoking a redundant transition.
- **FR-007**: System MUST stop with an explicit, named error and perform no mutation when (a) no available transition leads to a target status named `In Progress`, (b) more than one available transition leads to a target status named `In Progress`, (c) MM-640 cannot be located by the configured Jira credentials, or (d) Jira reports required transition fields whose values were not supplied.
- **FR-008**: System MUST NOT modify any field on MM-640 other than its workflow status, and MUST NOT modify any Jira issue other than MM-640.
- **FR-009**: System MUST NOT emit Jira credentials, API tokens, auth headers, cookies, or full environment dumps in any user-visible output, log, or artifact, including failure reports.
- **FR-010**: System MUST report, on completion, the issue key, the prior status, the chosen transition (if any), the action taken (transitioned, no-op, or stopped with reason), and the verified final status.

### Key Entities

- **Jira Issue (MM-640)**: The single tracked work item being acted on; identified by its issue key, with attributes for current workflow status and a list of currently available workflow transitions.
- **Workflow Transition**: A named operation exposed by Jira that moves an issue from its current workflow status to a target workflow status; identified by transition ID and target status name.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a successful run, 100% of operator verifications of MM-640 in the Jira tracker show the workflow status as `In Progress`.
- **SC-002**: 100% of runs end in exactly one of three terminal outcomes — successfully transitioned to `In Progress`, no-op (already `In Progress`), or stopped with a named error (including verification mismatch) — with zero partial mutations of MM-640.
- **SC-003**: Across all run outputs, logs, and artifacts, zero secrets (API tokens, auth headers, cookies, raw credentials) are exposed.
- **SC-004**: No Jira issue other than MM-640 is modified by the run, verifiable by inspecting the run report and Jira change history.
- **SC-005**: The run completes within a single operator-observed cycle, with no manual follow-up retry required when MM-640 is in an eligible source workflow status.
