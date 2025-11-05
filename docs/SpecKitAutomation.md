# Spec Kit Automation — Architecture & Operations

**Status:** Final
**Owners:** MoonMind Eng
**Last Updated:** Nov 13, 2025
**Related Artifacts:** `/specs/002-document-speckit-automation/*`, Celery chain guidance in `specs/001-celery-chain-workflow`

---

## 1. Overview

Spec Kit Automation runs the `/speckit.specify`, `/speckit.plan`, and `/speckit.tasks` prompts against a target repository and
packages the results as a draft Pull Request. A Celery worker launches an ephemeral job container per run, ensuring the toolchain
(`git`, `gh`, Codex CLI, prompts) executes in a predictable environment while repository metadata, artifacts, and metrics are
recorded for operators.

Key responsibilities:

1. Accept automation requests via Celery (`celery_worker/speckit_worker.py`).
2. Use `moonmind/workflows/speckit_celery/tasks.py` to orchestrate run phases (`discover_next_phase`, `submit_codex_job`,
   `apply_and_publish`).
3. Manage Docker job containers with `moonmind/workflows/speckit_celery/job_container.py` and workspace lifecycles via
   `moonmind/workflows/speckit_celery/workspace.py`.
4. Persist run/task metadata through `moonmind/workflows/speckit_celery/repositories.py` into PostgreSQL tables seeded by the
   Alembic migration in `api_service/migrations/versions/`.
5. Surface results through the FastAPI router in `api_service/api/routers/spec_automation.py` and supporting schemas.

---

## 2. Component Architecture

### 2.1 Celery Worker

* Containerized Celery process (see `celery_worker/` compose service) with the Docker SDK available.
* Mounts:
  * `/var/run/docker.sock` to control job containers.
  * Named volume `speckit_workspaces` → `/work` for shared workspaces.
* Coordinates work through `_TASK_SEQUENCE` in `tasks.py` ensuring deterministic discover → submit → publish transitions.
* Emits StatsD metrics via `_MetricsEmitter` when `SPEC_WORKFLOW_METRICS_ENABLED=true` and host/port values are supplied.

### 2.2 Job Container

* Image configured by `SPEC_AUTOMATION_JOB_IMAGE` (defaults to `moonmind/spec-automation-job:latest`).
* Starts with a `sleep infinity` command; Celery issues `docker exec` operations using `JobContainer.exec()`.
* Environment variables include:
  * `HOME=/work/runs/{run_id}/home` for Codex CLI state.
  * Git identity (`GIT_AUTHOR_*`, `GIT_COMMITTER_*`) and GitHub auth tokens.
  * Agent configuration snapshot fields (`CODEX_ENV`, `CODEX_MODEL`, etc.).
* Secrets are injected at runtime and redacted from structured logs by `_redact_environment()`.

### 2.3 Persistence Layer

* SQLAlchemy models in `models.py` back `spec_automation_runs`, `spec_automation_run_tasks`, and
  `spec_automation_task_artifacts` tables.
* Repository methods handle run lifecycle, phase state, and artifact registration with idempotent upserts.
* Task payloads record credential audits and agent configurations alongside run metadata for troubleshooting.

### 2.4 API Surface

* `/api/spec-automation/runs/{id}` returns run metadata, phase status, and artifact references.
* `/api/spec-automation/runs/{id}/artifacts/{artifact_id}` streams artifact contents from the workspace root.
* Schemas defined in `moonmind/schemas/workflow_models.py` ensure API compatibility with downstream dashboards.

---

## 3. Execution Lifecycle

1. **Kickoff** – `kickoff_spec_run.delay(...)` (Celery task) validates request options and ensures a `SpecAutomationRun` row
   exists.
2. **Workspace Prep** – `SpecWorkspaceManager` creates `/work/runs/{run_id}` directories (repo, home, artifacts) and registers
   clean-up callbacks respecting retention policy.
3. **Container Start** – `JobContainerManager.start()` launches the job container with collected secrets and agent env.
4. **Discovery Phase** – `_run_discover_phase()` reads `tasks.md`, marks items complete/incomplete, and determines next actions.
5. **Submission Phase** – `_run_submit_phase()` invokes the configured agent backend (Codex CLI by default) to generate plan and
   task updates, capturing stdout/stderr artifacts.
6. **Publish Phase** – `_run_publish_phase()` commits repo changes, pushes to `spec_automation/{run_id}` branch, and opens a draft
   PR via `GitHubClient`.
7. **Finalization** – Metrics, audit records, and artifacts are persisted; containers and workspace directories are removed unless
   retention is requested.

Failures at any step mark the run with terminal status and stop downstream phases. Cleanup routines still execute and emit their
own metrics/log entries.

---

## 4. Container Images & Compose Topology

| Component | Path | Notes |
|-----------|------|-------|
| Worker image | `images/worker/Dockerfile` | Python 3.11, Celery app, Docker SDK, no heavy toolchain. |
| Job image | `images/job/Dockerfile` | Ubuntu 22.04 base, Git/GitHub CLI, Codex CLI, Spec Kit prompts, jq/curl. |
| Compose services | `docker-compose.yaml` | Declares `celery-worker`, `api`, RabbitMQ broker, and shared `speckit_workspaces` volume. |

`docker compose up rabbitmq celery-worker api` is sufficient for local validation when secrets are supplied via environment
variables.

---

## 5. Workspace Layout & Retention

```
/work
└── runs/
    └── <run_id>/
        ├── repo/        # Cloned repository
        ├── home/        # HOME for Codex CLI & gh
        └── artifacts/   # Structured logs, diffs, credential audit reports
```

* `SpecWorkspaceManager.ensure_workspace()` creates directories with `0o750` permissions and records cleanup metadata.
* `cleanup_expired_workspaces()` prunes runs older than the configured TTL (default 7 days) unless marked for retention.
* Artifact files are linked to database rows so operators can retrieve them via API or direct filesystem access.

---

## 6. Credentials & Security

* `JobContainer` collects secrets from:
  * Process environment (`GITHUB_TOKEN`, `CODEX_API_KEY`, `SPEC_AUTOMATION_SECRET_*`, etc.).
  * `SpecWorkflowSettings` overrides (GitHub App tokens, agent runtime overrides).
  * Per-run options (`runtime_environment`) supplied with kickoff requests.
* `_redact_environment()` replaces sensitive values with `***REDACTED***` before logging.
* Git commits use `Spec Kit Bot` identity by default; overrides come from environment variables or request payloads.
* Docker socket access is limited to the Celery worker container. Production deploys SHOULD run the worker on hardened hosts or via
  rootless Docker to reduce blast radius.
* Cleanup steps (`JobContainerManager.stop()`, `SpecWorkspaceManager.cleanup_workspace()`) execute in `finally` blocks to prevent
  residual containers or volumes even on error paths.

---

## 7. Observability & Artifacts

### 7.1 Metrics

* Controlled by `SPEC_WORKFLOW_METRICS_ENABLED` (boolean) with optional `SPEC_WORKFLOW_METRICS_HOST`, `SPEC_WORKFLOW_METRICS_PORT`,
  and `SPEC_WORKFLOW_METRICS_NAMESPACE`.
* Metrics include `run.start`, `run.finish`, `phase.duration`, `phase.retry`, and `cleanup.duration` counters/timers tagged with
  `{run_id, phase, repo, result}`.
* When StatsD endpoints are unreachable, `_MetricsEmitter` backs off exponentially and logs warnings without failing the run.

### 7.2 Logging

* Structured logs use `_sanitize_for_log()` to avoid leaking secrets while preserving context such as `branch_name` or
  `container_id`.
* Each phase attaches stdout/stderr to artifacts; Celery logs reference artifact IDs for quick retrieval.

### 7.3 Artifacts

* Stored under `/work/runs/{run_id}/artifacts/`:
  * `discover-next-phase.log`, `submit-codex-job.log`, `apply-and-publish.log` (stdout/stderr pairs).
  * `diff-summary.txt` summarizing git changes (`git diff --stat`).
  * `credential-audit.json` recording injected environment keys (values redacted).
  * `commit_status.env` flagging whether changes were pushed.

Operators can fetch artifacts via the API router or by inspecting the mounted volume inside the worker container.

---

## 8. Operational Runbook

1. **Provision Secrets** – Export `GITHUB_TOKEN`, `CODEX_API_KEY`, and optional agent overrides before starting the worker.
2. **Start Services** – `docker compose up rabbitmq celery-worker api` (see Quickstart for additional options).
3. **Dispatch Runs** – Use `kickoff_spec_run.delay(...)` or the REST endpoint defined in the contracts package.
4. **Monitor Execution** –
   * Celery logs show phase transitions and container IDs.
   * StatsD metrics feed dashboards when enabled.
   * `/api/spec-automation/runs/{run_id}` exposes status, timestamps, agent metadata, and artifact references.
5. **Review Results** – Pull artifacts or inspect the GitHub draft PR branch (`spec_automation/{run_id_short}` by default).
6. **Handle Failures** –
   * Container start errors usually indicate missing `SPEC_AUTOMATION_JOB_IMAGE` or Docker socket issues.
   * Git/GitHub failures emit retry metrics and log the last stderr payload; inspect `apply-and-publish.log`.
   * Credential validation failures raise `CredentialValidationError` with audit artifacts for debugging.
7. **Cleanup** – `docker compose down` stops services; remove stale workspaces with `docker volume rm speckit_workspaces` when no
   runs require retention.

---

## 9. Testing & Validation

* **Unit tests** – `tests/unit/workflows/test_spec_automation_env.py` validates cleanup and agent selection controls; API schemas are
  covered by `tests/unit/api/test_spec_automation.py`.
* **Integration tests** – `tests/integration/workflows/test_spec_automation_pipeline.py` exercises a full happy-path run with a stub
  agent.
* **Manual smoke** – Trigger a dry-run (`options={"dry_run": True}`) to verify workspace provisioning, artifact creation, and PR skip
  logic without pushing commits.

---

## 10. Reference Environment Variables

| Variable | Purpose |
|----------|---------|
| `SPEC_AUTOMATION_JOB_IMAGE` | Docker image for job container executions. |
| `SPEC_AUTOMATION_WORKSPACE_ROOT` | Host path for the shared workspace mount (default `/work`). |
| `SPEC_AUTOMATION_AGENT_BACKEND` | Selected agent adapter (`codex_cli`, others allowed via `SPEC_AUTOMATION_ALLOWED_AGENT_BACKENDS`). |
| `SPEC_WORKFLOW_METRICS_*` | Enable and configure StatsD emission. |
| `SPEC_WORKFLOW_GITHUB_TOKEN` | Optional override for GitHub auth if not provided via environment. |
| `CODEX_API_KEY` | Credential for Codex CLI agent executions. |
| `SPEC_WORKFLOW_TEST_MODE` | When `true`, skips git push/PR creation but still writes artifacts for validation. |

These values complement the defaults defined in `moonmind/config/settings.py` and can be supplied through `.env`, compose
environment blocks, or deployment secrets managers.

---

## 11. Rollout Considerations

1. Begin with dry-run mode to validate repository coverage and artifact quality.
2. Gradually enable real pushes for allow-listed repositories; GitHub draft PRs are labeled `spec-kit` and `automation`.
3. Monitor StatsD metrics and API run reports for error trends before scaling worker concurrency beyond the default.
4. Capture job container images in artifact registries with semantic tags to coordinate upgrades with prompt pack releases.

---

## 12. Reference Snippets

### 12.1 Starting a Job Container

```python
from moonmind.workflows.speckit_celery.job_container import JobContainerManager

manager = JobContainerManager()
container = manager.start(run_id, environment=env)

try:
    result = container.exec(["git", "status"], cwd="/work/runs/{run_id}/repo")
finally:
    manager.stop(container)
```

### 12.2 Branch & PR Policy

* Branch: `spec_automation/{YYYYMMDD}/{run_id_short}` (configurable via request options).
* Commit message: `chore(spec-kit): apply specify/plan/tasks`.
* Draft PR title: `Spec Kit Automation – {repo} – {run_id_short}` with labels `spec-kit`, `automation`.

These conventions ensure downstream analytics can group runs by feature key and run identifier.
