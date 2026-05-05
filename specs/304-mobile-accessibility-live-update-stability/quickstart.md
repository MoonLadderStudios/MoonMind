# Quickstart: Mobile, Accessibility, and Live-Update Stability

## Focused Validation

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

Expected coverage:

- Mobile ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished filters are reachable.
- Mobile filter changes produce task-scoped list requests and reset pagination.
- Desktop filter dialogs move focus into the editor and return focus to the originating control.
- Escape/cancel/outside-click paths discard staged changes.
- Enter applies staged text-filter changes.
- Ordinary Tasks List users cannot expose workflow-kind browsing controls.

## Final Validation

```bash
./tools/test_unit.sh
```

Expected outcome:

- Full unit suite passes, including Python unit coverage and frontend Vitest coverage.
- `MM-591` remains preserved in `spec.md`, `plan.md`, `tasks.md`, and `verification.md`.
