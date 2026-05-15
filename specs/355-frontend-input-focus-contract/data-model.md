# Data Model: Frontend Input and Focus Contract

## Menu Surface

Represents an active frontend screen or panel participating in menu navigation.

Fields and state:
- `surface_id`: Stable identifier for the screen or panel.
- `surface_role`: Root, child panel, or modal-style panel.
- `input_contract`: Active confirm and cancel/back behavior for the surface.
- `focus_candidates`: Ordered generated actions that may receive focus.
- `previous_state`: Optional prior frontend state used by Back/Cancel.

Validation rules:
- An active surface with valid focus candidates must select one initial focus target.
- A child surface with a previous state must be able to return to that state on Back/Cancel.
- A root surface with no previous state must preserve valid focus when Back/Cancel does not navigate elsewhere.

## Generated Action Button

Represents a player-facing control generated from a menu action entry.

Fields and state:
- `action_id`: Stable action identifier.
- `label`: Player-facing action label.
- `is_visible`: Whether the action renders on the current surface.
- `is_focusable`: Whether the action can receive focus.
- `activation_target`: Coordinator action invoked by pointer or confirm activation.

Validation rules:
- Visible actionable buttons must be focusable.
- Pointer click, keyboard confirm, and controller confirm must invoke the same `activation_target`.

## Focus Return Target

Represents the preferred Home action to restore focus to after leaving a child surface.

Fields and state:
- `origin_surface_id`: The Home surface that launched the child flow.
- `origin_action_id`: The Home navigation action that should regain focus.
- `fallback_policy`: Ordered fallback behavior when the original action is unavailable.

Validation rules:
- Returning from Play restores `origin_action_id = Play` when valid.
- Returning from Options restores `origin_action_id = Options` when valid.
- If the original target is invalid, the fallback policy must return another valid focus target.

## State Transitions

- `Home -> Play`: Store the Home Play action as the focus return target, activate Play, and assign Play initial focus.
- `Play -> Home`: Return to Home and restore focus to the Play navigation action when valid.
- `Home -> Options`: Store the Home Options action as the focus return target, activate Options, and assign Options initial focus.
- `Options -> Home`: Return to Home and restore focus to the Options navigation action when valid.
- `Child Surface -> Previous State`: Back/Cancel dismisses the child surface and restores the previous state's focus target.
