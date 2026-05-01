# Quickstart: Define Step Type Authoring Model

1. Open Mission Control Create page.
2. Inspect Step 1 and confirm the selector is labeled `Step Type`.
3. Confirm the choices are `Skill`, `Tool`, and `Preset`.
4. Enter shared instructions and type-specific Skill state.
5. Change Step Type to `Tool`.
6. Confirm instructions remain and Skill-specific state is visibly discarded.
7. Attempt to submit a Tool step without choosing a Tool and confirm validation blocks submission.
8. Apply a Preset step and confirm expanded steps are Tool and/or Skill steps.

Automated checks:

```bash
./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
./tools/test_unit.sh
/moonspec-verify
```
