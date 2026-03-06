# Quickstart: Orchestrator Task Runtime Upgrade

## Prerequisites
- Branch: `042-orchestrator-task-runtime`
- Docker + Docker Compose available
- API DB migrations applied via `init-db` service
- Worker token configured for orchestrator queue worker

## 1. Start runtime services

```bash
docker compose up -d api-db rabbitmq api orchestrator celery-worker
```

Optional logs while validating:

```bash
docker compose logs -f orchestrator api
```

## 2. Validate API aliases and transitional IDs

```bash
# create via task alias
curl -sS -X POST http://localhost:5000/orchestrator/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "instruction":"Run runtime migration",
    "targetService":"orchestrator",
    "priority":"normal",
    "steps":[
      {
        "id":"step-1",
        "title":"Sync stack",
        "instructions":"Refresh MoonMind services",
        "skill":{"id":"update-moonmind","args":{}}
      }
    ]
  }'

# list via legacy alias
curl -sS http://localhost:5000/orchestrator/runs?limit=20
```

Expected:
- create/list/detail/approval/retry work under both `/orchestrator/tasks*` and `/orchestrator/runs*`
- responses include both `taskId` and `runId` during migration window

## 3. Validate unified dashboard routes

1. Open `/tasks/list` and confirm queue + orchestrator rows render together.
2. Open `/tasks/<taskId>?source=orchestrator` and verify shared detail shell renders orchestrator timeline/artifacts.
3. Confirm redirects:
- `/tasks/orchestrator` -> `/tasks/list?filterRuntime=orchestrator`
- `/tasks/queue` -> `/tasks/list?source=queue`
- `/tasks/orchestrator/<id>` -> `/tasks/<id>?source=orchestrator`

## 4. Validate orchestrator submit mode (runtime-aligned behavior)

1. Open `/tasks/queue/new?runtime=orchestrator`.
2. Add explicit steps with per-step `skill.id` and `skill.args`.
3. Confirm `targetService`, `priority`, and optional `approvalToken` are accepted.
4. Submit and verify request hits `/orchestrator/tasks` and redirects to `/tasks/<taskId>?source=orchestrator`.

## 5. Validate degraded mode (DB outage mid-task)

1. Start a multi-step orchestrator task.
2. During execution, stop DB:

```bash
docker compose stop api-db
```

3. Confirm worker continues executing steps and writes artifact snapshots (`state-snapshots.jsonl`).
4. Restore DB:

```bash
docker compose start api-db
```

5. Run reconciliation routine (implementation target) and verify DB task/step state catches up with artifact snapshots.
6. Confirm queue terminal state reconciliation retries succeed after connectivity returns.

## 6. Run validation tests

Unit + dashboard tests (required):

```bash
./tools/test_unit.sh
```

Optional orchestrator integration suite:

```bash
docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
```

## 7. Runtime scope gates

After `tasks.md` exists and implementation changes are present, run:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

Expected: both pass. If mode is switched to docs, scope checks are intentionally skipped; this feature must remain in `runtime` mode.

## 8. Execution Evidence (2026-02-26)

- `./tools/test_unit.sh`: **PASS** (`882 passed`, `8 subtests passed`, `301 warnings`, elapsed `124.08s`).
- `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`: **PASS** (`runtime tasks=24`, `validation tasks=14`).
- `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`: **PASS** (`runtime files=13`, `test files=7`).
- `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests`: **BLOCKED IN THIS ENVIRONMENT**. Compose reported missing Docker buildx plugin and daemon returned `403 Forbidden` while building `repo-orchestrator-tests`.
