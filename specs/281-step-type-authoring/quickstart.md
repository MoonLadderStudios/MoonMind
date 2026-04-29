# Quickstart: Present Step Type Authoring

## Focused Verification

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected result:

- Create page tests pass.
- A rendered step exposes one `Step Type` selector with Tool, Skill, and Preset.
- Helper copy for Tool, Skill, and Preset is visible.
- Switching Step Type changes visible controls and preserves instructions.
- Hidden Skill fields are not submitted for Tool steps.

## Manual Smoke Scenario

1. Open Mission Control Create.
2. Inspect Step 1.
3. Confirm `Step Type` has Tool, Skill, and Preset options.
4. Confirm the Step Type area explains Tool, Skill, and Preset with concise copy.
5. Enter instructions, switch among Tool, Skill, and Preset, and confirm instructions remain.
6. Confirm Tool, Skill, and Preset controls appear only for their selected Step Type.
