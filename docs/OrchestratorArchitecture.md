# Orchestrator Architecture

## 1. Purpose & Scope

The goal is to enable **MoonMind** to:

* Interpret high-level instructions (e.g., “add missing dependency and fix build for service X”).
* Safely **modify code and Dockerfiles** in the repository.
* **Rebuild and relaunch** one or more services in a `docker-compose` stack.
* Verify health and **rollback** if something goes wrong.

All of this runs **without Kubernetes**, using **Docker + docker-compose** on a single host (or a small set of hosts).

---

## 2. High-Level Architecture

### 2.1 Components

1. **Orchestrator Container (`mm-orchestrator`)**

   * Runs inside the same `docker-compose` project as the rest of MoonMind.
   * Mounts:

     * The **project repo** into `/workspace` (contains `docker-compose.yml`, services, Dockerfiles).
     * The **host Docker socket** (`/var/run/docker.sock`) for “docker-outside-of-docker”.
   * Contains:

     * Docker CLI + `docker compose` plugin.
     * Python runtime (Celery or equivalent task runner).
     * Code to:

       * Analyze logs / errors (optionally via LLM).
       * Generate patches (e.g., Dockerfile or dependency files).
       * Run `docker compose build` / `up`.
       * Verify health and handle rollback.

2. **Host Docker Engine**

   * Runs all containers, including the orchestrator.
   * The orchestrator uses the Docker socket to:

     * Build images (`docker compose build <service>`).
     * Restart services (`docker compose up -d --no-deps <service>`).
     * Inspect containers/logs if needed.

3. **MoonMind Services**

   * Existing services defined in `docker-compose.yml` (API, workers, DB, etc.).
   * Use images built by the orchestrator.
   * May expose `/health` endpoints or equivalent to support automated verification.

4. **Optional Infrastructure**

   * **Reverse proxy** (Traefik/Nginx) for stable external endpoints and future blue/green tricks.
   * **Metrics/Logging** stack (Prometheus/Grafana, ELK/EFK) for error rate monitoring.

---

## 3. Key Design Decisions

### 3.1 Docker-outside-of-Docker vs. DinD

* **Chosen:** Docker-outside-of-Docker (DooD).

  * Orchestrator mounts `/var/run/docker.sock` to speak directly to the host Docker daemon.
  * Pros:

    * No separate Docker daemon; reuses existing host daemon.
    * “Sibling” containers behave like manually launched ones.
  * Cons:

    * Orchestrator effectively has root-level power on the host (needs policy/safety measures).

### 3.2 Compose as the control plane

* Use `docker compose` as the primary control API for:

  * Building images for specific services.
  * Restarting services with `up -d --no-deps <service>`.
* Keeps behavior consistent with how you already manage MoonMind in dev/ops.

---

## 4. Orchestrator Service Specification

### 4.1 `docker-compose.yml` (conceptual)

```yaml
services:
  orchestrator:
    build:
      context: ./ops/orchestrator
    container_name: mm-orchestrator
    working_dir: /workspace
    volumes:
      - ./:/workspace                     # project repo (incl. docker-compose.yml)
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      DOCKER_HOST: unix:///var/run/docker.sock
      # Secrets/tokens via env or Docker secrets
      GITHUB_TOKEN: ${GITHUB_TOKEN}
      CODEX_TOKEN: ${CODEX_TOKEN}
    # optional: network_mode: "host" or shared network with api service
```

### 4.2 Orchestrator Image

* Base: `python:3.11-slim` + Docker CLI + Compose plugin copied from `docker:<version>-cli`.
* Python packages:

  * `pydantic` (typed config/plan models).
  * `requests` (health checks, HTTP).
  * `celery` (if you want asynchronous job orchestration).
* Entrypoint:

  * Either a long-running process (`orchestrator.server`) exposing an API.
  * Or a Celery worker that consumes “plan execution” jobs from a broker.

---

## 5. Execution Flow: “Fix missing dependency in service X”

### 5.1 Trigger

Source of trigger can be:

* User instruction in MoonMind: “Fix the missing dependency for `api` service.”
* Automated pipeline: CI logs show recurrent build failure for `api`.
* Observability trigger: logs show runtime `ModuleNotFoundError` or missing header errors.

### 5.2 Plan

1. **Error Analysis**

   * Orchestrator or LLM inspects build/runtime logs.
   * Identifies likely cause (missing apt package, missing Python or Node package).

2. **Plan Construction**

   * Build a small, typed **ActionPlan** object:

     * Steps: `clone` (optional), `patch`, `build`, `restart`, `verify`, `rollback?`.
     * Metadata: target service (`api`), health URL, etc.

### 5.3 Patch

* Modify the correct files under `/workspace`:

  **Examples:**

  * Missing system package:

    * Insert `RUN apt-get update && apt-get install -y libfoo-dev && rm -rf /var/lib/apt/lists/*` into `services/api/Dockerfile`, just before the first `COPY/ADD`.
  * Missing Python package:

    * Append `some_pkg==X.Y` to `services/api/requirements.txt` (if Dockerfile already installs from it).

* Optionally:

  * `git commit` the changes to a new branch, push to remote, and/or open a PR (GitHub API integration).

### 5.4 Build

* Orchestrator runs from `/workspace`:

```bash
docker compose --project-name moonmind build api
```

* Uses the host Docker daemon via the mounted socket.
* Logs build output and stores it in artifacts (e.g., `var/artifacts/builds/<run_id>.log`).

### 5.5 Relaunch

* If build succeeds:

```bash
docker compose --project-name moonmind up -d --no-deps api
```

* This restarts **only** the affected service with the new image.

### 5.6 Verification

* Perform multi-step health checking:

  1. **Container-level**: check container is running.
  2. **HTTP health**:

     * Hit `http://api:8000/health` (or whatever your API health endpoint is) with exponential backoff until success or timeout.
  3. **Optional application checks**:

     * Issue a simple application request and verify a known-good response.

* If verification fails, proceed to rollback.

### 5.7 Rollback

* At least one rollback strategy:

  1. **Git rollback**:

     * `git checkout -- services/api/Dockerfile services/api/requirements.txt` (revert patch locally).
  2. **Rebuild + restart**:

     * `docker compose build api`
     * `docker compose up -d --no-deps api`
  3. **Image rollback (if using tagged images)**:

     * Tag previous image as “current” and restart using that tag.

* Record failure reason, logs, and patch diff for later manual inspection.

---

## 6. Safety & Policy

### 6.1 File-level allow-lists

Restrict edits to:

* `Dockerfile`, `Dockerfile.*` in known service directories.
* Dependency files:

  * `requirements*.txt`, `pyproject.toml`, `poetry.lock`.
  * `package.json`, `package-lock.json`, `pnpm-lock.yaml`, etc.

Block modifications to:

* `docker-compose.yml` (or only allow when explicitly approved).
* Infrastructure / secrets files.
* Arbitrary code outside the target service directory unless policy-approved.

### 6.2 Approval gates

* Require explicit approval for:

  * Changes to production services.
  * Changes that affect cross-cutting concerns (reverse proxies, databases).
* Approvals could be:

  * CLI: a human runs `orchestrator approve <run_id>`.
  * API: an operator clicks “Approve” in a UI.

### 6.3 Credentials & Secrets

* Git and container registry credentials are injected as environment variables or secrets.
* Orchestrator must never log sensitive tokens.
* If Codex or any LLM is used, sanitize logs (no secrets).

---

## 7. Observability & Artifacts

* **Logs**:

  * Per-run logs: patch diff, build logs, compose up output, health-check logs.
* **Artifacts**:

  * Store patch as unified diff.
  * Store build logs and verification results for audit/debugging.
* **Metrics**:

  * Track:

    * Number of auto-fixes attempted.
    * Success/failure rates.
    * Mean time to repair (MTTR).
  * Optional integration with Prometheus/Grafana.

### 7.1 Operational Runbook

The orchestrator worker is intentionally simple so SREs can reason about failures with the same primitives they already use for
Compose-managed services.

1. **Start the stack** – `docker compose up -d rabbitmq api celery-worker orchestrator` (optionally add a StatsD sidecar).
2. **Submit a run** – POST `/orchestrator/runs` with `instruction` + `target_service`; confirm a new row in
   `orchestrator_runs` shows `queued` and the Celery worker logs the ActionPlan.
3. **Watch progress** – tail `celery-worker`/`orchestrator` logs for `analyze → patch → build → restart → verify`; poll
   `GET /orchestrator/runs/{run_id}` for step timestamps and artifact paths.
4. **Retrieve artifacts** – download `patch.diff`, `build.log`, `restart.log`, `verify.log`, and `rollback.log` from
   `GET /orchestrator/runs/{run_id}/artifacts`; files are stored on-disk under `var/artifacts/spec_workflows/<run_id>/` for easy
   rsync/scp.
5. **Clean up** – stop services with `docker compose down` or prune old artifact directories per retention policy.

### 7.2 Approval Flow

Protected services (see `service_profiles.py`) move runs into `awaiting_approval` after the plan is created and before any patch
is applied. Operators must:

1. **Fetch the pending run** – `GET /orchestrator/runs?status=awaiting_approval` to list queued requests with plan summaries and
   approver hints.
2. **Approve** – `POST /orchestrator/runs/{run_id}/approvals` with approver identity + optional token; the run resumes from the
   first actionable step and records the approver on the ActionPlan snapshot.
3. **Rollback on verify failure** – if verification fails, the run automatically executes rollback strategies from the stored
   plan and emits `rollback.log` in artifacts. Operators can retry with `POST /orchestrator/runs/{run_id}/retry` to re-use the
   captured plan/artifacts after addressing root causes.

### 7.3 StatsD Dashboards

The orchestrator emits lightweight StatsD metrics when `STATSD_HOST`/`STATSD_PORT` (or `ORCHESTRATOR_STATSD_*`) are set. Key
series include:

* `moonmind.orchestrator.runs.queued` / `.runs.status.<state>` – run lifecycle counters (e.g., `queued`, `running`,
  `succeeded`, `failed`, `awaiting_approval`).
* `moonmind.orchestrator.runs.duration.<state>` – completion timing per terminal state.
* `moonmind.orchestrator.steps.<step>.started` and `.steps.<step>.<outcome>` – per-step throughput and result breakdown
  (`succeeded`, `failed`, `skipped`).

Suggested dashboard cards:

* **Run funnel** – stacked counter of `runs.status.*` to spot approval backlogs or failure spikes.
* **Step latency** – timer percentiles for `steps.*.duration` (patch/build/verify) to highlight slow builds or flaky health
  checks.
* **Per-service health** – filter `runs.queued.service.<service>` to ensure the orchestrator is balancing work across services.
* **Alerting** – trigger alarms when `runs.status.failed` or `steps.verify.failed` increase rapidly, or when there is a sustained
  deficit between `runs.queued` and `runs.status.running` (stuck queue).

---

## 8. Limitations & Future Extensions

### 8.1 Limitations

* Orchestrator access to `/var/run/docker.sock` effectively grants **root-equivalent** powers on the host. Requires trust and physical/VM isolation.
* Compose has no native rolling update semantics; more advanced rollout strategies require proxy tricks or multiple services.

### 8.2 Future Extensions

* **Blue/Green / Canary** in Compose:

  * Run `api_v1` and `api_v2` behind Traefik and shift traffic gradually.
* **Watchtower / registry-based** upgrades:

  * Orchestrator builds & pushes images with new tags; a Watchtower container watches for new tags and restarts services accordingly.
* **More sophisticated planning**:

  * Integrate directly with MoonMind’s LLM planner:

    * Instruction → ActionPlan → orchestrator tasks.
* **Move to Kubernetes** (optional future path):

  * Replace `docker compose` operations with K8s Deployment updates.
  * Swap DooD for in-cluster builders (BuildKit/Kaniko) if you ever outgrow Compose.

---

