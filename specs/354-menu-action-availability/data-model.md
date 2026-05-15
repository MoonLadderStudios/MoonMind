# Data Model: Menu Action Availability and Unavailable Presentation

## Menu Action Entry

Represents one action that can appear in a generated menu panel.

Fields:
- `id`: Stable action identifier.
- `label`: Player-facing action label.
- `panel_scope`: Menu panels or windows where the action may appear.
- `selection_handler`: Behavior invoked only when the action is enabled and selected.
- `unavailable_reason`: Optional player-facing reason authored on the action.
- `visibility_policy`: Whether an unavailable action should remain visible or be hidden by menu window rules.

Validation rules:
- `id` must be unique within the action registry used by generated panels.
- `label` must be present for visible actions.
- `selection_handler` must not run when the eligibility result is disabled-visible or hidden-by-window.

## Eligibility Result

Represents the runtime availability decision for a menu action.

Fields:
- `state`: `enabled`, `disabled-visible`, or `hidden-by-window`.
- `unavailable_reason`: Player-facing reason required for disabled-visible state.
- `fallback_reason_used`: Whether the unavailable reason came from deterministic fallback copy.
- `blocked_side_effects`: Optional side-effect categories that must not run while blocked.

Validation rules:
- Enabled results must not include unavailable copy in the generated button.
- Disabled-visible results must include authored, computed, or fallback unavailable copy.
- Hidden-by-window results must not produce a generated button.
- Hidden-by-window takes precedence over disabled-visible when the current panel window excludes the action.

## Generated Menu Button

Represents the player-facing control created from a menu action entry and eligibility result.

Fields:
- `action_id`: The source action identifier.
- `label`: Player-facing button label.
- `enabled`: Whether the player can activate the action.
- `visible`: Whether the button is rendered.
- `unavailable_copy`: Player-facing copy rendered for disabled-visible actions.
- `panel_id`: Generated panel where the button appears.

Validation rules:
- A hidden-by-window action produces no visible button.
- A disabled-visible action produces a visible disabled button with unavailable copy.
- An enabled action produces a visible enabled button with no disabled-state copy.

## Online Co-op Blocked Action

Represents the required blocked-selection example for Online Co-op.

Fields:
- `action_id`: Stable Online Co-op action identifier.
- `blocked_reason`: Player-facing reason explaining why Online Co-op cannot currently be used.
- `feedback_event`: Player-visible feedback shown on blocked selection attempts.
- `side_effect_guard`: Guard preventing travel, matchmaking, session creation, and session joining.

Validation rules:
- Online Co-op remains visible while blocked.
- Blocked selection must emit unavailable feedback.
- Blocked selection must not run travel or session side effects.
