# Agent Instructions

## Retrieving GitHub Action Status

The script `tools/get_action_status.py` can be used to retrieve the latest GitHub Action workflow results for a particular branch with detailed error information.

**Usage:**

To run the script:
```bash
python tools/get_action_status.py [--branch <branch-name>]
```

--branch <branch-name> should be explicitly specified when git is in a detached HEAD state as branch auto-detection will no longer work.

## Active Technologies
- Python 3.11 (matches existing MoonMind services and supported pyproject range) + Celery 5.4, RabbitMQ 3.x (broker), PostgreSQL (result backend & existing MoonMind DB for run persistence), Codex CLI, GitHub CLI (001-celery-chain-workflow)
- PostgreSQL `spec_workflow_runs` + `spec_workflow_task_states` (Celery result backend and workflow history); RabbitMQ broker for task dispatch & state callbacks; object storage optional for large artifacts (initially local filesystem under `var/artifacts/spec_workflows/<run_id>`) (001-celery-chain-workflow)

## Recent Changes
- 001-celery-chain-workflow: Added Python 3.11 (matches existing MoonMind services and supported pyproject range) + Celery 5.4, RabbitMQ 3.x (broker), PostgreSQL (result backend & existing MoonMind DB for run persistence), Codex CLI, GitHub CLI

## Spec Workflow Verification Checklist
- Bring up RabbitMQ and the dedicated Celery worker alongside the API service when validating the Spec Kit workflow: `docker compose up rabbitmq celery-worker api`.
- Confirm the worker logs include `Spec workflow task ...` entries for discover, submit, and publish steps; these messages include per-task summaries and should align with repository state transitions.
- Optional metrics: set `STATSD_HOST`/`STATSD_PORT` or `SPEC_WORKFLOW_METRICS_HOST`/`SPEC_WORKFLOW_METRICS_PORT` to capture task counters and durations emitted via StatsD before running end-to-end tests.
