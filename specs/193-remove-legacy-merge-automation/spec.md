# Feature Specification: Remove Legacy Merge Automation Workflow

**Feature Branch**: `193-remove-legacy-merge-automation`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
# MM-364 MoonSpec Orchestration Input

## Source

- Jira issue: MM-364
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [PrMergeAutomation] Remove dead legacy MergeAutomation workflow code
- Labels: `cleanup`, `pr-merge-automation`, `temporal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-364 from MM project
Summary: [PrMergeAutomation] Remove dead legacy MergeAutomation workflow code
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-364 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-364: [PrMergeAutomation] Remove dead legacy MergeAutomation workflow code

User Story
As a MoonMind platform maintainer, I want the dead legacy MergeAutomation workflow implementation removed so the repository has one unambiguous PR merge automation execution path.

Acceptance Criteria
- Remove the duplicate legacy `MoonMindMergeAutomationWorkflow` workflow class from `moonmind/workflows/temporal/workflows/merge_gate.py` while preserving helper functions still used by `merge_automation.py`, such as readiness classification, resolver request construction, idempotency key creation, and timeout helpers.
- Confirm the only registered `MoonMind.MergeAutomation` workflow implementation is `moonmind/workflows/temporal/workflows/merge_automation.py`.
- Update or remove tests that import or exercise the legacy `merge_gate.py` workflow class directly; tests should validate the active workflow path instead.
- Grep the repository for references to `merge_automation.create_resolver_run` and remove the legacy activity path if it is no longer reachable after the workflow cleanup, including activity catalog/runtime registration and tests.
- Keep the invariant that merge automation launches `pr-resolver` through a child `MoonMind.Run` with `publishMode=none`; do not replace it with direct merge logic.
- Update `docs/Tasks/PrMergeAutomation.md` or related implementation notes if they imply both workflow paths are active.

Implementation Notes
- The active worker registers `MoonMindMergeAutomationWorkflow` from `moonmind/workflows/temporal/workflows/merge_automation.py`.
- `moonmind/workflows/temporal/workflows/merge_gate.py` still defines another class with the same workflow name and an older path that launches resolver runs via `merge_automation.create_resolver_run`.
- Remove the duplicate workflow path to avoid confusing operator and implementation review of PR merge automation.
- Preserve helper functions in `merge_gate.py` when they remain used by the active `merge_automation.py` workflow.
- Remove the legacy `merge_automation.create_resolver_run` activity path, including activity catalog/runtime registration and tests, when grep confirms it is no longer reachable.

Verification
- Run focused unit tests for merge automation and run workflow boundary coverage.
- Run `./tools/test_unit.sh` before completion.
- Include grep evidence that only one workflow class is registered for `MoonMind.MergeAutomation` and that no dead legacy resolver-run activity path remains unless explicitly justified.

Out of Scope
- Adding a Mission Control merge automation enablement UI.
- Changing pr-resolver behavior.
- Changing the merge readiness policy semantics except where needed to remove unreachable code safely.
```

## User Story - Unambiguous Merge Automation Runtime Path

**Summary**: As a MoonMind platform maintainer, I want the dead legacy MergeAutomation workflow implementation removed so the repository has one unambiguous PR merge automation execution path.

**Goal**: Maintain exactly one runtime workflow implementation for `MoonMind.MergeAutomation` while preserving the active child `MoonMind.Run` resolver behavior and shared helper semantics.

**Independent Test**: Can be fully tested by importing the active worker workflow registry, exercising merge automation helper and workflow-boundary tests, and grepping the repository to prove there is only one `MoonMind.MergeAutomation` workflow class and no legacy resolver-run activity path remains.

**Acceptance Scenarios**:

1. **Given** the repository is loaded by a Temporal worker, **When** workflow classes are registered, **Then** `MoonMind.MergeAutomation` resolves only to `moonmind/workflows/temporal/workflows/merge_automation.py`.
2. **Given** the active merge automation workflow needs readiness classification or resolver request construction, **When** those helpers are imported from `merge_gate.py`, **Then** the helpers remain available without importing a second workflow class.
3. **Given** merge automation reaches a ready PR state, **When** the active workflow launches remediation, **Then** it starts child workflow `MoonMind.Run` with the `pr-resolver` skill and `publishMode=none`.
4. **Given** repository references are searched, **When** `merge_automation.create_resolver_run` is grepped, **Then** no live activity catalog, runtime binding, workflow, or test path depends on that legacy activity.
5. **Given** merge automation documentation is reviewed, **When** it describes the active execution path, **Then** it does not imply both legacy and active workflow implementations are registered.

### Edge Cases

- Existing helper tests should keep validating readiness classification, blocker sanitization, resolver request construction, idempotency keys, and timeout helper behavior after the workflow class is removed from `merge_gate.py`.
- Tests that used the legacy workflow class must either move to the active workflow or be removed when they only cover the deleted activity-based resolver path.
- Removing the legacy activity must not remove `merge_automation.evaluate_readiness`, which remains used by the active workflow.
- The cleanup must not change merge readiness policy semantics except where unreachable legacy code is deleted.

## Assumptions

- In-flight compatibility for the deleted legacy workflow class is not preserved because MoonMind is pre-release and the active worker already registers `MoonMind.MergeAutomation` from `merge_automation.py`.
- `merge_gate.py` remains an acceptable helper module name for shared merge automation logic in this story; renaming helpers is out of scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose exactly one registered `MoonMind.MergeAutomation` workflow implementation, and that implementation MUST be the active workflow in `moonmind/workflows/temporal/workflows/merge_automation.py`.
- **FR-002**: The system MUST remove the duplicate legacy `MoonMindMergeAutomationWorkflow` class from `moonmind/workflows/temporal/workflows/merge_gate.py` while preserving helper functions used by the active workflow.
- **FR-003**: The system MUST keep active merge automation resolver launches as child `MoonMind.Run` executions using `pr-resolver` with `publishMode=none`.
- **FR-004**: The system MUST remove the unreachable `merge_automation.create_resolver_run` activity path from workflow code, activity catalog/runtime registration, and tests when the active workflow no longer calls it.
- **FR-005**: Tests MUST validate the active workflow path and helper behavior rather than importing or exercising the deleted legacy workflow class.
- **FR-006**: Documentation or implementation notes MUST not imply that both legacy and active merge automation workflow paths are active.
- **FR-007**: MoonSpec artifacts, verification evidence, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-364` and the original Jira preset brief.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Repository search finds only one `class MoonMindMergeAutomationWorkflow` definition and it is in `merge_automation.py`.
- **SC-002**: Repository search finds no live `merge_automation.create_resolver_run` references outside historical specs or generated verification notes.
- **SC-003**: Focused unit tests for merge automation helper behavior and active workflow boundary behavior pass.
- **SC-004**: `./tools/test_unit.sh` passes or reports only an environment blocker unrelated to MM-364.
- **SC-005**: Final verification can compare implementation evidence against the preserved `MM-364` Jira preset brief.
