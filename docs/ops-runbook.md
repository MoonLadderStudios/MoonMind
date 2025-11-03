# Operations Runbook

MoonMind background jobs run using the default user account.

- **User ID:** `00000000-0000-0000-0000-000000000000`
- **Email:** `default@example.com`

Keys for model providers (e.g. Google and OpenAI) are read from this user's profile whenever jobs execute. In `disabled` auth mode, the values from `.env` seed this profile on startup. Remove them from the environment to verify jobs fail until a key is stored on this user.

## Spec Workflow Celery Chain Operations

- **Services to run**: `docker compose up rabbitmq celery-worker api` (RabbitMQ broker, dedicated Celery worker, and API service). Ensure PostgreSQL is reachable for the Celery result backend.
- **Metrics**: The worker emits StatsD-compatible counters and timers (prefix `moonmind.spec_workflow`). Point `STATSD_HOST`/`STATSD_PORT` or `SPEC_WORKFLOW_METRICS_HOST`/`SPEC_WORKFLOW_METRICS_PORT` at your collector before triggering runs to capture observability data.
- **Log review**: Look for `Spec workflow task …` entries in the worker logs to confirm each stage transitions through `running`, `success`, or `failure` with summarized payloads.
- **Credential validation**: Failed runs often stem from missing Codex or GitHub credentials. The first task attempt records audit notes; resolve secrets and retry via `/api/workflows/speckit/runs/{id}/retry`.
- **Artifact locations**: Patches, JSONL logs, and GitHub API responses are stored under `var/artifacts/spec_workflows/<run_id>/`. Mount this directory when running the worker locally to inspect failures.
