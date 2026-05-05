# Research: Shareable Filter URL Compatibility

## Input Classification

Decision: MM-589 is a single-story runtime feature request.
Evidence: The Jira brief has one actor, one Tasks List URL-state goal, one acceptance set, and source sections limited to `docs/UI/TasksListPage.md` 5.3, 12, 12.1, and 12.2.
Rationale: The story can be independently validated through Tasks List URL loading, URL/API query state, and execution-list validation.
Alternatives considered: Treating all of `docs/UI/TasksListPage.md` as a broad design was rejected because the Jira brief selected only URL compatibility and canonical encoding requirements.
Test implications: Frontend unit tests and API unit tests are sufficient for the first implementation slice; no new integration service dependency is introduced.

## FR-004 Repeated And Comma-Encoded Values

Decision: Support repeated params by reading all values for each canonical list key and splitting each value on commas, then de-duplicating non-empty trimmed values in first-seen order.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` currently uses `params.get(...)` and comma splitting; `api_service/api/routers/executions.py` currently receives one string per query alias and comma splits it.
Rationale: This preserves current comma behavior and adds the equivalent repeated-value representation requested by the design without changing the public filter names.
Alternatives considered: Replacing comma encoding with repeated-only params was rejected because existing URLs and tests already use comma values.
Test implications: Add frontend URL-load coverage for repeated params and API unit coverage for repeated request params.

## FR-005 Contradictory Include And Exclude Filters

Decision: Treat non-empty include and exclude values for the same canonical field as a validation error after empty normalization.
Evidence: The source design requires clear validation errors; current frontend silently prefers exclude, while the API applies both include and exclude.
Rationale: Fail-fast behavior is safer for shared links and API callers because ambiguous include/exclude state can otherwise produce misleading task lists.
Alternatives considered: Letting exclude win was rejected because it hides contradictory state; merging both was rejected because it contradicts the brief.
Test implications: Add frontend visible error coverage and API `422 invalid_execution_query` coverage.

## FR-006 Task-Only Visibility

Decision: Keep the existing normal Tasks List behavior of ignoring unsupported workflow scope state while forcing `scope=tasks` API calls.
Evidence: `hasUnsupportedWorkflowScopeState` and `_normalize_temporal_list_scope` already bound normal list requests to task-run rows.
Rationale: This directly satisfies fail-safe behavior without adding a diagnostics redirect in this story.
Alternatives considered: Redirecting admins to diagnostics was rejected as unnecessary scope because the existing normal-page message is a permitted design option.
Test implications: Preserve and extend UI/API tests that prove system/all/manifest inputs cannot widen visibility.

## FR-007 Cursor Reset

Decision: Reset `nextPageToken` and previous cursor stack on filter changes and page-size changes.
Evidence: `resetToFirstPage` already exists for filter changes; page-size behavior needs explicit coverage.
Rationale: Shared URLs should not carry stale cursors after list shape changes.
Alternatives considered: Keeping cursor when only page size changes was rejected because the design explicitly names page-size changes as pagination resets.
Test implications: Add frontend test that starts with `nextPageToken`, changes page size, and verifies the API request and URL omit stale cursor.
