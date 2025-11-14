# Quickstart – MoonMind Orchestrator Implementation

## 1. Prerequisites
1. Install Docker + docker-compose plugin on the host.
2. Export required secrets (Codex, GitHub, StatsD) into `.env` or secret store consumed by compose.
3. Ensure PostgreSQL migrations for `spec_workflow_runs` and `spec_workflow_task_states` are up to date (existing Spec Workflow requirement).
4. Bring up RabbitMQ, Celery worker, and API as noted in the Spec Workflow Verification Checklist:
   ```bash
   docker compose up rabbitmq celery-worker api -d
   ```

## 2. Run the orchestrator container locally
1. Add the new service to `docker-compose.yaml` (see plan) and build it:
   ```bash
   docker compose build orchestrator
   ```
2. Start the orchestrator with repository + Docker socket mounted:
   ```bash
   docker compose up orchestrator -d
   ```
3. Confirm logs show readiness and queue subscription (look for `orchestrator.run`).

## 3. Submit a test instruction
1. Use the MoonMind API (per OpenAPI contract) or CLI wrapper:
   ```bash
   curl -X POST http://localhost:8000/orchestrator/runs \
     -H 'Content-Type: application/json' \
     -d '{"instruction": "Fix missing dependency for api", "target_service": "api"}'
   ```
2. Observe Celery worker logs for step-level updates (`analyze → patch → build → restart → verify`).
3. Verify artifacts are written under `var/artifacts/spec_workflows/<run_id>/`.

## 4. Approvals and retries
1. If the target service requires approval, the run status will move to `awaiting_approval`. Provide approval:
   ```bash
   curl -X POST http://localhost:8000/orchestrator/runs/<run_id>/approvals \
     -H 'Content-Type: application/json' \
     -d '{"approver": {"id": "user-123", "role": "SRE"}, "token": "signed-token"}'
   ```
2. For failed runs, request a retry from the failing step:
   ```bash
   curl -X POST http://localhost:8000/orchestrator/runs/<run_id>/retry \
     -H 'Content-Type: application/json' \
     -d '{"resume_from_step": "build", "reason": "Credentials refreshed"}'
   ```

## 5. Testing workflow
1. Execute integration tests that spin up the orchestrator stack:
   ```bash
   docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
   ```
2. Ensure the suite covers:
   - Allow-list enforcement (only permitted files changed).
   - Compose build/restart commands limited to target service.
   - Health-check timeout → rollback path.
   - Artifact presence (`patch.diff`, `build.log`, `verify.log`, `rollback.log`).

Following these steps validates the orchestrator end-to-end before promoting changes to higher environments.
