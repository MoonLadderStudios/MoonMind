# Feature Specification: Merge Automation Waits

**Feature Branch**: `186-merge-automation-waits`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**: MM-351: Evaluate merge gates with durable signal and polling waits

## Original Jira Preset Brief

```text
# MM-351 MoonSpec Orchestration Input

Jira issue: MM-351 from MM board
Summary: Evaluate merge gates with durable signal and polling waits
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-351 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-351: Evaluate merge gates with durable signal and polling waits

User Story
As a workflow operator, I need MoonMind.MergeAutomation to wait on explicit external merge-readiness state instead of time delays so resolver attempts only run when the current PR head SHA is ready.

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 8. New Workflow Type
- 10. MoonMind.MergeAutomation Input and Output
- 11. MoonMind.MergeAutomation Lifecycle
- 12. Merge Gate Evaluation
- 19. Continue-As-New
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-004
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-025
- DESIGN-REQ-029

Story Metadata
- Story ID: STORY-002
- Dependency mode: depends on STORY-001
- Story dependencies from breakdown: STORY-001

Acceptance Criteria
- MoonMind.MergeAutomation accepts parent workflow identifiers, publishContextRef, mergeAutomationConfig, and resolverTemplate without embedding large publish payloads in history.
- The workflow uses initializing, awaiting_external, executing, finalizing, completed, failed, and canceled vocabulary as applicable.
- A gate evaluation blocks resolver launch until configured review/check/Jira requirements are complete for the current head SHA.
- A new PR head SHA invalidates prior completion and returns waiting blockers for the new head when requirements are not fresh.
- External GitHub/Jira events can signal the workflow to re-evaluate before the fallback timer fires.
- Fallback polling is bounded by configured fallbackPollSeconds and does not become a fixed-delay merge strategy.
- Continue-As-New preserves parent id, publish context ref, PR number/URL, latest head SHA, gate policy, Jira key, blockers, cycle count, resolver history, and expire-at deadline.

Requirements
- Gate readiness must be state-based and head-SHA-sensitive.
- Gate output must be deterministic and machine-readable.
- Long-lived waits must remain replay-safe and compact in workflow history.
- Expired waits must produce the expired terminal status through the output contract.

Dependencies
- STORY-001

Independent Test
Run Temporal workflow tests against MoonMind.MergeAutomation with a stub PublishContext and fake gate provider. Cover waiting blockers, signal-driven re-evaluation, fallback timer re-evaluation, Continue-As-New state preservation, and a gate-open output without starting a real resolver.

Needs Clarification
- None
```

## User Story - Wait For Current PR Readiness

**Summary**: As a workflow operator, I want `MoonMind.MergeAutomation` to wait on explicit external merge-readiness state for the current pull request head SHA so resolver attempts start only when the latest revision is ready.

**Goal**: Replace fixed-delay merge automation with deterministic, head-SHA-sensitive readiness waiting that can wake from external signals, falls back to bounded polling, preserves compact state across Continue-As-New, and reports machine-readable terminal outcomes.

**Independent Test**: Run Temporal workflow-boundary tests for `MoonMind.MergeAutomation` with a compact publish context, fake readiness provider, and fake resolver-launch activity. The story passes only when blockers prevent resolver launch, external signals re-evaluate before fallback polling, fallback polling uses configured bounds, stale head SHAs invalidate readiness, Continue-As-New carries compact wait state, and gate-open output is deterministic without starting a real resolver in the test.

**Acceptance Scenarios**:

1. **Given** merge automation starts after a pull request is published, **when** the workflow receives parent identifiers, `publishContextRef`, `mergeAutomationConfig`, and `resolverTemplate`, **then** the start payload is accepted without embedding large publish payloads in workflow history.
2. **Given** the latest pull request head SHA has configured checks still running, review providers still incomplete, or Jira status still pending, **when** the gate evaluates readiness, **then** resolver launch is blocked and the workflow records machine-readable blockers for that head SHA.
3. **Given** the pull request receives a new head SHA, **when** readiness is evaluated, **then** readiness evidence for the previous SHA is invalidated and the workflow reports waiting blockers for the new current head SHA.
4. **Given** a GitHub or Jira event is signaled while the workflow is waiting, **when** the signal arrives before the fallback timer fires, **then** the workflow immediately re-evaluates readiness.
5. **Given** no external signal arrives, **when** the configured `fallbackPollSeconds` elapses, **then** the workflow re-evaluates readiness and does not use an unbounded or fixed-delay resolver strategy.
6. **Given** the workflow reaches a Continue-As-New boundary while waiting, **when** the next run starts, **then** parent id, publish context ref, PR number/URL, latest head SHA, gate policy, Jira key, blockers, cycle count, resolver history, and expire-at deadline are preserved.
7. **Given** readiness cannot be confirmed before the expire-at deadline, **when** the deadline is reached, **then** the workflow returns a deterministic `expired` terminal status and does not launch a resolver.
8. **Given** all configured readiness signals are complete for the current head SHA, **when** the gate opens, **then** the workflow emits a deterministic gate-open output and requests at most one resolver attempt for that head SHA.

### Edge Cases

- External readiness event arrives before the workflow enters its wait condition.
- Duplicate external events arrive for the same head SHA.
- Provider readiness payload contains an unknown blocker kind or secret-like details.
- Configured fallback polling is missing, zero, negative, or excessively large.
- Publish context lookup is unavailable or missing current head SHA.
- Continue-As-New is suggested during an active wait with blockers already recorded.
- The expire-at deadline passes during a wait.

## Assumptions

- STORY-001 provides the broader child workflow resolver strategy and parent publish context foundation.
- Existing GitHub and Jira integration activities remain the source of external readiness evidence.
- Resolver execution itself remains owned by a child `MoonMind.Run`; this story only gates when that resolver attempt may start.

## Source Design Requirements

- **DESIGN-REQ-004**: Add the stable internal workflow type `MoonMind.MergeAutomation` for post-publish, callback/poll-driven merge automation. Source: `docs/Tasks/PrMergeAutomation.md` section 8. Scope: in scope. Maps to FR-001 and FR-009.
- **DESIGN-REQ-010**: Accept compact `MoonMind.MergeAutomation` input containing parent identifiers, `publishContextRef`, `mergeAutomationConfig`, and `resolverTemplate`; return deterministic status, PR, cycle, resolver, head SHA, and blocker output. Source: section 10. Scope: in scope. Maps to FR-001, FR-002, FR-012, and FR-013.
- **DESIGN-REQ-011**: Use lifecycle vocabulary `initializing`, `awaiting_external`, `executing`, `finalizing`, `completed`, `failed`, and `canceled`. Source: section 11. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-012**: Evaluate PR open/closed/merged state, current head SHA, status checks, review providers, and optional Jira status before resolver launch. Source: section 12.1. Scope: in scope. Maps to FR-004 and FR-005.
- **DESIGN-REQ-013**: Gate readiness must be head-SHA-sensitive and invalidate prior readiness when a new push changes the current head SHA. Source: section 12.2. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-014**: Support external GitHub/Jira event signals, bounded timer re-evaluation, and Continue-As-New for long-lived waits. Source: section 12.3. Scope: in scope. Maps to FR-006, FR-007, and FR-008.
- **DESIGN-REQ-015**: Gate evaluation output must be deterministic and machine-readable, including status, head SHA, blockers, and resolver-readiness boolean. Source: section 12.4. Scope: in scope. Maps to FR-004 and FR-012.
- **DESIGN-REQ-016**: Preserve compact wait state across Continue-As-New, including parent workflow id, publish context ref, PR identity, latest head SHA, policy, Jira key, blockers, cycle count, resolver history, and expire-at deadline. Source: section 19. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-017**: Parent and child artifacts must expose enough merge automation state for operators to understand current blockers and resolver attempts. Source: sections 20.1 and 20.2. Scope: in scope. Maps to FR-011.
- **DESIGN-REQ-018**: Root terminal summary must include merge automation enabled state, status, PR identity, child workflow id, resolver ids, and cycle count. Source: section 20.3. Scope: out of scope for this story; summary projection depends on parent-finalization work outside this wait-loop story.
- **DESIGN-REQ-025**: Acceptance criteria require external signal completion waiting instead of fixed delays and resolver child runs with publish mode `none`. Source: section 23. Scope: in scope. Maps to FR-006, FR-007, and FR-010.
- **DESIGN-REQ-029**: Long-lived waits must keep workflow history compact and avoid large publish payloads. Source: sections 10 and 19. Scope: in scope. Maps to FR-001 and FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST register and use `MoonMind.MergeAutomation` as the canonical workflow type for post-publish merge automation waits.
- **FR-002**: `MoonMind.MergeAutomation` MUST accept compact start input containing parent workflow id, parent run id when available, `publishContextRef`, `mergeAutomationConfig`, `resolverTemplate`, and compact PR identity fields needed for waiting.
- **FR-003**: The workflow MUST expose lifecycle state using only `initializing`, `awaiting_external`, `executing`, `finalizing`, `completed`, `failed`, and `canceled` vocabulary where a state is applicable.
- **FR-004**: Gate evaluation MUST return deterministic machine-readable output containing status, current head SHA, blockers, and whether resolver launch is allowed.
- **FR-005**: Gate evaluation MUST block resolver launch when readiness evidence is missing, stale for a previous head SHA, still running, Jira-disallowed, or policy-denied for the current head SHA. Completed-but-failing checks MUST remain visible through readiness fields but MUST NOT be treated as wait-only blockers.
- **FR-006**: External GitHub/Jira event signals MUST cause readiness re-evaluation before the fallback polling timer would otherwise fire.
- **FR-007**: Fallback polling MUST be bounded by configured `fallbackPollSeconds`, with invalid or missing values normalized to a safe bounded default.
- **FR-008**: Continue-As-New input MUST preserve parent id, publish context ref, PR number/URL, latest head SHA, gate policy, Jira key, blockers, cycle count, resolver history, and expire-at deadline.
- **FR-009**: Workflow and worker registration MUST expose `MoonMind.MergeAutomation` without retaining `MoonMind.MergeGate` as a canonical alias.
- **FR-010**: Resolver launch requests MUST use child `MoonMind.Run` with pr-resolver intent and publish mode `none`.
- **FR-011**: Operator-visible summary state MUST include status, PR link, current blockers, latest head SHA, current cycle, and resolver attempt history without secret-like details.
- **FR-012**: Expired waits MUST return terminal status `expired` with current blockers and MUST NOT launch a resolver.
- **FR-013**: Gate-open output MUST be deterministic for tests and retries, including PR number/URL, latest head SHA, cycles, blockers, and resolver child workflow ids.

### Key Entities

- **Merge Automation Start Input**: Compact workflow start payload for one published pull request and merge automation policy.
- **Publish Context Reference**: Artifact-backed reference to larger publish context, with only compact PR identity carried in workflow input/history.
- **Merge Gate Evaluation**: Deterministic readiness result for one current head SHA.
- **Readiness Blocker**: Sanitized machine-readable reason resolver launch cannot start yet.
- **Resolver Attempt History**: Compact record of resolver child runs launched for the current merge automation workflow.
- **Continue-As-New State**: Compact restart payload preserving long-lived wait progress.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Focused model and parent-start tests prove 100% of canonical start payloads use `MoonMind.MergeAutomation`, include `publishContextRef`, and reject missing current head SHA.
- **SC-002**: Workflow tests prove 100% of stale, blocked, Jira-disallowed, policy-denied, and unavailable-readiness cases do not launch a resolver and produce machine-readable blockers.
- **SC-003**: Workflow tests prove signaled waits re-evaluate before fallback polling and unsignaled waits re-evaluate after the configured fallback bound.
- **SC-004**: Workflow tests prove Continue-As-New payloads preserve all required compact state fields.
- **SC-005**: Workflow tests prove expired waits return `expired` and ready gates produce deterministic gate-open output with at most one resolver launch request for the current head SHA.
- **SC-006**: Source design coverage for in-scope DESIGN-REQ-004, DESIGN-REQ-010 through DESIGN-REQ-017, DESIGN-REQ-025, and DESIGN-REQ-029 is mapped to passing verification evidence.
