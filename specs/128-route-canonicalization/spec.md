# Spec: Canonicalize Routes via Pure Redirects

## Problem

The dashboard currently has alias routes (`/tasks/create`, `/tasks/tasks-list`) that **render the same React pages** as their canonical counterparts (`/tasks/new`, `/tasks/list`) instead of redirecting. The `/tasks` root also redirects to `/tasks/list`. This means:

1. The same page is reachable under multiple URLs (e.g., both `/tasks/create` and `/tasks/new` render the task-create page).
2. Each alias passes a different `current_path` and `dashboardConfig` into the React boot payload, creating inconsistent client-side state.
3. Navigation links in the `_navigation.html` template point at `/tasks` (which redirects) and `/tasks/new` (canonical), but the alias routes remain active and reachable via direct URL or bookmarks.
4. `/tasks/secrets` and `/tasks/workers` already redirect correctly into settings — the same pattern should apply to `/tasks/create` → `/tasks/new` and `/tasks/tasks-list` → `/tasks/list`.

## Requirements

### Functional Requirements

- **FR-001**: `GET /tasks/create` MUST return an HTTP redirect (307) to `/tasks/new`.
- **FR-002**: `GET /tasks/tasks-list` MUST return an HTTP redirect (307) to `/tasks/list`.
- **FR-003**: The main "Tasks" nav link in `_navigation.html` MUST point directly at `/tasks/list` (canonical) instead of `/tasks`.
- **FR-004**: The "Create" nav link MUST point at `/tasks/new` (already canonical — no change needed to href).
- **FR-005**: Nav active-state logic MUST only match canonical paths (no longer checking aliases as "active").
- **FR-006**: The catch-all 404 error message MUST list only canonical routes.
- **FR-007**: All existing canonical routes (`/tasks/list`, `/tasks/new`, `/tasks/settings`, `/tasks/manifests`, `/tasks/schedules`, `/tasks/proposals`, `/tasks/skills`, `/tasks/manifests/new`) MUST continue to render React pages unchanged.

### Non-Functional Requirements

- **NF-001**: Redirects MUST use HTTP 307 (Temporary Redirect) to preserve request method and avoid caching issues during the transition period.
- **NF-002**: No changes to React frontend entrypoints, boot payload schema, or `dashboardConfig` shape — only server-side route handlers and nav template change.
- **NF-003**: Changes MUST be covered by unit tests at the HTTP route boundary.

### Acceptance Criteria

1. **Given** a user navigates to `/tasks/create`, **When** the request completes, **Then** the response is a 307 redirect to `/tasks/new`.
2. **Given** a user navigates to `/tasks/tasks-list`, **When** the request completes, **Then** the response is a 307 redirect to `/tasks/list`.
3. **Given** the nav bar renders, **When** the "Tasks" link is present, **Then** its href is `/tasks/list` (not `/tasks`).
4. **Given** the nav bar renders on `/tasks/list`, **When** the "Tasks" link is present, **Then** it has the `active` class.
5. **Given** the nav bar renders on `/tasks/new`, **When** the "Create" link is present, **Then** it has the `active` class.
6. **Given** a 404 dashboard error response, **When** the message is read, **Then** it lists only canonical routes (no `/tasks/create` or `/tasks/tasks-list`).

## Scope

- **In scope**: `api_service/api/routers/task_dashboard.py` route handlers for `/tasks/create` and `/tasks/tasks-list`; `api_service/templates/_navigation.html` nav links and active-state logic; catch-all 404 error message; unit tests.
- **Out of scope**: React frontend code, frontend routing, client-side navigation behavior, E2E browser tests (though existing E2E tests may need updates if they rely on alias routes).

## Clarifications

- **Why 307 instead of 301?** 307 avoids browser caching of the redirect during the transition. Once stable, a future cleanup could move to 301, but 307 is safer for the initial change.
- **Should `/tasks` root still redirect to `/tasks/list`?** Yes — that is already the canonical redirect and remains unchanged.
- **What about `/tasks/manifests/new`?** This is already a canonical route (not an alias of another route) and should remain unchanged.
