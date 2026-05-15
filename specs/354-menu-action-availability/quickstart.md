# Quickstart: Menu Action Availability and Unavailable Presentation

## Preconditions

- Use the THOR Tactics repository for implementation. The current MoonMind managed workspace does not contain the target Unreal/game source files.
- Confirm that the proposed target paths in `plan.md` match the THOR repository, or adapt them to established equivalent frontend/online modules before writing tests.
- Preserve the Jira issue key `THOR-403` and the spec path `specs/354-menu-action-availability/spec.md` in implementation notes and final verification.
- Do not hide Online Co-op while it is blocked, and do not trigger travel or session side effects from blocked selection attempts.

## TDD Sequence

1. Write failing unit tests for action eligibility outcomes:
   - enabled actions;
   - disabled-visible actions with authored, computed, and fallback unavailable reasons;
   - hidden-by-window actions;
   - precedence when an action is both unavailable and outside the current window.
2. Write failing unit tests for generated button view models:
   - enabled button has no unavailable copy;
   - disabled-visible button is disabled and shows unavailable copy;
   - hidden-by-window action does not produce a button.
3. Write failing unit tests for blocked Online Co-op selection side-effect guards.
4. Write failing integration/automation tests for Play, Home navigation, Options, and future-panel generated-button behavior.
5. Implement the smallest code changes needed to pass the tests.
6. Re-run unit and integration automation and capture evidence for `/speckit.verify`.

## Unit Test Strategy

Target command: use the THOR repository's standard unit/automation test runner for non-rendered menu model, generated button view-model, and blocked-selection guard tests.

Required unit coverage:
- Eligibility state resolution for enabled, disabled-visible, and hidden-by-window actions.
- Authored, eligibility-produced, and fallback unavailable reasons.
- Generated button state for enabled, disabled-visible, and hidden actions.
- Hidden-by-window precedence over disabled-visible.
- Action state changes between enabled and disabled-visible while a panel is open.
- Multiple disabled-visible actions on one generated panel.
- Blocked Online Co-op does not invoke travel/session side-effect adapters.

## Integration Test Strategy

Target command: use the THOR repository's standard game automation test runner for player-visible generated menu flows.

Required integration coverage:
- Play panel renders Online Co-op as visible disabled when blocked.
- Blocked Online Co-op selection shows unavailable feedback and produces no travel/session side effects across supported pointer, keyboard, and controller activation paths.
- Home navigation and Options generated-button panels render disabled-visible actions with unavailable copy.
- Hidden-by-window actions are absent from generated panels.
- A future-panel-compatible generated-button fixture receives the same behavior without panel-specific logic.

## End-to-End Verification

The story is ready for final verification when:
- all required unit tests pass;
- generated menu automation passes for enabled, disabled-visible, hidden-by-window, and blocked-selection behavior;
- Online Co-op remains visible while blocked;
- no blocked selection starts travel or session operations;
- the shared generated-button path covers Play, Home navigation, Options, and future panels.
