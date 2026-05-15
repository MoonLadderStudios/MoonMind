# UI Interaction Contract: Menu Action Availability and Unavailable Presentation

## Scope

This contract defines the player-visible behavior for generated menu buttons in THOR-403. It applies to Play, Home navigation, Options, and future generated-button panels that consume the shared menu action model.

## Eligibility Outcomes

Every generated menu action resolves to exactly one of:
- `enabled`: the button is visible, enabled, and selectable.
- `disabled-visible`: the button is visible, disabled, and shows player-facing unavailable copy.
- `hidden-by-window`: the action is not rendered in the current panel/window.

When multiple conditions apply, `hidden-by-window` wins over `disabled-visible` for the current panel.

## Unavailable Copy

Disabled-visible actions must expose player-facing unavailable copy from one of:
- the menu action entry;
- the eligibility result;
- deterministic fallback copy when no authored or computed reason exists.

The generated button must not show empty unavailable copy for disabled-visible actions.

## Generated Button Rendering

Given a generated menu panel renders actions:
- enabled actions produce visible enabled buttons;
- disabled-visible actions produce visible disabled buttons with unavailable copy;
- hidden-by-window actions produce no button.

The same rendering contract applies to Play, Home navigation, Options, and future generated-button panels.

## Online Co-op Blocked Selection

Given Online Co-op is blocked:
- Online Co-op remains visible in the Play menu;
- the button shows explicit unavailable feedback;
- selection attempts through supported pointer, keyboard, and controller activation paths present unavailable feedback;
- selection attempts through supported pointer, keyboard, and controller activation paths do not start travel, matchmaking, session creation, session joining, or equivalent online session side effects.

## Acceptance Evidence

The contract is satisfied when automated coverage demonstrates:
- enabled generated action rendering and selection;
- disabled-visible generated action rendering with unavailable copy;
- hidden-by-window action omission;
- blocked Online Co-op feedback with zero travel/session side effects across supported activation paths;
- shared behavior across Play, Home navigation, Options, and a future-panel-compatible generated-button fixture.
