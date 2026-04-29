# Data Model: Preview and Apply Preset Steps

## Preset Step Draft

Temporary task-authoring step selected by the user.

Fields:
- `localId`: stable local step identity.
- `stepType`: `preset`.
- `presetKey`: selected preset catalog key.
- `instructions`: optional user-authored context preserved while editing.
- `presetMessage`: status or validation message.
- `presetPreview`: latest successful preview, if any.

Validation:
- `presetKey` is required before preview.
- A Preset Step Draft is not executable by default and cannot be submitted unresolved.

## Preset Expansion Preview

Deterministic preview generated from the selected preset and current inputs.

Fields:
- `presetKey`: catalog key used to produce the preview.
- `version`: preset version used for expansion.
- `steps`: generated step previews with title and Step Type.
- `warnings`: non-blocking warnings returned by expansion.
- `inputValues`: normalized preset inputs used for preview.

Validation:
- Preview is replaced when preset selection or inputs change.
- Apply is enabled only for a current successful preview.
- Expansion errors do not mutate the draft.

## Preset-Derived Step

Concrete Tool or Skill step inserted by applying a preview.

Fields:
- Existing editable step state fields.
- `stepType`: `tool` or `skill` after mapping.
- `source`: optional preset provenance from expansion.

Validation:
- Must pass generated Tool or Skill validation before submission.
- Must remain editable like ordinary steps.

## State Transitions

```text
empty/manual step -> Preset Step Draft -> Preview Ready -> Applied Preset-Derived Step(s)
                         |                    |
                         |                    -> Preview Failed (draft unchanged)
                         -> Unresolved Submit Blocked
```
