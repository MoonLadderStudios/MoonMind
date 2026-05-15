# Data Model: Layered Modal Recovery Surfaces

## Modal State

Represents an active player-facing modal interruption or decision state.

Fields and state:
- `modal_id`: Stable identifier for the modal instance.
- `modal_kind`: Progress, blocking error, retry, dismiss, or confirmation.
- `message`: Player-facing modal text or status content.
- `blocks_interaction`: Whether non-modal player interaction is blocked while active.
- `available_actions`: Ordered modal actions available to the player.
- `prior_state_id`: Optional frontend destination for Dismiss.
- `recovery_action`: Optional captured action available to Retry.

Validation rules:
- Progress and blocking error modal states must block conflicting non-modal interaction.
- Retry must only be available or executable when a valid recovery action exists.
- Dismiss must resolve to `prior_state_id` when present, otherwise Home.
- Confirmation modals must define explicit outcomes before presentation.

## Recovery Action

Represents a captured operation that can be retried after a recoverable failure.

Fields and state:
- `action_id`: Stable identifier for the captured recovery operation.
- `can_execute`: Whether the action remains valid at retry time.
- `execute_once`: Guard that prevents duplicate execution for one selected retry attempt.
- `failure_result`: Optional result used when retry fails again.

Validation rules:
- A selected Retry executes the captured action exactly once for that attempt.
- Retry does not execute when no captured action is available.
- A failed retry may present another recovery modal without reusing stale execution state.

## Modal Destination

Represents where the frontend should navigate after a modal dismisses.

Fields and state:
- `destination_kind`: Home or explicit prior state.
- `prior_state_id`: Optional prior frontend state identifier.
- `fallback_policy`: Home fallback when no explicit prior state is available.

Validation rules:
- Dismiss without `prior_state_id` returns to Home.
- Dismiss with `prior_state_id` returns to that state.
- Invalid or unavailable prior state must not leave the frontend in an undefined state.

## Modal Layer Stack

Represents the frontend layer area responsible for modal presentation.

Fields and state:
- `active_modals`: Ordered modal instances currently in the modal layer.
- `top_modal`: The modal currently receiving player input.
- `presentation_source`: Production layer route or native fallback source.

Validation rules:
- Presenting a modal adds or replaces entries through the modal layer stack.
- Dismissing a modal removes it from the stack and restores the next valid layer state.
- Native fallback modals use the same stack semantics as authored modal presentation.

## State Transitions

- `None -> Progress Modal`: Push progress modal through the modal layer and block conflicting interaction.
- `Progress Modal -> Blocking Error Modal`: Replace or dismiss progress before presenting blocking error through the modal layer.
- `Blocking Error Modal -> Retry`: Execute captured recovery action once when available.
- `Any Modal -> Dismiss Home`: Dismiss modal and return to Home when no prior state exists.
- `Any Modal -> Dismiss Prior State`: Dismiss modal and return to configured prior state.
- `Confirmation Modal -> Confirm/Cancel`: Route selected outcome and remove modal from the modal layer stack.
