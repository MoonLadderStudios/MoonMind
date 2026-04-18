# Research: Create Task Publish Controls

## Input Classification

Decision: Single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-412-moonspec-orchestration-input.md`, `specs/208-create-task-publish-controls/spec.md`.
Rationale: The brief asks for one independently testable Create-page authoring change: represent merge automation as a PR-specific Publish Mode choice in the Steps card while preserving existing runtime contracts.
Alternatives considered: Broad design classification was rejected because the brief identifies one primary UI/runtime behavior story and explicit file targets.
Test implications: Unit and integration-style UI tests are required.

## Existing Create Page Placement

Decision: Repository, Branch, and Publish Mode placement is implemented but needs MM-412 regression coverage.
Evidence: `frontend/src/entrypoints/task-create.tsx` renders GitHub Repo plus Branch and Publish Mode controls in the Steps card footer; existing tests assert Branch and Publish Mode belong to `data-canonical-create-section="Steps"`.
Rationale: Placement work should be preserved, not rewritten.
Alternatives considered: Moving controls again was rejected because current placement already matches the desired-state structure.
Test implications: Update/extend placement test to include absence of standalone merge automation.

## Merge Automation UI Shape

Decision: Current standalone checkbox is partial and must be replaced by a combined Publish Mode option.
Evidence: `frontend/src/entrypoints/task-create.tsx` renders a checkbox labeled `Enable merge automation` in Execution context when `mergeAutomationAvailable` is true; tests cover checkbox visibility and payload behavior.
Rationale: MM-412 requires merge automation to be authored through Publish Mode, not as a separate control.
Alternatives considered: Keeping the checkbox and adding explanatory copy was rejected because it violates the brief and source design alignment.
Test implications: Add failing tests proving the checkbox is absent and the select includes a PR-specific merge automation option when eligible.

## Publish Selection Mapping

Decision: Add a UI-layer combined publish selection that maps to existing payload semantics.
Evidence: Submission currently validates `publishMode` against `none`, `branch`, `pr` and conditionally emits `mergeAutomation.enabled=true` from checkbox state.
Rationale: The backend contract must remain unchanged, so the combined UI value should not be submitted as a new publish mode.
Alternatives considered: Adding a backend publish enum was rejected by MM-412.
Test implications: Request-shape tests must cover None, Branch, PR, and PR with Merge Automation.

## Resolver And Runtime Constraints

Decision: Existing resolver constraints must be applied to the combined UI value.
Evidence: `isResolverSkill(effectiveSkillId)` currently hides and clears the standalone checkbox, while Jira breakdown preset can force publish mode to `none`.
Rationale: A stale combined value must not silently submit merge automation when skill or preset constraints disallow it.
Alternatives considered: Relying only on submission-time omission was rejected because the UI must not leave invalid selections active silently.
Test implications: Add coverage for direct resolver skill and template resolver cases.

## Edit And Rerun Hydration

Decision: Draft reconstruction needs a visible PR-with-merge state when stored task input has PR publishing and merge automation enabled.
Evidence: Current hydration sets `publishMode` from reconstructed draft state but does not represent merge automation as a Publish Mode value.
Rationale: MM-412 explicitly requires legacy edit/rerun states to map deterministically into the combined selection.
Alternatives considered: Showing PR and separately hiding merge automation was rejected because it loses visible authored intent.
Test implications: Add hydration tests for stored PR-with-merge, PR without merge, Branch, and None.

## Documentation Alignment

Decision: Update `docs/UI/CreatePage.md` to describe merge automation as a Publish Mode choice rather than a standalone Execution context checkbox.
Evidence: Section 10 still lists `Enable merge automation` under Execution context, while sections 5 and 7.6 already place Publish Mode in Steps.
Rationale: Canonical docs must describe desired-state behavior and not retain the old split UI.
Alternatives considered: Leaving docs unchanged was rejected by MM-412 acceptance criteria.
Test implications: Documentation is checked by final review, not executable tests.
