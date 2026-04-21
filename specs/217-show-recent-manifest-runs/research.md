# Research: Show Recent Manifest Runs

## FR-001 / DESIGN-REQ-001 Page Placement

Decision: `implemented_verified`; preserve existing page order.
Evidence: `frontend/src/entrypoints/manifests.tsx` renders the Run Manifest card before the Recent Runs card, and `frontend/src/entrypoints/manifests.test.tsx` verifies both headings.
Rationale: The story depends on the existing unified page layout from MM-419.
Alternatives considered: Reworking layout was rejected as out of scope.
Test implications: Covered by existing frontend test and final validation.

## FR-002 / DESIGN-REQ-002 Data Source

Decision: `implemented_verified`; preserve existing query path.
Evidence: `frontend/src/entrypoints/manifests.tsx` fetches `${payload.apiBase}/executions?entry=manifest&limit=200`.
Rationale: The Jira brief explicitly requires this endpoint for phase 1.
Alternatives considered: A manifest-specific history API was rejected by source requirements.
Test implications: Existing test setup already mocks `/api/executions?entry=manifest&limit=200`; final validation keeps it covered.

## FR-003 / DESIGN-REQ-003 / DESIGN-REQ-004 History Columns

Decision: `partial`; add manifest label, action, started, duration, and View details action while retaining run identity and status.
Evidence: Current table columns are Task ID, Source Label, and Status only.
Rationale: Users cannot answer manifest action, timing, duration, or how to open details from the current table.
Alternatives considered: Backend changes were rejected because the existing endpoint can provide optional fields and the UI can tolerate missing values.
Test implications: Add frontend tests that fail before implementation and pass after columns are added.

## FR-004 / DESIGN-REQ-005 Stage-Aware Status

Decision: `missing`; add a formatter that displays `Running · fetch` when status is active and a current stage is available.
Evidence: Current status column renders only `status`.
Rationale: Source design requires manifest-specific stage detail inline.
Alternatives considered: A separate Stage-only display was insufficient because the story names inline status detail.
Test implications: Add frontend test with a running row and stage data.

## FR-005 Fallbacks

Decision: `partial`; extend row schema with optional fields and fallback formatters.
Evidence: Current schema only accepts `taskId`, `source`, optional `sourceLabel`, and `status`.
Rationale: Real execution rows may omit manifest label, action, timestamps, or duration, and the UI should not break or hide the row.
Alternatives considered: Strictly requiring all fields was rejected because the source explicitly says optional values should appear when available.
Test implications: Add frontend assertions for placeholders.

## FR-006 / DESIGN-REQ-006 Filters

Decision: `missing`; add status, manifest, and free-text search controls.
Evidence: Current Recent Runs section has no filter controls.
Rationale: The Jira brief requires lightweight bounded filters without a heavy builder.
Alternatives considered: Server-side filtering was rejected for phase 1 because the endpoint requirement is fixed.
Test implications: Add tests for status and search filtering.

## FR-007 / DESIGN-REQ-007 Empty State

Decision: `partial`; replace the generic empty message with manifest-specific guidance.
Evidence: Current table message is `No manifest runs found.`
Rationale: Source design asks users to run a registry manifest or submit inline YAML above.
Alternatives considered: Separate empty-state card was unnecessary for this story.
Test implications: Add test for filtered empty state.

## FR-008 / DESIGN-REQ-008 Responsive Readability

Decision: `implemented_unverified`; preserve shared table container and avoid hiding identity, status, or action.
Evidence: `DataTable` uses shared table styling; no story-specific viewport test exists.
Rationale: This story can satisfy phase 1 with the shared responsive table surface.
Alternatives considered: A separate mobile card layout was rejected as extra scope.
Test implications: Final validation checks that core columns remain present.

## FR-009 / DESIGN-REQ-009 Accessibility

Decision: `partial`; add labels for filter controls and accessible names for row actions.
Evidence: Form controls are labeled, but new filters/actions do not exist.
Rationale: Row actions and filters must be accessible by role or label.
Alternatives considered: Icon-only unlabeled actions were rejected.
Test implications: Use Testing Library role/label queries.
