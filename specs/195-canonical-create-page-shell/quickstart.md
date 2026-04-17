# Quickstart: Canonical Create Page Shell

## Focused Validation

Run the backend route coverage:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py
```

Run the focused Create page UI coverage:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

## End-to-End Story Checks

1. Open `/tasks/new`.
2. Confirm the page is the Create page rendered from the Mission Control React shell.
3. Confirm the canonical section order in create mode is Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit.
4. Visit `/tasks/create` and confirm it redirects to `/tasks/new`.
5. Submit a manual task with optional Jira and attachment controls unavailable and confirm the browser posts to the MoonMind task creation endpoint.
6. Open edit and rerun URLs under `/tasks/new` and confirm they reuse the same task composition surface.

## Final Validation

Run the full unit suite before completion:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```
