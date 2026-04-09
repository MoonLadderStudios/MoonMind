# Feature Specification: Live Logs Continuity Unification

**Feature Branch**: `143-live-logs-continuity-unification`
**Created**: 2026-04-09
**Status**: Draft
**Input**: User description: "Implement Phase 5 using test-driven development from the Live Logs Session-Aware Implementation Plan."

## User Scenarios & Testing

### User Story 1 - Open continuity artifacts directly from timeline events (Priority: P1)

Mission Control operators need the session-aware Live Logs timeline to expose direct links to the related continuity artifacts so they can move from "what happened" to durable evidence without leaving the observability flow.

**Why this priority**: Phase 5 exists to make the timeline and continuity artifacts feel like one observability model instead of two disconnected surfaces.

**Independent Test**: Render the Live Logs panel for a managed run with `summary_published`, `checkpoint_published`, `session_cleared`, and `session_reset_boundary` rows and verify each row exposes the expected artifact links.

**Acceptance Scenarios**:

1. **Given** a `summary_published` timeline row includes a durable summary artifact ref, **When** the operator opens Live Logs, **Then** the row exposes a direct summary artifact link.
2. **Given** a `checkpoint_published` timeline row includes a durable checkpoint artifact ref, **When** the operator opens Live Logs, **Then** the row exposes a direct checkpoint artifact link.
3. **Given** a clear/reset pair exists for the latest session epoch, **When** the operator reads the `session_cleared` or `session_reset_boundary` row, **Then** the row exposes direct links to both the control-event artifact and the reset-boundary artifact.

### User Story 2 - Explain timeline vs continuity drill-down clearly (Priority: P1)

Operators need the Live Logs panel and the continuity panel to explain their different jobs so the page reads as one observability workflow instead of duplicate features.

**Why this priority**: The product goal for Phase 5 is to reduce the split-brain feeling between logs and continuity without removing durable artifact drill-down.

**Independent Test**: Render the task detail page for a managed session run and verify the Live Logs panel copy describes the timeline as event history while the continuity panel copy describes artifacts as durable drill-down evidence.

**Acceptance Scenarios**:

1. **Given** the session timeline feature flag is enabled, **When** Live Logs renders, **Then** the panel includes operator-facing copy that states the timeline shows what happened.
2. **Given** a session continuity projection is present, **When** the continuity panel renders, **Then** the panel includes operator-facing copy that states continuity artifacts are durable evidence or drill-down.
3. **Given** the continuity projection still groups runtime, continuity, and control artifacts, **When** the panel renders, **Then** the grouped artifacts remain available without becoming the primary timeline model.

### Edge Cases

- Structured history rows may come from the persisted observability journal or from historical artifact synthesis; artifact-link rendering must work for both sources.
- Older runs may have timeline rows with only a generic `artifactRef`; the UI must still render a useful link when a specific ref key is absent.
- A clear/reset pair may expose one artifact but not the other; the row should show only the refs that exist and must not fabricate missing links.
- The continuity panel may still be polling while the timeline has already loaded; direct timeline links must not depend on the continuity panel request succeeding first.

## Requirements

### Functional Requirements

- **FR-001**: The session-aware Live Logs timeline MUST render direct artifact links for `summary_published` and `checkpoint_published` rows when artifact refs are present in event metadata.
- **FR-002**: The session-aware Live Logs timeline MUST render direct artifact links for clear/reset events so operators can open the control-event artifact and reset-boundary artifact from the relevant timeline rows when those refs exist.
- **FR-003**: Historical event synthesis for session publication and reset rows MUST preserve artifact-ref metadata needed by the Phase 5 UI.
- **FR-004**: The task detail page MUST include operator-facing copy that distinguishes the Live Logs timeline ("what happened") from continuity artifacts ("durable evidence / drill-down").
- **FR-005**: The Session Continuity panel MUST remain the durable artifact drill-down surface and MUST continue to show grouped runtime, continuity, and control artifacts.
- **FR-006**: The Phase 5 implementation MUST ship production runtime/frontend code changes plus automated validation tests; docs-only edits are insufficient.

### Key Entities

- **Timeline Artifact Link**: A direct artifact affordance rendered inline with one timeline row and resolved from event metadata.
- **Continuity Drill-Down Copy**: Operator-facing text that explains continuity artifacts as durable evidence instead of duplicated log history.
- **Historical Event Ref Metadata**: The artifact-ref fields attached to publication and boundary observability rows for both journal-backed and synthesized history.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Frontend tests prove `summary_published`, `checkpoint_published`, `session_cleared`, and `session_reset_boundary` rows expose the expected artifact links.
- **SC-002**: Backend tests prove synthesized historical session publication/boundary events preserve the artifact-ref metadata needed by the UI.
- **SC-003**: Frontend tests prove the task detail copy explains `Live Logs` as event history and continuity artifacts as durable drill-down evidence.
- **SC-004**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`, and scope validation pass.
