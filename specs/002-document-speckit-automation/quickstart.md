# Quickstart: Spec Kit Automation Pipeline

This guide demonstrates how to execute the Spec Kit automation workflow locally using the Docker Compose stack and inspect the resulting run metadata and artifacts.

---

## Prerequisites

- Docker with Compose v2 and access to the host Docker socket (`/var/run/docker.sock`).
- Python 3.11 with project dependencies installed (`poetry install` or `pip install -e .[dev]`).
- Valid automation credentials:
  - `GITHUB_TOKEN` scoped to clone/push/PR on the target repository (PAT or GitHub App token).
  - `CODEX_API_KEY` for Codex CLI access (skip push by enabling test mode if unavailable).
- Optional: StatsD endpoint reachable from the worker if metrics should be emitted.

> **Tip:** For smoke tests that should not push commits or open PRs, export `SPEC_WORKFLOW_TEST_MODE=true`.

---

## Step 1 – Install Dependencies

```bash
poetry install  # or: pip install -e .[dev]
```

Ensure Docker is running and the current user can access the Docker socket. Create a `.env` file if you prefer loading secrets automatically when Compose starts.

---

## Step 2 – Export Secrets & Configuration

```bash
export GITHUB_TOKEN="<github-token>"
export CODEX_API_KEY="<codex-token>"
export SPEC_WORKFLOW_TEST_MODE=true                # optional dry-run mode
export SPEC_WORKFLOW_METRICS_ENABLED=true          # optional
export SPEC_WORKFLOW_METRICS_HOST=localhost        # optional
export SPEC_WORKFLOW_METRICS_PORT=8125             # optional
export GIT_AUTHOR_NAME="Spec Kit Bot"              # optional overrides
export GIT_AUTHOR_EMAIL="speckit-bot@example.com"  # optional overrides
```

When `SPEC_WORKFLOW_TEST_MODE=true`, the publish phase records artifacts but skips git push and PR creation, making it safe for local validation without touching remote repositories.

---

## Step 3 – Start Supporting Services

```bash
docker compose up rabbitmq celery-worker api
```

* The API container runs database migrations during startup (`api_service/prestart.sh`).
* The Celery worker mounts `/var/run/docker.sock` and the `speckit_workspaces` named volume; watch `docker compose logs -f celery-worker` for readiness messages such as `Spec workflow task discover_next_phase started`.

---

## Step 4 – Trigger a Workflow Run

Open a new terminal (Compose continues running) and execute:

```bash
python - <<'PY'
import asyncio
from moonmind.workflows.speckit_celery.orchestrator import trigger_spec_workflow_run

async def main() -> None:
    triggered = await trigger_spec_workflow_run(
        feature_key="002-document-speckit-automation"
    )
    print("Run ID:", triggered.run_id)
    print("Celery Chain ID:", triggered.celery_chain_id)

asyncio.run(main())
PY
```

The command returns the workflow run identifier and Celery chain task ID. Keep the Celery logs open to observe phase transitions (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`).

---

## Step 5 – Monitor Status

1. **Celery logs** – `docker compose logs -f celery-worker` shows phase start/finish events, credential audit results, and any retries.
2. **REST API** – Query run details and artifacts:

   ```bash
   curl http://localhost:8080/api/spec-automation/runs/<run_id>
   ```

   Replace `<run_id>` with the UUID printed in Step 4. The response includes agent configuration, phase timestamps, branch/PR metadata, and artifact IDs.
3. **Database (optional)** – Inspect `spec_automation_runs` and `spec_automation_run_tasks` tables using `psql` or your preferred client to confirm persisted status transitions.

---

## Step 6 – Review Artifacts

Artifacts live under the shared workspace volume:

```bash
docker compose exec celery-worker ls /work/runs/<run_id>/artifacts
docker compose exec celery-worker cat /work/runs/<run_id>/artifacts/diff-summary.txt
```

Use `docker compose cp` to copy logs locally if needed. Artifacts include stdout/stderr for each phase, Codex diff patches, GitHub API responses, and credential audit reports (values redacted).

---

## Step 7 – Cleanup

When finished testing:

```bash
docker compose down
docker volume rm speckit_workspaces   # optional: remove cached workspaces
```

Removing the volume ensures subsequent runs start from a clean workspace.
