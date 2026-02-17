# Contract: Dashboard View Model and Data Fetching

## Purpose

Define client-side model normalization and endpoint usage for consolidated and source views.

## Endpoint Usage

### Consolidated active page

- Queue running: `GET /api/queue/jobs?status=running&limit=200`
- Queue queued: `GET /api/queue/jobs?status=queued&limit=200`
- Orchestrator running: `GET /orchestrator/runs?status=running&limit=100`
- Orchestrator pending: `GET /orchestrator/runs?status=pending&limit=100`
- Orchestrator awaiting approval: `GET /orchestrator/runs?status=awaiting_approval&limit=100`

### Detail pages

- Queue detail: `GET /api/queue/jobs/{job_id}`
- Queue events: `GET /api/queue/jobs/{job_id}/events?after=<iso8601>&limit=200`
- Queue artifacts: `GET /api/queue/jobs/{job_id}/artifacts`

- Orchestrator detail: `GET /orchestrator/runs/{run_id}`
- Orchestrator artifacts: `GET /orchestrator/runs/{run_id}/artifacts`

### Submit pages

- Queue submit: `POST /api/queue/jobs`
- Orchestrator submit: `POST /orchestrator/runs`
- Queue skill-based SpecKit launch: emitted as queue submit payload (`type="task"`) with `task.skill.id=speckit-*` and optional `task.skill.args` JSON object.

## Normalized Status Contract

- Shared status set: `queued`, `running`, `awaiting_action`, `succeeded`, `failed`, `cancelled`.
- Mapping rules follow `specs/017-thin-dashboard-ui/data-model.md`.

## Polling Contract

- List polling interval: 5 seconds.
- Detail polling interval: 2 seconds.
- Queue event incremental polling interval: 1 second.
- Polling paused when page visibility is hidden.

## Error Boundary Contract

1. A failed source request must not clear successful data from other sources.
2. Source errors are displayed as scoped warnings on relevant views.
3. Submit failures keep current form values and surface API error message.
4. Queue list supports filtering by task skill id so SpecKit-backed queue tasks are discoverable without a dedicated SpecKit category.
