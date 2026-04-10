# Quickstart: Live Logs Session Timeline UI

1. Ensure frontend dependencies are installed and up to date.
2. Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`.
3. Run `npm run ui:typecheck`.
4. Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`.
5. Open a task detail page with a `taskRunId`, expand Live Logs, and verify:
   - the session snapshot header renders when available,
   - structured history appears before live follow starts,
   - reset boundaries render as banners,
   - ANSI output is visible without raw escape codes.
