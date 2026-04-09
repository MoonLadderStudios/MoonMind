# Feature Specification: Live Logs Phase 6 Compatibility and Cleanup

**Feature Branch**: `144-live-logs-phase6`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Implement Phase 6 using test-driven development from the Live Logs Session-Aware Implementation Plan. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Respect rollout scope when enabling the session timeline (Priority: P1)

Mission Control operators need the Live Logs session-aware timeline to turn on only for runs that are inside the configured Phase 6 rollout scope so the new viewer can be deployed gradually without forcing every managed run onto the new path at once.

**Why this priority**: Phase 6 is explicitly about staged rollout safety. Without rollout-aware eligibility, `liveLogsSessionTimelineEnabled` behaves like a global on/off switch and skips the codex-only versus all-managed migration boundary described in the plan.

**Independent Test**: Render the task detail page with different `liveLogsSessionTimelineRollout` values and run types, then confirm the session timeline activates only for eligible runs while ineligible runs stay on the legacy line viewer.

**Acceptance Scenarios**:

1. **Given** the rollout scope is `codex_managed`, **When** an operator opens Live Logs for a Codex managed run, **Then** the session-aware timeline is enabled.
2. **Given** the rollout scope is `codex_managed`, **When** an operator opens Live Logs for a non-Codex managed run, **Then** the legacy line viewer remains active.
3. **Given** the rollout scope is `all_managed`, **When** an operator opens Live Logs for any managed run with a task-run-backed observability surface, **Then** the session-aware timeline is enabled.
4. **Given** the rollout scope is absent or `off`, **When** the frontend is deployed against older or disabled config, **Then** Live Logs degrades to the legacy line viewer without crashing.

---

### User Story 2 - Degrade cleanly across mixed observability payloads and empty history (Priority: P1)

Operators need Live Logs to stay readable while frontend and backend slices roll independently, including historical runs that only expose merged text and active runs whose live events still arrive in older minimal payload shapes.

**Why this priority**: Phase 6 requires a short deployment window where the frontend and backend can move independently. The UI must therefore tolerate both canonical and legacy-shaped observability payloads while keeping historical runs observable.

**Independent Test**: Load Live Logs with empty structured history plus merged fallback content, then stream live events using both camelCase and snake_case session metadata fields and confirm the panel renders and updates session context correctly.

**Acceptance Scenarios**:

1. **Given** `/observability/events` returns success with no rows but `/logs/merged` still has compatibility content, **When** the operator opens Live Logs, **Then** the UI falls back to merged text instead of showing a false empty timeline.
2. **Given** a live or historical observability event carries session metadata with camelCase field names, **When** the frontend parses that event, **Then** the timeline row and session snapshot still update correctly.
3. **Given** a live or historical observability event carries session metadata with snake_case field names, **When** the frontend parses that event, **Then** the same timeline row and session snapshot render correctly.
4. **Given** the SSE stream emits an older minimal event payload without session metadata or event kind, **When** the frontend receives it, **Then** the output row still renders in sequence order without breaking the panel.

---

### User Story 3 - Remove remaining legacy-only assumptions from the Live Logs viewer path (Priority: P2)

MoonMind engineers need the task-detail observability code to use one compatibility-aware timeline normalization path instead of scattered assumptions about which payload shape or viewer mode is authoritative.

**Why this priority**: Phase 6 ends with cleanup after rollout safety is in place. If the viewer still depends on hardcoded boolean gating or one exact payload alias set, future cleanup and flag removal will stay brittle.

**Independent Test**: Inspect and exercise the Live Logs viewer helpers through automated tests that cover rollout eligibility, event normalization, and fallback ordering without depending on duplicate legacy-only branches.

**Acceptance Scenarios**:

1. **Given** the session timeline feature flag and rollout scope are both present, **When** the task-detail page computes Live Logs behavior, **Then** one helper decides whether the timeline viewer is enabled for the current run.
2. **Given** observability events arrive from history or SSE, **When** the frontend normalizes them, **Then** one shared compatibility path handles field aliases and older minimal payloads.
3. **Given** the compatibility path determines the run is not eligible for the session-aware timeline, **When** the operator opens Live Logs, **Then** the legacy line viewer still works without duplicating fetch or stream lifecycle logic.

### Edge Cases

- Structured history exists but contains zero events while merged artifacts still contain historical text; the viewer must show the merged fallback instead of a blank timeline.
- SSE rows may arrive with camelCase session metadata while historical rows use snake_case aliases, or vice versa; both must normalize into the same frontend row model.
- The boot payload may expose `liveLogsSessionTimelineEnabled` without `liveLogsSessionTimelineRollout`, or the reverse, during an independent deploy window; the viewer must choose a safe fallback.
- A non-Codex managed run may have `session` rows in the future; `all_managed` must enable the session-aware viewer without special-casing only Codex.
- Older minimal SSE rows may omit `kind` and session metadata entirely; they must still render as output/system rows without throwing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task-detail frontend MUST enable the session-aware Live Logs timeline only when the current run is eligible for the configured rollout scope.
- **FR-002**: Rollout scope `codex_managed` MUST enable the session-aware Live Logs timeline for Codex managed runs and MUST keep non-Codex managed runs on the legacy line viewer.
- **FR-003**: Rollout scope `all_managed` MUST enable the session-aware Live Logs timeline for any managed run that exposes task-run observability.
- **FR-004**: Rollout scope `off` or missing rollout metadata MUST degrade to the legacy line viewer without breaking Live Logs.
- **FR-005**: The Live Logs viewer MUST fall back to `/logs/merged` when structured history is unavailable or when structured history returns zero rows and merged compatibility content exists.
- **FR-006**: The Live Logs viewer MUST normalize observability events from both historical retrieval and SSE through one compatibility path that accepts camelCase and snake_case session metadata aliases.
- **FR-007**: The Live Logs viewer MUST continue to render older minimal observability events that only provide sequence, stream, text, and timestamp.
- **FR-008**: The session snapshot header MUST update from compatible live or historical event metadata regardless of whether the source uses camelCase or snake_case field names.
- **FR-009**: The Phase 6 slice MUST ship production frontend/runtime code changes plus automated validation tests; docs-only changes are insufficient.

### Key Entities *(include if feature involves data)*

- **Timeline Eligibility**: The frontend decision that determines whether the session-aware Live Logs viewer or the legacy line viewer should render for the current run.
- **Compatibility-Normalized Observability Event**: A frontend-owned normalized event shape that accepts both canonical and legacy field aliases before mapping rows into the timeline.
- **Merged Fallback Trigger**: The condition that tells the viewer to use `/logs/merged` because structured history is missing or empty for a historical compatibility run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Browser tests prove `codex_managed`, `all_managed`, and disabled rollout modes enable the correct Live Logs viewer for eligible and ineligible runs.
- **SC-002**: Browser tests prove empty structured history falls back to merged content instead of leaving the panel blank.
- **SC-003**: Browser tests prove the Live Logs viewer accepts both camelCase and snake_case session metadata from historical responses and SSE payloads.
- **SC-004**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:typecheck`, `SPECIFY_FEATURE=144-live-logs-phase6 ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`, and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` pass.
