# Feature Specification: PR Resolver Child Re-Gating

**Feature Branch**: `187-pr-resolver-regate`
**Created**: 2026-04-16
**Status**: Draft
**Input**: User description: "Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-352 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-352: Run pr-resolver children and re-gate after resolver pushes

Story ID: STORY-003

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 6.3 Why the resolver itself is a child MoonMind.Run
- 13. Resolver Child Workflow Strategy
- 14. Post-Resolver Re-Gating
- 15. Shared Gate Semantics Between Gate and Resolver
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-005
- DESIGN-REQ-014
- DESIGN-REQ-016
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-029

User Story
As a maintainer relying on merge automation, I need each resolver attempt to run through the existing pr-resolver skill substrate and return a disposition so MoonMind can merge, retry through the gate, or stop for manual review.

Independent Test
Use a workflow-boundary test where the gate opens, a stub child MoonMind.Run returns each allowed mergeAutomationDisposition, and the parent MergeAutomation workflow either completes, re-enters awaiting_external with an incremented cycle, or fails with the expected blocker outcome.

Acceptance Criteria
- When the gate opens, MoonMind.MergeAutomation starts a child MoonMind.Run rather than directly invoking the pr-resolver skill.
- Resolver child initialParameters.publishMode is exactly none.
- Resolver child task.tool is exactly {type: skill, name: pr-resolver, version: 1.0}.
- A resolver result with merged or already_merged completes merge automation successfully.
- A resolver result with reenter_gate returns to gate evaluation and does not treat the prior review/check signal as final authority after a new push.
- A resolver result with manual_review or failed produces a non-success merge automation outcome with blockers or failure summary.
- Gate and resolver contract tests use the same logical blocker categories and head-SHA freshness rules.

Requirements
- Resolver execution must reuse MoonMind.Run substrate for workspace, runtime setup, artifacts, logs, and skill routing.
- Resolver-generated pushes must not allow immediate merge unless external readiness is fresh for the new head SHA.
- Resolver disposition must be explicit so the workflow does not infer high-level outcomes from free-form logs.

Dependencies
- STORY-001
- STORY-002

Implementation Notes
- Implement resolver execution as a child MoonMind.Run, not a direct pr-resolver skill invocation inside MoonMind.MergeAutomation.
- Configure the resolver child with `initialParameters.publishMode` set exactly to `none`.
- Configure the resolver child task tool as `{type: skill, name: pr-resolver, version: 1.0}`.
- Define and consume explicit `mergeAutomationDisposition` values for at least `merged`, `already_merged`, `reenter_gate`, `manual_review`, and `failed`.
- Treat `merged` and `already_merged` as successful merge automation completion dispositions.
- Treat `reenter_gate` as a signal to repeat gate evaluation, increment the gate cycle, and require fresh readiness for the new head SHA rather than trusting prior external review or check signals.
- Treat `manual_review` and `failed` as non-success merge automation outcomes with clear blockers or failure summaries.
- Keep gate and resolver boundary tests aligned on shared logical blocker categories and head-SHA freshness rules.

Source Design Coverage
- DESIGN-REQ-005
- DESIGN-REQ-014
- DESIGN-REQ-016
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-029"

## User Story - Run Resolver Children And Re-Gate

**Summary**: As a maintainer relying on merge automation, I want each resolver attempt to run as a child MoonMind.Run with an explicit disposition so that MoonMind can merge, re-enter the gate after resolver pushes, or stop for manual review without inferring outcomes from logs.

**Goal**: Merge automation preserves MoonMind.Run execution boundaries for pr-resolver attempts, handles resolver outcomes deterministically, and requires fresh external readiness after resolver-generated pushes.

**Independent Test**: Run a workflow-boundary test where the merge gate opens, stub resolver child MoonMind.Run results return each allowed merge automation disposition, and MoonMind.MergeAutomation either succeeds, re-enters awaiting external readiness with an incremented cycle, or returns a non-success outcome with blockers.

**Acceptance Scenarios**:

1. **Given** merge automation reaches an open gate, **When** it launches the resolver attempt, **Then** it starts a child MoonMind.Run whose top-level initial parameters set publishMode to none and whose task tool is the pr-resolver skill at version 1.0.
2. **Given** the resolver child returns merged or already_merged, **When** MoonMind.MergeAutomation consumes the result, **Then** merge automation completes successfully with the matching terminal status.
3. **Given** the resolver child returns reenter_gate after changing the PR head, **When** MoonMind.MergeAutomation consumes the result, **Then** it increments the resolver cycle, returns to awaiting external readiness, and does not treat prior review or check signals as fresh for the new head SHA.
4. **Given** the resolver child returns manual_review or failed, **When** MoonMind.MergeAutomation consumes the result, **Then** merge automation returns a non-success outcome that includes blockers or a failure summary.
5. **Given** gate and resolver checks report blockers for the same PR head, **When** their outputs are compared, **Then** they use the same logical blocker categories and head-SHA freshness rules.

### Edge Cases

- The resolver child returns no machine-readable mergeAutomationDisposition.
- The resolver child returns an unsupported disposition value.
- The resolver child reports a reenter_gate disposition without a new head SHA.
- Prior external review or status-check signals exist for an older head SHA when the resolver requests re-gating.
- A resolver child is canceled while merge automation is awaiting its result.

## Assumptions

- STORY-001 and STORY-002 provide the parent-owned merge automation child workflow and initial gate evaluation behavior required before this story can run resolver attempts.
- The resolver child result can expose compact structured output that MoonMind.MergeAutomation can read without embedding large logs or transcripts in workflow history.

## Source Design Requirements

- **DESIGN-REQ-005**: Source `docs/Tasks/PrMergeAutomation.md` section 6.3. Resolver attempts should run as child MoonMind.Run executions to reuse workspace setup, artifacts, logs, and skill routing. Scope: in scope. Mapped to FR-001, FR-002.
- **DESIGN-REQ-014**: Source section 13.1. When the gate opens, MoonMind.MergeAutomation must start a child MoonMind.Run for pr-resolver. Scope: in scope. Mapped to FR-001.
- **DESIGN-REQ-016**: Source section 13.1. Resolver child runs must set task.tool to the pr-resolver skill at version 1.0 and top-level publishMode to none. Scope: in scope. Mapped to FR-002, FR-003.
- **DESIGN-REQ-019**: Source section 13.3. pr-resolver should expose a machine-readable merge automation disposition. Scope: in scope. Mapped to FR-004.
- **DESIGN-REQ-020**: Source section 13.3. Allowed resolver dispositions are merged, already_merged, reenter_gate, manual_review, and failed. Scope: in scope. Mapped to FR-004, FR-005, FR-006, FR-007.
- **DESIGN-REQ-021**: Source section 14. Resolver-generated pushes must return control to the gate instead of treating the resolver as final merge authority. Scope: in scope. Mapped to FR-006.
- **DESIGN-REQ-022**: Source section 15. Gate and resolver logic must share logical blocker categories and head-SHA freshness semantics. Scope: in scope. Mapped to FR-008.
- **DESIGN-REQ-029**: Source section 23. Design acceptance criteria require resolver child launch, publishMode none, resolver re-gating, and truthful terminal outcomes. Scope: in scope. Mapped to FR-001 through FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST launch each pr-resolver attempt from MoonMind.MergeAutomation as a child MoonMind.Run rather than directly executing the pr-resolver skill in the merge automation workflow.
- **FR-002**: System MUST configure each resolver child run to use the standard MoonMind.Run execution substrate for workspace setup, runtime setup, artifacts, logs, and skill routing.
- **FR-003**: System MUST set resolver child top-level initialParameters.publishMode exactly to none and resolver child task.tool exactly to type skill, name pr-resolver, and version 1.0.
- **FR-004**: System MUST consume a machine-readable mergeAutomationDisposition from each resolver child result instead of inferring merge automation outcomes from free-form logs.
- **FR-005**: System MUST treat merged and already_merged dispositions as successful merge automation completion outcomes.
- **FR-006**: System MUST treat reenter_gate as a non-terminal outcome that increments the resolver cycle and returns MoonMind.MergeAutomation to awaiting external readiness for the current PR head.
- **FR-007**: System MUST treat manual_review and failed dispositions as non-success merge automation outcomes with operator-visible blockers or failure summaries.
- **FR-008**: System MUST keep merge gate and resolver contract tests aligned on shared logical blocker categories and head-SHA freshness rules.
- **FR-009**: System MUST fail fast with a deterministic non-success outcome when the resolver child result omits mergeAutomationDisposition or returns an unsupported value.
- **FR-010**: System MUST retain Jira issue key MM-352 and the original Jira preset brief in MoonSpec artifacts and verification output.

### Key Entities

- **Resolver Child Run**: A child MoonMind.Run started by MoonMind.MergeAutomation for one pr-resolver attempt, including publish mode, tool identity, PR inputs, and child workflow identity.
- **Merge Automation Disposition**: A compact machine-readable result emitted by the resolver child that tells merge automation whether to complete, re-enter the gate, stop for manual review, or fail.
- **Gate Freshness State**: The PR head SHA, readiness signals, and blocker categories used to decide whether resolver execution or merge completion is currently allowed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Workflow-boundary tests prove resolver child launch uses MoonMind.Run with publishMode none and pr-resolver skill version 1.0.
- **SC-002**: Tests cover all allowed resolver dispositions: merged, already_merged, reenter_gate, manual_review, and failed.
- **SC-003**: Tests prove reenter_gate moves merge automation back to awaiting external readiness and does not reuse stale readiness for a previous head SHA.
- **SC-004**: Tests prove missing or unsupported resolver dispositions produce deterministic non-success outcomes.
- **SC-005**: Verification evidence preserves MM-352 and maps every in-scope source design requirement to functional requirements.
