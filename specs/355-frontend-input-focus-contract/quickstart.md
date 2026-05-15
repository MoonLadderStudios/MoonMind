# Quickstart: Frontend Input and Focus Contract

## Preconditions

- Use the THOR Tactics repository for implementation. The current MoonMind managed workspace does not contain the target Unreal/game source files.
- Confirm that the proposed target paths in `plan.md` match the THOR repository, or adapt them to established equivalent frontend modules before writing tests.
- Preserve the Jira issue key `THOR-404` and the spec path `specs/355-frontend-input-focus-contract/spec.md` in implementation notes and final verification.
- Run the story with authored presentation assets disabled or absent for native fallback widget coverage.

## TDD Sequence

1. Write failing unit tests for shared default input behavior on menu screens and panels.
2. Write failing unit tests for generated button focusability and initial focus target selection.
3. Write failing unit tests proving mouse click, keyboard confirm, and controller confirm converge on the same coordinator behavior.
4. Write failing unit tests for missing focus return target fallback behavior.
5. Write failing integration/automation tests for Home -> Play -> Back and Home -> Options -> Back with native fallback widgets.
6. Implement the smallest code changes needed to pass the tests.
7. Re-run unit and integration automation and capture evidence for `/moonspec-verify`.

## Unit Test Strategy

Target command: use the THOR repository's standard unit/automation test runner for non-rendered menu model, focus target selection, input config defaults, and coordinator activation tests.

Required unit coverage:
- Default confirm and cancel/back input behavior for active menu screens and panels.
- Generated action button focusability.
- Initial focus selection when one or more generated actions are valid.
- Pointer, keyboard confirm, and controller confirm routing into the same coordinator action.
- Previous-state and focus return target selection.
- Fallback focus selection when the preferred return target is unavailable.

## Integration Test Strategy

Target command: use the THOR repository's standard game automation test runner for player-visible native fallback menu flows.

Required integration coverage:
- Home fallback surface assigns initial focus.
- Play fallback surface assigns initial focus after Home -> Play.
- Options fallback surface assigns initial focus after Home -> Options.
- Mouse click, keyboard confirm, and controller confirm activate the same generated action behavior.
- Back/Cancel returns from Play to Home and restores focus to Play.
- Back/Cancel returns from Options to Home and restores focus to Options.
- Authored presentation assets absent does not break the native fallback input/focus contract.

## End-to-End Verification

The story is ready for final verification when:
- all required unit tests pass;
- generated menu automation passes for initial focus, confirm parity, cancel/back, and focus restoration;
- Play and Options return focus to the originating Home actions when valid;
- fallback focus is valid when a preferred return target is unavailable;
- native fallback widgets remain fully navigable without authored presentation assets.
