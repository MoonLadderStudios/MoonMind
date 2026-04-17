# Quickstart: Jira Import Into Declared Targets

## Focused Validation

Run the focused Create page test file:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

If the managed shell does not place `node_modules/.bin` on npm's script path, run the equivalent local binary directly:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
```

Expected coverage:
- Jira browser opens from preset objective text and step text targets.
- Jira browser opens from objective and step attachment targets when attachment policy is enabled.
- Target switching inside the browser preserves the selected issue.
- Jira text imports can append to or replace the declared text target.
- Jira images become structured attachments on the declared attachment target.
- Jira failures remain local and manual task authoring remains available.

## Final Repository Validation

Run the standard unit test wrapper:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

If this wrapper cannot complete because local dependencies or runtime services are unavailable, record the exact blocker and retain the focused Vitest evidence.
