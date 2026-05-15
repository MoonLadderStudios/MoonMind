# Data Model: Native Options Menu Surface

## Options Navigation Action

Represents the Home-menu action that opens the Options surface.

Fields:
- `id`: Stable action identifier; required value for this story is `frontend.nav.options`.
- `label`: Player-facing action label.
- `target`: The menu surface opened when the action is activated.
- `focusable`: Whether the action can receive restored focus.

Validation rules:
- `id` must be stable and unique within Home navigation actions.
- `target` must resolve to the Options surface.
- `focusable` must be true so focus can be restored after Back/Cancel.

State transitions:
- Home focused on Options action -> Options open after activation.
- Options closed via Back/Cancel -> Home focused on Options action.

## Options Surface

Represents the panel or screen that displays Options categories.

Fields:
- `state`: Closed or Open.
- `categories`: Ordered list of category actions rendered for the player.
- `source_status`: Whether categories came from authored data, fallback entries, or a combination.
- `return_focus_action_id`: Home action that receives focus when Options closes.

Validation rules:
- Open state must contain at least the Video, Audio, and Input categories.
- Missing authored presentation assets must not prevent Open state.
- `return_focus_action_id` must match the Home Options navigation action.

State transitions:
- Closed -> Open when Home Options action activates.
- Open -> Closed when Back or Cancel is used.

## Options Category

Represents one settings category action.

Fields:
- `tag`: Stable category identifier.
- `label`: Player-facing category label.
- `source`: Authored or fallback.
- `enabled`: Whether the category can be selected.

Required baseline categories:
- `frontend.options.video`
- `frontend.options.audio`
- `frontend.options.input`

Validation rules:
- Required baseline categories must exist even when authored data is missing.
- Authored categories may override labels/order, but must not remove required baseline category availability.
- Category actions must not require persisted settings state for this story.
