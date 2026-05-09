# Quickstart: Preview and Apply Preset Steps

## Current Step

This task creation step is complete when the `MM-572` MoonSpec handoff artifacts exist and preserve:

- target Jira issue `MM-572`,
- source story `STORY-004`,
- source Jira issue `manual-mm-569-mm-574`,
- original brief reference `Jira issue MM-572 recommended preset brief`,
- explicit note that implementation was not run inline.

## Downstream Verification Commands

Run these only when the Jira Orchestrate implementation or verification step is authorized:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx
```

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
```

Expected result: focused Create page coverage proves Preset selection, preview, apply, failure handling, and unresolved submission blocking for the `MM-572` story or identifies the remaining implementation gap.
