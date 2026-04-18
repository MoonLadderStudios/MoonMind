# Research: Create Button Right Arrow

## Input Classification

Decision: Treat MM-390 as a single-story runtime feature request.

Rationale: The Jira preset brief contains one actor, one goal, and one bounded user-visible behavior: the Create Page primary submit action should use a right-pointing arrow while preserving the existing create flow.

Alternatives considered: Broad design classification was rejected because the brief does not contain multiple independently testable stories. Documentation-only intent was rejected because runtime mode was selected and the requested outcome is observable UI behavior.

## Source Requirements

Decision: Treat `docs/UI/CreatePage.md` sections 7.1 and 14 as runtime source requirements for submit placement and explicit submission behavior.

Rationale: Section 7.1 places create/edit/rerun submit actions at the bottom of the shared Steps card. Section 14 states that submission remains explicit and task creation is not triggered by attachment selection alone. These constraints keep the arrow change from becoming a submit-flow change.

Alternatives considered: Treating the source document as the implementation target was rejected because the selected mode is runtime and the source document is used only to constrain behavior.

## UI Surface

Decision: Implement the visible arrow change at the existing Create Page primary submit action surface.

Rationale: Existing tests already query the action by its accessible role and name, and existing submission tests cover the configured create endpoint. Keeping the change at this surface avoids touching task payload construction or backend execution contracts.

Alternatives considered: Adding a separate new submit control was rejected because it would duplicate the primary action and risk divergent disabled, loading, and validation behavior.

## Accessibility And Copy

Decision: Preserve a Create-oriented accessible name while adding or adjusting the right-pointing arrow as visual presentation.

Rationale: The story asks for a visual direction cue, not a replacement of the action meaning. Screen-reader users still need the action to announce as Create or task creation rather than as an icon-only arrow.

Alternatives considered: Using an icon-only button was rejected because it could obscure the primary action name and weaken discoverability.

## Responsive Stability

Decision: Verify the primary submit action remains stable in normal desktop and mobile-width render states.

Rationale: The spec requires the button text and arrow to fit without overlap or layout shift. A narrow presentation check protects the highest-risk UI regression for a small visual change.

Alternatives considered: Treating responsiveness as out of scope was rejected because the original MM-390 brief explicitly requires desktop and mobile layout stability.

## Test Strategy

Decision: Use focused Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx` for arrow presentation, accessible name, disabled/loading preservation where practical, and unchanged submit behavior. Use `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for required hermetic integration checks when Docker is available.

Rationale: MM-390 is a Create Page UI behavior story. Vitest can inspect the rendered control and submitted request without external provider credentials. The final unit runner is required by repo policy, while integration checks remain the hermetic CI tier.

Alternatives considered: Provider verification was rejected because no external provider behavior is involved. Backend-only tests were rejected because the requested outcome is visual Create Page behavior.
