# Research: Route Canonicalization

## R001: Existing Redirect Patterns

The codebase already uses this pattern for legacy routes:

```python
@router.get("/tasks/secrets")
async def task_secrets_route(...) -> RedirectResponse:
    return RedirectResponse(url="/tasks/settings?section=providers-secrets", status_code=307)

@router.get("/tasks/workers")
async def task_workers_route(...) -> RedirectResponse:
    return RedirectResponse(url="/tasks/settings?section=operations", status_code=307)
```

Both use `status_code=307` (Temporary Redirect). This is the pattern to follow.

## R002: Nav Template Active-State Logic

Current `_navigation.html` checks both canonical and alias paths for active state:
- Tasks link: checks `/tasks`, `/tasks/list`, `/tasks/tasks-list`, plus a catch-all for non-excluded sub-paths
- Create link: checks `/tasks/new`, `/tasks/create`

After canonicalization:
- Tasks link should check only `/tasks/list` (and the catch-all for detail pages)
- Create link should check only `/tasks/new`

## R003: Test Client Redirect Behavior

FastAPI `TestClient` (built on `httpx`) follows redirects by default when `follow_redirects=True` (the default). Tests that want to verify the redirect itself must use `follow_redirects=False` and check `status_code == 307` plus `headers["location"]`.

## R004: E2E Test Impact

Existing E2E tests use `page.goto(f"{base_url}/tasks/create")`. Playwright follows redirects by default, so the page will still load. However, assertions like `assert page.url.endswith("/tasks/create")` will fail because the browser URL will be `/tasks/new` after the redirect. These assertions need updating to expect `/tasks/new`.

## R005: `_STATIC_PATHS` Set

The `_STATIC_PATHS` set in `task_dashboard.py` currently includes `"create"` and `"tasks-list"`. After conversion to redirects, these paths will no longer need to be in `_STATIC_PATHS` since they won't render pages — but keeping them doesn't break anything since the redirect handlers are registered before the catch-all. For cleanliness, they should be removed from `_STATIC_PATHS`.

## R006: No Data Model Changes

This feature involves no data model changes. No `data-model.md` is required.
