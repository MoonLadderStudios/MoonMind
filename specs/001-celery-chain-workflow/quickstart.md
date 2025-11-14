# Quickstart: Celery Chain Workflow Integration

## Prerequisites
- Python 3.11 + Poetry (matches MoonMind runtime)
- RabbitMQ 3.x broker reachable at `amqp://guest:guest@localhost:5672//`
- PostgreSQL with the MoonMind schema (serves both app DB + Celery backend)
- Codex CLI logged in (`codex login`) and GitHub credentials with branch/PR rights
- Docker (for Spec Kit job containers) and access to the target repository

## Setup Steps
1. **Install dependencies**
   ```bash
   poetry install
   ```
2. **Run database migrations**
   ```bash
   poetry run alembic upgrade head
   ```
3. **Configure environment**
   ```dotenv
   CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
   CELERY_RESULT_BACKEND=db+postgresql://moonmind:***@localhost:5432/moonmind
   SPEC_WORKFLOW_CODEX_QUEUE=codex
   SPEC_WORKFLOW_ARTIFACT_ROOT=var/artifacts/spec_workflows
   CODEX_ENV=prod
   GH_TOKEN=ghp_xxx
   ```
4. **Start services**
   ```bash
   # Terminal 1 – MoonMind API
   poetry run uvicorn api_service.main:app --reload

   # Terminal 2 – Celery worker dedicated to Spec workflows
   poetry run celery -A moonmind.workflows.speckit_celery.tasks worker \\
     -Q codex --loglevel=info --hostname=codex@%h
   ```
   _Recommended Celery settings (`celeryconfig.py`):_
   ```python
   broker_url = "pyamqp://guest:guest@localhost:5672//"
   result_backend = "db+postgresql://moonmind:***@localhost:5432/moonmind"
   task_acks_late = True
   task_reject_on_worker_lost = True
   worker_prefetch_multiplier = 1
   task_serializer = "json"
   result_serializer = "json"
   accept_content = ["json"]
   ```
5. **Verify credentials and toolchain**
   ```bash
   poetry run python -m api_service.scripts.ensure_codex_config
   poetry run speckit --version
   poetry run codex --version
   ```
   Both commands must succeed before attempting a run; otherwise the submission task will halt with `credential_invalid`.
6. **Trigger a workflow run**
   ```bash
   curl -X POST http://localhost:8000/api/workflows/speckit/runs \\
     -H 'Content-Type: application/json' \\
     -d '{
           "repository": "moonmind/spec-kit-reference",
           "featureKey": "spec-42-refresh-docs"
         }'
   ```
   Response payloads follow `SpecWorkflowRun` in `specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`. The API immediately returns HTTP `202` with the run record (status `pending` or `running`).
7. **Monitor progress**
   ```bash
   curl http://localhost:8000/api/workflows/speckit/runs/{run_id}
   ```
   Task-level telemetry (`tasks`) shows the Celery chain states, while `artifacts` lists log/patch files stored under `var/artifacts/spec_workflows/{run_id}`. Poll `/api/workflows/speckit/runs?status=running` to watch multiple runs.
8. **Retry a failed run**
   ```bash
   curl -X POST http://localhost:8000/api/workflows/speckit/runs/{run_id}/retry \\
     -H 'Content-Type: application/json' \\
     -d '{"mode":"resume_failed_task","notes":"Credentials fixed; retry publish"}'
   ```
   The API enqueues a new Celery chain from the failing task and returns the updated run with status `retrying`.

## Tips
- Logs and patches land in `var/artifacts/spec_workflows/{run_id}`; ensure the directory exists and is writable by the Celery worker UID.
- Set `SPEC_WORKFLOW_TEST_MODE=1` to stub Codex/GitHub for unit/integration testing.
- Use `celery -A ... worker -Q codex -B` to enable periodic cleanup tasks (e.g., stale artifact pruning) once implemented.
