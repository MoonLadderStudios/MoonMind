# Quickstart: Validate Live Task Handoff

## 1. Apply schema updates

```bash
docker compose exec -T api alembic -c /app/api_service/migrations/alembic.ini upgrade head
```

## 2. Run focused live-handoff unit coverage

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py
```

## 3. Run full unit regression

```bash
./tools/test_unit.sh
```

## 4. Validate scope gates

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## 5. Manual queue dashboard smoke flow

1. Open `/tasks/queue/<job-id>` for a running `task` job.
2. In **Live Session** card, click **Enable Live Session** and wait for `ready`.
3. Click **Grant Write (15m)** and confirm RW attach info appears with expiration.
4. Trigger **Pause**, send an operator message, then **Resume**.
5. Trigger **Revoke Session** and verify status transitions to revoked/ended.

## 6. Worker failure-mode smoke flow

1. Run worker without `tmate` binary in `PATH` (or simulate startup failure).
2. Submit a task with live session enabled.
3. Confirm task run continues and live session reports `error` state in API/dashboard.
