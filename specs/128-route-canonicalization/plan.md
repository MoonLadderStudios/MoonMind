# Plan: Route Canonicalization via Pure Redirects

## Summary

Convert alias route handlers (`/tasks/create`, `/tasks/tasks-list`) from rendering React pages into pure HTTP 307 redirects pointing at canonical routes (`/tasks/new`, `/tasks/list`). Update the navigation template to point the main "Tasks" link at `/tasks/list` and simplify active-state matching to canonical paths only. Update the 404 error message to list only canonical routes.

## Change Map

| Artifact | Change | Rationale |
|---|---|---|
| `api_service/api/routers/task_dashboard.py` | Replace `task_tasks_list_route` body with `RedirectResponse(url="/tasks/list")`. Replace `task_create_alias_route` body with `RedirectResponse(url="/tasks/new")`. Remove `response_class=HTMLResponse` from both. | FR-001, FR-002 |
| `api_service/api/routers/task_dashboard.py` | Update `_DASHBOARD_ROUTE_NOT_FOUND_DETAIL["message"]` to remove `/tasks/create` from the list. | FR-006 |
| `api_service/templates/_navigation.html` | Change main Tasks link href from `/tasks` to `/tasks/list`. Simplify active-state `if` to match only canonical paths. Remove alias paths from active-state checks. | FR-003, FR-005 |
| `tests/unit/api/routers/test_task_dashboard.py` | Add tests verifying `/tasks/create` and `/tasks/tasks-list` return 307 to canonical destinations. Update nav-active tests. Update 404 message assertion. | NF-003 |

## Execution Order

1. **Write tests first** (TDD): New test cases for redirect behavior, updated assertions for nav and 404 message.
2. **Implement route handler changes**: Convert alias handlers to redirects.
3. **Update nav template**: Change href and active-state logic.
4. **Update 404 message**: Remove alias references.
5. **Run tests**: Verify all pass.

## Risks

- **E2E test breakage**: Existing E2E tests in `tests/e2e/test_task_create_submit_browser.py` and `tests/e2e/test_settings_ui_browser.py` navigate to `/tasks/create`. Playwright's `page.goto()` follows redirects by default, so these should still work — but assertions on `page.url` may need updating. This is out of scope for unit tests but will be noted.
- **Bookmarked URLs**: Users with bookmarks to `/tasks/create` or `/tasks/tasks-list` will be redirected transparently (307).

## Testing Strategy

- Unit tests at the FastAPI `TestClient` boundary verify:
  - `/tasks/create` → 307 → `/tasks/new`
  - `/tasks/tasks-list` → 307 → `/tasks/list`
  - Nav template renders correct hrefs and active states
  - 404 message lists only canonical routes
- Existing tests that assert 200 on alias routes must be updated to expect 307 redirects.
