## DOOD Plan: Generic Container Runner for MoonMind Agent Workers (Desired State)

Status: **Proposed**
Owners: **MoonMind Eng**
Last Updated: **2026-02-17**

---

### 1) Purpose

MoonMind agent workers (starting with `codex-worker`) must remain lightweight and must not embed heavyweight toolchains (Unreal, Unity, .NET SDK variants, etc.). When a task needs project-specific build/test tooling, the worker launches an ephemeral job container on the host Docker Engine (DooD), mounts the task workspace, executes the requested command, and publishes artifacts through the existing queue artifact pipeline.

---

### 2) Decision

All external build/test execution runs in ephemeral containers launched by the worker via `docker-proxy` (`DOCKER_HOST=tcp://docker-proxy:2375`) instead of mounting the raw Docker socket into the worker.

This keeps workers generic and lets each repository/task choose different runner images and commands without adding toolchain-specific logic to workers.

---

### 3) Current Repo Facts This Plan Builds On

* The runtime image already includes Docker CLI + docker compose plugin, so workers can invoke `docker run`.
* `docker-compose.yaml` already defines `docker-proxy` (tecnativa/docker-socket-proxy).
* The worker runtime already creates per-job directories under `MOONMIND_WORKDIR` with `repo/` and `artifacts/`.
* Canonical task payloads already allow additive fields under `task` (`extra="allow"`), so `task.container` can be added without breaking existing task shapes.

---

### 4) Architecture (Steady State)

#### Components

1. **Agent Worker Container (`codex-worker`)**
   * Runs task lifecycle (prepare -> execute -> publish) and stays lightweight.
   * Uses Docker CLI against `docker-proxy`.
   * Treats runner images/commands as task data, not hardcoded engine logic.

2. **Docker Proxy (`docker-proxy`)**
   * Proxies host Docker daemon APIs needed for container execution.

3. **Runner Images (Repo/Task-specific)**
   * Any pullable image may be used per task/repository (Unity, .NET, Unreal, custom CI tool images).
   * Runner executes commands against mounted workspace.

4. **Shared Workspace Volume**
   * Worker and runner mount the same workspace location so runner sees checked-out repo and writes artifacts directly.

---

### 5) Workspace & Volume Contract

#### Workspace root

* `MOONMIND_WORKDIR=/work/agent_jobs` (recommended inside named volume).
* Worker per-job structure:
  * `job_root = /work/agent_jobs/<job_id>`
  * `repo_dir = <job_root>/repo`
  * `artifacts_dir = <job_root>/artifacts`

#### Required shared volume

* `agent_workspaces` mounted at `/work/agent_jobs` in both worker and runner.

#### Optional cache volumes

* Additional named volumes can be mounted when requested by task input (for engine/package caches).

---

### 6) Task Payload Contract (`task.container`)

`task.container` is an optional additive object. If absent, task execution follows existing local-worker behavior.

Required fields when enabled:

* `enabled: true`
* `image: "<registry/image:tag or @digest>"`
* `command: ["executable", "arg1", "..."]` (arbitrary command; list form required)

Recommended fields:

* `workdir: "/workspace/repo"` (default)
* `env: { "KEY": "VALUE" }`
* `artifactsSubdir: "container"` (default)
* `timeoutSeconds: 3600` (default)
* `resources: { cpus: 8, memory: "16g" }` (optional)
* `pull: "if-missing" | "always"` (default `if-missing`)
* `cacheVolumes: [{ "name": "nuget_cache", "target": "/home/app/.nuget/packages" }]` (optional)

Capability routing requirement:

* Tasks using `task.container.enabled=true` must include `docker` in `requiredCapabilities`.
* Workers that can run containerized tasks advertise `docker` in `MOONMIND_WORKER_CAPABILITIES`.

---

### 7) Runner Invocation Contract (Standard Wrapper, Arbitrary Command)

Worker launches one ephemeral container per job with:

* Deterministic name: `mm-task-<job_id>`
* Labels: `moonmind.job_id`, `moonmind.repository`, `moonmind.runtime=container`
* Workspace mount: `agent_workspaces` -> `/work/agent_jobs`
* Default working directory inside container: `/work/agent_jobs/<job_id>/repo` (or `task.container.workdir`)
* Environment includes task-provided variables plus job metadata (`JOB_ID`, `REPOSITORY`, `ARTIFACT_DIR`)
* Image and command come directly from task payload

Example shape:

```bash
docker run --rm \
  --name "mm-task-${JOB_ID}" \
  --label "moonmind.job_id=${JOB_ID}" \
  --label "moonmind.repository=${REPOSITORY}" \
  --mount type=volume,src=agent_workspaces,dst=/work/agent_jobs \
  --workdir "/work/agent_jobs/${JOB_ID}/repo" \
  -e JOB_ID="${JOB_ID}" \
  -e REPOSITORY="${REPOSITORY}" \
  -e ARTIFACT_DIR="/work/agent_jobs/${JOB_ID}/artifacts/container" \
  -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  bash -lc "dotnet test ./MySolution.sln --logger junit"
```

Exit code contract:

* Exit code `0` = success.
* Non-zero = failure; worker still uploads produced logs/artifacts.

---

### 8) Repo-by-Repo Switching

The worker does not need prior knowledge of Unreal/Unity/.NET-specific images.

Switching is data-driven:

* Different repositories can submit different `task.container.image` and `task.container.command`.
* Different tasks in the same repository can use different images/commands.
* Optional profile indirection can be added later, but direct task-level image/command is sufficient for initial implementation.

Illustrative examples:

* Unity task: `image=unityci/editor:ubuntu-2022.3.20f1-linux-il2cpp-3`, command runs Unity batchmode tests.
* .NET task: `image=mcr.microsoft.com/dotnet/sdk:8.0`, command runs `dotnet restore/build/test`.
* Unreal task: `image=ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3`, command runs project build/test script.

---

### 9) Artifact Contract

Runner writes only inside shared job workspace so worker can upload via queue artifact endpoint.

Under:

* `${job_root}/artifacts/<artifactsSubdir>`

Must produce (at minimum):

* `logs/runner.log` (combined stdout/stderr or worker-captured output)
* `metadata/run.json` (job id, image, command, timing, exit code)

Optional:

* `test-results/*.xml`
* `packages/`
* `coverage/`
* Tool-specific outputs

---

### 10) Observability Contract

Worker emits events around container execution:

* `moonmind.task.container.started` (image, command summary)
* `moonmind.task.container.finished` (exitCode, duration, artifact summary)

Container labels include `moonmind.job_id` to support operator debugging via `docker ps` and `docker logs`.

---

### 11) Resource, Concurrency, and Timeouts

* Each worker processes one job at a time (current behavior).
* Runner resource limits come from `task.container.resources` when provided.
* Worker enforces `timeoutSeconds`; on timeout, worker stops the container and marks job failed.

---

### 12) Desired `docker-compose.yaml` Shape (Target)

#### Keep (already present)

* `docker-proxy` service with the required Docker API surface.

#### Update `codex-worker`

* Add `DOCKER_HOST=${ORCHESTRATOR_DOCKER_HOST:-tcp://docker-proxy:2375}`
* Add `depends_on: docker-proxy`
* Use shared workspace named volume and set `MOONMIND_WORKDIR=/work/agent_jobs`
* Include `docker` in `MOONMIND_WORKER_CAPABILITIES`

#### Add/retain volumes

* `agent_workspaces` (required)
* Additional named cache volumes as needed by runner images

---

### 13) Non-Goal for This Revision

Policy controls (image allowlists, registry restrictions, signature enforcement) are explicitly out of scope for this revision and can be layered on in a follow-up design.

---

If you share your first set of target runner images (Unity/.NET/Unreal), we can add concrete payload examples and a minimal migration checklist for worker implementation.
