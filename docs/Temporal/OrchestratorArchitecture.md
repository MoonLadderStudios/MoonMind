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
     * The **Docker API proxy** (`tcp://docker-proxy:2375`) for “docker-outside-of-docker” (DooD).
   * Contains:

     * Docker CLI + `docker compose` plugin.
     * Python runtime providing a **Temporal Worker**.
     * Code (Temporal Activities) to:

       * Analyze logs / errors.
       * Generate patches (e.g., Dockerfile or dependency files).
       * Run `docker compose build` / `up`.
       * Verify health and handle rollback.

2. **Host Docker Engine (via Proxy)**

   * Runs all containers.
   * The orchestrator uses the Docker API proxy to:

     * Build images (`docker compose build <service>`).
     * Restart services (`docker compose up -d --no-deps <service>`).
     * Inspect containers/logs if needed.

3. **MoonMind Services**

   * Existing services defined in `docker-compose.yml` (API, temporal workers, DB, etc.).
   * Use images built by the orchestrator.
   * May expose `/health` endpoints or equivalent to support automated verification.

4. **Optional Infrastructure**

   * **Reverse proxy** (Traefik/Nginx) for stable external endpoints and future blue/green tricks.
   * **Metrics/Logging** stack (Prometheus/Grafana, ELK/EFK) for error rate monitoring.

---

## 3. Key Design Decisions

### 3.1 Docker-outside-of-Docker vs. DinD

* **Chosen:** Docker-outside-of-Docker (DooD) via `docker-proxy`.

  * Orchestrator connects to `tcp://docker-proxy:2375` to speak to the host Docker daemon.
  * Pros:

    * No separate Docker daemon; reuses existing host daemon.
    * “Sibling” containers behave like manually launched ones.
  * Cons:

    * Orchestrator effectively has powerful access to the host (mitigated by exposing only necessary Docker API endpoints through the proxy).

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
    environment:
      DOCKER_HOST: tcp://docker-proxy:2375
      TEMPORAL_HOST: temporal:7233
      # Secrets/tokens via env or Docker secrets
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    depends_on:
      - docker-proxy
      - temporal
```

### 4.2 Orchestrator Image

* Base: `python:3.11-slim` + Docker CLI + Compose plugin copied from `docker:<version>-cli`.
* Python packages:
  * `temporalio` (for Temporal workflows and activities).
  * `pydantic` (typed config/plan models).
  * `requests` (health checks, HTTP).
* Entrypoint:
  * A Temporal worker that consumes “plan execution” Workflows from the Temporal server.

---

## 5. Execution Flow: “Fix missing dependency in service X”

### 5.1 Trigger

Source of trigger can be:

* User instruction in MoonMind: “Fix the missing dependency for `api` service.”
* Automated pipeline: CI logs show recurrent build failure for `api`.
* Observability trigger: logs show runtime `ModuleNotFoundError` or missing header errors.

### 5.2 Plan

1. **Error Analysis**

   * Temporal Workflow / LLM Agent assesses build/runtime logs.
   * Identifies likely cause (missing apt package, missing Python or Node package).

2. **Plan Construction**

   * Build a small, typed **ActionPlan** object:

     * Steps: `clone` (optional), `patch`, `build`, `restart`, `verify`, `rollback?`.
     * Metadata: target service (`api`), health URL, etc.

### 5.3 Patch

* Temporal Activity modifies the correct files under `/workspace`:

  **Examples:**

  * Missing system package:

    * Insert `RUN apt-get update && apt-get install -y libfoo-dev && rm -rf /var/lib/apt/lists/*` into `services/api/Dockerfile`, just before the first `COPY/ADD`.
  * Missing Python package:

    * Append `some_pkg==X.Y` to `services/api/requirements.txt` (if Dockerfile already installs from it).

* Optionally:

  * `git commit` the changes to a new branch, push to remote, and/or open a PR (GitHub API integration).

### 5.4 Build

* Orchestrator Activity runs from `/workspace`:

```bash
docker compose --project-name moonmind build api
```

* Uses the host Docker daemon via the proxy.
* Logs build output and stores it in artifacts.

### 5.5 Relaunch

* If build succeeds:

```bash
docker compose --project-name moonmind up -d --no-deps api
```

* This restarts **only** the affected service with the new image.

### 5.6 Verification

* Perform multi-step health checking (as Temporal Activities):

  1. **Container-level**: check container is running.
  2. **HTTP health**:

     * Hit `http://api:8000/health` (or whatever your API health endpoint is) with Activity retry policies (exponential backoff).
  3. **Optional application checks**:

     * Issue a simple application request and verify a known-good response.

* If verification fails, proceed to rollback.

### 5.7 Rollback

* At least one rollback strategy executes if Verification fails:

  1. **Git rollback**:

     * `git checkout -- services/api/Dockerfile services/api/requirements.txt` (revert patch locally).
  2. **Rebuild + restart**:

     * `docker compose build api`
     * `docker compose up -d --no-deps api`
  3. **Image rollback (if using tagged images)**:

     * Tag previous image as “current” and restart using that tag.

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

* Require explicit approval for changes to production services.
* A Temporal Workflow can use native Temporal Signals to pause execution and wait for a human operator to click "Approve" in the UI before proceeding with the `build` or `relaunch` activities.

### 6.3 Credentials & Secrets

* Git and container registry credentials are injected into the orchestrator worker securely.
* The orchestrator worker must never log sensitive tokens.

---

## 7. Observability & Artifacts

* **Logs**:
  * Per-run logs: patch diff, build logs, compose up output, health-check logs tracked securely via Temporal workflow history or pushed to external blob storage.
* **Metrics**:
  * Track Temporal Workflow execution stats: success/failure rates, MTTR.

### 7.1 Operational Runbook

The orchestrator worker leverages Temporal so SREs can reason about failures with standard Temporal UI observability.

1. **Start the stack** – `docker compose up -d db temporal api temporal-worker-sandbox mm-orchestrator`.
2. **Submit a run** – Trigger the Orchestrator Workflow via the API with `instruction` + `target_service`.
3. **Watch progress** – Observe the Workflow Execution in the Temporal UI to see `analyze → patch → build → restart → verify` Activities.
4. **Retrieve artifacts** – Download outputs from the workflow's artifact staging directory.

### 7.2 Approval Flow

Protected services invoke a human-in-the-loop wait state in the Workflow:
1. The Workflow prepares the ActionPlan and signals the UI that it is `awaiting_approval`.
2. Operators inspect the pending plan in Mission Control.
3. Operators **Approve** by sending an approval Signal back to the Temporal Workflow.
4. If verification fails, the Workflow automatically executes rollback Activities. Operators can start a new Workflow to retry fixes.

---

## 8. Limitations & Future Extensions

### 8.1 Limitations

* Orchestrator access to `docker-proxy` effectively grants powerful access to the host. Requires strict networking and trusted worker boundaries.
* Compose has no native rolling update semantics; more advanced rollout strategies require proxy tricks or multiple services.

### 8.2 Future Extensions

* **Blue/Green / Canary** in Compose: (Shift traffic gradually using Traefik).
* **Move to Kubernetes** (optional future path):
  * Replace `docker compose` operations with K8s Deployment updates.
  * Swap DooD for in-cluster builders (BuildKit/Kaniko).
