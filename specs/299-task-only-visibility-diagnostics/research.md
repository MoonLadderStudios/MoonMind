# Research: Task-only Visibility and Diagnostics Boundary

## FR-001 / DESIGN-REQ-005

Decision: Partial behavior exists; implement task-run-only request semantics from the Tasks List UI.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` defaults to `scope=tasks` but lets users choose `all`, `system`, or `user` and sends those values to `/api/executions`.
Rationale: The normal list must be task-oriented and not a workflow-kind browser.
Alternatives considered: Add a diagnostics link now; rejected because the MM-586 story only requires a separated diagnostics boundary and allows safe ignore/recoverable handling.
Test implications: UI integration-style tests.

## FR-002 / SC-002

Decision: Missing; remove `Scope`, `Workflow Type`, and `Entry` controls from the normal Tasks List page.
Evidence: Existing tests currently assert these controls exist and can widen scope.
Rationale: Ordinary workflow-kind browsing UX is explicitly out of scope for `/tasks/list`.
Alternatives considered: Disable the controls; rejected because visible disabled workflow-kind controls still make the normal page read as a workflow browser.
Test implications: UI integration-style tests assert absence and preserved Status/Repository controls.

## FR-003 / FR-008 / FR-009 / DESIGN-REQ-017

Decision: Partial; preserve task-compatible old `state` and `repo` parameters, ignore broad workflow-scope parameters, rewrite emitted URL state, and show a recoverable notice when unsafe legacy state was present.
Evidence: Current initialization uses `scope`, `workflowType`, and `entry` to initialize broad state and syncs those params back to the URL.
Rationale: Old shared links should not break, but must not reveal system/all/manifest workflows.
Alternatives considered: Redirect to diagnostics; rejected because no diagnostics route is in scope for this story.
Test implications: UI test for old URL normalization and notice.

## FR-004 / DESIGN-REQ-008

Decision: Implemented but add focused MM-586 verification.
Evidence: `TABLE_COLUMNS` contains ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, Finished. Existing tests already cover the absence of Started, but not MM-586 forbidden headers directly.
Rationale: The forbidden headers are a primary acceptance criterion and need traceable evidence.
Alternatives considered: No new assertion; rejected due traceability gap.
Test implications: UI test.

## FR-006 / FR-007 / DESIGN-REQ-025

Decision: Partial; harden the source-temporal execution list route so ordinary query parameters cannot widen beyond task-run semantics.
Evidence: `_TEMPORAL_SCOPE_QUERIES` supports `all`, `user`, and `system`; `test_list_executions_source_temporal_scope_all_keeps_raw_temporal_query` expects `scope=all` to omit task filtering.
Rationale: The backend/query boundary must be fail-safe because URL parameters are user editable.
Alternatives considered: Rely only on frontend normalization; rejected because backend boundary is explicitly required.
Test implications: backend unit tests update broad-scope expectations and add system/manifest filter cases.

## FR-010

Decision: Implemented; preserve text rendering.
Evidence: React renders filter chip labels/values and table text through JSX text interpolation, not HTML injection.
Rationale: No new HTML rendering surface is needed for this story.
Alternatives considered: Add sanitizer abstraction; rejected as unnecessary for non-HTML rendering.
Test implications: final verification only.
