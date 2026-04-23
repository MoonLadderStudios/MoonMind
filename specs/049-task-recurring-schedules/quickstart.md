# Quickstart: Task Recurring Schedules System

## 1. Prepare environment

1. Ensure DB is reachable and migrations are current.

```bash
poetry run alembic upgrade head
```

2. Start runtime services needed for schedule dispatch checks.

```bash
docker compose up -d api api-db codex-worker scheduler
```

Optional if validating manifest/housekeeping queue handling in a broader environment: include additional workers as configured for your deployment.

## 2. Create a recurring schedule via API

Set auth values for your environment first:

```bash
export MOONMIND_API_BASE="http://localhost:8000"
export MOONMIND_TOKEN="<bearer-token>"
```

Create a queue-task schedule:

```bash
curl -sS -X POST "$MOONMIND_API_BASE/api/recurring-tasks" \
  -H "Authorization: Bearer $MOONMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Nightly Queue Task",
    "scheduleType": "cron",
    "cron": "*/5 * * * *",
    "timezone": "UTC",
    "scopeType": "personal",
    "target": {
      "kind": "queue_task",
      "job": {
        "type": "task",
        "priority": 0,
        "maxAttempts": 3,
        "payload": {
          "repository": "MoonLadderStudios/MoonMind",
          "targetRuntime": "codex",
          "task": {
            "instructions": "Recurring maintenance task",
            "skill": {"id": "auto", "args": {}},
            "publish": {"mode": "none"}
          }
        }
      }
    },
    "policy": {
      "overlap": {"mode": "skip", "maxConcurrentRuns": 1},
      "catchup": {"mode": "last", "maxBackfill": 3},
      "misfireGraceSeconds": 900,
      "jitterSeconds": 0
    }
  }'
```

Capture the returned schedule `id` for detail/history checks.

## 3. Execute scheduler tick and verify run generation

Run one scheduler tick:

```bash
poetry run moonmind-scheduler --once
```

List schedule runs:

```bash
curl -sS "$MOONMIND_API_BASE/api/recurring-tasks/<schedule-id>/runs?limit=50" \
  -H "Authorization: Bearer $MOONMIND_TOKEN"
```

Expected result:
- At least one run row with `outcome` transitioning from `pending_dispatch` to `enqueued`.
- `queueJobId` populated for queue-backed dispatch success.

## 4. Validate dashboard contract surfaces

1. Open `/tasks/schedules` and confirm list rendering.
2. Create a schedule from `/tasks/new` and verify it appears in list/detail.
3. From schedule detail, trigger `Run Now` and verify a new run row appears with queue job linkage when dispatch succeeds.

## 5. Run automated validation suites

Run repository unit/contract tests (required entrypoint):

```bash
./tools/test_unit.sh
```

Run orchestrator integration validation when exercising end-to-end orchestrator workflow paths:

```bash
docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
```

Run runtime scope-gate validation before handoff:

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

Expected result:
- Both commands print `Scope validation passed`.

## 6. Runtime-mode completion checklist

- Confirm production runtime files were updated (not docs-only artifacts).
- Confirm recurring APIs, scheduler daemon, and dashboard schedule routes are all wired.
- Confirm tests for cron/timezone, policy/idempotency, API router behavior, and dashboard route/source contracts pass.
