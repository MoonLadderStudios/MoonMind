## DOOD Plan: Generic Container Runner for Temporal Managed Agents (Desired State)

Status: **Proposed**
Owners: **MoonMind Eng**
Last Updated: **2026-03-14**

---

### 1) Purpose

MoonMind's `temporal-worker-sandbox` and related managed agent runtimes must remain lightweight and must not embed heavyweight toolchains (Unreal, Unity, .NET SDK variants, etc.). When a Temporal workflow or an agent skill needs project-specific build/test tooling, the sandbox worker launches an ephemeral job container on the host Docker Engine (DooD) via a secure local proxy, mounts the agent workspace, executes the requested command, and returns the result (or artifacts) back to the Temporal execution context.

---

### 2) Decision

All external build/test execution initiated by Managed Agents runs in ephemeral containers launched via the `docker-proxy` (`DOCKER_HOST=tcp://docker-proxy:2375`) instead of mounting the raw Docker socket into the sandbox.

This keeps the underlying Temporal workers generic and lets each repository/workflow choose different runner images and commands without adding toolchain-specific logic to the worker Dockerfile.

---

### 3) Current Repo Facts This Plan Builds On

* The runtime image already includes Docker CLI + `docker-compose` plugins, so sandbox workers can invoke `docker run`.
* `docker-compose.yaml` already defines `docker-proxy` (tecnativa/docker-socket-proxy).
* The worker runtime creates job or run directories inside shared mounted volumes.
* Temporal Activities and OpenHands skills can execute arbitrary shell commands within the sandbox environment, leveraging these CLI tools.

---

### 4) Architecture (Steady State)

#### Components

1. **Temporal Sandbox Worker (`temporal-worker-sandbox`)**
   * Runs the Temporal AI agent loop and OpenHands logic.
   * Stays lightweight (e.g. built on Python/Node base).
   * Uses Docker CLI against `docker-proxy` when executing large build skills.
   * Treats runner images/commands as dynamic inputs, not hardcoded engine logic.

2. **Docker Proxy (`docker-proxy`)**
   * Proxies host Docker daemon APIs needed for container execution.
   * Exposes necessary Docker subset APIs without exposing destructive host daemon commands.

3. **Runner Images (Repo/Workflow-specific)**
   * Any pullable image may be used per repository (Unity, .NET, Unreal, custom CI tool images).
   * The container executes commands against the mounted workspace.

4. **Shared Workspace Volume**
   * The sandbox worker and the runner container mount the same workspace location (e.g. `agent_workspaces`) so the runner sees the checked-out repo and writes artifacts directly where the sandbox can read them.

---

### 5) Workspace & Volume Contract

#### Workspace root

* `MOONMIND_WORKDIR=/work/agent_jobs` (recommended inside named volume).
* Worker per-run structure:
  * `run_root = /work/agent_jobs/<workflow_id>`
  * `repo_dir = <run_root>/repo`
  * `artifacts_dir = <run_root>/artifacts`

#### Required shared volume

* `agent_workspaces` named volume mounted at `/work/agent_jobs` in both the sandbox worker and the target runner container.

---

### 6) Execution Contract (Arbitrary Command Wrapper)

The Managed Agent launches one ephemeral container per heavy build command with rules such as:

* Deterministic naming: `mm-build-<workflow_id>-<run_idx>`
* Labels: `moonmind.workflow_id`, `moonmind.repository`, `moonmind.runtime=container`
* Workspace mount: `agent_workspaces` -> `/work/agent_jobs`
* Default working directory inside container: `/work/agent_jobs/<workflow_id>/repo`
* Environment includes necessary API keys if explicitly delegated by the agent.

Example shape constructed by a Temporal Activity or OpenHands skill:

```bash
docker run --rm \
  --name "mm-build-${WORKFLOW_ID}" \
  --label "moonmind.workflow_id=${WORKFLOW_ID}" \
  --mount type=volume,src=agent_workspaces,dst=/work/agent_jobs \
  --workdir "/work/agent_jobs/${WORKFLOW_ID}/repo" \
  -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  bash -lc "dotnet test ./MySolution.sln --logger junit"
```

Exit code contract:

* Exit code `0` = success.
* Non-zero = failure; the Activity/Skill should capture the output stream and fail gracefully or return the error to the LLM agent for self-healing.

---

### 7) Repo-by-Repo Switching

The Temporal worker does not need prior knowledge of Unreal/Unity/.NET-specific images.

Switching is data-driven:

* The Agent identifies the required toolchain based on repository structure (e.g. noticing a `.uproject` file).
* It invokes the heavy-build DOOD capability, specifying the target image.

Illustrative examples:

* **Unity**: `image=unityci/editor:ubuntu-2022.3.20f1-linux-il2cpp-3`, command runs Unity batchmode tests.
* **.NET**: `image=mcr.microsoft.com/dotnet/sdk:8.0`, command runs `dotnet restore/build/test`.
* **Unreal**: `image=ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3`, command runs project build/test script.

---

### 8) Artifact Contract

The runner container writes only inside the shared job workspace so the sandbox worker can access the outputs after the container exits.

Under:

* `${run_root}/artifacts/<subdir>`

Must produce (at minimum):

* Standard output and standard error logs (often captured directly by the `docker run` execution wrapper in python/node).

Optional:

* `test-results/*.xml`
* Compiled binaries

---

### 9) Resource, Concurrency, and Timeouts

* Runner resource limits should be passed into `docker run` flags if necessary.
* Temporal Activities manage the wall-clock timeout. If the Activity times out, it should attempt to `docker kill mm-build-${WORKFLOW_ID}` to prevent orphaned engine builds running on the daemon.

---

### 10) Desired `docker-compose.yaml` Shape

#### Keep

* `docker-proxy` service with the required Docker API surface.

#### Update `temporal-worker-sandbox`

* Add `DOCKER_HOST=${SYSTEM_DOCKER_HOST:-tcp://docker-proxy:2375}`
* Add `depends_on: docker-proxy`
* Ensure `agent_workspaces` volume is accurately mounted.

---

### 11) Future Refinements

Policy controls (image allowlists, registry restrictions) can be layered into the `docker-proxy` configuration or enforced within the Temporal execution layer to restrict what images the agent is allowed to execute via DOOD.
