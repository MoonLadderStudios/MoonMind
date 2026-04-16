# Feature Specification: Merge Outcome Propagation

**Feature Branch**: `188-merge-outcome-propagation`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
# MM-353 MoonSpec Orchestration Input

## Source

- Jira issue: MM-353
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Map merge automation outcomes to parent completion and cancellation
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`

## Canonical MoonSpec Feature Request

Jira issue: MM-353 from MM project
Summary: Map merge automation outcomes to parent completion and cancellation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-353 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-353: Map merge automation outcomes to parent completion and cancellation

Story ID: STORY-004

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 10.2 Output
- 16. Dependency Semantics
- 17. Terminal Outcome Rules
- 18. Cancellation Semantics
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-002
- DESIGN-REQ-012
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-029

User Story
As a downstream task author, I need parent task completion to faithfully reflect merge automation success, failure, expiration, or cancellation so dependency behavior is deterministic.

Independent Test
Run parent workflow tests with stub MoonMind.MergeAutomation completions for every allowed terminal status and assert the resulting parent terminal state, dependency satisfaction behavior, and cancellation propagation to child workflows.

Acceptance Criteria
- Parent MoonMind.Run succeeds only when the child returns merged or already_merged.
- Parent MoonMind.Run fails when the child returns blocked, failed, or expired.
- Parent MoonMind.Run is canceled when the child returns canceled or when operator-initiated parent cancellation propagates.
- Downstream dependsOn relationships are satisfied only by parent terminal success.
- Canceling the parent requests cancellation of MoonMind.MergeAutomation, and canceling MoonMind.MergeAutomation requests cancellation of any active resolver child run.
- Cancellation and cleanup summaries do not claim success for best-effort cleanup that did not complete.

Requirements
- Terminal outcome mapping must be deterministic and covered at the workflow boundary.
- Cancellation must preserve truthful operator-visible state.
- Non-success merge outcomes must not satisfy dependencies under the current dependency model.

Dependencies
- STORY-001
- STORY-002
- STORY-003

Implementation Notes
- Map child MoonMind.MergeAutomation `merged` and `already_merged` terminal statuses to successful parent MoonMind.Run completion.
- Map child MoonMind.MergeAutomation `blocked`, `failed`, and `expired` terminal statuses to failed parent MoonMind.Run completion.
- Map child MoonMind.MergeAutomation `canceled` terminal status to canceled parent MoonMind.Run completion.
- Propagate operator-initiated parent cancellation to the active MoonMind.MergeAutomation child workflow.
- Propagate MoonMind.MergeAutomation cancellation to any active resolver child run.
- Ensure downstream `dependsOn` relationships are satisfied only when the parent terminal state is successful.
- Keep cancellation and cleanup summaries truthful when best-effort cleanup is incomplete.
- Add workflow-boundary coverage for each allowed terminal status and cancellation path.

Source Design Coverage
- DESIGN-REQ-002
- DESIGN-REQ-012
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-029
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus workflow-boundary validation tests.

## User Story - Propagate Merge Automation Outcomes

**Summary**: As a downstream task author, I want the parent `MoonMind.Run` terminal state to faithfully reflect merge automation success, failure, expiration, or cancellation so that dependency behavior is deterministic.

**Goal**: A parent task that publishes a pull request and awaits merge automation produces one truthful terminal signal: success only after merge automation succeeds, failure for non-success merge outcomes, and cancellation for cancellation outcomes.

**Independent Test**: Run parent workflow-boundary tests with stubbed `MoonMind.MergeAutomation` completions for every allowed terminal status. The story passes when the parent terminal state, downstream dependency satisfaction behavior, and cancellation propagation all match the outcome mapping.

**Acceptance Scenarios**:

1. **Given** a parent `MoonMind.Run` is awaiting a merge automation child, **When** the child returns `merged`, **Then** the parent completes successfully and may satisfy success-only downstream dependencies.
2. **Given** a parent `MoonMind.Run` is awaiting a merge automation child, **When** the child returns `already_merged`, **Then** the parent completes successfully and may satisfy success-only downstream dependencies.
3. **Given** a parent `MoonMind.Run` is awaiting a merge automation child, **When** the child returns `blocked`, `failed`, or `expired`, **Then** the parent reaches a failed terminal state with an operator-readable merge automation reason and does not satisfy success-only downstream dependencies.
4. **Given** a parent `MoonMind.Run` is awaiting a merge automation child, **When** the child returns `canceled`, **Then** the parent reaches a canceled terminal state rather than reporting failure or success.
5. **Given** an operator cancels the parent while merge automation is active, **When** cancellation is processed, **Then** the parent requests cancellation of `MoonMind.MergeAutomation` and records truthful cancellation or cleanup information.
6. **Given** `MoonMind.MergeAutomation` is canceled while a resolver child run is active, **When** cancellation is processed, **Then** merge automation requests cancellation of the active resolver child run and does not claim successful cleanup unless cleanup completes.

### Edge Cases

- The merge automation child returns no terminal status.
- The merge automation child returns an unsupported terminal status.
- The merge automation child fails before producing a structured output.
- Cancellation races with a child terminal result.
- Best-effort child cancellation cannot be confirmed before the parent or merge automation finalizes.
- Downstream dependency evaluation observes a canceled or failed parent.

## Assumptions

- MM-353 is the active Jira issue and its Jira preset brief is the canonical Moon Spec input.
- Runtime mode is selected; documentation-only output is not sufficient.
- STORY-001, STORY-002, and STORY-003 provide the parent-owned merge automation child workflow, merge gate waiting, and resolver child re-gating behavior that this story maps to parent completion.
- Existing dependency satisfaction semantics use parent terminal success as the only success condition for downstream `dependsOn` relationships.

## Source Design Requirements

- **DESIGN-REQ-002**: Source `docs/Tasks/PrMergeAutomation.md` sections 2, 5, and 16 require downstream dependency satisfaction to remain tied to the original parent workflow and to wait for publish plus merge automation completion. Scope: in scope. Maps to FR-001, FR-002, FR-006.
- **DESIGN-REQ-012**: Source section 10.2 defines allowed `MoonMind.MergeAutomation` terminal statuses: `merged`, `already_merged`, `blocked`, `failed`, `expired`, and `canceled`. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005.
- **DESIGN-REQ-023**: Source section 17 requires parent success only for `merged` and `already_merged`, and parent failure for `blocked`, `failed`, and `expired`. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-024**: Source section 18 requires parent cancellation to cancel `MoonMind.MergeAutomation`, merge automation cancellation to cancel any in-flight resolver child run, and cleanup reporting to remain best-effort and truthful. Scope: in scope. Maps to FR-004, FR-007, FR-008.
- **DESIGN-REQ-029**: Source section 23 acceptance criteria require downstream tasks to wait for merge automation completion, non-success terminal outcomes to fail the parent except canceled, and root or child artifacts to explain waiting or failure. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat `merged` and `already_merged` merge automation child statuses as successful parent `MoonMind.Run` completion outcomes.
- **FR-002**: The system MUST satisfy downstream `dependsOn` relationships only when the original parent `MoonMind.Run` reaches terminal success after merge automation succeeds.
- **FR-003**: The system MUST treat `blocked`, `failed`, and `expired` merge automation child statuses as failed parent `MoonMind.Run` completion outcomes with operator-readable merge automation reasons.
- **FR-004**: The system MUST treat `canceled` merge automation child status as canceled parent `MoonMind.Run` completion rather than success or failure.
- **FR-005**: The system MUST fail deterministically with an operator-readable non-success outcome when merge automation returns a missing or unsupported terminal status.
- **FR-006**: The system MUST preserve the original parent workflow identity as the downstream dependency target and MUST NOT redirect dependency satisfaction to merge automation or resolver child workflow identities.
- **FR-007**: Operator-initiated parent cancellation MUST request cancellation of the active `MoonMind.MergeAutomation` child workflow when one exists.
- **FR-008**: `MoonMind.MergeAutomation` cancellation MUST request cancellation of any active resolver child `MoonMind.Run` and report cleanup as best-effort unless cancellation completion is confirmed.
- **FR-009**: Parent terminal summaries and merge automation summaries MUST expose enough sanitized state for operators to distinguish merged, already merged, blocked, failed, expired, canceled, unsupported-status, and cleanup-incomplete outcomes.
- **FR-010**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-353 and the original preset brief as traceability evidence.

### Key Entities

- **Merge Automation Terminal Status**: The child workflow status that determines the parent task terminal outcome. Allowed values are `merged`, `already_merged`, `blocked`, `failed`, `expired`, and `canceled`.
- **Parent Completion Outcome**: The final success, failure, or cancellation state emitted by the original `MoonMind.Run`.
- **Dependency Satisfaction Signal**: The parent terminal success signal consumed by downstream `dependsOn` relationships.
- **Cancellation Propagation State**: The relationship between parent cancellation, merge automation cancellation, resolver child cancellation, and truthful cleanup reporting.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Workflow-boundary tests cover 100% of allowed merge automation terminal statuses and prove the parent maps them to success, failure, or cancellation as specified.
- **SC-002**: Dependency evaluation tests prove downstream `dependsOn` relationships are satisfied only by parent terminal success and are not satisfied by failed or canceled parent outcomes.
- **SC-003**: Cancellation tests prove parent cancellation requests merge automation cancellation and merge automation cancellation requests active resolver child cancellation.
- **SC-004**: Tests prove missing or unsupported merge automation terminal statuses produce deterministic non-success outcomes with operator-readable reasons.
- **SC-005**: Verification evidence preserves MM-353 and maps every in-scope source design requirement to functional requirements and passing test evidence.
