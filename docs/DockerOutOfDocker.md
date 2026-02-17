## DOOD Plan: UE5 Build/Test Runner for MoonMind Agent Workers (Desired State)

Status: **Proposed**
Owners: **MoonMind Eng**
Last Updated: **2026-02-16**

---

### 1) Purpose

MoonMind agent workers (starting with `codex-worker`) must remain lightweight and **must not** embed Unreal Engine. When a Task requires Unreal build + automation tests, the worker will **launch a separate heavy “Unreal Runner” container** on the host Docker Engine (DooD), run the build/test against the same workspace, and upload logs/artifacts back through the existing queue artifact pipeline.

---

### 2) Decision

**All UE build/test execution runs in an ephemeral Unreal Runner container launched by the worker via the existing `docker-proxy` service** (Docker socket proxy) rather than mounting the raw Docker socket into the worker.

This matches the repo’s existing direction of using `DOCKER_HOST=tcp://docker-proxy:2375` for container orchestration (already set for the orchestrator) and keeps Docker API access behind the limited `docker-proxy` surface .

---

### 3) Current Repo Facts This Plan Builds On

* The shared runtime image already includes **Docker CLI + docker compose plugin**, so workers can invoke `docker run` without adding new tooling .
* `docker-compose.yaml` already defines `docker-proxy` (tecnativa/docker-socket-proxy) with a minimal allowlist for containers/images/networks/volumes .
* The current `codex-worker` service uses `MOONMIND_WORKDIR` (default `/workspace/agent_jobs`) and advertises capabilities via `MOONMIND_WORKER_CAPABILITIES` (default `codex,git,gh`) .
* The worker runtime builds per-job directories as `workdir/<job_id>/...` with `repo/` and `artifacts/` under that job root , and `MOONMIND_WORKDIR` is the single root for those job folders .

---

### 4) Architecture (Steady State)

#### Components

1. **Agent Worker Container (`codex-worker`)**

   * Runs the Task lifecycle (prepare → execute → publish) and remains “light” (no UE).
   * Has Docker CLI available .
   * Uses `DOCKER_HOST=tcp://docker-proxy:2375` to launch the Unreal runner .

2. **Docker Proxy (`docker-proxy`)**

   * Proxies the host Docker daemon with endpoint allowlist (containers/images/networks/volumes) .

3. **Unreal Runner Image (Heavy)**

   * Contains Unreal Engine + toolchain.
   * Executes build + tests based on environment variables and writes outputs into the shared workspace.

4. **Shared Workspace Volume (Named Volume)**

   * A single named volume mounted into both worker and runner at the same mountpoint.
   * The worker’s `MOONMIND_WORKDIR` points inside this volume so the runner can access `job_root/repo` and `job_root/artifacts`.

5. **Shared Cache Volumes**

   * UE DDC and compiler cache volumes mounted into runner for speed.

---

### 5) Workspace & Volume Contract

#### Workspace root

* `MOONMIND_WORKDIR=/work/agent_jobs` (inside a named volume).
* Worker creates per-job structure:

  * `job_root = /work/agent_jobs/<job_id>`
  * `repo_dir = <job_root>/repo`
  * `artifacts_dir = <job_root>/artifacts`

#### Volumes (names are stable)

* `agent_workspaces` → mounted at `/work/agent_jobs` (worker) and `/work/agent_jobs` (runner).
* `ue_ddc_<UE_VERSION>` → mounted at runner DDC path.
* `ue_sccache_<UE_VERSION>` (or `ue_ccache_<UE_VERSION>`) → mounted at runner compiler cache path.

---

### 6) Task Payload Contract (How a Task Requests UE Build/Test)

MoonMind canonical Tasks already support:

* `requiredCapabilities` at the top level, which is normalized and merged into routing requirements .
* Extra fields inside `task` because the model is `extra="allow"` .

#### Task extension: `task.unreal` (new, additive)

`task.unreal` is an optional object (ignored by workers that don’t implement UE).

Required fields when enabled:

* `enabled: true`
* `uprojectPath: "<relative path within repo>"`

Recommended fields:

* `engineVersion: "5.3"` (or `"5.4"`, etc.)
* `runnerImage: "ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3"` (optional override)
* `buildConfig: "Development"` (default)
* `target: "Editor"` (default)
* `runTests: true|false`
* `testFilter: "Project"` (default)
* `timeoutSeconds: 3600` (default)
* `resources: { cpus: 12, memory: "48g" }` (optional)

#### Capability routing requirement

Any Task that sets `task.unreal.enabled=true` **must** include:

* `requiredCapabilities` includes `docker` and `ue5` (or `unreal`) in addition to the runtime/git capabilities already derived by the contract .

Workers that can run UE builds advertise:

* `MOONMIND_WORKER_CAPABILITIES` includes `docker,ue5` (alongside `codex,git,gh` as appropriate) .

---

### 7) Runner Invocation Contract (Single Standard `docker run`)

The worker launches a single ephemeral container per job with:

* A deterministic name: `mm-ue-<job_id>`
* Labels: `moonmind.job_id`, `moonmind.repository`, `moonmind.engine=unreal`
* Volume mounts:

  * `agent_workspaces` to `/work/agent_jobs`
  * cache volumes (DDC + compiler cache)
* Working directory: `/work/agent_jobs/<job_id>/repo`
* Environment: `JOB_ID`, `UPROJECT_PATH`, `ARTIFACT_DIR`, `BUILD_CONFIG`, `RUN_TESTS`, `TEST_FILTER`, etc.

Example contract (shape only; exact flags are stable in implementation):

```bash
docker run --rm \
  --name "mm-ue-${JOB_ID}" \
  --label "moonmind.job_id=${JOB_ID}" \
  --mount type=volume,src=agent_workspaces,dst=/work/agent_jobs \
  --mount type=volume,src=ue_ddc_5_3,dst=/home/ue/.cache/UnrealEngine/DerivedDataCache \
  --mount type=volume,src=ue_sccache_5_3,dst=/home/ue/.cache/sccache \
  --workdir "/work/agent_jobs/${JOB_ID}/repo" \
  -e JOB_ID="${JOB_ID}" \
  -e UPROJECT_PATH="/work/agent_jobs/${JOB_ID}/repo/${UPROJECT_RELATIVE_PATH}" \
  -e ARTIFACT_DIR="/work/agent_jobs/${JOB_ID}/artifacts/unreal" \
  -e BUILD_CONFIG="Development" \
  -e RUN_TESTS="1" \
  -e TEST_FILTER="Project" \
  ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3 \
  /bin/bash -lc "/opt/moonmind/bin/ue_build_and_test"
```

**Exit code contract**

* Runner exit code `0` = build/test success.
* Non-zero = failure; logs and reports must still be written to `${ARTIFACT_DIR}`.

---

### 8) Artifact Contract (What the Runner Must Produce)

The runner writes **only** inside the shared job workspace so the worker can upload artifacts through `/api/queue/jobs/{jobId}/artifacts/upload`.

Under:

* `${job_root}/artifacts/unreal/`

Must produce:

* `logs/runner.log` (combined stdout/stderr)
* `metadata/run.json` (job id, UE version, start/end, exit code, command summary)
* `test-results/`:

  * JUnit XML when tests are enabled (one or more `*.xml`)

Optional:

* `packages/` (packaged build outputs)
* `symbols/` (PDB/dSYM equivalents if applicable)

---

### 9) Observability Contract

The worker emits queue events around the runner:

* `moonmind.task.unreal.started` (includes engineVersion, runnerImage, uprojectPath)
* `moonmind.task.unreal.finished` (includes exitCode, duration, artifact summary)

The runner container is labeled with `moonmind.job_id` to support `docker ps` / debugging by operators.

---

### 10) Resource, Concurrency, and Timeouts

* Each `codex-worker` processes one job at a time; UE concurrency is controlled by the number of UE-capable workers deployed.
* Runner resource limits are enforced by `docker run` flags (cpus/memory) derived from `task.unreal.resources`.
* A hard timeout is enforced by the worker; on timeout, the worker stops the runner container and marks the job failed.

---

### 11) Security Guardrails

* Workers do **not** mount `/var/run/docker.sock`; they use `docker-proxy` .
* UE tasks must be routed only to workers explicitly advertising `docker` + `ue5` capability.
* The worker enforces an allowlist of runner images (by exact tag/prefix) and uses **named volumes only** for mounts (no host-path mounts, no `--privileged`).

---

### 12) Desired `docker-compose.yaml` Shape (Target)

#### Keep (already present)

* `docker-proxy` with minimal endpoint enablement .

#### Update `codex-worker`

* Add:

  * `DOCKER_HOST=${ORCHESTRATOR_DOCKER_HOST:-tcp://docker-proxy:2375}` (same pattern as orchestrator)
  * `depends_on: docker-proxy`
  * Replace bind-based job workspace (`/workspace/agent_jobs`) with a **named volume mount** and set `MOONMIND_WORKDIR=/work/agent_jobs` (today it defaults to `/workspace/agent_jobs`)
  * Extend `MOONMIND_WORKER_CAPABILITIES` to include `docker,ue5` for the UE-capable worker pool (today defaults to `codex,git,gh`)

#### Add volumes

* `agent_workspaces`
* `ue_ddc_5_3`, `ue_sccache_5_3` (and equivalents per engine version)

---

If you want, paste your preferred UE versions (e.g., 5.3 vs 5.4) and whether your build host is Linux-only, and I’ll produce the exact `docker-compose.yaml` delta (single canonical version) plus the runner env var table that the worker and runner will share.
