# Research: Task-only Visibility and Diagnostics Boundary

## FR-001 / DESIGN-REQ-005

Decision: Implemented and verified; Tasks List request semantics are task-run-only.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` always sets `scope=tasks`; `frontend/src/entrypoints/tasks-list.test.tsx` asserts default and legacy URL request shapes.
Rationale: The normal list must be task-oriented and not a workflow-kind browser.
Alternatives considered: Add a diagnostics link now; rejected because the MM-586 story only requires a separated diagnostics boundary and allows safe ignore/recoverable handling.
Test implications: UI integration-style tests.

## FR-002 / SC-002

Decision: Implemented and verified; `Scope`, `Workflow Type`, and `Entry` controls are absent from the normal Tasks List page.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` renders only Status and Repository filters; `frontend/src/entrypoints/tasks-list.test.tsx` asserts the workflow-kind controls are absent.
Rationale: Ordinary workflow-kind browsing UX is explicitly out of scope for `/tasks/list`.
Alternatives considered: Disable the controls; rejected because visible disabled workflow-kind controls still make the normal page read as a workflow browser.
Test implications: UI integration-style tests assert absence and preserved Status/Repository controls.

## FR-003 / FR-008 / FR-009 / DESIGN-REQ-017

Decision: Implemented and verified; task-compatible old `state` and `repo` parameters are preserved, broad workflow-scope parameters are ignored, emitted URL state is rewritten, and a recoverable notice appears when unsafe legacy state was present.
Evidence: `hasUnsupportedWorkflowScopeState()` in `frontend/src/entrypoints/tasks-list.tsx`; legacy URL coverage in `frontend/src/entrypoints/tasks-list.test.tsx`.
Rationale: Old shared links should not break, but must not reveal system/all/manifest workflows.
Alternatives considered: Redirect to diagnostics; rejected because no diagnostics route is in scope for this story.
Test implications: UI test for old URL normalization and notice.

## FR-004 / DESIGN-REQ-008

Decision: Implemented and verified.
Evidence: `TABLE_COLUMNS` contains ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, Finished; `frontend/src/entrypoints/tasks-list.test.tsx` asserts `Kind`, `Workflow Type`, and `Entry` headers are absent.
Rationale: The forbidden headers are a primary acceptance criterion and need traceable evidence.
Alternatives considered: No new assertion; rejected due traceability gap.
Test implications: UI test.

## FR-006 / FR-007 / DESIGN-REQ-025

Decision: Implemented and verified; the source-temporal execution list route cannot widen ordinary query parameters beyond task-run semantics.
Evidence: `_normalize_temporal_list_scope()` returns task scope for recognized broad scopes; `tests/unit/api/test_executions_temporal.py` covers `scope=all`, system workflow params, and unknown-scope validation.
Rationale: The backend/query boundary must be fail-safe because URL parameters are user editable.
Alternatives considered: Rely only on frontend normalization; rejected because backend boundary is explicitly required.
Test implications: backend unit tests update broad-scope expectations and add system/manifest filter cases.

## FR-010

Decision: Implemented; preserve text rendering.
Evidence: React renders filter chip labels/values and table text through JSX text interpolation, not HTML injection.
Rationale: No new HTML rendering surface is needed for this story.
Alternatives considered: Add sanitizer abstraction; rejected as unnecessary for non-HTML rendering.
Test implications: final verification only.
