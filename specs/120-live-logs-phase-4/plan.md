# Fully implement Phase 4 of docs/tmp/009-LiveLogsPlan.md

This implements the **Mission Control observability UI** using a native React log viewer, replacing legacy `tmate`/terminal session assumptions. We will use test-driven development (TDD) for every task. 

## User Review Required
> [!IMPORTANT]
> Since we're replacing the legacy log viewer with a native React component, I'll need to install three new dependencies in `frontend/package.json`:
> 1. `react-virtuoso` (for rendering large lists)
> 2. `anser` (for parsing ANSI terminal colors into HTML/React elements)
> 3. `@types/anser` (for TypeScript support)
> Do you approve of adding these dependencies?

> [!NOTE]
> The prompt specified: "implement tests for one task at a time. Run them and get a red result. Implement the task. Then run tests again and fix the code until you get a green result." I will strictly adhere to this Red-Green-Refactor loop per sub-task.

## Proposed Changes

### Frontend Dependencies

#### [MODIFY] package.json
- Add `react-virtuoso`
- Add `anser` and `@types/anser`

### Main UI Components

#### [MODIFY] frontend/src/entrypoints/task-detail.tsx
Currently, the `LiveLogsPanel` fetches a merged artifact tail and opens an `EventSource`, dumping text into a single `<pre>` tag. Phase 4 tasks require splitting observability into discrete surfaces:
- **Live Logs**: Virtualized rendering of live logs with `EventSource`, ANSI coloring via `anser`.
- **Stdout/Stderr/Diagnostics panels**: Fetch logic using React Query for standalone static viewing, with download links.
- **Connection Lifecycle Management**: Pause/stop tracking when panel collapses or tab is backgrounded.
- **Viewer state indicators**: show provenances (stdout/stderr) and statuses (live, errored, ended).

#### [MODIFY] frontend/src/entrypoints/task-detail.test.tsx
- Setup mocked `EventSource` tests.
- Setup test blocks for load states, background/visibility reconnection, collapse lifecycle, and ended-run behaviors in isolation.

## Open Questions
- Do you want me to mock out TanStack Query and EventSource using `vitest` mocks in `task-detail.test.tsx`?
- Should I invoke `speckit-*` CLI commands sequentially alongside these code executions as dictated by the `speckit-orchestrate` prompt, or should I proceed primarily as Antigravity following this plan? (I've created the `120-live-logs-phase-4` git branch using the `speckit-specify` bash script automatically).

## Verification Plan

### Automated Tests
- For each piece of functionality (e.g. "Default the Live Logs panel to collapsed with no active connection"):
  1. Write failing test in `task-detail.test.tsx` (Red).
  2. Implement functional change in `task-detail.tsx` (Green).
  3. Refactor (Refactor).

### Manual Verification
- Once the automated tests pass, run `npm run ui:test` and UI checks locally before creating the PR.
