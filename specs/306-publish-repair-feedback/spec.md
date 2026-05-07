# Feature Specification: Publish Repair Feedback

**Input**: Operator asked whether `publishMode=pr` finalization failures can be fed back to the agent, and whether Jira Orchestrate's PR instructions can stop contradicting managed publish behavior.

## User Story

As a MoonMind operator, I want PR publish postcondition failures to produce one bounded corrective agent turn before the workflow fails, so simple missed handoff steps such as committing on the wrong branch can be repaired without manual diagnosis.

## Requirements

- **FR-001**: When `publishMode=pr` cannot produce a PR because the current publish branch has no publishable diff, MoonMind MUST send one explicit repair instruction to the managed agent when a managed session is available.
- **FR-002**: The repair instruction MUST include the observed publish failure, expected branch/base context when available, and the required corrective action.
- **FR-003**: The repair loop MUST be bounded to one attempt and MUST preserve the existing terminal failure when repair does not produce a publishable outcome.
- **FR-004**: Jira Orchestrate instructions MUST not ask managed agents to both manually create a PR and rely on infrastructure-owned PR publishing.
- **FR-005**: Existing no-change PR publish failures MUST remain truthful when no repair turn is available or repair fails.

## Acceptance Scenarios

1. Given a managed PR-publishing run has no publishable diff on the publish branch, when final publish validation detects the failure, then the workflow sends one corrective agent turn before terminal failure.
2. Given the repair turn produces pushed commits, when native PR creation runs, then workflow completion can proceed with the created PR URL.
3. Given the repair turn is unavailable or fails, when finalization runs, then the workflow fails with the original publish postcondition error.
4. Given Jira Orchestrate expands its PR handoff step, then the wording aligns with managed infrastructure publishing and no longer instructs unmanaged push/PR creation as the primary path.
