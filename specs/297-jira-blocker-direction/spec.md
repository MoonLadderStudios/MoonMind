# Feature Specification: Jira Blocker Direction

**Feature Branch**: `297-jira-blocker-direction`  
**Created**: 2026-05-04  
**Status**: Draft  
**Input**:

```text
Jira dependencies must align with MoonMind's ordered story dependencies. Step 1
must not be blocked by step 2. Fix blocker creation and blocker detection so the
logic is deterministic and uses Jira link direction correctly.
```

## User Story - Directionally Correct Jira Blockers

As a MoonMind operator, I want Jira blocker links and Jira Orchestrate blocker
preflight checks to use the same ordered dependency semantics so earlier stories
block later stories, and later stories never block earlier work due to reversed
link interpretation.

## Requirements

- **FR-001**: `linear_blocker_chain` MUST continue to create links where each
  earlier story blocks the immediately later story.
- **FR-002**: Jira Orchestrate preflight MUST only block a target issue when
  trusted Jira GET issue data exposes the other issue as `outwardIssue`,
  meaning the other issue is on the outward "blocks" side and the target is on
  the blocked/inward side of a `Blocks` relationship.
- **FR-003**: A `Blocks` relationship where the target issue blocks another
  issue MUST NOT block the target orchestration.
- **FR-004**: Blocker status MUST be read from trusted Jira data, fetching the
  linked blocker issue when status is not embedded in the target response.
- **FR-005**: Missing trusted blocker status MUST fail closed as a blocked
  outcome.
- **FR-006**: The preflight MUST be executable as a deterministic MoonMind tool,
  not dependent on prompt-only link interpretation.

## Success Criteria

- **SC-001**: A three-story chain creates `story1 -> story2` and
  `story2 -> story3` Jira blocker requests.
- **SC-002**: Target issue `MM-1` whose Jira GET response exposes `MM-2` as
  `inwardIssue` continues even when `MM-2` is not Done.
- **SC-003**: Target issue `MM-2` whose Jira GET response exposes `MM-1` as
  `outwardIssue`
  blocks when `MM-1` is not Done.
- **SC-004**: The seeded Jira Orchestrate preset uses the deterministic
  blocker-preflight tool before MoonSpec implementation.
