# Feature Specification: Merge Gate Head Drift Resilience

**Feature Branch**: `219-merge-gate-head-drift`
**Created**: 2026-04-21
**Status**: Draft
**Input**: Operator request after workflow `mm:f5f2911f-b35d-499e-88c2-c15e2d51ba4f` failed because Jira Orchestrate added a handoff commit after PR publication but before merge automation evaluated readiness.

## User Story

As a MoonMind operator running Jira Orchestrate with merge automation, I want the merge gate to tolerate pre-resolver pull request head changes and I want Jira Orchestrate pull requests to be ready for review, so automation does not fail on normal handoff commits or leave review-hidden draft PRs.

## Requirements

- **FR-001**: Merge automation MUST refresh the tracked pull request head when GitHub reports a new head before any resolver child has launched.
- **FR-002**: After refreshing a pre-resolver head, merge automation MUST reclassify readiness for the current revision instead of returning a terminal `stale_revision` blocker.
- **FR-003**: Merge automation MUST still apply configured readiness gates, including running checks and review blockers, to the refreshed current revision.
- **FR-004**: Resolver child workflow IDs MUST remain revision-scoped to the refreshed head SHA.
- **FR-005**: Jira Orchestrate PR creation instructions MUST require non-draft pull requests and must require verification or conversion to ready-for-review before Jira is moved to Code Review.

## Success Criteria

- **SC-001**: A merge automation run that sees `abc123` at start and `def456` before resolver launch waits or resolves against `def456` without failing on `stale_revision`.
- **SC-002**: Jira Orchestrate seeded template expansion tells agents to create or convert the PR as non-draft before writing the handoff artifact.
