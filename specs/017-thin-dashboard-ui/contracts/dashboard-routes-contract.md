# Contract: Dashboard Routes

## Purpose

Define the runtime page routes required for Strategy 1 thin dashboard MVP.

## Route Surface

All routes serve the same dashboard shell template while preserving unique URLs.

- `GET /tasks`
- `GET /tasks/queue`
- `GET /tasks/queue/new`
- `GET /tasks/queue/{job_id}`
- `GET /tasks/orchestrator`
- `GET /tasks/orchestrator/new`
- `GET /tasks/orchestrator/{run_id}`

## Route Behavior

1. Response content type is `text/html` and includes dashboard shell markup.
2. Shell includes links to static assets:
   - `/static/task_dashboard/dashboard.css`
   - `/static/task_dashboard/dashboard.js`
3. Shell exposes current path to client-side renderer.

## Authentication Behavior

- Dashboard page routes require authenticated user context via existing auth dependency.
- In `AUTH_PROVIDER=disabled` mode, current default-user behavior applies.

## Non-Goals

- No server-side rendering of source data for MVP.
- No server-owned dashboard API proxy layer; client calls existing source endpoints directly.
