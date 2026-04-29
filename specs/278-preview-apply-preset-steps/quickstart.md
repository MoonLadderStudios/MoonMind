# Quickstart: Preview and Apply Preset Steps

## Focused Unit Verification

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:
- Select Step Type `Preset` inside a step editor.
- Preview generated steps without mutating the draft.
- Render generated step titles, Step Types, and warnings.
- Apply the preview and replace the temporary Preset step with editable generated steps.
- Show preview failures without draft mutation.
- Block submission while unresolved Preset steps remain.

## Full Unit Verification

```bash
./tools/test_unit.sh
```

## Manual Scenario

1. Open Mission Control Create page.
2. Add or edit a step and choose `Step Type = Preset`.
3. Select a preset.
4. Preview the expansion and confirm generated step titles, Step Types, and warnings are visible.
5. Apply the preview.
6. Confirm the Preset placeholder is gone and generated steps are editable.
7. Try submitting another unresolved Preset step and confirm submission is blocked.
