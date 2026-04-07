# Feature Specification: pr-resolver-retry-policy

**Feature Branch**: `codex/phase-8-managed-session-artifacts`
**Created**: 2026-04-07
**Status**: Draft
**Input**: User description: "Fix the pr-resolver skill so it automatically proceeds to fix merge conflicts when finalize retries move from ci_running into merge_conflicts."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Escalate merge conflicts after transient CI waits (Priority: P1)

Operators need `pr-resolver` to keep following its documented priority order when GitHub only surfaces merge conflicts after CI finishes.

**Why this priority**: The current behavior stops at manual review even though the skill contract and design docs describe merge conflicts as an actionable remediation path.

**Independent Test**: Simulate finalize attempts returning `ci_running` before `merge_conflicts` and verify orchestration escalates to full remediation with `run_fix_merge_conflicts_skill` instead of returning `manual_review`.

**Acceptance Scenarios**:

1. **Given** finalize retries previously returned `ci_running`, **When** a later finalize attempt returns `merge_conflicts`, **Then** orchestration treats the new reason as actionable full remediation and invokes the conflict-fix path.
2. **Given** merge-conflict remediation runs after transient CI states, **When** a later finalize attempt returns `merged`, **Then** orchestration exits successfully without reporting a reason-transition manual review blocker.
3. **Given** a blocker reason changes from a finalize-only retry state to an unknown or unsupported reason, **When** orchestration evaluates the transition, **Then** it still returns `manual_review` rather than silently expanding remediation scope.

### Edge Cases

- A PR is `ci_running` for several retries before GitHub recomputes mergeability as `DIRTY`.
- A finalize-only retry reason changes to another actionable remediation reason after one or more waits.
- Unknown blocker transitions must remain fail-fast and require manual review.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `pr-resolver` MUST treat `merge_conflicts` as actionable remediation even when the previous blocked reason was a finalize-only retry state such as `ci_running`.
- **FR-002**: The orchestration transition guard MUST preserve explicit manual-review behavior for unknown or unsupported reason changes.
- **FR-003**: The retry-policy behavior MUST be covered by a regression test at the pr-resolver orchestration boundary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A focused pr-resolver unit test fails before the fix and passes after the fix for the `ci_running -> merge_conflicts -> merged` sequence.
- **SC-002**: Existing finalize-only retry behavior for pure `ci_running` waits remains unchanged.
