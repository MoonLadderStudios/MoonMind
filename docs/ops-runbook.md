# Operations Runbook

MoonMind background jobs run using the default user account.

- **User ID:** `00000000-0000-0000-0000-000000000000`
- **Email:** `default@example.com`

Keys for model providers (e.g. Google and OpenAI) are read from this user's profile whenever jobs execute. In `disabled` auth mode, the values from `.env` seed this profile on startup. Remove them from the environment to verify jobs fail until a key is stored on this user.

**Gemini CLI authentication**: Set `GOOGLE_API_KEY` in the deployment environment so the orchestrator and Celery worker can authenticate when calling the Gemini CLI. In `disabled` auth mode this value is copied from `.env` into the default user profile on startup; otherwise, ensure the default user has a stored Google API key before launching automation workloads.

## Spec Workflow Celery Chain Operations

- **Services to run**: `docker compose up rabbitmq celery-worker api` (RabbitMQ broker, dedicated Celery worker, and API service). Ensure PostgreSQL is reachable for the Celery result backend.
- **Metrics**: The worker emits StatsD-compatible counters and timers (prefix `moonmind.spec_workflow`). Point `STATSD_HOST`/`STATSD_PORT` or `SPEC_WORKFLOW_METRICS_HOST`/`SPEC_WORKFLOW_METRICS_PORT` at your collector before triggering runs to capture observability data.
- **Log review**: Look for `Spec workflow task …` entries in the worker logs to confirm each stage transitions through `running`, `success`, or `failure` with summarized payloads.
- **Credential validation**: Failed runs often stem from missing Codex or GitHub credentials. The first task attempt records audit notes; resolve secrets and retry via `/api/workflows/speckit/runs/{id}/retry`.
- **Artifact locations**: Patches, JSONL logs, and GitHub API responses are stored under `var/artifacts/spec_workflows/<run_id>/`. Mount this directory when running the worker locally to inspect failures.

### Codex routing observability

- **Metrics detail**: Codex-aware tasks increment the following StatsD series (all under the `moonmind.spec_workflow` prefix):
  - `task_start` / `task_success` / `task_failure` counters for each task transition.
  - `task_duration` timer reported when a task succeeds or fails.
  Tags always include `task=<celery task name>` and `attempt=<n>`; status tags are `status=running|success|failure` with `retry=true|false` on start events. Use these to graph Codex queue throughput and alert on elevated retry rates.
- **Codex queue logging**: Every Codex shard task (`submit_codex_job`, `apply_and_publish`, etc.) logs a `Spec workflow task …` line with sanitized `details=` metadata. Expect to see keys such as `codex_queue`, `codex_volume`, and `codex_shard_index` in the start log and a matching `summary=` payload on success. These fields come from the workflow context and confirm which queue/volume pair executed the run.
- **Pre-flight visibility**: The Docker-based login probe emits `Codex pre-flight check passed/failed` log lines that include `codex_volume`, the Docker exit code, and the condensed CLI output. Use these messages to pinpoint shards that need re-authentication before rerunning the workflow.
- **Structured extras**: Failures propagate structured `details=` entries (for example `codex_task_id`, `codex_preflight_status`, `codex_queue`). Forward worker logs to a JSON-aware sink so these extras remain queryable when triaging routing or credential issues.
