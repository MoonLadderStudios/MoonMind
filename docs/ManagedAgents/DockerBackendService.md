# Docker Backend Service

- **Status:** Desired state
- **Owners:** MoonMind Platform
- **Last updated:** 2026-07-13
- **Document class:** Canonical declarative design

**Related:**

- [`docs/ManagedAgents/ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/Omnigent/CombinedStackValidationAndRollback.md`](../Omnigent/CombinedStackValidationAndRollback.md)
- [`docs/ExternalAgents/ModelContextProtocol.md`](../ExternalAgents/ModelContextProtocol.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Workflows/SkillAndPlanContracts.md`](../Workflows/SkillAndPlanContracts.md)

---

## 1. Purpose

This document defines the desired-state **Docker Backend Service** for MoonMind.
The service is an API-owned subsystem that accepts governed container-job
requests, turns them into durable MoonMind executions, and runs them against one
configured Docker daemon without exposing Docker authority to the requesting
agent runtime.

The service is the normal route for containerized repository work originating
from:

- MoonMind managed sessions,
- Omnigent-hosted sessions,
- workflow steps and skills,
- the MoonMind dashboard or API,
- other authenticated MCP clients.

Examples include .NET tests, Unreal automation tests, JavaScript toolchains,
containerized linters, integration tests, and any other permitted image selected
at request time.

The selected Docker daemon owns a persistent image store. Images pulled for one
job remain available to later jobs and later workflows until deployment-level
retention removes them. Image lifetime is therefore independent of managed
session and workflow lifetime.

### 1.1 Supersession

This document supersedes the desired-state per-session Docker-in-Docker design
formerly defined in `DockerSidecarRuntime.md`.

The previous design made every managed session own a private `dockerd` and a
private Docker graph. That topology provided strong daemon isolation, but it
also made large image acquisition session-scoped and prevented ordinary
cross-workflow reuse. The desired state instead centralizes Docker access behind
an API-owned job service and one deployment-selected backend daemon.

Existing sidecar implementation code may remain during migration. It is legacy
runtime materialization, not the target architecture.

### 1.2 Relationship to DockerOutOfDocker

[`DockerOutOfDocker.md`](./DockerOutOfDocker.md) remains the lower-level workload
execution and policy contract for Docker-backed workload containers. This
document owns the higher-level API service, asynchronous job surface, backend
selection, workspace resolution, and cross-workflow image-cache semantics.

Where older documentation says ordinary repository tests should run through a
per-session sidecar, this document is authoritative: ordinary agent-originated
container jobs are submitted through MoonMind and executed by the Docker Backend
Service.

---

## 2. Core decisions

1. **MoonMind owns container launches.** Agents request container jobs through a
   typed MoonMind tool. Agents do not receive a Docker socket or raw Docker API
   authority.
2. **The Docker Backend Service is part of the MoonMind API subsystem.** The API
   authenticates callers, authorizes requests, resolves logical workspaces,
   creates durable jobs, and exposes job status and control surfaces.
3. **Long-running execution is asynchronous and Temporal-backed.** An MCP or
   HTTP request submits a job and returns a stable job identifier. Container
   execution does not occupy an API request for the duration of a build.
4. **One configured Docker daemon is the default execution backend.** In the
   current deployment this is the existing system Docker daemon reached through
   MoonMind's Docker socket proxy.
5. **The daemon is selected by deployment configuration, not by an arbitrary
   caller-supplied endpoint.** The public job contract is backend-neutral.
6. **Images are acquired on demand and cached at daemon scope.** The first job
   that requires a missing image pulls it. Later workflows reuse it.
7. **Workflow cleanup never removes shared images.** Containers, per-job scratch,
   and per-job temporary objects are removed; image retention is a separate
   deployment-level policy.
8. **Workspaces are logical references, not arbitrary host paths.** MoonMind
   resolves the caller's authorized workspace into a daemon-visible mount.
9. **The structured container contract is the normal public interface.** Raw
   Docker CLI execution remains an explicitly gated internal escape hatch.
10. **The core is workload-agnostic.** No backend branch is added for Unreal,
    .NET, Unity, Node, or any other toolchain.
11. **A future Docker endpoint must not require an agent or MCP contract change.**
    A deployment may later point the same adapter at another Docker daemon.
12. **No separate MoonMind workload daemon is required by this design today.**
    The default and only required implementation uses the configured system
    Docker backend.

---

## 3. Scope and non-goals

### 3.1 In scope

This document defines:

- the API-owned Docker Backend Service,
- the MCP and HTTP container-job surface,
- asynchronous job lifecycle and status,
- the declarative backend profile,
- the current system-Docker backend,
- workspace and artifact resolution,
- arbitrary on-demand image acquisition,
- cross-workflow image reuse,
- registry credentials and private-image policy,
- resource, mount, network, and cleanup policy,
- Omnigent and managed-session integration,
- backend portability without a second implementation today.

### 3.2 Out of scope

This document does not define:

- a Docker daemon pool,
- an Unreal-specific or .NET-specific worker pool,
- direct Docker socket access from agent containers,
- concurrent sharing of one writable `/var/lib/docker` by multiple daemons,
- Kubernetes orchestration of MoonMind,
- a general remote shell service,
- durable arbitrary services with no explicit owner or TTL,
- automatic migration of images between two different Docker daemons,
- implementation of a dedicated MoonMind workload daemon at this time.

---

## 4. Terminology

### 4.1 Docker Backend Service

The **Docker Backend Service** is the API-owned MoonMind subsystem that accepts,
validates, records, dispatches, observes, and controls container jobs.

It is a service boundary in MoonMind code, not necessarily a separate deployed
container.

### 4.2 Container job

A **container job** is one bounded, owned execution request against an image.
It has a durable job identifier and an explicit terminal outcome.

A container job is not a managed agent session and is not a new
`MoonMind.AgentRun` unless the workload itself is an agent runtime.

### 4.3 Docker backend

A **Docker backend** is a deployment-declared adapter configuration identifying
the Docker daemon used to run jobs. The current backend kind is
`docker-engine`.

### 4.4 System Docker backend

The **system Docker backend** is the existing Docker daemon used by the
MoonMind deployment, reached through the configured `SYSTEM_DOCKER_HOST` or
Docker socket proxy.

### 4.5 Workspace reference

A **workspace reference** is a logical, authenticated pointer to a MoonMind or
Omnigent workspace. It is resolved by MoonMind into a mount source visible to
the selected daemon.

### 4.6 Image cache

The **image cache** is the selected daemon's content and image store. Its scope
is the daemon, not a job, workflow, session, repository, or toolchain.

---

## 5. Architectural model

```text
Omnigent session        Managed session        Dashboard / API client
       |                       |                         |
       +-----------------------+-------------------------+
                               |
                     MCP / HTTP container tools
                               |
                  MoonMind API: DockerBackendService
                  - authentication and authorization
                  - schema and policy validation
                  - workspace resolution
                  - durable job submission
                  - status, logs, artifacts, cancellation
                               |
                         Temporal execution
                               |
                    DockerBackendAdapter
                               |
                 configured Docker host or proxy
                               |
                    persistent daemon image store
                               |
                  bounded workload containers
```

### 5.1 API ownership

The MoonMind API server owns the public service contract and constructs the
`DockerBackendService` from deployment settings.

The API server is responsible for:

- exposing MCP and HTTP tools,
- authenticating the principal,
- binding the request to a MoonMind or Omnigent session when present,
- authorizing images, workspaces, credentials, mounts, and resources,
- creating the durable container-job record,
- starting the Temporal execution,
- returning the job identifier,
- serving status, logs, artifact refs, and cancellation.

The API service must not keep a multi-hour build alive as an in-memory request
or background task.

### 5.2 Temporal execution

Temporal owns the durable execution interval after submission.

A container-job workflow or activity sequence is responsible for:

1. resolving the configured backend profile,
2. acquiring an image pull lock,
3. ensuring the requested image is available,
4. creating the workload container,
5. streaming or periodically publishing bounded logs,
6. enforcing timeout and cancellation,
7. collecting declared outputs,
8. removing job-scoped runtime objects,
9. recording terminal status.

The current physical executor may run on the `agent_runtime` fleet. A future
fleet change does not alter the job contract or make the service a worker pool.

### 5.3 Backend adapter

`DockerBackendAdapter` is the narrow implementation boundary between MoonMind's
job model and a Docker Engine endpoint.

Conceptually it provides:

```python
class DockerBackendAdapter(Protocol):
    async def inspect_image(self, image: str) -> ImageObservation: ...
    async def pull_image(self, request: ImagePullRequest) -> ImageObservation: ...
    async def create_job_container(self, request: ResolvedContainerJob) -> str: ...
    async def start_job_container(self, container_id: str) -> None: ...
    async def wait_job_container(self, container_id: str) -> ContainerExit: ...
    async def read_logs(self, container_id: str, cursor: str | None) -> LogChunk: ...
    async def stop_job_container(self, container_id: str, grace_seconds: int) -> None: ...
    async def remove_job_container(self, container_id: str) -> None: ...
```

The adapter receives a resolved, policy-checked job. It does not decide caller
authorization or accept arbitrary workspace paths.

### 5.4 Selected daemon

The selected daemon provides:

- the shared image and layer cache,
- container lifecycle,
- job-scoped networks and volumes,
- runtime isolation supplied by Docker,
- daemon-visible workspace mounts.

The daemon's storage lifetime is deployment-owned. It is not created or deleted
with an agent session or workflow.

---

## 6. Declarative service profile

The canonical desired-state shape is:

```yaml
kind: DockerBackendService
apiVersion: moonmind.io/v1alpha1
metadata:
  name: default
spec:
  enabled: true

  ownership:
    component: moonmind-api
    durableExecutor: temporal

  publicSurface:
    mcp:
      enabled: true
      tools:
        - container.submit
        - container.status
        - container.logs
        - container.artifacts
        - container.cancel
    http:
      enabled: true

  backendSelection:
    defaultBackendRef: system
    callerMaySelectBackend: false

  backends:
    system:
      kind: docker-engine
      endpoint:
        source: system-docker-host
        valueFromEnvironment: SYSTEM_DOCKER_HOST
      transportSecurity:
        rawSocketExposedToAgents: false
        apiUsesDockerProxy: true
      workspaceResolverRef: local-compose
      imageCache:
        scope: daemon
        pullPolicy: if-missing
        removeOnJobEnd: false

  workspaceResolvers:
    local-compose:
      acceptedKinds:
        - moonmind-run
        - moonmind-managed-session
        - omnigent-session
      targetPath: /workspace
      arbitraryCallerPaths: false
      mappings:
        - kind: moonmind-run
          logicalRoot: /work/agent_jobs
          daemonSource:
            type: docker-volume
            name: agent_workspaces
        - kind: omnigent-session
          logicalRoot: /workspaces
          daemonSource:
            type: host-path-map
            hostRootFromEnvironment: MOONMIND_OMNIGENT_WORKSPACE_HOST_ROOT

  defaults:
    networkMode: none
    pullPolicy: if-missing
    timeoutSeconds: 3600
    stopGraceSeconds: 30
    resources:
      cpu: "2"
      memory: 4g
      shmSize: 1g

  limits:
    maxTimeoutSeconds: 14400
    maxConcurrentJobs: 4
    maxCpu: "8"
    maxMemory: 32g
    maxShmSize: 8g

  security:
    privileged: false
    capDrop: [ALL]
    noNewPrivileges: true
    allowHostNetwork: false
    allowHostPid: false
    allowHostIpc: false
    allowDevices: false
    allowDockerSocketMount: false
    allowHostRootMount: false
    rawDockerCliMcpTool: false

  cleanup:
    removeContainerOnTerminal: true
    removeJobNetworksOnTerminal: true
    removeJobScratchVolumesOnTerminal: true
    removeImagesOnTerminal: false

  retention:
    images:
      mode: disk-pressure-lru
      minimumAgeDays: 30
      highWatermarkPercent: 85
      lowWatermarkPercent: 70
      protectRunningJobs: true
      protectPinnedDigests: true
```

The YAML is normative at the semantic level. Exact Pydantic model and environment
variable names may differ while the implementation is being introduced, but
the declared responsibilities and invariants must remain intact.

---

## 7. Backend configuration

### 7.1 Current required backend

The current implementation target is one backend:

```yaml
name: system
kind: docker-engine
endpoint: ${SYSTEM_DOCKER_HOST}
```

For the local Compose deployment, `SYSTEM_DOCKER_HOST` normally points at the
existing Docker socket proxy, which in turn reaches the host Docker daemon.

This gives immediate reuse of images already present on the host daemon and
causes newly pulled images to remain available across MoonMind and Omnigent
workflows.

### 7.2 Backend-neutral code boundary

The service must not hardcode `tcp://docker-proxy:2375` into the job model.
The configured endpoint is supplied to `DockerBackendAdapter` at service
construction.

A future deployment may point the same `docker-engine` adapter at another
persistent daemon. That future option is configuration work, not a second public
API or a required service today.

### 7.3 Backend selection policy

Callers do not submit a Docker endpoint.

The default backend is selected by deployment configuration. If named backend
selection is introduced later, it must be policy-controlled and expressed as a
stable `backendRef`, never as a caller-provided socket path or URL.

### 7.4 No cache federation promise

Two Docker daemons have separate image stores. If a deployment changes the
selected endpoint, the new daemon may need to acquire images once.

The service guarantees reuse among jobs sent to the same selected daemon. It
does not promise automatic image synchronization between daemons.

---

## 8. Public container-job tools

The agent-facing tools are asynchronous.

### 8.1 `container.submit`

Validates and submits one bounded container job, then returns immediately.

Example result:

```json
{
  "jobId": "container-job:01J...",
  "status": "queued",
  "image": "mcr.microsoft.com/dotnet/sdk:8.0",
  "submittedAt": "2026-07-13T18:00:00Z"
}
```

### 8.2 `container.status`

Returns current state, bounded progress metadata, timestamps, exit status, and
artifact availability.

### 8.3 `container.logs`

Returns a bounded log chunk using a cursor. It must not return unbounded terminal
history in one response.

### 8.4 `container.artifacts`

Returns declared and collected output references after or during execution when
available.

### 8.5 `container.cancel`

Requests cancellation. Temporal remains authoritative for durable cancellation
and cleanup.

### 8.6 Raw Docker CLI

`container.run_docker` is not part of the normal MCP surface. It may remain an
internal, deployment-gated escape hatch for trusted control-plane operations.
Agents should use structured container jobs for normal build and test work.

---

## 9. Container job request

The declarative request shape is:

```yaml
image: ghcr.io/example/toolchain@sha256:...
workspaceRef:
  kind: omnigent-session
  sessionId: conv_123
workdir: /workspace
entrypoint: []
command: ["bash", "-lc", "./scripts/test.sh"]
environment:
  CI: "1"
registryCredentialRef: null
pullPolicy: if-missing
networkMode: none
resources:
  cpu: "4"
  memory: 8g
  shmSize: 2g
timeoutSeconds: 3600
cacheMounts: []
outputs: {}
metadata: {}
```

### 9.1 Required fields

- `image`
- `workspaceRef`
- `command`

### 9.2 Image reference

Arbitrary permitted images may be selected at request time. Digest pinning is
strongly preferred and may be required by deployment policy for private or
high-impact images.

### 9.3 Command representation

Commands are arrays. Shell interpretation occurs only when the request
explicitly invokes a shell such as `bash -lc`.

### 9.4 Environment

Environment values are policy-checked. Secrets are passed by reference and
resolved at the controlled execution boundary; raw secret values are not stored
in workflow history or job metadata.

### 9.5 Backend fields

The normal request does not include `dockerHost`, socket paths, daemon URLs, or
arbitrary host mount sources.

---

## 10. Job lifecycle

The canonical states are:

```text
queued
resolving
pulling
starting
running
collecting
succeeded | failed | timed_out | canceled
```

A job may also enter `cleanup_failed` after its primary terminal outcome when
best-effort cleanup cannot fully remove job-scoped objects. The primary outcome
must remain visible.

Each job records:

- `job_id`,
- authenticated owner principal,
- source system and source session,
- workflow and step association when present,
- selected backend reference,
- requested image reference,
- resolved image digest,
- container identifier,
- timestamps,
- timeout and cancellation reason,
- exit code,
- log and artifact refs,
- cleanup observation.

---

## 11. Workspace resolution

### 11.1 Logical references only

A caller identifies its workspace logically. MoonMind resolves and authorizes
that reference.

Accepted examples:

```yaml
workspaceRef: { kind: moonmind-run, agentRunId: "mm:..." }
workspaceRef: { kind: moonmind-managed-session, sessionId: "session:..." }
workspaceRef: { kind: omnigent-session, sessionId: "conv_..." }
```

### 11.2 Daemon-visible source

Docker bind mounts are resolved by the daemon. The resolver therefore produces a
source that exists in the selected daemon's namespace.

For the system Docker backend this may be:

- a Docker-managed named volume plus an authorized subpath strategy, or
- an operator-declared host root mapped from the logical container path.

The resolver must probe workspace visibility before acquiring a very large
image.

### 11.3 Mount target

The default target inside workload containers is `/workspace`, independent of
the source runtime's original absolute path.

This removes the requirement that an Omnigent runner, managed session, API
container, and Docker daemon all expose the checkout under the same path.

### 11.4 Read/write policy

The request declares whether the job needs a read-only or read-write workspace.
Repository build and test jobs normally receive read-write access to their own
workspace and no access to any other workspace.

### 11.5 Artifact path

Artifacts are mounted separately at `/artifacts` or collected from declared
workspace-relative output paths. Artifact publication is controlled by
MoonMind, not by arbitrary host bind paths supplied by the agent.

---

## 12. Image acquisition and reuse

### 12.1 Pull policy

Supported policies are:

- `if-missing` — default,
- `always` — explicitly refresh the tag,
- `never` — fail when the image is absent.

Digest references with `if-missing` provide the strongest reproducibility and
cache behavior.

### 12.2 Cross-workflow cache

The service inspects the selected daemon before pulling. If the requested image
or digest is present, the job proceeds without registry acquisition.

The cache survives:

- job completion,
- agent-session completion,
- workflow completion,
- Omnigent runner replacement,
- API request completion.

It survives as long as the selected daemon's storage survives.

### 12.3 Pull locking

MoonMind maintains a lock keyed by normalized registry reference or digest so
concurrent jobs do not initiate equivalent large pulls independently.

Waiting jobs report `status=pulling` with metadata indicating that acquisition
is shared or already in progress.

### 12.4 No job-scoped image deletion

Job cleanup must not call global image prune and must not remove the job image
merely because one job has completed.

### 12.5 Deployment-level image retention

Image eviction is a separate maintenance operation based on:

- disk pressure,
- last successful use,
- image size,
- protected digests,
- active container references,
- operator pins,
- minimum retention age.

On the system Docker backend, MoonMind must only remove images it can prove are
managed by the container-job subsystem or explicitly approved for eviction. The
default posture is conservative retention.

---

## 13. Security model

### 13.1 Agents do not receive Docker authority

Neither `omnigent-host`, Omnigent runner subprocesses, nor managed session
containers receive:

- `/var/run/docker.sock`,
- the Docker proxy endpoint,
- `DOCKER_HOST` pointing at the selected daemon,
- credentials that permit arbitrary direct Docker API access.

### 13.2 Structured execution

MoonMind constructs the container request and enforces:

- `--privileged=false`,
- `--cap-drop=ALL`,
- `no-new-privileges`,
- no host PID, IPC, or network namespace,
- no arbitrary devices,
- no Docker socket mount,
- no host root mount,
- approved workspace and artifact mounts only,
- resource and timeout ceilings,
- bounded network mode.

### 13.3 Host backend blast radius

The selected system daemon also owns MoonMind and operator containers. The
broker boundary is therefore mandatory: an agent cannot submit raw Docker API
requests.

Cleanup is label- and ownership-based. It must never use broad host-wide removal
commands such as unscoped `docker system prune -a`.

### 13.4 Trust domains

A daemon-level image cache is shared by every job routed to that daemon. Private
image authorization must be checked on every run, not merely when an image is
first pulled.

---

## 14. Registry authentication

Private registry credentials are supplied as references:

```yaml
registryCredentialRef: secret:ghcr-read-moonladder
```

At execution time MoonMind:

1. resolves the credential,
2. materializes an ephemeral Docker client config,
3. pulls or verifies the requested image,
4. records only non-secret diagnostics,
5. destroys the temporary config.

The job record may retain:

- registry host,
- authenticated versus anonymous acquisition,
- resolved digest,
- credential reference identifier,
- manifest and pull outcome.

It must not retain the credential value.

---

## 15. Resource, network, and mount policy

### 15.1 Resources

Every job has explicit defaults and deployment ceilings for:

- CPU,
- memory,
- shared memory,
- wall-clock timeout,
- concurrent job count.

### 15.2 Network

`none` is the preferred default. `bridge` is permitted when builds or tests need
network access and deployment policy allows it.

Host networking is denied.

### 15.3 Cache mounts

Named build caches may be requested through logical cache keys:

```yaml
cacheMounts:
  - key: nuget-v1
    target: /root/.nuget/packages
  - key: unreal-ccache-ue58
    target: /home/ue4/.ccache
```

MoonMind maps keys to deployment-owned named volumes. Agents do not choose raw
host paths or arbitrary existing volume names.

### 15.4 Container names and labels

MoonMind generates names and applies labels such as:

```text
moonmind.kind=container-job
moonmind.container_job_id=<job_id>
moonmind.owner_principal=<bounded-owner-id>
moonmind.workflow_id=<workflow_id-if-present>
moonmind.session_id=<session-id-if-present>
moonmind.image_digest=<resolved-digest>
moonmind.expires_at=<cleanup-deadline>
```

Caller-provided labels cannot override MoonMind ownership labels.

---

## 16. Logs and artifacts

### 16.1 Logs

Stdout and stderr are captured separately, redacted, bounded in memory, and
published to durable artifact storage.

`container.logs` reads from the durable or streaming log surface using cursors.
The Docker daemon's local log files are not durable truth.

### 16.2 Declared outputs

The request may declare outputs relative to `/artifacts` or the workspace:

```yaml
outputs:
  junit: test-results/results.xml
  summary: artifacts/summary.json
```

MoonMind validates that collected paths remain inside authorized roots and do
not escape through symlinks.

### 16.3 Terminal result

The terminal job result includes:

- status,
- exit code,
- resolved image digest,
- duration,
- log refs,
- declared output refs,
- diagnostics ref,
- cleanup observation.

---

## 17. Cleanup and retention

### 17.1 Job cleanup

On every terminal path MoonMind best-effort removes:

- the workload container,
- job-created networks,
- job-created scratch volumes,
- temporary Docker configs,
- transient credential material.

It does not remove:

- the image,
- shared package/build caches,
- the source workspace,
- durable artifacts.

### 17.2 Orphan cleanup

A recurring reconciler finds containers with MoonMind job labels whose durable
job is terminal or absent and removes those job-scoped objects after a grace
period.

### 17.3 Image maintenance

Image maintenance is daemon-scoped and independent of job reconciliation.

For the system backend, destructive image cleanup is disabled by default until
MoonMind has reliable ownership and last-use evidence. Operator-driven Docker
disk cleanup remains separate from job cleanup.

---

## 18. Omnigent integration

### 18.1 Tool transport

Omnigent connects to MoonMind's Streamable HTTP MCP endpoint and discovers the
`container.*` tools.

Example operator-owned MCP declaration:

```yaml
name: moonmind-containers
transport: http
url: http://api:8000/mcp
headers:
  Authorization: Bearer ${MOONMIND_MCP_TOKEN}
timeout: 60
```

The credential is provisioned at an operator-controlled registration boundary;
a tenant-uploaded bundle must not expand arbitrary server environment values.

### 18.2 Session association

When an Omnigent runner calls `container.submit`, MoonMind derives or validates:

- Omnigent user identity,
- conversation/session identity,
- host identity when relevant,
- authorized workspace reference,
- source tool-call correlation.

The caller does not provide a host filesystem root.

### 18.3 Runner requirements

`omnigent-host` and its runner subprocesses do not need:

- the Docker CLI,
- `DOCKER_HOST`,
- a mounted Docker socket,
- registry credentials for the backend daemon.

They need only authenticated network access to the MoonMind MCP endpoint.

### 18.4 Long-running jobs

The Omnigent agent submits, polls status or reads logs, and may cancel. It does
not hold one MCP request open for the duration of an Unreal build.

---

## 19. Managed-session integration

Managed Codex, Claude, and future session runtimes use the same API-owned tools.

The session runtime may inspect files and determine which image and command are
needed, but MoonMind performs the launch.

The target managed-session image therefore does not require a Docker daemon or
host Docker authority. A Docker CLI is optional local tooling rather than the
canonical workload interface.

Session reset and container-job lifecycle remain independent. Association
metadata may connect a job to a source turn without merging their identities.

---

## 20. Examples

### 20.1 .NET tests

```yaml
image: mcr.microsoft.com/dotnet/sdk:8.0@sha256:...
workspaceRef: { kind: omnigent-session, sessionId: conv_123 }
workdir: /workspace
command:
  - dotnet
  - test
  - --logger
  - trx;LogFileName=/artifacts/test-results.trx
networkMode: bridge
cacheMounts:
  - { key: nuget-v1, target: /root/.nuget/packages }
resources: { cpu: "4", memory: 8g }
timeoutSeconds: 3600
outputs:
  testResults: /artifacts/test-results.trx
```

### 20.2 Unreal automation

```yaml
image: ghcr.io/moonladderstudios/tactics-ue-base@sha256:...
workspaceRef: { kind: omnigent-session, sessionId: conv_456 }
workdir: /workspace
command: ["bash", "-lc", "./tools/run_unreal_validation.sh"]
networkMode: none
cacheMounts:
  - { key: unreal-ccache-ue58, target: /home/ue4/.ccache }
  - { key: unreal-ubt-ue58, target: /home/ue4/.config/Epic/UnrealBuildTool }
  - { key: unreal-ddc-ue58, target: /home/ue4/.cache/UnrealEngine }
resources: { cpu: "8", memory: 32g, shmSize: 4g }
timeoutSeconds: 14400
```

The backend contains no Unreal-specific branch. The image and repository script
define the workload.

---

## 21. Observability

Every job emits structured observations including:

```text
job_id
owner_principal
source_system
source_session_id
workflow_id
step_id
backend_ref
backend_kind
image_requested
image_digest
image_present_at_start
image_cache_hit
pull_waited_on_existing_lock
pull_duration_seconds
container_id
status
exit_code
started_at
completed_at
duration_seconds
workspace_ref_kind
log_refs
artifact_refs
cleanup_status
```

Secrets, raw registry auth payloads, and unbounded logs are excluded.

Metrics should distinguish:

- image cache hit,
- registry or manifest check,
- image pull,
- container startup,
- workload runtime,
- artifact collection,
- cleanup.

---

## 22. Backend portability

### 22.1 Current target

The current target is `docker-engine` against the existing system Docker host or
proxy.

### 22.2 Future endpoint switch

A future deployment may configure another persistent Docker Engine endpoint.
The same adapter, request schema, job workflow, and MCP tools remain in place.

No dedicated-daemon Compose service, lifecycle manager, or migration mechanism
is required now.

### 22.3 Future non-Docker backends

A Kubernetes Job or another container executor would require another adapter
kind. The public container-job contract should remain mostly stable, but this
document does not require or design that implementation.

---

## 23. Validation rules

The service fails closed unless all applicable rules pass:

1. The configured backend exists and uses a supported kind.
2. The backend endpoint comes from deployment configuration, not request input.
3. The Docker daemon is reachable before the service reports ready.
4. The caller is authenticated and authorized for container jobs.
5. The workspace reference resolves to an authorized workspace.
6. The daemon-visible workspace probe succeeds before a missing large image is
   pulled.
7. The mount plan contains no arbitrary host path from the caller.
8. The Docker socket, Docker data root, and host root cannot be mounted.
9. Privileged mode, host namespaces, and devices are denied unless a separate
   operator-owned contract explicitly permits them.
10. Requested resources and timeout fit deployment ceilings.
11. Registry credentials are references resolved only at execution time.
12. Private-image authorization is checked on every run.
13. Job cleanup does not remove shared images.
14. Image maintenance does not remove images used by active containers.
15. MoonMind ownership labels cannot be overridden.

---

## 24. Migration from per-session Docker-in-Docker

The transition is declarative and can be staged:

1. Introduce `DockerBackendService` settings and the system Docker adapter.
2. Add asynchronous MCP tools for submit, status, logs, artifacts, and cancel.
3. Route Omnigent container requests through the service.
4. Route MoonMind managed-session repository container work through the same
   service.
5. Keep the existing per-session sidecar implementation available only as a
   temporary compatibility path while callers migrate.
6. Stop enabling sidecar Docker by default.
7. Remove session-owned graph and socket lifecycle from the desired-state
   profile after compatibility use reaches zero.

Migration must not delete cached host images or existing workflow artifacts.

---

## 25. Stable design rules

1. Agents request container jobs through MoonMind; they do not receive Docker
   authority.
2. The Docker Backend Service is owned by the MoonMind API subsystem.
3. Temporal owns long-running job durability, timeout, cancellation, and retry
   boundaries.
4. One deployment-selected daemon supplies the shared cross-workflow image
   cache.
5. Images are arbitrary and acquired on demand subject to policy.
6. Image lifetime is deployment-scoped, not workflow- or session-scoped.
7. Job cleanup removes job objects but not shared images.
8. Workspaces are logical, authenticated references resolved by MoonMind.
9. The structured container contract is the normal public surface.
10. The default backend is the existing system Docker host or proxy.
11. A future Docker endpoint switch is configuration behind the same adapter,
    not a new agent integration.
12. The core remains workload-agnostic.
13. Artifacts, job records, and bounded observations remain durable truth.

---

## 26. Final declarative contract

```yaml
dockerBackendService:
  owner: moonmind-api
  durability: temporal

  callers:
    - moonmind-managed-session
    - omnigent-session
    - workflow-tool
    - authenticated-api-client

  tools:
    asynchronous:
      - container.submit
      - container.status
      - container.logs
      - container.artifacts
      - container.cancel
    rawDockerCliExposedToAgents: false

  backend:
    selectedBy: deployment
    currentKind: docker-engine
    currentEndpoint: system-docker-host
    futureEndpointSwitchAllowed: true
    dedicatedMoonMindDaemonRequiredNow: false

  images:
    selection: arbitrary-permitted-reference
    acquisition: on-demand
    defaultPullPolicy: if-missing
    cacheScope: selected-daemon
    reusableAcrossWorkflows: true
    removeOnJobEnd: false

  workspaces:
    callerProvidesLogicalRef: true
    callerProvidesHostPath: false
    resolvedByMoonMind: true
    visibilityProbeBeforeLargePull: true

  security:
    dockerSocketInAgent: false
    dockerHostInAgent: false
    privilegedJobs: false
    arbitraryHostMounts: false
    privateImageAuthorizationPerRun: true

  cleanup:
    containers: job-scoped
    scratch: job-scoped
    images: deployment-retention
```

Mental model:

- Omnigent and managed agents ask MoonMind to run a container job.
- The API owns the request, identity, policy, and durable job surface.
- Temporal owns the long-running execution interval.
- The configured Docker daemon runs the container and keeps downloaded images.
- Later workflows reuse those images automatically.
- No dedicated Unreal pool, daemon pool, or dedicated MoonMind Docker daemon is
  required.
