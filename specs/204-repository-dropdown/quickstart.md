# Quickstart: Create Page Repository Dropdown

## Focused Unit Validation

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py
```

Expected result:

- Configured default and `GITHUB_REPOS` values are normalized into repository options.
- Credential-visible GitHub repositories are included when the mocked GitHub API succeeds.
- Invalid, duplicate, and credential-bearing values are excluded.
- Discovery failure returns configured/manual-safe options without leaking secrets.

## Focused Frontend Validation

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected result:

- The repository field exposes dropdown suggestions from runtime config.
- Selecting an option updates the repository field.
- Submit payload uses the selected repository value.
- Manual owner/repo entry remains possible when no options are present.

## Final Validation

```bash
./tools/test_unit.sh
```

Expected result:

- Required unit suite passes.
- MM-393 remains referenced in MoonSpec artifacts and verification evidence.
