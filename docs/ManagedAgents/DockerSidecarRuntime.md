# Managed Agent Docker Sidecar Runtime

- **Status:** Desired state
- **Owners:** MoonMind Platform
- **Last updated:** 2026-05-16

**Related:**

- [`docs/ManagedAgents/ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/CodexManagedSessionPlane.md`](./CodexManagedSessionPlane.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Tasks/SkillAndPlanContracts.md`](../Tasks/SkillAndPlanContracts.md)

---

## 1. Purpose

This document defines how MoonMind gives **managed agent sessions** the ability to run ordinary container workloads — `docker run`, `docker build`, `docker compose run` — for tests, build toolchains, and short-lived application containers, **without** mounting the host Docker socket and **without** embedding a Docker daemon in the managed agent image.

The model is a **per-session Docker sidecar**: the managed session container holds a Docker CLI only, and a sibling container holds a private Docker daemon scoped to that session.

This is the **default execution path** for containerized work that originates from a managed agent session. The Temporal-launched Docker-out-of-Docker (DooD) workload plane defined in [`DockerOutOfDocker.md`](./DockerOutOfDocker.md) remains, but is now reserved for the narrow set of operations that must run from the control plane itself: MoonMind admin/update flows, helper or one-shot workloads with no managed session attached, and deliberately deployment-gated exceptional workloads.

### 1.1 Relationship to existing architecture

This document supersedes two specific clauses in the prior architecture for the case of normal task workloads originating from a managed agent session:

- `ManagedAgentArchitecture.md §5.5` ("No direct Docker control from the managed session by default") — replaced for the sidecar deployment shape. A managed session with the sidecar runtime enabled has a private `DOCKER_HOST` pointing at its own daemon. It never gets the host socket and never sees other sessions' containers.
- `DockerOutOfDocker.md §17.3` ("Important boundary") — replaced for ordinary repository test workloads. Control-plane-launched Docker workloads remain the model for MoonMind admin/update flows, helper or one-shot workloads with no managed session attached, and deliberately deployment-gated exceptional workloads; they are no longer the default model for repo tests.

All other invariants from those documents still apply: artifacts and bounded metadata remain authoritative, session identity and workload identity remain separable, deployment credentials never reach the session, and the host Docker socket is never exposed to a managed session.

The related architecture documents carry the same desired-state split: ordinary session-originated Docker work uses the sidecar runtime; control-plane DooD remains reserved for MoonMind admin/update, helper, and deliberately gated exceptional workloads.

---

## 2. Core decisions

1. **Managed agents run normal Docker commands.** No MoonMind-specific workload syntax is required for ordinary tests and builds.
2. **A private Docker daemon lives in a sibling sidecar container, not in the agent container.** No `dockerd`, `containerd`, or `runc` is installed in the managed agent image.
3. **The host Docker socket is never exposed to a managed agent or its sidecar.**
4. **The Docker sidecar image is prebuilt and generic.** It does not need the MoonMind codebase, MoonMind deployment credentials, or registry secrets.
5. **The workspace is mounted at the same absolute path in both containers.** Bind mounts of the form `-v "$PWD":/workspace` resolve identically on the agent CLI and the sidecar daemon.
6. **The Docker daemon scope is per session.** Containers, images, and graph storage do not bleed across sessions or across users.
7. **The control-plane DooD path is preserved for operations that must originate from the control plane**, including MoonMind admin/update flows, helper or one-shot workloads with no managed session attached, and deliberately deployment-gated exceptional workloads. It is not the default path for ordinary task workloads.
8. **The design has a clean Kubernetes mapping** as a future-state target (sidecar container in the same Pod, shared volumes for workspace and socket).

---

## 3. Scope and non-goals

### 3.1 In scope

- The container topology that lets a managed agent run ordinary Docker commands safely.
- The agent-image and sidecar-image contracts.
- The workspace, socket, and graph volume contracts.
- The runtime mode set (`docker-sidecar`, `docker-sidecar-rootless`, `no-docker`, future `kubernetes-job`).
- Per-session lifecycle, cleanup, readiness, and audit metadata.
- The boundary between managed-agent Docker use and MoonMind ops/admin Docker use.

### 3.2 Out of scope

- Kubernetes orchestration of MoonMind itself.
- A general arbitrary-shell execution surface from the control plane.
- A generic container marketplace or cross-session container reuse.
- The detailed Codex / Claude / Gemini managed session protocols (owned by the runtime-specific docs).
- The provider-profile, secrets, and OAuth subsystems.

---

## 4. Terminology

- **Managed agent session container** (or "agent container"): the task-scoped runtime container that holds the agent runtime — Codex CLI, Claude Code, Gemini CLI, or equivalent — and the workspace bind. In this document this is the container that runs `docker version`, `docker run`, and so on.
- **Docker sidecar container**: a sibling container in the same managed session that runs `dockerd` (classic or rootless) and exposes the daemon socket on a shared volume.
- **Workspace volume**: the shared volume mounted at the same path into both containers so that `-v "$PWD":/workspace` bind mounts work transparently.
- **Docker socket volume**: the shared volume that carries the Unix socket exposed by `dockerd` and consumed by the Docker CLI in the agent container.
- **Docker graph volume**: the volume that backs `/var/lib/docker` (or the rootless equivalent) for the per-session daemon.
- **Session runtime profile**: the declarative description of agent container + sidecar shape applied at session launch.
- **MoonMind ops runtime**: the separate, narrowly-scoped Docker access path used by MoonMind itself for deploy/restart/rollback. Defined in §21 and elaborated in [`DockerOutOfDocker.md`](./DockerOutOfDocker.md).

---

## 5. Runtime model

A managed session with Docker capability is two containers and a small set of shared volumes.

```
Managed session
├── agent container
│   ├── agent runtime (Codex / Claude / Gemini / shell)
│   ├── git, repo tools
│   ├── Docker CLI (+ Compose plugin, optional)
│   ├── DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock
│   └── workspace mounted at /workspace
│
├── docker sidecar container
│   ├── dockerd (classic or rootless)
│   ├── no MoonMind codebase
│   ├── no MoonMind deployment credentials
│   ├── workspace mounted at the same /workspace
│   ├── /var/run/moonmind-docker (socket volume)
│   └── /var/lib/docker (graph volume, session-scoped)
│
└── shared volumes
    ├── workspace        (RW, both containers, same mount path)
    ├── docker-socket    (RW, both containers)
    ├── docker-graph     (RW, sidecar only)
    └── optional caches  (RW, both containers, deployment-approved)
```

### 5.1 Workspace path invariant

The single most important property of the topology:

> The workspace volume must be mounted at the **same absolute path** in the agent container and in the Docker sidecar.

Docker daemon bind mounts are resolved by the daemon, not by the CLI. When the agent runs:

```bash
docker run --rm -v "$PWD":/workspace -w /workspace alpine ls
```

the daemon receives `$PWD` as a literal host path and resolves it on its own filesystem. Unless the sidecar sees the same path that the agent sees, the bind mount silently points at the wrong directory or fails.

If the agent uses `/workspace` and the sidecar uses `/mnt/workspace`, normal Docker testing is broken. Session launch must reject this configuration (see §23).

### 5.2 MoonMind workspace convention

MoonMind workspaces follow the canonical layout from `DockerOutOfDocker.md §10`:

- `agent_workspaces` is the named volume.
- `/work/agent_jobs/<task_run_id>` is the run root.
- `/work/agent_jobs/<task_run_id>/repo` is the checked-out repository.
- `/work/agent_jobs/<task_run_id>/artifacts/<step_id>` is the durable artifact area.

The sidecar runtime keeps this convention. The "workspace" mount in this document is `agent_workspaces` mounted at `/work/agent_jobs` in both containers; `/workspace` is shown in examples as a synonym for the per-run repo path made visible to the agent via `MOONMIND_REPO_DIR`. Deployments may choose to expose either the run root or the repo path as the agent's working directory; the invariant in §5.1 applies to whatever path is actually shared.

---

## 6. Session runtime profile

A session runtime profile describes the agent + sidecar shape declaratively. The session launcher materializes this profile differently for Docker deployments today and Kubernetes deployments later.

The YAML below is **illustrative**: the canonical configuration surface is the existing MoonMind launcher inputs and provider/runtime profile records. It is shown here to make the contract concrete.

```yaml
kind: ManagedAgentRuntimeProfile
name: default-docker-sidecar
spec:
  workloadMode: docker-sidecar

  workspace:
    volume: agent_workspaces
    mountPath: /work/agent_jobs
    repoEnv: MOONMIND_REPO_DIR
    lifecycle: session

  agent:
    image: moonmind/managed-agent:<pinned>
    dockerClient:
      enabled: true
      composePlugin: true
      daemonInAgent: false
    env:
      DOCKER_HOST: unix:///var/run/moonmind-docker/docker.sock
    mounts:
      - { name: workspace,     mountPath: /work/agent_jobs }
      - { name: docker-socket, mountPath: /var/run/moonmind-docker }

  dockerSidecar:
    enabled: true
    mode: dind                      # or dind-rootless
    image: docker:27-dind            # pinned, not :latest
    socket:
      path: /var/run/moonmind-docker/docker.sock
      volumeName: docker-socket
    storage:
      volumeName: docker-graph
      mountPath: /var/lib/docker
      lifecycle: session
    security:
      privileged: true              # required for classic dind
      hostDockerSocket: forbidden
      moonmindDeploymentSecrets: forbidden
    mounts:
      - { name: workspace,     mountPath: /work/agent_jobs }
      - { name: docker-socket, mountPath: /var/run/moonmind-docker }
      - { name: docker-graph,  mountPath: /var/lib/docker }

  resources:
    agent:         { cpu: "2", memory: 4Gi }
    dockerSidecar: { cpu: "4", memory: 8Gi, ephemeralStorage: 40Gi }

  labels:
    moonmind.kind: managed-session
    moonmind.workload_mode: docker-sidecar

  readiness:
    docker:
      required: true
      timeoutSeconds: 60
      intervalSeconds: 2
      probes: [ "docker version", "docker info" ]

  policy:
    hostDockerSocket: forbidden
    sharedDaemonAcrossUsers: forbidden
    moonmindDeploymentSecretsInSession: forbidden
    appContainerControlFromSession: forbidden
    apiContainerWorkloadDockerSocketAccess: false
    kubernetesJobRuntimeSupported: false
```

---

## 7. Runtime modes

A session profile declares one of the following modes:

| Mode | Meaning | When to use |
|---|---|---|
| `docker-sidecar` | Classic DinD sidecar, privileged. | Default for trusted Docker deployments. |
| `docker-sidecar-rootless` | Rootless dockerd sidecar, no `--privileged`. | Hardened deployments where the rootless feature set is sufficient. |
| `no-docker` | No Docker capability in the session. | Lightweight sessions that never need containers. |
| `kubernetes-job` (future) | Per-workload Kubernetes Job instead of nested Docker. | Clusters that disallow DinD; future portability path. |

Recommended defaults:

- Current Docker deployments: `docker-sidecar`.
- Future hardened Docker deployments: `docker-sidecar-rootless`.
- Locked-down Kubernetes clusters: `kubernetes-job` (see §13).

The mode is a deployment / profile decision. Task instructions cannot raise it.
The durable profile contract includes the future `kubernetes-job` mode so the
workspace, labels, capability, and resource semantics remain portable, but that
mode fails closed unless the deployment profile explicitly sets
`policy.kubernetesJobRuntimeSupported: true`.

Docker deployments may set `MOONMIND_MANAGED_SESSION_DOCKER_MODE` to
`docker-sidecar` or `no-docker` at the worker launcher boundary. When the
variable is unset, the managed session launcher uses the workflow-provided
Docker capability mode for that launch request and does not infer capability
from unrelated ambient task text. The `docker-sidecar-rootless` mode remains a
profile contract target, but Docker launcher materialization fails closed until
that runtime shape is implemented.

The Docker sidecar image defaults to the pinned generic image `docker:27-dind`.
Operators may override it with `MOONMIND_MANAGED_SESSION_DOCKER_SIDECAR_IMAGE`,
but the value must remain pinned to a non-`latest` tag or digest.

---

## 8. Agent image contract

The managed agent image stays lightweight.

**Must include:**

- shell (bash), git, curl/wget, ca-certificates
- Docker CLI
- Docker Compose plugin (when sessions are expected to use `docker compose`)
- the agent runtime tooling (Codex CLI, Claude Code, Gemini CLI, or equivalent)

**Must not include:**

- `dockerd`, `containerd`, `runc`
- Docker graph storage
- a mounted host Docker socket
- MoonMind deployment credentials
- registry push credentials beyond what an individual task is authorized to use

Smoke test inside an agent container with sidecar enabled:

```bash
docker version
docker run --rm alpine echo hello
docker build -t local-test-image .
docker compose run --rm test
```

All of these should work without privilege escalation in the agent container itself.

---

## 9. Docker sidecar contract

The Docker sidecar is a prebuilt image with the Docker daemon, its runtime dependencies, and nothing MoonMind-specific.

It needs only:

- a Docker daemon
- the shared socket volume
- the shared workspace volume
- session-scoped graph storage

It must not receive:

- the host Docker socket
- MoonMind deployment credentials
- MoonMind session tokens
- the MoonMind codebase

Recommended sidecar images:

| Variant | Image | Privileged | Notes |
|---|---|---|---|
| Classic | `docker:27-dind` | Yes | Broadest workload compatibility. Pin the tag. |
| Rootless | `docker:27-dind-rootless` | No | Preferred where the workload set is compatible. Some features (e.g. cgroup v1, certain storage drivers) are restricted. |

Start with classic DinD on trusted deployments. Roll rootless out as a hardening track per deployment as workload compatibility is confirmed.

---

## 10. Volume contract

| Volume | Lifecycle | Mount path | Shared with | Purpose |
|---|---|---|---|---|
| `workspace` | session | same path in both (e.g. `/work/agent_jobs`) | agent + sidecar | the agent's repo and run artifacts |
| `docker-socket` | session | `/var/run/moonmind-docker` | agent + sidecar | Unix socket exposed by `dockerd` |
| `docker-graph` | session | `/var/lib/docker` (or rootless equivalent) | sidecar only | nested image/container storage |
| optional caches | session or user | deployment-defined | agent + sidecar (as declared) | package manager / build caches |

Invariants:

- `agent.workspace.mountPath == dockerSidecar.workspace.mountPath` (the §5.1 rule).
- The socket volume is exclusive to this session.
- The graph volume is exclusive to this session and removed at session end by default.
- Optional caches must be deployment-approved named volumes; arbitrary host-path mounts are not part of this contract.

---

## 11. Materialized Docker shape

For current Docker deployments the profile materializes as two containers and three volumes per session.

Illustrative Compose-style output (real launcher names are generated per-session):

```yaml
services:
  session-agent:
    image: moonmind/managed-agent:<pinned>
    environment:
      MOONMIND_REPO_DIR: /work/agent_jobs/<task_run_id>/repo
      DOCKER_HOST: unix:///var/run/moonmind-docker/docker.sock
    volumes:
      - session-workspace:/work/agent_jobs
      - session-docker-socket:/var/run/moonmind-docker
    depends_on:
      - session-docker

  session-docker:
    image: docker:27-dind
    privileged: true
    command:
      - dockerd
      - --host=unix:///var/run/moonmind-docker/docker.sock
    volumes:
      - session-workspace:/work/agent_jobs
      - session-docker-socket:/var/run/moonmind-docker
      - session-docker-graph:/var/lib/docker

volumes:
  session-workspace:
  session-docker-socket:
  session-docker-graph:
```

Per-session names follow the existing MoonMind labeling convention:

- `moonmind-session-<session_id>-agent`
- `moonmind-session-<session_id>-docker`
- `moonmind-session-<session_id>-workspace`
- `moonmind-session-<session_id>-docker-socket`
- `moonmind-session-<session_id>-docker-graph`

Containers must carry MoonMind ownership labels (see §22).

---

## 12. Materialized Kubernetes shape (future)

The same profile maps to a single Pod with two containers and shared volumes.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: moonmind-session-<session_id>
  labels:
    app: moonmind
    moonmind.kind: managed-session
    moonmind.session_id: <session_id>
spec:
  containers:
    - name: agent
      image: moonmind/managed-agent:<pinned>
      env:
        - { name: MOONMIND_REPO_DIR, value: /work/agent_jobs/<task_run_id>/repo }
        - { name: DOCKER_HOST,       value: unix:///var/run/moonmind-docker/docker.sock }
      volumeMounts:
        - { name: workspace,     mountPath: /work/agent_jobs }
        - { name: docker-socket, mountPath: /var/run/moonmind-docker }
    - name: docker-sidecar
      image: docker:27-dind
      securityContext:
        privileged: true
      command: [ "dockerd" ]
      args:    [ "--host=unix:///var/run/moonmind-docker/docker.sock" ]
      volumeMounts:
        - { name: workspace,     mountPath: /work/agent_jobs }
        - { name: docker-socket, mountPath: /var/run/moonmind-docker }
        - { name: docker-graph,  mountPath: /var/lib/docker }
  volumes:
    - { name: workspace,     persistentVolumeClaim: { claimName: moonmind-session-<session_id>-workspace } }
    - { name: docker-socket, emptyDir: {} }
    - { name: docker-graph,  emptyDir: {} }
```

For clusters that disallow DinD, the `kubernetes-job` mode replaces nested Docker with per-workload Jobs; the agent then requests workloads through a MoonMind capability rather than `docker run`. That mode is a future option, not the default.

---

## 13. Session request and status

The Temporal `MoonMind.AgentSession` workflow requests a session and reads its status. The Docker-related portions of the request and the status surface are:

Session request (excerpt):

```yaml
runtimeProfileRef: default-docker-sidecar

repo:
  mountPath: /work/agent_jobs/<task_run_id>/repo
  checkout: { provider: github, repository: owner/repo, ref: main }

capabilities:
  docker:
    required: true
    mode: sidecar
    compose: optional

lifecycle:
  destroySidecarOnSessionEnd: true
  destroyDockerGraphOnSessionEnd: true
  preserveWorkspaceOnSessionEnd: configurable
```

Session status (excerpt):

```yaml
phase: running
workspace: { mountPath: /work/agent_jobs, ready: true }
capabilities:
  docker:
    available: true
    mode: sidecar-dind
    dockerHost: unix:///var/run/moonmind-docker/docker.sock
    composeAvailable: true
    daemon: { ready: true, version: "27.x" }
    checks:  { dockerVersion: passed, dockerInfo: passed }
containers:
  agent:         { phase: running }
  dockerSidecar: { phase: running, ready: true }
```

If the sidecar fails to come up:

```yaml
capabilities:
  docker:
    available: false
    mode: sidecar-dind
    reason: sidecar_not_ready
    message: "Docker daemon did not become ready within 60s."
```

Skill and plan logic can then branch cleanly:

- `docker.available == true` → run normal containerized tests.
- `docker.available == false` → report the environment limitation or use a non-Docker fallback.

---

## 14. What the session sees

Inside the agent container, with sidecar mode enabled:

```bash
$ echo "$MOONMIND_REPO_DIR"
/work/agent_jobs/<task_run_id>/repo

$ echo "$DOCKER_HOST"
unix:///var/run/moonmind-docker/docker.sock

$ docker version
$ docker run --rm alpine echo hello
$ docker ps                 # only this session's containers
```

The session does **not** see:

- `/var/run/docker.sock` from the host
- containers from other sessions
- containers from the MoonMind application itself (api, worker, temporal, etc.)

The dedicated socket path `/var/run/moonmind-docker/docker.sock` is the convention; deployments must not silently substitute `/var/run/docker.sock` for the host socket.

---

## 15. Repository workload examples

### 15.1 Smoke test

```bash
docker run --rm alpine sh -lc 'echo hello from docker'
```

### 15.2 Workspace visibility

```bash
echo "agent wrote this" > "$MOONMIND_REPO_DIR/sidecar-check.txt"

docker run --rm \
  -v "$MOONMIND_REPO_DIR":/workspace -w /workspace \
  alpine cat sidecar-check.txt
# agent wrote this
```

### 15.3 .NET test workload

```bash
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PWD":/workspace -w /workspace \
  -e HOME=/workspace/.moonmind/home \
  -e DOTNET_CLI_HOME=/workspace/.moonmind/dotnet-home \
  -e NUGET_PACKAGES=/workspace/.moonmind/nuget/packages \
  -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  bash ./scripts/ci-unit-tests.sh
```

This is the workflow MoonMind recommends for ordinary containerized testing.

---

## 16. Repository script convention

Repos should expose ordinary test entrypoints:

```
scripts/
  test-container.sh     # outer: docker run … bash ./scripts/ci-unit-tests.sh
  ci-unit-tests.sh      # inner: actual build/test commands
```

This keeps the contract uniform across runtimes: the agent invokes a repo-provided script, the sidecar daemon runs the requested image, and the session reads results from the workspace.

---

## 17. Policy model

The strongest security boundary in this design comes from the topology itself:

- a private per-session Docker daemon,
- no host Docker socket,
- no MoonMind deployment credentials in the agent or sidecar,
- no visibility into MoonMind application containers,
- explicit per-session cleanup,
- resource limits applied outside the nested daemon.

Beyond topology, policy is declared so that hardening can tighten over time. A Docker API proxy is **not** required for MVP, but the policy below is the target enforcement surface for a future proxy:

```yaml
policy:
  daemonScope: session

  forbidden:
    hostDockerSocket: true
    sharedDaemonAcrossUsers: true
    moonmindDeploymentSecretsInSession: true
    appContainerControlFromSession: true

  dockerOperations:
    run:                  allowed
    build:                allowed
    pull:                 allowed
    compose:              allowed
    inspectOwnContainers: allowed
    logsOwnContainers:    allowed
    systemPrune:          denied
    pluginInstall:        denied
    swarm:                denied

  dangerousRunOptions:
    privileged:        warn-or-deny
    hostNetwork:       deny
    hostPid:           deny
    hostIpc:           deny
    devices:           deny
    dockerSocketMount: deny
    hostRootMount:     deny
```

MVP enforces the topology rules at launch time. Per-operation enforcement (e.g. blocking `docker swarm init` inside the session) is a follow-up that can be added with an API proxy once the broader policy surface is needed.

---

## 18. Resource model

Resource limits live **outside** the nested daemon so the daemon cannot grant itself more than the session sidecar received.

```yaml
resources:
  session:        { maxRuntimeSeconds: 14400 }
  agent:          { cpu: "2", memory: 4Gi }
  dockerSidecar:  { cpu: "4", memory: 8Gi, ephemeralStorage: 40Gi }
  nestedContainers:
    defaultCpu:    "2"
    defaultMemory: 4Gi
    maxContainers: 16
```

The sidecar can run multiple nested containers concurrently (build + test + compose stack). Size the outer sidecar limits for the realistic concurrent workload, not for a single shell.

---

## 19. Readiness behavior

The agent waits briefly for the sidecar's daemon to come up before reporting the session ready.

```yaml
readiness:
  docker:
    required: true
    timeoutSeconds: 60
    intervalSeconds: 2
    probes: [ "docker version", "docker info" ]
```

Behavior:

- If `required: true` and the daemon does not become ready within `timeoutSeconds`, session startup fails (or is marked degraded per profile) and the status surface in §13 records the reason.
- If `required: false` and the daemon is not ready, the session still starts and the status reports `docker.available=false`. Skill/plan logic must check that field before attempting Docker work.

Recommended defaults:

- Normal managed-agent sessions: `required: true`.
- Lightweight non-Docker sessions: `required: false` (or `workloadMode: no-docker`).

---

## 20. Cleanup model

Cleanup is declared explicitly so failures don't leave dangling state.

```yaml
cleanup:
  onSessionEnd:
    stopNestedContainers: true
    removeDockerGraph:    true
    removeDockerSocket:   true
    preserveWorkspace:    configurable     # follows session-policy default

  onSidecarFailure:
    markDockerCapabilityUnavailable: true
    preserveAgentSession:            true

  onAgentFailure:
    stopSidecar:        true
    preserveWorkspace:  configurable
```

Default posture: destroy the sidecar and its graph storage at session end; preserve the workspace only according to the existing managed-session retention policy.

---

## 21. Separation from the MoonMind admin/update path

The session sidecar is the **agent workload** path. It is not the path MoonMind uses to manage itself.

Ordinary managed sessions must not:

- restart MoonMind application containers,
- deploy or roll back MoonMind services,
- interact with the host Docker socket directly,
- inspect or kill containers owned by other sessions or the MoonMind app.

MoonMind admin/update operations (`deploy`, `restart`, `rollback`, image refresh) live in a dedicated **MoonMind ops runtime**:

```yaml
kind: MoonMindOpsRuntime
name: docker-admin-runtime
spec:
  purpose: moonmind-application-operations
  backend: docker
  exposedToManagedAgents: false
  allowedOperations: [ status, deploy, restart, rollback, imageRefresh, logs ]
  dockerBackend:
    hostDockerAccess: true
    component: moonmind-ops-runner
    allowedServices: [ api, worker, session-manager, ops-runner ]
  futureBackends: [ kubernetes ]
```

This is one part of the residual scope of the older Docker-out-of-Docker plane described in [`DockerOutOfDocker.md`](./DockerOutOfDocker.md). The same control-plane DooD boundary also covers helper or one-shot workloads with no managed session attached and deliberately deployment-gated exceptional workloads. For ordinary repo tests, builds, and compose-driven workloads, use the sidecar runtime defined in this document, not `container.run_workload` / `container.run_container` / `container.run_docker`.

If the MoonMind API container ever needs Docker access for admin reasons, that access lives in a dedicated `moonmind-ops-runner`, not in the API container itself.

---

## 22. Audit and observability

Every per-session sidecar deployment must be discoverable and traceable. The two containers and their volumes carry MoonMind ownership labels consistent with `DockerOutOfDocker.md §13.6`:

- `moonmind.kind=managed-session` (agent) / `moonmind.kind=session-docker-sidecar` (sidecar)
- `moonmind.session_id=<session_id>`
- `moonmind.session_epoch=<session_epoch>`
- `moonmind.task_run_id=<task_run_id>` (when bound)
- `moonmind.workload_mode=docker-sidecar | docker-sidecar-rootless`

The session-status surface (§13) reports daemon readiness, version, and probe results. Durable evidence for agent work continues to flow through the existing artifact pipeline; the sidecar itself is not the system of record. Per-container stdout/stderr capture for the daemon belongs in worker logs, not in the task artifact area, unless explicitly attached for debugging.

---

## 23. Validation rules

The session launcher must validate at least the following before starting a session, and fail closed on any violation:

1. If `dockerSidecar.enabled=true`, then `agent.dockerClient.enabled=true`.
2. `agent.dockerClient.daemonInAgent` must be `false`.
3. `agent.env.DOCKER_HOST` must point at the declared sidecar socket path.
4. `agent.workspace.mountPath == dockerSidecar.workspace.mountPath` (§5.1).
5. The sidecar mount set must not include the host Docker socket.
6. Neither container may receive MoonMind deployment credentials, registry push secrets, or session-bridge tokens for unrelated sessions.
7. The MoonMind API container must not mount the host Docker socket as part of normal workload support.
8. Admin/ops Docker access must be isolated to the MoonMind ops runtime (§21).
9. The sidecar image tag must be pinned (`docker:27-dind`, not `docker:latest`).
10. The Docker daemon scope must be per session — no shared daemon across sessions or users.

Example failure message:

```
Invalid ManagedAgentRuntimeProfile:
  agent workspace mountPath /workspace does not match
  dockerSidecar workspaceMountPath /mnt/workspace.

  Normal `docker run -v "$PWD":/workspace` will not work because
  bind mount sources are resolved by the Docker daemon, which sees
  a different filesystem path than the agent.
```

---

## 24. Skill and plan guidance

Skills and plans that drive containerized work in managed sessions should:

- Prefer repo-provided scripts (`./scripts/test-container.sh`, `make test-container`, `docker compose run --rm test`).
- Use a `docker version` probe to confirm capability before assuming Docker is available, and consult `capabilities.docker.available` in the session status when planning across steps.
- Use ordinary Docker commands, not MoonMind workload-bridge tools, for repo tests and short-lived build/test containers.
- Reserve MoonMind admin tools (deploy / restart / rollback) for MoonMind-managing flows, not for repo work.

A skill should not assume that `container.run_workload` is the route to a `dotnet test` or `npm test`. The route is `docker run …` or a repo-provided script that calls it.

---

## 25. Backend portability

### 25.1 Docker today

A session is two containers and three volumes (§11). The session launcher creates them; the agent uses Docker normally; cleanup removes the sidecar and graph at session end.

### 25.2 Kubernetes later

The same profile maps to a single Pod with two containers (§12). Volumes become a PVC for the workspace and `emptyDir` volumes for the socket and graph.

### 25.3 Kubernetes-native fallback

For clusters that disallow DinD, `workloadMode: kubernetes-job` lets the same logical workload become a Kubernetes Job:

```yaml
image:     mcr.microsoft.com/dotnet/sdk:8.0
command:   [ "bash", "./scripts/ci-unit-tests.sh" ]
workspace: current-session
```

That mode preserves the principle (no host Docker, no shared daemon) but trades the "agents see normal Docker" property for native cluster scheduling. It is a future option, not the default execution path today.

The profile-level `labels` map is part of the durable runtime profile rather
than a Docker-rendering detail. Docker launchers render those labels as Docker
labels today; Kubernetes renderers map the same values to Pod or Job metadata.
Kubernetes Job profiles omit `dockerSidecar`, `resources.dockerSidecar`, and
`resources.nestedContainers`; backend-specific rendering owns the Job resource
shape after `policy.kubernetesJobRuntimeSupported` has explicitly opted the
deployment into that mode.

---

## 26. Stable design rules

The rules below remain stable as implementation details evolve.

1. Managed agent sessions run ordinary Docker commands; the daemon is a sibling container, not embedded in the agent image.
2. The host Docker socket is never exposed to a managed session or its sidecar.
3. The workspace volume is mounted at the same absolute path in the agent and the sidecar.
4. The Docker daemon scope is per session; no daemon is shared across sessions or users.
5. The Docker sidecar image is prebuilt and generic; it does not need the MoonMind codebase or deployment credentials.
6. The session container is the actor for ordinary containerized work; control-plane-launched Docker workloads (DooD) are reserved for MoonMind admin/update flows, helper or one-shot workloads with no managed session attached, and deliberately deployment-gated exceptional workloads.
7. Artifacts and bounded session metadata remain authoritative; nested container state is not durable truth.
8. The design has a clean Kubernetes mapping (sidecar in Pod, shared volumes) and a fallback to Kubernetes Jobs where DinD is disallowed.
9. Resource limits live outside the nested daemon.
10. The MoonMind API container stays lightweight and never carries the host Docker socket for normal workload support.

---

## 27. Final declarative contract

```yaml
managedAgentDockerContract:
  normalWorkloads:
    executionModel:       docker-sidecar
    agentHas:             docker-cli-only
    sidecarHas:           private-docker-daemon
    hostDockerSocket:     forbidden
    workspaceSharing:     same-path-shared-volume
    repoTestingInterface: ordinary-docker-commands

  agentContainer:
    lightweight:                true
    runsDockerd:                false
    hasDockerCli:               true
    hasComposePlugin:           optional
    receivesDeploymentSecrets:  false

  sidecarContainer:
    prebuilt:                  true
    needsMoonMindCodebase:     false
    daemonScope:               per-session
    receivesDeploymentSecrets: false
    seesWorkspaceAt:           same-as-agent

  adminUpdates:
    executionModel:         separate-ops-runtime
    dockerOutsideAllowed:   only-for-admin-backend
    exposedToNormalAgents:  false
    futureBackend:          kubernetes
```

Mental model:

- Agents run Docker like normal developers.
- The Docker daemon is private to the session.
- The workspace is shared with the sidecar at the same path.
- MoonMind admin/update, helper or one-shot workloads with no managed session attached, and deliberately deployment-gated exceptional workloads use a separate, narrow Docker path.
- The MoonMind API container stays lightweight.
- The sidecar is prebuilt and carries no MoonMind code or credentials.

---

## 28. Related architecture alignment

The related managed-agent architecture documents use this document as the
reference for sidecar runtime semantics:

- `docs/ManagedAgents/ManagedAgentArchitecture.md §5.5` describes the sidecar as the default Docker capability path for managed sessions while preserving the no-host-socket and no-app-container-control boundary.
- `docs/ManagedAgents/DockerOutOfDocker.md` defines the remaining control-plane Docker workload scope for MoonMind admin/update, helper, and deliberately gated exceptional workloads.
- Top-level architecture references distinguish ordinary session-originated Docker work from control-plane DooD workloads.
