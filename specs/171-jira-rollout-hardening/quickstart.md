# Quickstart: Jira Create-Page Rollout Hardening

## Prerequisites

- Python and Node/npm available in the workspace.
- Frontend dependencies installed through `./tools/test_unit.sh` or `npm ci --no-fund --no-audit`.
- No live Jira credentials are required for the automated validation path.

## Validate Runtime Config and Backend Contracts

Run the focused Python unit tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only \
  tests/unit/api/routers/test_jira_browser.py \
  tests/unit/integrations/test_jira_browser_service.py \
  tests/unit/api/routers/test_task_dashboard_view_model.py \
  tests/unit/config/test_settings.py \
  tests/unit/integrations/test_jira_client.py
```

Expected result:

- Jira Create-page runtime config is hidden when disabled.
- Jira UI endpoint templates and defaults appear only when enabled.
- Jira browser routes return normalized response shapes.
- Project policy, validation, safe errors, and redaction are covered.
- Jira Agile path resolution remains covered.

## Validate Create-Page UI Behavior

Run the targeted Create-page Vitest suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args \
  frontend/src/entrypoints/task-create.test.tsx
```

Expected result:

- Jira controls are hidden when disabled.
- Browser opens from preset and step targets.
- Project, board, column, issue list, and issue preview flows work.
- Replace and append imports update only the selected target.
- Template-derived step imports detach template instruction identity.
- Preset reapply messaging appears without hidden step rewrites.
- Provenance chips and session memory behave as expected.
- Jira failures remain local and manual task creation still works.

## Validate Frontend Type and Lint Gates

Use direct local binaries in managed workspaces whose absolute paths contain colons:

```bash
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src
```

Expected result:

- TypeScript passes with no type errors.
- ESLint passes with no lint errors.

## Manual Runtime Smoke Check

1. Start MoonMind normally.
2. Confirm `/tasks/new` shows no Jira browser controls when the Jira Create-page feature is disabled.
3. Enable the Jira Create-page feature and configure Jira credentials/project policy.
4. Open `/tasks/new`.
5. Open `Browse Jira story` from the preset objective field.
6. Select project, board, column, and story.
7. Import with `Replace target text`.
8. Confirm the preset objective field changes, a `Jira: <issue>` chip appears, and the task can still be submitted.
9. Repeat from a step instructions field and confirm only that step changes.
10. Temporarily break Jira configuration and confirm manual task authoring and submission remain available.

## Rollback Check

Disable the Jira Create-page feature flag and reload `/tasks/new`.

Expected result:

- Jira browser controls disappear.
- Existing manual Create-page behavior remains available.
- Backend trusted Jira tooling remains independently configurable.
