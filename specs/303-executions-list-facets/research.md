# Research: Executions List and Facet API Support for Column Filters

## FR-001 / DESIGN-REQ-006: Server-Authoritative List Sorting And Filters

Decision: Partial existing implementation; extend the current Temporal-backed `/api/executions` list route.
Evidence: `api_service/api/routers/executions.py` already accepts canonical state/repo/runtime/skill/date params and builds Temporal visibility queries; it does not accept `sort`, `sortDir`, ID/title text filters, or repo contains filters.
Rationale: The existing route is the product API the Tasks List already calls, so extending it keeps the browser on MoonMind APIs and avoids a second list contract.
Alternatives considered: New list endpoint was rejected because the existing `/api/executions` response and frontend are already wired and tested.
Test implications: Unit tests for query validation and contract tests for request/response behavior.

## FR-002 / DESIGN-REQ-020: Canonical Include/Exclude/Text/Date/Blank Semantics

Decision: Add shared bounded parsing helpers inside the executions router before query construction.
Evidence: Current helper functions are nested inside `list_executions`, cover only some pairs, and do not enforce text length or value-list limits.
Rationale: Router-local helpers keep validation close to the route until there is another consumer; they can be reused by the new facet route in the same module.
Alternatives considered: A new standalone parser module was rejected for this story because only one router needs it and the blast radius would be larger.
Test implications: Unit tests for contradictory pairs, oversize lists, invalid blank mode, invalid date range, unsupported sort/facet, and structured errors.

## FR-003: Count And Pagination Metadata

Decision: Preserve the existing Temporal `count_workflows` + `list_workflows` behavior and ensure new filters/sorts feed both count and list query construction.
Evidence: `ExecutionListResponse` already includes `count`, `countMode`, `nextPageToken`, `degradedCount`, and `refreshedAt`.
Rationale: Count and pagination are already established response fields; this story should not create new storage or count semantics.
Alternatives considered: Estimating counts client-side was rejected because the source design requires full filtered result metadata when available.
Test implications: Contract tests should assert filtered requests call both count and list with the same task-scoped query.

## FR-004 / DESIGN-REQ-019: Facet Request Surface

Decision: Add `GET /api/executions/facets` returning a Pydantic facet response model and deriving facet counts through trusted server-side Temporal queries.
Evidence: No existing `/api/executions/facets` route or facet schema was found.
Rationale: Facets are a public API boundary for the Tasks List; returning values, counts, blank count, count mode, truncation, and next token matches the source design while keeping the browser away from Temporal.
Alternatives considered: Frontend-only current-page values were rejected because they do not satisfy counts beyond the current page.
Test implications: Contract tests for dynamic facet values, status facet counts, filter-exclusion behavior, and task-scope authorization.

## FR-005: Client Fallback Notice

Decision: Keep current-page value fallback but expose it as a visible notice when authoritative facets fail or are unavailable.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` currently derives repository and skill options from `data.items` without a user-visible fallback notice.
Rationale: This delivers the acceptance criterion without requiring the full future popover design in this backend-focused story.
Alternatives considered: Building the complete Google Sheets-style filter popover was rejected as out of scope for MM-590.
Test implications: Vitest coverage for facet fetch failure and visible current-page-values notice.

## FR-006 / DESIGN-REQ-025: Authorization And System Values

Decision: Reuse the same task-scope and owner enforcement for facets that list requests use.
Evidence: Existing tests verify non-admin users are constrained to `WorkflowType="MoonMind.Run" AND mm_entry="run" AND mm_owner_id="user-123"` and system/all scopes fail safe for list.
Rationale: Facets must not become a side channel for unauthorized values or counts.
Alternatives considered: Client-side filtering of facet values was rejected because authorization must be a backend decision.
Test implications: Unit/contract tests inspect generated facet queries for task scope and owner scope.

## FR-007: Structured Validation

Decision: Raise `TemporalExecutionValidationError` and convert it to existing `invalid_execution_query` 422 responses for list and facets.
Evidence: The list route already maps `TemporalExecutionValidationError` to structured HTTP 422 details.
Rationale: Reusing the established error code avoids a new error envelope while preventing raw backend query failures.
Alternatives considered: Allowing Temporal RPC failures to surface was rejected by source security requirements.
Test implications: Unit tests assert status code and error code/message for invalid inputs.

## FR-008 / SC-007: Traceability

Decision: Preserve `MM-590` and source design IDs in all MoonSpec artifacts and final reports.
Evidence: `spec.md` already preserves the canonical Jira brief and source coverage IDs.
Rationale: Verification and PR metadata need a stable trace back to the Jira story.
Alternatives considered: None.
Test implications: Final MoonSpec verification checks artifact traceability.
