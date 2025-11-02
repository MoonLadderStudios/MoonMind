# Quickstart: Celery Chain Workflow Integration

## Prerequisites
- Python 3.11 with Poetry installed
- Redis 8 instance reachable at `redis://localhost:6379/0`
- PostgreSQL database configured via existing MoonMind settings
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
4. **Configure secrets**
   - Add `CODEX_ENV`, `CODEX_DEVICE_TOKEN` (if applicable), and `GH_TOKEN` to MoonMind secret store or `.env`.
   - Validate with `poetry run python -m moonmind.workflows.speckit_celery.verify_secrets` (new utility).

5. **Trigger a workflow run**
   ```bash
   curl -X POST http://localhost:8000/api/workflows/speckit/runs \
     -H 'Content-Type: application/json' \
     -d '{}'
   ```
6. **Monitor progress**
   ```bash
   curl http://localhost:8000/api/workflows/speckit/runs
   ```
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
