# Feature Specification: Merge Automation Visibility

**Feature Branch**: `189-merge-automation-visibility`
**Created**: 2026-04-16
**Status**: Draft
**Input**:

```text
# MM-354 MoonSpec Orchestration Input

## Source

- Jira issue: MM-354
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose merge automation status, settings, and artifacts
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-354 from MM project
Summary: Expose merge automation status, settings, and artifacts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-354 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-354: Expose merge automation status, settings, and artifacts

User Story
As an operator watching Mission Control, I need merge automation settings, blockers, resolver attempts, workflow links, and durable artifacts so I can understand why a PR-publishing task is waiting, merged, failed, or canceled.

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 20. Visibility and Artifacts
- 21. UI Contract
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-006
- DESIGN-REQ-018
- DESIGN-REQ-026
- DESIGN-REQ-027
- DESIGN-REQ-029

Story Metadata
- Story ID: STORY-005
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004

Independent Test
Run API/UI contract tests against a task projection containing merge automation metadata and artifact refs. Assert that Mission Control receives status, blockers, PR link, head SHA, cycles, resolver child links, and run summary data without a separate dependency/schedule resource.

Acceptance Criteria
- PR publish settings can enable automatic resolve/merge, configure external review signal trigger, optional Jira gate, and optional review providers.
- Parent task detail exposes status, PR URL, current blockers, latest head SHA, current cycle, resolver attempt history, and child workflow links.
- MoonMind.MergeAutomation writes `reports/merge_automation_summary.json`.
- MoonMind.MergeAutomation writes `artifacts/merge_automation/gate_snapshots/<cycle>.json` and `artifacts/merge_automation/resolver_attempts/<attempt>.json`.
- Parent `reports/run_summary.json` includes mergeAutomation enabled, status, prNumber, prUrl, childWorkflowId, resolverChildWorkflowIds, and cycles.
- Mission Control does not expose merge automation as a separate dependency or scheduling surface.

Requirements
- Operator-visible state must explain waiting and failed merge automation outcomes.
- Artifacts must be durable and inspectable.
- UI settings must remain scoped to PR publish configuration.

Implementation Notes
- Add or update the PR publish settings surface so merge automation configuration remains part of PR publishing, including automatic resolve/merge, trigger configuration, optional Jira gate, and optional review-provider configuration.
- Surface merge automation state on the parent task detail as compact operator-facing metadata: status, PR URL, current blockers, latest head SHA, cycle count, resolver attempt history, and child workflow links.
- Persist merge automation child artifacts at `reports/merge_automation_summary.json`, `artifacts/merge_automation/gate_snapshots/<cycle>.json`, and `artifacts/merge_automation/resolver_attempts/<attempt>.json`.
- Include merge automation summary data in the parent `reports/run_summary.json`, including enabled state, status, PR number, PR URL, child workflow id, resolver child workflow ids, and cycle count.
- Keep merge automation out of separate dependency and scheduling surfaces; it is parent-owned PR publish behavior.
- Cover the API projection, UI contract, artifact references, and run summary shape with tests that exercise the real task-detail or dashboard boundary.

Source Design Coverage
- DESIGN-REQ-006
- DESIGN-REQ-018
- DESIGN-REQ-026
- DESIGN-REQ-027
- DESIGN-REQ-029

Needs Clarification
- None
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Inspect Merge Automation State

**Summary**: As an operator watching Mission Control, I want merge automation settings, blockers, resolver attempts, workflow links, and artifacts exposed on the parent task so that I can understand why a PR-publishing task is waiting, merged, failed, or canceled.

**Goal**: Operators can diagnose merge automation from the original task detail and durable run artifacts without opening a separate dependency or schedule surface.

**Independent Test**: Run API/UI contract tests against a task projection containing merge automation metadata and artifact refs, plus workflow tests for merge automation artifact writes. The story passes when Mission Control and run summary data expose status, blockers, PR link, latest head SHA, cycles, resolver child links, and artifact references while merge automation remains scoped to PR publishing.

**Acceptance Scenarios**:

1. **Given** a PR-publishing task has merge automation enabled, **When** the parent task detail is viewed, **Then** it exposes merge automation status, PR URL, blockers, latest head SHA, current cycle, resolver attempt history, and child workflow links.
2. **Given** merge automation evaluates readiness or launches resolver work, **When** durable artifacts are inspected, **Then** summary, gate snapshot, and resolver attempt artifacts exist at the documented paths with compact operator-readable state.
3. **Given** the parent run writes `reports/run_summary.json`, **When** merge automation was enabled, **Then** the summary includes a `mergeAutomation` object with enabled state, status, PR number, PR URL, child workflow id, resolver child workflow ids, and cycle count.
4. **Given** an operator configures PR publishing, **When** merge automation settings are shown or submitted, **Then** the settings remain scoped to PR publish configuration and do not create a separate dependency or scheduling surface.
5. **Given** blockers or resolver attempt details contain provider data, **When** they are surfaced to operators, **Then** only bounded, sanitized, compact fields are exposed.

### Edge Cases

- Merge automation is configured but no pull request URL or head SHA is available.
- Merge automation is waiting with no blockers yet.
- Merge automation fails before a resolver child is launched.
- Resolver child workflow identifiers are absent, duplicated, or already completed.
- Artifact creation fails while workflow state should still complete truthfully.

## Assumptions

- MM-354 depends on the prior merge automation stories that already start, wait, re-gate, and propagate merge outcomes.
- The active story is limited to visibility, durable artifacts, run summary projection, and Mission Control display.
- Existing artifact storage is the durable surface for merge automation JSON reports.

## Source Design Requirements

- **DESIGN-REQ-006**: Source sections 9.1 and 21 require merge automation configuration to remain part of PR publishing settings, including automatic resolve/merge, external review trigger, optional Jira gate, and review-provider configuration. Scope: in scope. Maps to FR-001 and FR-009.
- **DESIGN-REQ-018**: Source sections 20.1 and 20.3 require operator-visible parent detail and root terminal summary state for merge automation. Scope: in scope. Maps to FR-002, FR-003, FR-006, and FR-007.
- **DESIGN-REQ-026**: Source section 20.1 requires parent task detail to expose status, PR link, blockers, latest head SHA, current cycle, resolver attempt history, and child workflow links. Scope: in scope. Maps to FR-002, FR-003, FR-004, and FR-008.
- **DESIGN-REQ-027**: Source section 20.2 requires `MoonMind.MergeAutomation` to write summary, gate snapshot, and resolver attempt artifacts. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-029**: Source section 23 requires root and child artifacts to expose enough state for Mission Control to explain waiting or failure. Scope: in scope. Maps to FR-002, FR-005, FR-006, and FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST keep merge automation settings scoped to PR publish configuration and MUST NOT expose merge automation as a separate dependency or scheduling surface.
- **FR-002**: Parent task detail MUST expose compact merge automation state when merge automation is active or completed.
- **FR-003**: Exposed merge automation state MUST include status, PR URL, latest head SHA, current cycle count, child workflow id, resolver child workflow ids, and resolver attempt history when available.
- **FR-004**: Exposed merge automation state MUST include current blockers with bounded summaries and sources when blockers are present.
- **FR-005**: `MoonMind.MergeAutomation` MUST write durable JSON artifacts for the summary, each gate snapshot, and each resolver attempt using the documented artifact names.
- **FR-006**: Parent `reports/run_summary.json` MUST include a compact top-level `mergeAutomation` object when merge automation was enabled.
- **FR-007**: The run summary `mergeAutomation` object MUST include enabled state, status, PR number, PR URL, child workflow id, resolver child workflow ids, and cycles when known.
- **FR-008**: Mission Control MUST render merge automation state from run summary or task projection data without requiring a separate dependency or schedule resource.
- **FR-009**: Merge automation configuration fields MUST include automatic resolve/merge, external review trigger, optional Jira gate, and review provider configuration where those inputs are available.
- **FR-010**: Visibility payloads MUST remain compact and sanitized, avoiding raw provider payloads, secrets, and large workflow history content.

### Key Entities

- **Merge Automation Visibility State**: Compact operator-facing state derived from parent publish context and merge automation child results.
- **Merge Automation Artifact Set**: Durable JSON artifacts for summary, gate snapshots, and resolver attempts.
- **Run Summary Merge Automation Projection**: Top-level `mergeAutomation` object in the parent run summary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: API/UI tests verify that Mission Control can render status, PR URL, blockers, latest head SHA, cycles, resolver child links, and artifact refs from one task detail payload.
- **SC-002**: Workflow tests verify that merge automation writes all three documented artifact categories during readiness and resolver paths.
- **SC-003**: Run summary tests verify a merge-automation-enabled parent includes the top-level `mergeAutomation` object with all required known fields.
- **SC-004**: Tests verify merge automation remains absent from dependency and scheduling projections.
- **SC-005**: Visibility payload tests verify blocker summaries are bounded and no raw provider payloads are surfaced.
