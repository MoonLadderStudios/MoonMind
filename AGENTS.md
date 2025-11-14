# Agent Instructions

## Active Technologies
- Python 3.11 (matches existing MoonMind services and supported pyproject range) + Celery 5.4, RabbitMQ 3.x (broker), PostgreSQL (result backend & existing MoonMind DB for run persistence), Codex CLI, GitHub CLI (001-celery-chain-workflow)
- PostgreSQL `spec_workflow_runs` + `spec_workflow_task_states` (Celery result backend and workflow history); RabbitMQ broker for task dispatch & state callbacks; object storage optional for large artifacts (initially local filesystem under `var/artifacts/spec_workflows/<run_id>`) (001-celery-chain-workflow)
- Docker Compose hosted mm-orchestrator service (Python 3.11 + Celery task chain) mounting `/workspace` and `/var/run/docker.sock`, emitting StatsD metrics and writing artifacts to `var/artifacts/spec_workflows/<run_id>` (005-orchestrator-architecture)

## Recent Changes
- 001-celery-chain-workflow: Added Python 3.11 (matches existing MoonMind services and supported pyproject range) + Celery 5.4, RabbitMQ 3.x (broker), PostgreSQL (result backend & existing MoonMind DB for run persistence), Codex CLI, GitHub CLI
- 005-orchestrator-architecture: Documented mm-orchestrator container responsibilities (plan/patch/build/restart/verify/rollback), StatsD instrumentation hooks, approval enforcement, and sequential worker processing against the shared Docker daemon

## Spec Workflow Verification Checklist
- Bring up RabbitMQ and the dedicated Celery worker alongside the API service when validating the Spec Kit workflow: `docker compose up rabbitmq celery-worker api`.
- Confirm the worker logs include `Spec workflow task ...` entries for discover, submit, and publish steps; these messages include per-task summaries and should align with repository state transitions.
- Optional metrics: set `STATSD_HOST`/`STATSD_PORT` or `SPEC_WORKFLOW_METRICS_HOST`/`SPEC_WORKFLOW_METRICS_PORT` to capture task counters and durations emitted via StatsD before running end-to-end tests.
- For orchestrator validation runs, also start the `orchestrator` service, watch for `ActionPlan` step logs (analyze → patch → build → restart → verify), and confirm artifacts land under `var/artifacts/spec_workflows/<run_id>/`.
