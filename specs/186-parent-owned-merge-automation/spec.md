# Feature Specification: Parent-Owned Merge Automation

**Feature Branch**: `186-parent-owned-merge-automation`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
# MM-350 MoonSpec Orchestration Input

## Source

- Jira issue: MM-350
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Start and await child merge automation after PR publish
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`

## Canonical MoonSpec Feature Request

Jira issue: MM-350 from MM project
Summary: Start and await child merge automation after PR publish
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-350 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-350: Start and await child merge automation after PR publish

User Story
As an operator running a PR-publishing task, I need the original MoonMind.Run to own and await merge automation as a child workflow so downstream tasks can depend on the original workflowId and receive the correct completion signal.

Source Document
docs/Tasks/PrMergeAutomation.md

Source Sections
- 1. Purpose
- 2. Design Decision
- 3. Goals
- 5. Summary of the Strategy
- 6. Why This Uses Child Workflows
- 9. Parent Workflow Behavior
- 16. Dependency Semantics
- 22. Rejected Alternatives

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-028
- DESIGN-REQ-029

Independent Test
Run a workflow-boundary test for a PR-publishing MoonMind.Run with mergeAutomation.enabled=true and a stub publish result. Assert that PublishContext is persisted, MoonMind.MergeAutomation is started as a child, the parent remains awaiting_external while the child is running, and a downstream dependency target remains the parent workflowId.

Acceptance Criteria
- Given publishMode is pr and mergeAutomation.enabled is true, when publish succeeds, the parent starts exactly one MoonMind.MergeAutomation child for that publish context.
- The worker-bound MoonMind.Run input keeps publishMode as a top-level field and does not require a nested publish-only replacement contract.
- The publish step emits repository, prNumber, prUrl, baseRef, headRef, headSha, publishedAt, optional jiraIssueKey, and a compact artifact reference.
- The parent records the merge automation child workflow id in compact metadata and uses mm_state awaiting_external while waiting.
- The parent cannot reach terminal success while MoonMind.MergeAutomation is still running.
- No new top-level task dependency, fixed-delay follow-up task, or specs/ artifact is introduced by this story.

Requirements
- Parent-owned subordinate work is represented by a child workflow, not a later-created top-level workflow.
- PublishContext must be durable before the child workflow can depend on it.
- Dependency satisfaction remains tied to the original parent task terminal success.
- Any dedicated merge automation stage marker must be updated through the standard MoonMind.Run search-attribute path.

Dependencies
- None

Implementation Notes
- Preserve the existing top-level MoonMind.Run publishMode contract while adding mergeAutomation configuration support.
- Extend publish result/state handling so repository, prNumber, prUrl, baseRef, headRef, headSha, publishedAt, optional jiraIssueKey, and a compact PublishContext artifact reference are available before child workflow start.
- Start MoonMind.MergeAutomation from the parent workflow after successful PR publish when merge automation is enabled.
- Keep the parent in mm_state awaiting_external while the merge automation child is active, and prevent parent terminal success until the child returns a success outcome.
- Record the merge automation child workflow id in compact parent metadata rather than embedding large child state in workflow history.
- Ensure downstream dependency checks continue to target the original parent workflowId and are not redirected to a later top-level task.
- Do not introduce a fixed-delay follow-up task or separate top-level resolver task for this story.
- Cover the worker-bound invocation shape with workflow-boundary tests, including the parent input shape, publish output shape, child workflow start, waiting state, and dependency-target behavior.

Source Design Coverage
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-028
- DESIGN-REQ-029
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Await Parent-Owned Merge Automation

**Summary**: As a MoonMind operator using pull-request publishing, I want the original task run to own and await merge automation as child workflow work so downstream dependencies complete only after publish and automated merge resolution are finished.

**Goal**: Operators can depend on the original task workflow and receive one truthful completion signal that covers implementation, pull request publication, and configured merge automation.

**Independent Test**: Run or simulate a PR-publishing MoonMind.Run with merge automation enabled and a stub publish result. Verify the parent persists the publish context, starts exactly one parent-owned merge automation child workflow, moves into an external-waiting state while the child is active, does not complete successfully until the child succeeds, and remains the dependency target for downstream tasks.

**Acceptance Scenarios**:

1. **Given** a task has pull-request publishing and merge automation enabled, **When** pull request publication succeeds, **Then** the parent run starts exactly one parent-owned merge automation child workflow linked to the published pull request context.
2. **Given** merge automation has been started by the parent, **When** the child workflow is still waiting, blocked on readiness, or executing resolver work, **Then** the parent remains in an external-waiting state and does not reach terminal success.
3. **Given** the merge automation child returns a successful merged or already-merged outcome, **When** the parent receives the child result, **Then** the parent can complete successfully and satisfy downstream dependencies on the original workflow.
4. **Given** the merge automation child returns blocked, failed, or expired, **When** the parent receives the child result, **Then** the parent fails with an operator-readable merge automation reason and does not satisfy success-only downstream dependencies.
5. **Given** parent workflow execution is retried or replayed after publish, **When** merge automation has already been started for the publish context, **Then** MoonMind does not start a duplicate child workflow and preserves the existing child workflow identity.
6. **Given** publish succeeds with merge automation disabled, **When** the parent finalizes, **Then** existing pull-request publishing behavior continues without starting merge automation or changing dependency semantics.

### Edge Cases

- Pull request publication succeeds but the durable publish context is missing a required pull request identity field.
- A duplicate publish or replay attempts to start merge automation more than once for the same publish context.
- The merge automation child is canceled because the parent is canceled.
- The merge automation child pushes a remediation commit and needs to continue waiting for refreshed readiness signals.
- Optional Jira status gating is enabled but the linked Jira issue key is absent or inaccessible.
- Mission Control needs to show that the parent is waiting on merge automation without introducing a new top-level dependency target.

## Assumptions

- MM-350 is the active Jira issue and its Jira preset brief is the canonical Moon Spec input.
- Runtime mode is selected; documentation-only output is not sufficient.
- Existing merge-gate work may provide reusable contracts or helpers, but MM-350 requires parent-owned child workflow semantics and cannot be satisfied by a later-created top-level workflow.
- Merge automation remains opt-in and does not affect tasks that publish a pull request without enabling merge automation.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tasks/PrMergeAutomation.md` sections 1 and 2 require PR merge automation to be parent-owned subordinate orchestration inside the original `MoonMind.Run`, using child workflows rather than a separate top-level dependency target. Scope: in scope. Maps to FR-001, FR-002, FR-008.
- **DESIGN-REQ-002**: Source sections 2, 5, and 16 require downstream dependency satisfaction to remain tied to the original parent workflow and to wait for publish plus merge automation completion. Scope: in scope. Maps to FR-003, FR-006, FR-007.
- **DESIGN-REQ-003**: Source sections 5, 9, and 17 require the parent not to complete successfully while merge automation is active and to succeed only for merged or already-merged child outcomes. Scope: in scope. Maps to FR-003, FR-004, FR-005.
- **DESIGN-REQ-006**: Source section 9.1 requires the worker-bound parent input to preserve `publishMode` as a top-level field while carrying merge automation configuration. Scope: in scope. Maps to FR-009.
- **DESIGN-REQ-007**: Source section 9.2 requires publish output to persist repository, pull request number, pull request URL, base ref, head ref, head SHA, publication time, optional Jira issue key, and a compact artifact reference before the child depends on it. Scope: in scope. Maps to FR-010.
- **DESIGN-REQ-008**: Source section 9.3 requires the parent to record compact child workflow metadata and use the standard waiting-state vocabulary while merge automation is active. Scope: in scope. Maps to FR-003, FR-011.
- **DESIGN-REQ-009**: Source sections 13 and 14 require resolver execution to remain under merge automation control and to be able to re-enter waiting after resolver-generated pushes. Scope: in scope. Maps to FR-012.
- **DESIGN-REQ-028**: Source section 22 rejects fixed-delay follow-up tasks and separate top-level resolver tasks for this parent-owned waiting story. Scope: in scope. Maps to FR-008, FR-013.
- **DESIGN-REQ-029**: Source sections 20 and 23 require root and child artifacts or summaries to expose enough state for operators to understand waiting and failure. Scope: in scope. Maps to FR-011, FR-014.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow a pull-request-publishing parent run to request merge automation as parent-owned subordinate workflow work.
- **FR-002**: After successful pull request publication with merge automation enabled, the parent run MUST start exactly one merge automation child workflow for the durable publish context.
- **FR-003**: The parent run MUST enter an external-waiting posture while the merge automation child is active and MUST NOT reach terminal success until the child completes successfully.
- **FR-004**: The parent run MUST complete successfully only when the merge automation child reports `merged` or `already_merged`.
- **FR-005**: The parent run MUST fail or cancel with an operator-readable merge automation reason when the child reports blocked, failed, expired, or canceled outcomes according to cancellation semantics.
- **FR-006**: Downstream dependencies MUST continue to target the original parent workflow identity and MUST be satisfied only by the parent terminal success.
- **FR-007**: The system MUST NOT create a new top-level dependency target, fixed-delay follow-up task, or separate completion signal for the parent-owned merge automation story.
- **FR-008**: Parent replay, retry, or duplicate publish handling MUST preserve an existing merge automation child workflow identity and prevent duplicate child starts for the same publish context.
- **FR-009**: Worker-bound parent input MUST preserve `publishMode` as a top-level field while carrying merge automation configuration.
- **FR-010**: The publish step MUST persist a durable publish context before child start, including repository, pull request number, pull request URL, base ref, head ref, head SHA, publication time, optional Jira issue key, and a compact artifact reference.
- **FR-011**: Parent run metadata and operator-visible summaries MUST identify that the parent is waiting on merge automation and include compact child workflow identity and current outcome state.
- **FR-012**: Merge automation MUST be able to run resolver work and re-enter waiting when resolver-generated changes require fresh readiness evidence.
- **FR-013**: Existing pull request publishing without merge automation enabled MUST retain current behavior and must not start a merge automation child.
- **FR-014**: Validation evidence MUST include workflow-boundary coverage for the real worker-bound invocation shape, child start, parent waiting state, duplicate-start prevention, successful child completion, and non-success child outcomes.

### Key Entities

- **Merge Automation Request**: The parent-run configuration that enables parent-owned merge automation after pull request publication.
- **Publish Context**: Durable pull request publication evidence used to start and resume merge automation, including pull request identity, head revision, optional Jira key, and compact artifact reference.
- **Merge Automation Child Workflow**: Parent-owned subordinate workflow work that waits on readiness, runs resolver work, and returns a terminal merge automation outcome to the parent.
- **Parent Waiting State**: The operator-visible parent run state indicating that implementation and publication have succeeded but parent-owned merge automation is still active.
- **Dependency Completion Signal**: The original parent workflow terminal success event used by downstream dependencies.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation, 100% of merge-automation-enabled pull request publications start exactly one parent-owned merge automation child workflow.
- **SC-002**: In validation, 100% of active child workflow cases keep the parent out of terminal success until the child succeeds.
- **SC-003**: In validation, 100% of successful child outcomes allow the original parent workflow to complete successfully and satisfy downstream dependencies.
- **SC-004**: In validation, 100% of blocked, failed, expired, or canceled child outcomes prevent parent success and expose an operator-readable reason.
- **SC-005**: In validation, 100% of retry, replay, or duplicate publish scenarios preserve a single child workflow identity for one publish context.
- **SC-006**: Final verification can trace Jira issue MM-350 and every in-scope `DESIGN-REQ-*` to at least one functional requirement and acceptance scenario.
