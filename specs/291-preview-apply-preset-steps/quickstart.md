# Quickstart: Preview and Apply Preset Steps

## Focused Unit/Integration Validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:

1. Select Step Type `Preset` in the step editor.
2. Configure a preset and preview generated steps.
3. Confirm preview shows generated titles, Step Types, and warnings.
4. Apply the preview and confirm the Preset placeholder becomes editable executable Tool/Skill steps.
5. Confirm unresolved Preset submission is blocked.
6. Confirm failed preview leaves the draft unchanged.
7. Confirm step-editor preset use does not require Preset Management.

## Managed Unit Runner

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Use the managed runner before finalizing because repository instructions require `./tools/test_unit.sh` for final unit verification.
