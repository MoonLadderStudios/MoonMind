# Quickstart: Worker Self-Heal System

## Prerequisites
- Docker (or WSL2) with the MoonMind compose stack (`docker compose up rabbitmq api celery-worker`), Git, and Python 3.11 tooling installed.
- Required secrets exported: `GITHUB_TOKEN`, `CODEX_API_KEY`, and any vault credentials used by the worker.
- Optional observability: set `SPEC_WORKFLOW_METRICS_HOST`/`SPEC_WORKFLOW_METRICS_PORT` (or `STATSD_HOST`/`STATSD_PORT`) if you want to capture the new StatsD metrics locally.

## Configure Worker Budgets
Set defaults that match `docs/WorkerSelfHealSystem.md`. These env vars can be injected into the Celery worker container or `env/.env`:
```bash
export STEP_MAX_ATTEMPTS=3
export STEP_TIMEOUT_SECONDS=900
export STEP_IDLE_TIMEOUT_SECONDS=300
export STEP_NO_PROGRESS_LIMIT=2
export JOB_SELF_HEAL_MAX_RESETS=1
```
The worker maps these values into `SelfHealConfig`. Use smaller numbers (e.g., 30 s wall, 10 s idle) when running local smoke tests so timeouts trigger quickly.

## Smoke Test: Soft Reset on Idle Timeout
1. Launch the queue worker (`celery_worker/speckit_worker.py`) with the env vars above.
2. Submit a task that intentionally stalls inside a step (e.g., add `sleep 9999` to a Spec Kit step or use a dummy repo script). One option is to enqueue the `samples/self-heal-hang.json` payload via the API or CLI.
3. Watch `tests/unit/agents/codex_worker/test_worker.py::test_idle_timeout_soft_reset` for the expected behavior and tail worker logs:
   ```bash
   docker compose logs -f celery-worker | grep self_heal
   ```
4. After ~idle-timeout seconds you should see `task.self_heal.triggered` followed by `task.step.attempt.started` for attempt 2. The worker terminates the hung runtime, restarts the same step, and writes `state/self_heal/attempt-0001-0002.json`.
5. Confirm StatsD counters (`task.self_heal.attempts_total{class:transient_runtime,strategy:soft_reset}`) increment if metrics are enabled.

## Smoke Test: Hard Reset + Resume
1. Run or craft a task that produces identical failures (e.g., deterministic lint failure) so `step_no_progress_limit` is exceeded.
2. Observe the worker escalating to `strategy="hard_reset"` and rebuilding the workspace. Artifacts `patches/steps/step-0000.patch` + `state/steps/step-0000.json` are replayed before attempt N+1.
3. Kill the worker between attempts, restart it, and use the queue API to issue a `resume_from_step` command:
   ```bash
   curl -X POST \
     -H "Authorization: Bearer <operator-token>" \
     -H "Content-Type: application/json" \
     http://localhost:5000/api/task-runs/<job_id>/control \
     -d '{"action":"resume_from_step","stepId":"step-3"}'
   ```
4. The next heartbeat payload includes `payload.liveControl.recovery`. The worker rebuilds the workspace from `startingBranch`, reapplies `patches/steps/step-0000.patch`…`step-0002.patch`, emits `task.resume.from_step`, and continues at step 3.

## Operator Commands via Dashboard/API
- Pause/resume/takeover actions continue to work; the dashboard gains buttons for `Retry Step`, `Hard Reset Step`, and `Resume From Step`. Each call records a `task_run_control_events` row and updates `payload.liveControl.recovery`.
- Workers acknowledge each request via `task.control.recovery.ack` events, so dashboards can show whether a request is pending or completed.

## Running Tests
1. Run targeted worker + queue tests:
   ```bash
   ./tools/test_unit.sh -k "self_heal or recovery"
   ```
   This covers the new controller, idle/wall timeout handling, repository recovery payloads, and API router validation.
2. Run the full suite before publishing:
   ```bash
   ./tools/test_unit.sh
   ```
3. Optional integration: `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` exercises queue APIs against a live Postgres/RabbitMQ pair.

## Observability Checklist
- Ensure `task.self_heal.*` events and `task.step.attempt.*` events appear under the job’s event stream (`/api/queue/jobs/<id>/events`).
- Verify StatsD counters `task.self_heal.attempts_total`, `task.self_heal.recovered_total`, `task.self_heal.exhausted_total` and timers `task.step.duration_seconds`, `task.step.wall_timeout_total`, `task.step.idle_timeout_total`, `task.step.no_progress_total` using `nc -ul 8125` or your collector of choice.
- Confirm `state/steps` and `state/self_heal` artifacts are uploaded under `var/artifacts/agent_jobs/<job_id>/` for later inspection or download via the task dashboard.
