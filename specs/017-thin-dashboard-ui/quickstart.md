# Quickstart: Thin Dashboard Task UI

## 1. Prerequisites

- MoonMind API service running.
- Queue/workflow/orchestrator endpoints reachable from the same origin.
- Auth mode configured (`AUTH_PROVIDER=disabled` or OIDC).

## 2. Launch API Service

Use existing project workflow to run the API service (for example via Docker Compose).

## 3. Open Dashboard Routes

Visit:

- `/tasks` (consolidated active view)
- `/tasks/queue`, `/tasks/speckit`, `/tasks/orchestrator` (source lists)
- `/tasks/queue/new`, `/tasks/speckit/new`, `/tasks/orchestrator/new` (submit forms)

## 4. Manual Validation Checklist

1. Confirm consolidated page renders rows from all available sources.
2. Confirm list pages refresh automatically.
3. Submit one queue job and verify navigation to queue detail.
4. Submit one SpecKit run and verify run detail/task timeline loads.
5. Submit one Orchestrator run and verify run detail loads.
6. Confirm queue detail events update while polling.
7. Confirm artifact lists render where available.
8. Simulate one failing source and confirm partial rendering remains available.

## 5. Automated Validation

Run:

```bash
./tools/test_unit.sh
```

Ensure dashboard route and view-model tests pass.
