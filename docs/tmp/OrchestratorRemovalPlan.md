# Orchestrator Removal Plan

## 1. Overview
The orchestrator component (`mm-orchestrator`) is no longer required for the project. Restarting and upgrading MoonMind does not need to happen from within MoonMind anymore. This plan outlines the necessary steps to safely remove the orchestrator and all its associated dependencies, endpoints, database models, and workflows from the codebase.

## 2. Components to Remove

### 2.1 Docker Compose Configuration
- Remove the `orchestrator` service definition from `docker-compose.yaml`.
- Remove the `orchestrator-tests` service from `docker-compose.test.yaml`.
- Remove any associated environment variables (e.g., `ORCHESTRATOR_*`, `MOONMIND_ORCHESTRATOR_*`).

### 2.2 Python Codebase
- **API Routers**: Remove `api_service/api/routers/orchestrator.py` and remove its inclusion in the main API router setup (`api_service/api/main.py` or similar).
- **Workflows**: Delete the `moonmind/workflows/orchestrator` directory (contains queue worker, tasks, command runner, etc.).
- **Models**: Remove Orchestrator-related database models (e.g., `OrchestratorRun`, `OrchestratorPlanStep`, `OrchestratorRunArtifactType`) from `moonmind/models` or `db_models`.
- **Services**: Remove the `services/orchestrator` directory (if it contains any specific orchestrator files other than `requirements.txt`).

### 2.3 Tests & CI
- Remove `tests/integration/orchestrator` directory.
- Remove `tests/unit/workflows/orchestrator` directory.
- Remove `tests/contract/test_orchestrator_api.py`.
- Remove `.github/workflows/orchestrator-integration-tests.yml`.
- Remove or update references to orchestrator in `tools/test-integration.ps1`.
- Clean up tests that mention orchestrator in `tests/task_dashboard/test_submit_runtime.js` and `tests/unit/api/routers/test_task_compatibility.py`.

### 2.4 Documentation
- Remove or archive `docs/Temporal/OrchestratorTaskRuntime.md`.
- Remove or archive `docs/Temporal/OrchestratorArchitecture.md`.
- Update references to `mm-orchestrator` in `docs/MoonMindArchitecture.md`.
- Update `docs/Temporal/TemporalArchitecture.md` to remove references to orchestrator.

### 2.5 OpenAPI Specs
- Remove `specs/050-orchestrator-task-runtime` and its openapi.yaml.
- Remove `specs/005-orchestrator-architecture` and its openapi.yaml.

## 3. Database Migration
- Generate a new Alembic migration to drop the tables associated with the Orchestrator (e.g., `orchestrator_runs`, `orchestrator_plan_steps`, etc.). Ensure the migration logic safely handles existing data (e.g., dropping foreign keys first).

## 4. Post-Removal Validation
- Run unit and integration tests (`poetry run ./tools/test_unit.sh` or equivalent) to ensure no tests fail due to missing orchestrator imports or configurations.
- Start the application stack via `docker compose up -d` and verify that the API starts successfully and `mm-orchestrator` is no longer running.
