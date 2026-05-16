# Managed Agent Docker Sidecar Runtime

## 1. Design decision

MoonMind should support ordinary Docker-based task workloads through a **per-session Docker sidecar**.

Normal agent testing should look like this:

```bash
docker run --rm -v "$PWD":/workspace -w /workspace mcr.microsoft.com/dotnet/sdk:8.0 ...
```

or:

```bash
./scripts/test-container.sh
```

The managed agent should not need MoonMind-specific workload syntax for ordinary testing.

The Docker daemon should not run inside the managed agent container. Instead:

```text
managed agent container:
  Docker CLI only

docker sidecar container:
  private Docker daemon

shared workspace volume:
  mounted into both containers at the same path
```

Docker-outside-of-Docker should be reserved for MoonMind admin/update tasks only.

---

# 2. Primary goals

```text
1. Let agents run normal Docker commands.
2. Keep the managed agent image lightweight.
3. Avoid installing dockerd/containerd/runc in the agent container.
4. Avoid exposing the host Docker socket to managed agents.
5. Use a prebuilt Docker sidecar image that does not need the MoonMind codebase.
6. Ensure Docker containers run by agents see the agent’s current workspace changes.
7. Keep MoonMind application updates separate from normal task workloads.
8. Preserve a future path to Kubernetes.
```

---

# 3. Non-goals

```text
1. Do not expose /var/run/docker.sock from the host to the managed agent.
2. Do not let normal task agents restart MoonMind application containers.
3. Do not require moonmind workload run-container for ordinary tests.
4. Do not install the full MoonMind codebase into the Docker sidecar.
5. Do not put deployment credentials into the agent or sidecar.
6. Do not make Docker the internal app-management abstraction.
```

---

# 4. Conceptual runtime model

```text
Managed Session
├── agent container
│   ├── Codex / moonspec / shell
│   ├── git and repo tools
│   ├── docker CLI only
│   ├── DOCKER_HOST=unix:///var/run/moonmind-docker/docker.sock
│   └── /workspace mounted
│
├── docker sidecar container
│   ├── dockerd or rootless dockerd
│   ├── no MoonMind app code required
│   ├── no MoonMind deployment credentials
│   ├── /workspace mounted at the same path
│   ├── /var/run/moonmind-docker mounted for socket sharing
│   └── /var/lib/docker or rootless storage mounted
│
└── shared volumes
    ├── workspace
    ├── docker-socket
    ├── docker-graph
    └── optional cache/scratch volumes
```

The key rule:

```text
The workspace must be mounted at the same absolute path in both the agent and sidecar.
```

For example:

```text
agent container:
  /workspace

docker sidecar:
  /workspace
```

Then this works correctly:

```bash
docker run --rm -v "$PWD":/workspace -w /workspace alpine ls
```

because the Docker daemon can resolve the same `/workspace` path that the agent sees.

---

# 5. Top-level declarative object

Use a profile object to describe managed agent runtime behavior.

```yaml
apiVersion: moonmind.dev/v1
kind: ManagedAgentRuntimeProfile
metadata:
  name: default-docker-sidecar
spec:
  workloadMode: docker-sidecar

  workspace:
    mountPath: /workspace
    mode: shared-rw
    lifecycle: session
    exposeAsEnv: MOONMIND_REPO_DIR

  agent:
    image: moonmind/managed-agent:latest
    dockerClient:
      enabled: true
      composePlugin: true
      daemonInAgent: false
    env:
      MOONMIND_REPO_DIR: /workspace
      DOCKER_HOST: unix:///var/run/moonmind-docker/docker.sock
    mounts:
      - name: workspace
        mountPath: /workspace
      - name: docker-socket
        mountPath: /var/run/moonmind-docker

  dockerSidecar:
    enabled: true
    mode: dind
    image: docker:27-dind
    socket:
      type: unix
      path: /var/run/moonmind-docker/docker.sock
      volumeName: docker-socket
    workspaceMountPath: /workspace
    storage:
      volumeName: docker-graph
      mountPath: /var/lib/docker
      lifecycle: session
    security:
      privileged: true
      hostDockerSocket: forbidden
      moonmindSecrets: forbidden
    mounts:
      - name: workspace
        mountPath: /workspace
      - name: docker-socket
        mountPath: /var/run/moonmind-docker
      - name: docker-graph
        mountPath: /var/lib/docker

  resources:
    agent:
      cpu: "2"
      memory: 4Gi
    dockerSidecar:
      cpu: "4"
      memory: 8Gi
      ephemeralStorage: 40Gi

  readiness:
    docker:
      required: true
      timeoutSeconds: 60
      checks:
        - docker version
        - docker info

  policy:
    hostDockerAccess: forbidden
    appContainerControlFromSession: forbidden
    deploymentSecretsInSession: forbidden
```

This profile is declarative. The session launcher materializes it differently depending on whether the deployment is Docker-based or Kubernetes-based.

---

# 6. Runtime modes

The profile should support several modes.

```yaml
spec:
  workloadMode: docker-sidecar
```

Allowed values:

```text
docker-sidecar
  Normal default. Agent gets Docker CLI. Sidecar runs private Docker daemon.

docker-sidecar-rootless
  Preferred future option where supported.

no-docker
  Agent has no Docker capability.

moonmind-workload-bridge
  Optional fallback/advanced mode. Not the default for normal testing.

kubernetes-job
  Future backend for workloads that should run as Kubernetes Jobs instead of DinD.
```

Recommended current default:

```yaml
workloadMode: docker-sidecar
```

Future Kubernetes-capable default:

```yaml
workloadMode: docker-sidecar-rootless
```

or, for locked-down clusters:

```yaml
workloadMode: kubernetes-job
```

---

# 7. Agent image contract

The managed agent image should be lightweight.

It should include:

```text
bash
git
curl/wget
ca-certificates
Docker CLI
Docker Compose plugin, if needed
moonspec/Codex/session tooling
```

It should not include:

```text
dockerd
containerd
runc
Docker graph storage
host Docker socket
MoonMind deployment credentials
```

Declarative image contract:

```yaml
agentImageContract:
  mustHave:
    - shell
    - git
    - docker-cli
  optional:
    - docker-compose-plugin
    - make
    - jq
  mustNotHave:
    - dockerd-running
    - host-docker-socket
    - deployment-secrets
```

The agent should be able to run:

```bash
docker version
docker run --rm alpine echo hello
docker build -t local-test-image .
docker compose run --rm test
```

assuming the sidecar and policy allow those operations.

---

# 8. Docker sidecar contract

The Docker sidecar should be a prebuilt container image.

It does not need the MoonMind codebase.

It needs only:

```text
Docker daemon
runtime dependencies
shared socket mount
shared workspace mount
Docker graph storage
```

Declarative sidecar contract:

```yaml
dockerSidecarContract:
  requiresMoonMindCodebase: false
  receivesMoonMindSessionToken: false
  receivesDeploymentCredentials: false
  receivesHostDockerSocket: false
  ownsDockerDaemon: true
  daemonScope: session
  lifecycle: session
```

Recommended sidecar image options:

```yaml
sidecarImages:
  classic:
    image: docker:27-dind
    requiresPrivileged: true

  rootless:
    image: docker:27-dind-rootless
    requiresPrivileged: false
    compatibility: environment-dependent
```

Start with classic DinD if your current Docker deployments are trusted and controlled. Add rootless support as a future hardening option.

---

# 9. Shared volume design

The sidecar design depends on shared volumes.

```yaml
volumes:
  - name: workspace
    purpose: current agent repo/filesystem
    lifecycle: session
    mountPath: /workspace
    sharedWith:
      - agent
      - dockerSidecar

  - name: docker-socket
    purpose: Docker client/daemon Unix socket
    lifecycle: session
    mountPath: /var/run/moonmind-docker
    sharedWith:
      - agent
      - dockerSidecar

  - name: docker-graph
    purpose: nested Docker image/container storage
    lifecycle: session
    mountPath: /var/lib/docker
    sharedWith:
      - dockerSidecar

  - name: cache
    purpose: optional package/build caches
    lifecycle: session-or-user
    mountPath: /cache
    sharedWith:
      - agent
      - dockerSidecar
```

The most important invariant:

```yaml
invariants:
  - name: workspace-same-path
    rule: agent.mounts.workspace.mountPath == dockerSidecar.mounts.workspace.mountPath
```

Without this, normal Docker bind mounts from the agent will break.

---

# 10. Materialized Docker deployment shape

For current Docker-based deployments, the profile can be materialized as a pair of containers.

Conceptual Compose-style output:

```yaml
services:
  session-agent:
    image: moonmind/managed-agent:latest
    environment:
      MOONMIND_REPO_DIR: /workspace
      DOCKER_HOST: unix:///var/run/moonmind-docker/docker.sock
    volumes:
      - session-workspace:/workspace
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
      - session-workspace:/workspace
      - session-docker-socket:/var/run/moonmind-docker
      - session-docker-graph:/var/lib/docker

volumes:
  session-workspace:
  session-docker-socket:
  session-docker-graph:
```

In the real system, these names should be generated per session:

```text
moonmind-session-<sessionId>-agent
moonmind-session-<sessionId>-docker
moonmind-session-<sessionId>-workspace
moonmind-session-<sessionId>-docker-socket
moonmind-session-<sessionId>-docker-graph
```

---

# 11. Materialized Kubernetes shape

For future Kubernetes deployments, the same profile maps to a Pod.

Conceptual Pod:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: moonmind-session-example
  labels:
    app: moonmind
    moonmind.dev/component: managed-session
    moonmind.dev/session-id: example
spec:
  containers:
    - name: agent
      image: moonmind/managed-agent:latest
      env:
        - name: MOONMIND_REPO_DIR
          value: /workspace
        - name: DOCKER_HOST
          value: unix:///var/run/moonmind-docker/docker.sock
      volumeMounts:
        - name: workspace
          mountPath: /workspace
        - name: docker-socket
          mountPath: /var/run/moonmind-docker

    - name: docker-sidecar
      image: docker:27-dind
      securityContext:
        privileged: true
      command:
        - dockerd
      args:
        - --host=unix:///var/run/moonmind-docker/docker.sock
      volumeMounts:
        - name: workspace
          mountPath: /workspace
        - name: docker-socket
          mountPath: /var/run/moonmind-docker
        - name: docker-graph
          mountPath: /var/lib/docker

  volumes:
    - name: workspace
      persistentVolumeClaim:
        claimName: moonmind-session-example-workspace

    - name: docker-socket
      emptyDir: {}

    - name: docker-graph
      emptyDir: {}
```

The rootless version would use a different sidecar image, different security context, and possibly a different storage path.

---

# 12. Session request object

A managed session should request Docker capability declaratively.

```yaml
apiVersion: moonmind.dev/v1
kind: ManagedAgentSession
metadata:
  sessionId: sess_123
spec:
  runtimeProfileRef: default-docker-sidecar

  repo:
    mountPath: /workspace
    checkout:
      provider: github
      repository: owner/repo
      ref: main

  capabilities:
    docker:
      required: true
      mode: sidecar
      compose: optional

  lifecycle:
    destroySidecarOnSessionEnd: true
    destroyDockerGraphOnSessionEnd: true
    preserveWorkspaceOnSessionEnd: configurable

  resources:
    sessionTimeoutSeconds: 14400
```

The launcher should then create the agent and sidecar according to the referenced runtime profile.

---

# 13. Session status object

The runtime should expose status back to MoonMind and possibly to the agent.

```yaml
apiVersion: moonmind.dev/v1
kind: ManagedAgentSessionStatus
metadata:
  sessionId: sess_123
status:
  phase: running

  workspace:
    mountPath: /workspace
    ready: true

  capabilities:
    docker:
      available: true
      mode: sidecar-dind
      dockerHost: unix:///var/run/moonmind-docker/docker.sock
      composeAvailable: true
      daemon:
        ready: true
        version: "27.x"
      checks:
        dockerVersion: passed
        dockerInfo: passed

  containers:
    agent:
      phase: running
    dockerSidecar:
      phase: running
      ready: true
```

If the sidecar fails:

```yaml
status:
  capabilities:
    docker:
      available: false
      mode: sidecar-dind
      reason: sidecar_not_ready
      message: Docker daemon did not become ready within 60 seconds.
```

This lets moonspec reason cleanly:

```text
Docker available: run containerized tests.
Docker unavailable: report environment limitation or use fallback.
```

---

# 14. What the agent sees

When sidecar mode is enabled, the agent should see:

```bash
echo "$MOONMIND_REPO_DIR"
# /workspace

echo "$DOCKER_HOST"
# unix:///var/run/moonmind-docker/docker.sock

docker version
docker run --rm alpine echo hello
```

The agent should not see:

```bash
/var/run/docker.sock
```

unless that is the private sidecar socket path by design. Prefer:

```text
/var/run/moonmind-docker/docker.sock
```

to make it clear this is not the host Docker socket.

The agent should not be able to inspect MoonMind application containers:

```bash
docker ps
```

should show only containers created inside the private session sidecar.

---

# 15. Normal task workload examples

## 15.1 Simple smoke test

```bash
docker run --rm alpine sh -lc 'echo hello from docker'
```

## 15.2 Workspace visibility test

```bash
echo "agent wrote this" > /workspace/sidecar-check.txt

docker run --rm \
  -v /workspace:/workspace \
  -w /workspace \
  alpine \
  cat sidecar-check.txt
```

Expected output:

```text
agent wrote this
```

## 15.3 .NET test example

```bash
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$PWD":/workspace \
  -w /workspace \
  -e HOME=/workspace/.moonmind/home \
  -e DOTNET_CLI_HOME=/workspace/.moonmind/dotnet-home \
  -e NUGET_PACKAGES=/workspace/.moonmind/nuget/packages \
  -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  -e DOTNET_NOLOGO=1 \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  bash ./scripts/ci-unit-tests.sh
```

This is the normal workflow MoonMind should encourage.

---

# 16. Repository script convention

Docs should recommend that repos define normal scripts.

```text
scripts/
  ci-unit-tests.sh
  test-container.sh
```

Example `scripts/test-container.sh`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p \
  "$ROOT/.moonmind/test-results" \
  "$ROOT/.moonmind/home" \
  "$ROOT/.moonmind/dotnet-home" \
  "$ROOT/.moonmind/nuget/packages"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$ROOT":/workspace \
  -w /workspace \
  -e HOME=/workspace/.moonmind/home \
  -e DOTNET_CLI_HOME=/workspace/.moonmind/dotnet-home \
  -e NUGET_PACKAGES=/workspace/.moonmind/nuget/packages \
  -e DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  -e DOTNET_NOLOGO=1 \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  bash ./scripts/ci-unit-tests.sh
```

Example `scripts/ci-unit-tests.sh`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

(
  cd .nuget/local-feed
  sha256sum -c SHA256SUMS
)

dotnet restore crash_server_main.sln --configfile NuGet.config
dotnet build crash_server_main.sln --no-restore
dotnet test main_service_tests/ \
  --filter "FullyQualifiedName!~Tests.RNG" \
  --no-build \
  --no-restore \
  --logger "trx;LogFileName=unit-tests.trx" \
  --results-directory .moonmind/test-results
```

---

# 17. Policy model

For MVP, isolation comes mostly from the private per-session daemon.

Still, define policy declaratively so it can harden over time.

```yaml
policy:
  daemonScope: session

  forbidden:
    hostDockerSocket: true
    sharedDaemonAcrossUsers: true
    moonmindDeploymentSecretsInSession: true
    appContainerControlFromSession: true

  dockerOperations:
    run: allowed
    build: allowed
    pull: allowed
    compose: allowed
    inspectOwnContainers: allowed
    logsOwnContainers: allowed
    systemPrune: denied
    pluginInstall: denied
    swarm: denied

  dangerousRunOptions:
    privileged: warn-or-deny
    hostNetwork: deny
    hostPid: deny
    hostIpc: deny
    devices: deny
    dockerSocketMount: deny
    hostRootMount: deny
```

MVP does not have to enforce all of these with a Docker API proxy. But the policy should be documented.

The strongest security boundary is:

```text
private daemon
no host Docker socket
no app secrets
no app containers visible
session cleanup
outer resource limits
```

A Docker API proxy can be added later for strict enforcement.

---

# 18. Resource model

Set resource limits outside the nested daemon.

```yaml
resources:
  session:
    maxRuntimeSeconds: 14400

  agent:
    cpu: "2"
    memory: 4Gi

  dockerSidecar:
    cpu: "4"
    memory: 8Gi
    ephemeralStorage: 40Gi

  nestedContainers:
    defaultCpu: "2"
    defaultMemory: 4Gi
    maxContainers: 16
```

The sidecar can run multiple nested containers, so the outer sidecar limits must be sized for expected workloads.

---

# 19. Cleanup model

Declare cleanup behavior explicitly.

```yaml
cleanup:
  onSessionEnd:
    stopNestedContainers: true
    removeDockerGraph: true
    removeDockerSocket: true
    preserveWorkspace: configurable

  onSidecarFailure:
    markDockerCapabilityUnavailable: true
    preserveAgentSession: true

  onAgentFailure:
    stopSidecar: true
    preserveWorkspace: configurable
```

Recommended default:

```text
Destroy Docker sidecar and Docker graph storage when the session ends.
Preserve workspace only according to the normal MoonMind session policy.
```

---

# 20. Separation from MoonMind admin/update path

Normal managed sessions should not use Docker-outside-of-Docker.

Declare admin/update separately:

```yaml
apiVersion: moonmind.dev/v1
kind: MoonMindOpsRuntime
metadata:
  name: docker-admin-runtime
spec:
  purpose: moonmind-application-operations
  backend: docker
  exposedToManagedAgents: false

  allowedOperations:
    - status
    - deploy
    - restart
    - rollback
    - logs

  dockerBackend:
    hostDockerAccess: true
    component: moonmind-ops-runner
    allowedServices:
      - api
      - worker
      - session-manager
      - ops-runner

  futureBackends:
    - kubernetes
```

This path is for:

```text
moonmind ops deploy
moonmind ops restart
moonmind ops rollback
```

not for:

```text
repo tests
dotnet test
npm test
docker compose run test
```

The MoonMind API container should stay lightweight. If host Docker access is needed for admin tasks, put that access in a dedicated `moonmind-ops-runner`, not in the API container.

---

# 21. Backend portability

This design should support Docker deployments today and Kubernetes deployments later.

## Docker deployment today

```text
Managed session = two Docker containers:
  agent container
  docker sidecar container

Volumes:
  workspace
  docker-socket
  docker-graph
```

## Kubernetes deployment later

```text
Managed session = one Pod:
  agent container
  docker sidecar container

Volumes:
  workspace PVC or emptyDir
  docker-socket emptyDir
  docker-graph emptyDir or PVC
```

## Future Kubernetes-native workload mode

For clusters that disallow DinD:

```yaml
workloadMode: kubernetes-job
```

The same logical task:

```yaml
image: mcr.microsoft.com/dotnet/sdk:8.0
command: ["bash", "./scripts/ci-unit-tests.sh"]
workspace: current-session
```

can become a Kubernetes Job instead of a nested Docker container.

But that should be future/optional, not the default testing path right now.

---

# 22. Validation rules

The session launcher should validate:

```text
1. If dockerSidecar.enabled=true, agent.dockerClient.enabled must be true.
2. agent.dockerClient.daemonInAgent must be false.
3. agent DOCKER_HOST must point to the declared sidecar socket.
4. agent and sidecar workspace mount paths must match exactly.
5. sidecar must not receive host Docker socket.
6. sidecar must not receive MoonMind deployment credentials.
7. API container must not mount host Docker socket for normal workload support.
8. ops/admin Docker access must be isolated to the ops runtime.
9. sidecar image must be pinned, not latest.
10. session Docker daemon scope must be per-session.
```

Example validation failure:

```text
Invalid ManagedAgentRuntimeProfile:
agent workspace mountPath /workspace does not match dockerSidecar workspaceMountPath /mnt/workspace.
Normal docker run -v "$PWD":/workspace will not work because bind mount sources are resolved by the Docker daemon.
```

---

# 23. Readiness behavior

The agent startup should wait briefly for Docker.

Declarative readiness:

```yaml
readiness:
  docker:
    required: true
    timeoutSeconds: 60
    intervalSeconds: 2
    commands:
      - docker version
      - docker info
```

Behavior:

```text
If Docker is required and unavailable:
  mark session startup failed or degraded according to profile.

If Docker is optional and unavailable:
  start session, but report docker.available=false.
```

Recommended for normal managed-agent sessions:

```yaml
docker:
  required: true
```

Recommended for lightweight non-Docker sessions:

```yaml
docker:
  required: false
```

---

# 24. Moonspec behavior

Moonspec guidance should become:

```text
Prefer repo-provided scripts.

If local toolchain is missing, use Docker-based scripts or Docker commands.

Examples:
  ./scripts/test-container.sh
  make test-container
  docker compose run --rm test
  docker run --rm -v "$PWD":/workspace -w /workspace <image> ...

Do not use MoonMind-specific workload commands for ordinary tests unless Docker is unavailable.
```

Moonspec should be able to check:

```bash
docker version
```

If Docker works, it should proceed with normal Docker workflows.

---

# 25. Final declarative contract summary

The core contract is:

```yaml
managedAgentDockerContract:
  normalWorkloads:
    executionModel: docker-sidecar
    agentHas: docker-cli-only
    sidecarHas: private-docker-daemon
    hostDockerSocket: forbidden
    workspaceSharing: same-path-shared-volume
    repoTestingInterface: ordinary-docker-commands

  agentContainer:
    lightweight: true
    runsDockerd: false
    hasDockerCli: true
    hasComposePlugin: optional
    receivesDeploymentSecrets: false

  sidecarContainer:
    prebuilt: true
    needsMoonMindCodebase: false
    daemonScope: per-session
    receivesDeploymentSecrets: false
    seesWorkspaceAt: /workspace

  adminUpdates:
    executionModel: separate-ops-runtime
    dockerOutsideAllowed: only-for-admin-backend
    exposedToNormalAgents: false
    futureBackend: kubernetes
```

The resulting mental model is simple:

Agents run Docker like normal developers.

The Docker daemon is private to the session.

The workspace is shared with the Docker sidecar.

MoonMind app updates are separate admin operations.

The API container stays lightweight.

The sidecar is prebuilt and does not need MoonMind code.
