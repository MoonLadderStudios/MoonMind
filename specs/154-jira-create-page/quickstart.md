# Quickstart: Jira Create Page Integration

## Prerequisites

- MoonMind services can run locally with the existing Docker Compose path.
- Jira credentials are configured through the existing Atlassian/Jira settings or managed secret references.
- `ATLASSIAN_JIRA_TOOL_ENABLED` may be enabled for trusted tools, but Create-page browser exposure is controlled separately by `FEATURE_FLAGS__JIRA_CREATE_PAGE_ENABLED`.

## Configuration Smoke Checks

1. Disable the Create-page Jira UI:

   ```bash
   FEATURE_FLAGS__JIRA_CREATE_PAGE_ENABLED=false pytest tests/unit/api/routers/test_task_dashboard_view_model.py -q
   ```

2. Enable the Create-page Jira UI with defaults:

   ```bash
   FEATURE_FLAGS__JIRA_CREATE_PAGE_ENABLED=true \
   FEATURE_FLAGS__JIRA_CREATE_PAGE_DEFAULT_PROJECT_KEY=MM \
   FEATURE_FLAGS__JIRA_CREATE_PAGE_DEFAULT_BOARD_ID=42 \
   pytest tests/unit/api/routers/test_task_dashboard_view_model.py -q
   ```

3. Confirm boot payload behavior:

   - Disabled: no `sources.jira` and no `system.jiraIntegration`.
   - Enabled: `sources.jira` and `system.jiraIntegration.enabled=true` are present.

## Backend Verification

Run the focused browser service and router suites:

```bash
pytest tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py -q
```

Expected coverage:

- connection verification through the trusted Jira boundary
- allowed-project policy denial
- board column normalization and ordering
- board issue grouping by column
- issue detail text normalization
- safe structured errors without leaked credentials

## Frontend Verification

Run the Create-page suite:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:

- Jira controls hidden when disabled
- browser opens from preset and step targets
- project, board, column, issue detail navigation
- issue selection does not mutate the draft
- replace and append import into preset and step fields
- template-bound step detaches on import
- preset reapply-needed message after preset import
- provenance chip and session memory behavior
- Jira failure isolation

## End-to-End Manual Smoke

1. Start MoonMind with Jira UI disabled and open `/tasks/new`.
2. Confirm manual task creation works and no Jira entry points are shown.
3. Enable Jira UI and configure Jira credentials.
4. Open `/tasks/new`, then open `Browse Jira story` from preset initial instructions.
5. Select project, board, column, and issue detail.
6. Import a preset brief with Replace and confirm preset initial instructions change.
7. Apply a preset, import again into preset initial instructions, and confirm steps do not change until explicit reapply.
8. Open `Browse Jira story` from a step, import an execution brief with Append, and confirm only that step changes.
9. Break Jira credentials or simulate a failed browser request and confirm manual task editing and Create remain usable.

## Final Verification

```bash
./tools/test_unit.sh
SPECIFY_FEATURE=154-jira-create-page ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=154-jira-create-page ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

If `./tools/test_unit.sh` fails for an unrelated pre-existing test, record the exact failing test and rerun the feature-focused suites before finalizing.
