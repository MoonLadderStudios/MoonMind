# Contract: Create Page Preset Preview And Apply

## Scope

This UI contract describes the Mission Control Create page behavior for MM-578.

## Authoring Controls

- The step editor exposes Step Type choices: `Tool`, `Skill`, and `Preset`.
- Choosing `Preset` reveals preset selection and input configuration controls in the step editor.
- Choosing and applying a preset does not require the separate Presets management section.

## Preview

- Preview requests expansion for the selected preset and current input values.
- Preview displays generated step titles, Step Types, and warnings before any draft mutation.
- Preview failure displays a visible error and preserves the current draft.
- Changing selected preset or inputs invalidates stale preview data.

## Apply

- Applying a valid preview replaces the temporary Preset step with generated executable Tool and/or Skill steps.
- Generated steps remain editable like ordinary authored steps.
- Generated Tool/Skill payloads are preserved for submission.

## Submission Guard

- If any unresolved Preset step remains in the draft, submission is rejected by default with visible feedback.
- Applied preset-derived steps submit as executable Tool/Skill steps, not as Preset placeholders.
