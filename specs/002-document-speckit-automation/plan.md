# Implementation Plan: Spec Kit Automation Pipeline

**Branch**: `002-document-speckit-automation` | **Date**: 2025-11-03 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/002-document-speckit-automation/spec.md`

**Note**: This plan follows the `/speckit.plan` workflow and stays aligned with research and design artifacts produced in Phases 0–1.

## Summary

Automate end-to-end execution of Spec Kit phases by having Celery workers spin up ephemeral job containers, run the `speckit.specify`, `speckit.plan`, and `speckit.tasks` prompts against a repository workspace, and publish results (branch + draft PR + artifacts) while keeping the agent backend swappable and the environment isolated per run. Research confirmed Docker-outside-of-Docker orchestration, env-var-only secret injection, optional StatsD hooks, and seven-day artifact retention as the baseline approach.

## Technical Context

**Language/Version**: Python 3.11 for workers and job environment shell tooling  
**Primary Dependencies**: Celery 5.4, RabbitMQ 3.x (broker), PostgreSQL result backend, Codex CLI, Git CLI/GitHub CLI, Docker client/SDK  
**Storage**: PostgreSQL tables `spec_workflow_runs` and `spec_workflow_task_states`; artifacts persisted under named Docker volume `speckit_workspaces` (optionally mirrored to object storage)  
**Testing**: pytest suites (unit/integration), Celery chain integration tests, docker-compose smoke tests  
**Target Platform**: Containerized Linux services deployed via Docker Compose / Kubernetes nodes with Docker socket access  
**Project Type**: Backend workflow automation (Celery worker plus orchestration library)  
**Performance Goals**: ≥95% of runs complete Spec Kit phases and publish artifacts within 20 minutes; structured status emitted for 100% of tasks  
**Constraints**: Secrets must remain ephemeral (env injection only), job containers cleaned after run, deterministic branch naming, retries with backoff, network egress limited to GitHub/Codex endpoints  
**Scale/Scope**: Initial capacity sized for low concurrency (1–2 simultaneous runs) with roadmap to scale via worker count; supports per-run workspace isolation and artifact retention ≥7 days

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Constitution file contains placeholders only; no enforceable principles defined. Recorded governance gap in research and proceeding with default compliance stance.
- Post-design review confirmed feature outputs (data model, contracts, quickstart) stay within the placeholder governance allowance.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
celery_worker/
├── speckit_worker.py         # Celery worker entrypoint/orchestration
└── __init__.py

moonmind/workflows/speckit_celery/
├── orchestrator.py           # Workflow coordination + context handling
├── tasks.py                  # Celery task definitions for phases
├── repositories.py           # Persistence adapters
├── models.py                 # Pydantic schemas for run/task state
└── __init__.py

docs/
└── SpecKitAutomation.md      # Technical design source material

docker-compose.yaml           # Service orchestration (worker, rabbitmq, api)
tools/
└── get_action_status.py      # Operational utilities (unchanged but reference for monitoring)
```

**Structure Decision**: Extend existing Celery worker and workflow orchestration modules under `celery_worker/` and `moonmind/workflows/speckit_celery/`, augmenting documentation and compose definitions without introducing new top-level packages.

## Operations Runbook (Phase 6)

1. **Pre-flight checks**
   - Confirm Docker daemon access and ensure the `speckit_workspaces` volume exists (created automatically by Compose if missing).
   - Export runtime secrets: `GITHUB_TOKEN`, `CODEX_API_KEY`, and optional StatsD settings (`SPEC_WORKFLOW_METRICS_ENABLED`, `SPEC_WORKFLOW_METRICS_HOST`, `SPEC_WORKFLOW_METRICS_PORT`).
   - Set `SPEC_WORKFLOW_TEST_MODE=true` for dry runs that should skip git push/PR while still generating artifacts.
   - Override agent metadata via `SPEC_AUTOMATION_AGENT_VERSION` / `SPEC_AUTOMATION_PROMPT_PACK_VERSION` when coordinating prompt updates.
2. **Launch services**
   - `docker compose up rabbitmq celery-worker api` — API container applies Alembic migrations; Celery worker mounts `/var/run/docker.sock` and the shared workspace volume.
   - Verify worker readiness in logs (look for `Spec workflow task discover_next_phase started`).
3. **Trigger automation**
   - Python REPL helper: `asyncio.run(trigger_spec_workflow_run(feature_key="002-document-speckit-automation"))`.
   - REST helper: `POST /api/spec-automation/runs` (see contracts) to enqueue via FastAPI.
4. **Monitor execution**
   - Tail Celery logs: `docker compose logs -f celery-worker` to follow `discover_next_phase`, `submit_codex_job`, `apply_and_publish` progress.
   - Observe StatsD metrics when enabled (prefix `spec_automation.*`) for phase durations, retries, cleanup timing.
   - Query run status: `curl http://localhost:8080/api/spec-automation/runs/<run_id>` for branch/PR metadata, credential audit snapshot, and artifact IDs.
5. **Review artifacts**
   - Inspect `/work/runs/<run_id>/artifacts` via `docker compose exec celery-worker ls /work/runs/<run_id>/artifacts`.
   - Retrieve diff summaries or logs with `docker compose cp` when attaching evidence to reviews/incidents.
6. **Cleanup**
   - `docker compose down` followed by `docker volume rm speckit_workspaces` (optional) resets cached workspaces between test cycles.

## Monitoring & Incident Response Notes

- **Metrics expectations**: `_MetricsEmitter` emits `task_start`, `task_success`, `task_failure`, and `task_duration` tagged with `{run_id, task, attempt}`. Alert on sustained `task_failure` increments or missing `task_success` for >15 minutes per run.
- **Credential validation**: `CredentialValidationError` raises early if Codex/GitHub tokens are misconfigured. Review the `credential-audit.json` artifact and confirm env overrides via `settings.spec_workflow` or exported variables.
- **Container lifecycle**: `JobContainerManager` tears down job containers. If orphaned `job-<run_id>` containers persist, remove manually and invoke `SpecWorkspaceManager.cleanup_expired_workspaces()` for workspace hygiene.
- **Retry guidance**: Use `retry_spec_workflow_run(run_id, notes=...)` from the orchestrator when resuming failed runs; notes surface in the API payload for auditing.
- **API availability**: Run detail endpoints live under `/api/spec-automation`; ensure the API container remains healthy so dashboards can retrieve status and artifacts.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
