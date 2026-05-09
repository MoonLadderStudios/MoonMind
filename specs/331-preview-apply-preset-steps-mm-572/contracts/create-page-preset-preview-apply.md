# Contract: Create Page Preset Preview and Apply

## Authoring Contract

The Create page must expose `Preset` as a Step Type in the step editor alongside `Tool` and `Skill`.

Required behavior:

1. Selecting `Preset` shows a preset selector and schema-driven input form.
2. The form is driven by preset capability metadata, not preset-specific Create page branches.
3. Preview requests call the backend-owned preset expansion path with the selected preset, version, and inputs.
4. Preview renders generated step titles, generated Step Types, warnings, and blocking errors before draft mutation.
5. Apply replaces the temporary Preset step with concrete Tool and/or Skill steps only when preview is valid.
6. Generated steps remain editable and validate under their own Tool or Skill rules.
7. Submission rejects unresolved Preset placeholders by default.

## Failure Contract

Preview or apply failure must:

- leave the existing draft unchanged,
- show visible author feedback,
- preserve editable Preset step inputs,
- prevent stale preview data from being applied.

## Management Separation

Preset use for the current task belongs in the step editor. The Presets management area may manage catalog lifecycle and audit, but must not be required to choose, preview, or apply a preset to the current draft.
