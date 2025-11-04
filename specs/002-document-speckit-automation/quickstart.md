# Quickstart: Spec Kit Automation Pipeline

## Prerequisites

- Docker installed with access to the host Docker socket.
- Python 3.11 environment with project dependencies (`poetry install` or `pip install -r requirements`).
- RabbitMQ 3.x and PostgreSQL available (use `docker compose` services for local dev).
- Valid `GITHUB_TOKEN` and `CODEX_API_KEY` in the environment.

## 1. Start Supporting Services

```bash
docker compose up rabbitmq celery-worker api
```

This launches the Celery worker (with Docker socket mount), RabbitMQ broker, and API service. Logs from the worker should show readiness to accept `speckit` queue tasks.

## 2. Seed Environment Variables

```bash
export GITHUB_TOKEN=<token>
export CODEX_API_KEY=<token>
export SPEC_WORKFLOW_METRICS_HOST=localhost   # optional
export SPEC_WORKFLOW_METRICS_PORT=8125        # optional
```

Set git identity overrides if not already configured:

```bash
export GIT_AUTHOR_NAME="Spec Kit Bot"
export GIT_AUTHOR_EMAIL="speckit-bot@example.com"
```

## 3. Trigger a Test Run

```bash
python - <<'PY'
from celery_worker.speckit_worker import kickoff_spec_run

k = kickoff_spec_run.delay(
    repo="moonmind/moonmind",
    specify_text="Spec Kit Automation smoke test",
    options={"dry_run": True}
)
print("Run dispatched:", k.id)
PY
```

Monitor Celery logs to verify container creation, phase execution, and cleanup. When `dry_run` is true, git push and PR creation are skipped while artifacts are still generated.

## 4. Inspect Run Status

Use the API once contracts are implemented:

```bash
curl http://localhost:8080/api/spec-automation/runs/<run_id>
```

Or query the PostgreSQL `spec_workflow_runs` table to confirm status transitions and artifact references.

## 5. Review Artifacts

Artifacts for each run are stored under the named volume mounted at `/work/runs/<run_id>/artifacts`. Use `docker compose exec celery-worker ls /work/runs` to locate the directory, then download logs/diff summaries for validation.

## 6. Cleanup

```bash
docker compose down
```

Remove stale workspaces if needed:

```bash
docker volume rm speckit_workspaces
```
