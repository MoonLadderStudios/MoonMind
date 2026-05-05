# Research: Empty/Error States and Regression Coverage for Final Rollout

## FR-001 / SC-001 - Loading State

Decision: Treat as implemented and verified for MM-592.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` renders `<p className="loading">Loading tasks...</p>` while the list query is loading, and `frontend/src/entrypoints/tasks-list.test.tsx` verifies the pending request state.
Rationale: MM-592 requires final rollout regression evidence, and focused UI validation now covers it.
Alternatives considered: Additional code change was rejected because the existing behavior passed the new regression test.
Test implications: UI unit covered and passing.

## FR-002 / FR-009 / SC-002 / SC-006 - Structured List API Errors

Decision: Treat as implemented and verified after adding structured API error parsing for failed list responses.
Evidence: `tasks-list.tsx` now derives sanitized messages from structured response payloads before falling back to status text, and `tasks-list.test.tsx` verifies `detail.message` rendering.
Rationale: MM-592 requires visible API errors and invalid filter parameter recovery with structured messages when available.
Alternatives considered: Keeping generic `statusText` was rejected because FastAPI validation responses already provide safer, more actionable detail.
Test implications: UI unit covered with red-first evidence and passing validation.

## FR-003 / FR-004 / SC-003 - Empty First Page Recovery

Decision: Treat as implemented and verified with a focused active-filter empty-state test.
Evidence: `tasks-list.tsx` renders `No tasks found for the current filters.` for an empty first page and keeps the control deck with `Clear filters` above the result state; `tasks-list.test.tsx` verifies the active-filter recovery path.
Rationale: Existing tests covered empty later pages; MM-592 now also has first-page active-filter recovery evidence.
Alternatives considered: Moving the Clear filters button into the empty panel was rejected because the current active filter row is already the consistent recovery location.
Test implications: UI unit covered and passing.

## FR-005 / FR-006 / SC-004 - Empty Later Page and Pagination

Decision: Treat as implemented and verified by existing tests.
Evidence: `frontend/src/entrypoints/tasks-list.test.tsx` includes `keeps the previous-page button enabled on empty pages after pagination` and page-size cursor reset coverage.
Rationale: The existing test directly covers the MM-592 later-page recovery requirement.
Alternatives considered: Adding a duplicate test was rejected.
Test implications: Final UI validation only.

## FR-007 / SC-005 - Facet Failure Fallback

Decision: Treat as implemented and verified by existing tests.
Evidence: `tasks-list.test.tsx` includes `shows a current-page values notice when facet values fail to load`, asserting the inline fallback notice and preserved table data.
Rationale: This directly satisfies the facet-failure acceptance criterion.
Alternatives considered: Adding retry behavior now was rejected because the design allows fallback or retry.
Test implications: Final UI validation only.

## FR-008 - Local Invalid Filter Recovery

Decision: Treat as implemented and verified by existing tests.
Evidence: `tasks-list.test.tsx` covers contradictory canonical URL filters and recovery after `Clear filters`.
Rationale: The page preserves recovery without sending an invalid list request.
Alternatives considered: Moving all validation to the server was rejected because local validation avoids unnecessary requests and keeps recovery immediate.
Test implications: Final UI validation only.

## FR-010 / FR-011 / SC-007 / DESIGN-REQ-027 - Old Controls and Non-Goals

Decision: Treat as implemented and verified.
Evidence: `tasks-list.test.tsx` asserts no Scope, Workflow Type, Entry, Kind, or old filter form controls exist and that task scope is forced.
Rationale: The normal Tasks List remains task-scoped and does not expose system workflow browsing.
Alternatives considered: Reintroducing compatibility top controls was rejected by the source design.
Test implications: Final UI validation only.

## FR-012 / DESIGN-REQ-026 - Final Regression Gate

Decision: Treat as implemented and verified.
Evidence: Existing tests cover many final rollout behaviors, and MM-592-specific loading, structured API error, and empty-first-page recovery tests now pass.
Rationale: The Jira brief explicitly makes regression evidence a rollout gate, and the focused plus full unit validation evidence satisfies that gate.
Alternatives considered: Relying on code inspection alone was rejected.
Test implications: UI unit plus final verification completed.

## FR-013 / SC-008 / DESIGN-REQ-028 - Traceability

Decision: Preserve MM-592 and source design IDs through all MoonSpec artifacts and final verification.
Evidence: `spec.md`, `plan.md`, `tasks.md`, `verification.md`, and this research artifact preserve MM-592 and DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, and DESIGN-REQ-028.
Rationale: Downstream verification, commit text, and PR metadata need the Jira key.
Alternatives considered: Referencing only the artifact path was rejected because final verification needs the source key visible in each artifact.
Test implications: Artifact review completed.
