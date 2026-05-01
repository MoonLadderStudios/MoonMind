# Quickstart: Preview and Apply Preset Steps

## Focused Unit/Integration Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
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
./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Use the managed dashboard runner before finalizing frontend-only changes. The full repository-wide unit wrapper runs the Python unit suite before frontend validation; MM-578 verification records the current unrelated Python-suite flake separately in `verification.md`.
