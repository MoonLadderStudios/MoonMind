# Quickstart: Preview and Apply Preset Steps Into Executable Steps

## Focused Validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected result:

- Step Type `Preset` is available in the step editor.
- Preview lists generated Tool/Skill steps and warnings before apply.
- Apply replaces the Preset placeholder with editable executable steps.
- Failed preview leaves the draft unchanged.
- Unresolved Preset steps block submission.

## Managed Unit Runner

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected result:

- The managed unit wrapper prepares frontend dependencies if needed and runs the focused Create page Vitest target.

## Manual UI Scenario

1. Open Mission Control Create.
2. Add or edit a step.
3. Choose Step Type `Preset`.
4. Select a preset and configure required inputs.
5. Preview the expansion.
6. Confirm generated step titles, Step Types, and warnings are visible.
7. Apply the preview.
8. Confirm the draft contains editable Tool/Skill steps and no unresolved Preset placeholder.
