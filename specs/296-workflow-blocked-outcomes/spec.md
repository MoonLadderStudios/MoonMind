# Feature Specification: Workflow Blocked Outcomes

**Feature Branch**: `296-workflow-blocked-outcomes`  
**Created**: 2026-05-04  
**Status**: Draft  
**Input**:

```text
Troubleshoot and fix workflow failure for task mm:9b25b452-8bbc-4a20-8901-966e793cea33.
Make the solution robust, performant, generally usable, and not overly tailored to any particular repo.
```

## User Story - Stop Plans On Explicit Blockers

As a MoonMind operator, I want a workflow step that reports an explicit blocked outcome to stop downstream execution before implementation or publishing so the run records the real blocker instead of failing later with a misleading publish error.

## Requirements

- **FR-001**: The workflow MUST detect explicit structured blocked outcomes from agent or tool step outputs.
- **FR-002**: Detection MUST be generic and MUST NOT depend on a repository name, Jira project key, branch name, or issue key.
- **FR-003**: When a step reports a blocked outcome, the workflow MUST stop executing remaining plan steps.
- **FR-004**: Remaining plan steps MUST be marked skipped in the step ledger.
- **FR-005**: PR/branch publishing MUST be suppressed for blocked outcomes.
- **FR-006**: The final workflow result MUST preserve the blocker summary without throwing a publish failure.
- **FR-007**: Existing no-change PR publish behavior MUST still fail when no blocked outcome is present.

## Success Criteria

- **SC-001**: A structured `decision/status/outcome: blocked` report stops the plan after the reporting step.
- **SC-002**: The blocked step is recorded, remaining steps are skipped, and no native PR creation is attempted.
- **SC-003**: The final result status is `blocked` with the reported summary and `publish_failure` is false.
