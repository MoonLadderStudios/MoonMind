# Implementation Plan: Live Logs Session Timeline UI

**Branch**: `142-live-logs-session-timeline-ui` | **Date**: 2026-04-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/142-live-logs-session-timeline-ui/spec.md`

## Summary

Implement the frontend Phase 4 slice of the session-aware Live Logs plan by upgrading [`frontend/src/entrypoints/task-detail.tsx`](../../frontend/src/entrypoints/task-detail.tsx) from a line viewer into a timeline viewer. The page will keep the shipped summary -> history -> SSE lifecycle, switch initial history preference to structured observability events, show a compact session snapshot header, render distinct timeline row types with feature-flagged session-aware UX, and harden the viewer with `react-virtuoso` plus `anser`.

## Technical Context

**Language/Version**: TypeScript, React 19, Vitest, existing Mission Control CSS  
**Primary Dependencies**: TanStack Query, Zod, `react-virtuoso`, `anser`, task-detail runtime config, existing `/api/task-runs/*` observability routes  
**Storage**: Browser query cache only; no backend schema change required for this slice  
**Testing**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:typecheck`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`  
**Target Platform**: Mission Control React/Vite frontend served by FastAPI  
**Project Type**: Frontend task-detail timeline upgrade with browser-test coverage and package dependency updates  
**Performance Goals**: Remove naive unbounded timeline DOM growth, keep SSE follow bounded to the current panel lifecycle, and preserve responsive rendering for long histories  
**Constraints**: Keep the current transport and page lifecycle, do not regress older runs that only have merged text, preserve feature-flagged rollout behavior, and keep the legacy line view available while the session timeline flag is disabled  
**Scale/Scope**: Live Logs panel rendering and related task-detail types/tests/styles only; no new backend routes and no continuity-panel API redesign

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The UI consumes MoonMind-owned summary/history/SSE contracts instead of provider-native payloads.
- **II. One-Click Agent Deployment**: PASS. The work stays inside the existing frontend build and runtime-config path.
- **III. Avoid Vendor Lock-In**: PASS. Timeline rows remain based on MoonMind’s canonical event model.
- **IV. Own Your Data**: PASS. The UI prefers MoonMind-owned structured history and only uses merged artifacts as compatibility fallback.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The main change is replacing the local row/viewer model around the existing canonical event contract.
- **VII. Powerful Runtime Configurability**: PASS. The richer viewer is gated behind `liveLogsSessionTimelineEnabled`, independent from `logStreamingEnabled`.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay bounded to the task-detail entrypoint, CSS, and package dependency wiring.
- **IX. Resilient by Default**: PASS. The viewer still degrades through merged-text fallback and avoids SSE for ended or unsupported runs.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This phase has its own spec/plan/tasks package before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical semantics remain in `docs/ManagedAgents/LiveLogs.md`; this plan captures the rollout slice only.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The new timeline path reuses the existing event contract directly rather than inventing another intermediate model.

## Project Structure

### Documentation (this feature)

```text
specs/142-live-logs-session-timeline-ui/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── live-logs-timeline-ui.md
├── checklists/
│   └── requirements.md
├── speckit_analyze_report.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-detail.tsx      # MODIFY: timeline row model, feature-flagged viewer, Virtuoso + ANSI rendering
frontend/src/entrypoints/task-detail.test.tsx # MODIFY: timeline/fallback/flag/browser coverage
frontend/src/styles/mission-control.css       # MODIFY: timeline header, row, and boundary styling
package.json                                  # MODIFY: add react-virtuoso and anser
package-lock.json                             # MODIFY: dependency lock update
```

**Structure Decision**: Keep the Live Logs upgrade inside the existing task-detail entrypoint and Mission Control stylesheet. Do not create a separate route or alternate viewer entrypoint; the panel should switch behavior based on the session-timeline feature flag and the available observability sources.

## Research

- The current frontend already consumes `/observability-summary`, `/observability/events`, and `/logs/stream`, but it still renders a mostly line-oriented `<div>` list with inline styles and no virtualization.
- The current `TimelineRow` model already recognizes `session_reset_boundary`, which means the Phase 4 gap is mainly richer row treatment, feature-flag rollout, and viewer hardening rather than a greenfield UI rewrite.
- The backend Phase 1-3 slices already provide the required feature flag (`liveLogsSessionTimelineEnabled`) and structured history/session snapshot contract, so this phase should avoid adding backend churn unless a test proves a route mismatch.
- `docs/ManagedAgents/LiveLogs.md` explicitly names `react-virtuoso` and `anser` as the desired-state viewer baseline, so package updates are in-scope for this phase.

## Data Model

- See [data-model.md](./data-model.md) for the frontend timeline row model, header snapshot precedence, and fallback behavior.

## Contracts

- [contracts/live-logs-timeline-ui.md](./contracts/live-logs-timeline-ui.md)

## Implementation Plan

1. Add failing browser tests for:
   - structured-history-first loading and merged fallback,
   - feature-flagged timeline vs legacy line viewer behavior,
   - session snapshot header fields and distinct session/system row rendering,
   - explicit boundary banners,
   - virtualization and ANSI rendering markers.
2. Introduce a feature-flag-aware Live Logs viewer model in `task-detail.tsx` that keeps the current summary -> history -> SSE lifecycle intact.
3. Replace the current direct row mapping with a `react-virtuoso` timeline renderer and `anser`-based ANSI fragments for output rows.
4. Move the inline Live Logs styles into Mission Control CSS classes for the new header, rows, and boundary treatment.
5. Update package dependencies and rerun focused UI verification, scope validation, and the task-state file.

## Verification Plan

### Automated Tests

1. `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
2. `npm run ui:typecheck`
3. `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
4. `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
5. `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

### Manual Validation

1. Open a task detail page for a structured-history-enabled run and confirm the Live Logs panel shows the session snapshot header and timeline rows before any SSE message arrives.
2. Open a legacy run without structured history and confirm the panel degrades to merged text without breaking the old operator flow.
3. Toggle the `liveLogsSessionTimelineEnabled` runtime config off and confirm the legacy line viewer remains available.
