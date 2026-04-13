# Quickstart: Jira Failure Handling

## 1. Confirm Runtime Scope

This feature is runtime implementation work. Passing documentation changes alone is insufficient. Production backend/frontend code changes and validation tests are required.

## 2. Run Focused Backend Validation

```bash
pytest tests/unit/api/routers/test_jira_browser.py -q
```

Expected coverage:

- Known Jira browser errors return structured safe details.
- Secret-like messages are sanitized.
- Unexpected Jira browser exceptions return structured safe failures.

If service-level empty-state normalization changes are made, also run:

```bash
pytest tests/unit/integrations/test_jira_browser_service.py -q
```

## 3. Run Focused Frontend Validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:

- Jira browser failures render inside the browser panel.
- Failure copy says the user can continue creating the task manually.
- Manual instructions remain editable after Jira failure.
- Manual task creation remains available after Jira failure.
- Issue-detail failures do not mutate draft content.

## 4. Run Repository Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result:

- Python unit suite passes.
- Dashboard Vitest suite passes.

## 5. Manual Smoke Check

1. Enable Create-page Jira browser runtime config in a local/dev environment.
2. Simulate a Jira browser endpoint failure or temporarily misconfigure Jira credentials.
3. Open `/tasks/new`.
4. Open `Browse Jira story`.
5. Confirm the Jira failure is shown inside the browser panel.
6. Close the Jira browser.
7. Enter manual task instructions.
8. Confirm the Create button is available and submits through the normal task path.

## 6. Non-Goals

- Do not add a Jira-specific task type.
- Do not add browser-to-Jira credential access.
- Do not persist Jira failure/provenance metadata in the submitted task payload.
- Do not alter objective resolution order or artifact fallback behavior.
