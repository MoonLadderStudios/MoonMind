# Quickstart: Celery Chain Workflow Integration

## Prerequisites
- Python 3.11 with Poetry installed
- RabbitMQ 3.x instance reachable at `amqp://guest:guest@localhost:5672//` (single node using default classic queues)
- PostgreSQL database configured via existing MoonMind settings (also serves as the Celery result backend)
- Codex CLI authenticated (`codex login`) and GitHub CLI authorized with a token that can create branches/PRs

## Setup Steps
1. **Install dependencies**
   ```bash
   poetry install
   ```
2. **Run database migrations**
   ```bash
   poetry run alembic upgrade head
   ```
3. **Start services**
   ```bash
   # Terminal 1 - MoonMind API
   poetry run uvicorn api_service.main:app --reload

   # Terminal 2 - Celery worker dedicated to Spec Kit flows
   poetry run celery -A moonmind.workflows.speckit_celery.tasks worker -Q speckit --loglevel=info
   ```
   Add the following to your `.env` so Celery picks up the correct broker and result backend configuration:
   ```dotenv
   CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
   CELERY_RESULT_BACKEND=db+postgresql://moonmind:***@localhost:5432/moonmind
   ```
   Minimal Celery configuration (for example in `celeryconfig.py`) aligned with the single-node RabbitMQ setup:
   ```python
   broker_url = "pyamqp://guest:guest@localhost:5672//"
   result_backend = "db+postgresql://moonmind:***@localhost:5432/moonmind"

   task_acks_late = True
   task_acks_on_failure_or_timeout = True
   task_reject_on_worker_lost = True
   worker_prefetch_multiplier = 1
   accept_content = ["json"]
   task_serializer = result_serializer = "json"
   result_extended = True
   result_expires = 604800  # 7 days
   ```
4. **Configure secrets**
   - Add `CODEX_ENV`, `CODEX_DEVICE_TOKEN` (if applicable), and `GH_TOKEN` to MoonMind secret store or `.env`.
   - Validate with `poetry run python -m moonmind.workflows.speckit_celery.verify_secrets` (new utility).

5. **Trigger a workflow run**
   ```bash
   curl -X POST http://localhost:8000/api/workflows/speckit/runs \
     -H 'Content-Type: application/json' \
     -d '{}'
   ```
   The response is a JSON document matching `SpecWorkflowRun` in
   `specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`. A successful
   trigger returns HTTP `202` with the run already hydrated with task state
   placeholders. Example keys to expect:

   ```json
   {
     "id": "<uuid>",
     "status": "succeeded",
     "phase": "complete",
     "branchName": "001-celery-chain-workflow/t999",
     "tasks": [
       {"taskName": "discover_next_phase", "status": "succeeded"}
     ]
   }
   ```

   When the Spec tasks document has no unchecked items, the discovery task
   returns a `no_work` payload and the chain short-circuits with status
   `succeeded`.
6. **Monitor progress**
   ```bash
   curl http://localhost:8000/api/workflows/speckit/runs
   ```
   The list endpoint returns a collection with `items` and `nextCursor` (always
   `null` in the initial implementation). Use `/api/workflows/speckit/runs/<id>`
   to retrieve a specific run including serialized task state, artifact
   metadata, and credential audit results.
7. **Retry on failure**
   ```bash
   curl -X POST http://localhost:8000/api/workflows/speckit/runs/{run_id}/retry \
     -H 'Content-Type: application/json' \
     -d '{"notes":"Retry after credentials refreshed"}'
   ```

## Tips
- Use `poetry run celery -A moonmind.workflows.speckit_celery.tasks worker -Q speckit -B` to enable scheduled cleanup tasks.
- Logs and patches are written to `var/artifacts/spec_workflows/{run_id}`; ensure the directory exists and is writable.
- For integration tests, export `SPEC_WORKFLOW_TEST_MODE=1` to stub Codex/GitHub interactions.
