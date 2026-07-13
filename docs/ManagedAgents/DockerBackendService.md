# Docker Backend Service

- **Status:** Desired state
- **Owners:** MoonMind Platform
- **Last updated:** 2026-07-13
- **Document class:** Canonical declarative design

**Related:**

- [`ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md)
- [`CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`../ExternalAgents/ModelContextProtocol.md`](../ExternalAgents/ModelContextProtocol.md)
- [`../Omnigent/CombinedStackValidationAndRollback.md`](../Omnigent/CombinedStackValidationAndRollback.md)
- [`../Workflows/SkillAndPlanContracts.md`](../Workflows/SkillAndPlanContracts.md)

---

## 1. Purpose and authority

This document defines MoonMind's desired-state **Docker Backend Service**. The
service is part of the MoonMind API subsystem. It accepts governed container-job
requests, records durable job identity, resolves caller-authorized workspaces,
and dispatches bounded container execution against one deployment-selected
Docker backend.

The service is the canonical route for containerized repository work requested
by:

- Omnigent-hosted sessions;
- MoonMind managed sessions;
- workflow tools and skills;
- the dashboard and HTTP API;
- authenticated MCP clients.

Examples include .NET tests, Unreal automation tests, JavaScript toolchains,
linters, integration tests, and any other permitted image and command selected
at request time.

The requesting runtime never receives the Docker socket, a Docker API endpoint,
`DOCKER_HOST`, or raw daemon authority. It asks MoonMind to execute a typed job.

The per-session Docker-in-Docker topology is not a supported desired state or
compatibility path. Container jobs are API-governed workloads with identity
separate from managed-session continuity.

The selected daemon owns a persistent image store. An image acquired for one job
remains reusable by later jobs and later workflows until deployment-level image
retention removes it. Image lifetime is independent of workflow and session
lifetime.

---

## 2. Core decisions

1. **MoonMind owns container launches.** Agents request typed container jobs;
   they do not control a Docker daemon.
2. **The service is API-owned.** The API authenticates callers, authorizes the
   request, resolves logical workspaces, creates durable job identity, and
   exposes status and control surfaces.
3. **Temporal owns the execution interval.** Long-running jobs are asynchronous;
   submission returns a stable job identifier instead of holding an MCP or HTTP
   request open for a build.
4. **One configured daemon is the current backend.** The required implementation
   uses the existing system Docker daemon through MoonMind's configured Docker
   host or socket proxy.
5. **Backend selection is deployment-owned.** Callers cannot provide a Docker
   endpoint. The public job contract is backend-neutral.
6. **Images are arbitrary within policy and acquired on demand.** The first job
   requiring a missing permitted image pulls it; later workflows reuse it.
7. **Job cleanup never removes shared images.** Container, scratch, network, and
   temporary credential lifecycles are job-scoped. Image retention is separate.
8. **Workspaces are logical references.** Callers do not provide daemon-visible
   host paths. MoonMind resolves an authorized workspace into a mount plan.
9. **The structured container contract is the normal public interface.** Docker
   CLI execution remains an explicitly gated internal escape hatch.
10. **The core is workload-agnostic.** No backend branch exists for Unreal,
    .NET, Unity, Node, or another toolchain.
11. **The endpoint abstraction is narrow.** A future deployment may change the
    configured Docker endpoint without changing MCP tools or job schemas.
12. **No dedicated MoonMind Docker daemon is required today.** This design does
    not add a daemon service, daemon pool, or specialized worker pool.
13. **Artifacts and bounded job records are durable truth.** Daemon-local state
    is an execution cache, not the system of record.

---

## 3. Scope and non-goals

### 3.1 In scope

This document defines:

- API ownership and service boundaries;
- MCP and HTTP container-job tools;
- asynchronous lifecycle and terminal states;
- declarative backend configuration;
- the current system-Docker backend;
- logical workspace and artifact resolution;
- arbitrary on-demand image acquisition;
- cross-workflow image reuse;
- private-registry authorization;
- mount, network, resource, and cleanup policy;
- Omnigent and managed-session integration;
- backend portability without implementing a second backend.

### 3.2 Out of scope

This document does not define:

- a Docker daemon pool;
- a dedicated MoonMind workload daemon;
- an Unreal-specific or .NET-specific worker pool;
- direct Docker access from an agent runtime;
- concurrent sharing of one writable Docker data root by multiple daemons;
- Kubernetes orchestration;
- a generic shell service;
- unowned detached services without a TTL;
- migration of cached images between Docker daemons.

---

## 4. Terminology

### 4.1 Docker Backend Service

The API-owned subsystem that validates, records, dispatches, observes, and
controls container jobs. It is a MoonMind service boundary, not necessarily a
separate deployed container.

### 4.2 Container job

One bounded, owned execution request against an image. It has durable identity,
explicit inputs, a finite timeout, and one terminal outcome. It is not a managed
session or `MoonMind.AgentRun` unless the image itself hosts a true agent runtime.

### 4.3 Docker backend

Deployment configuration identifying the Docker endpoint used by the service.
The current backend kind is `docker-engine`.

### 4.4 System Docker backend

The existing daemon used by the MoonMind installation, reached through
`SYSTEM_DOCKER_HOST` or the existing Docker socket proxy.

### 4.5 Workspace reference

A logical, authenticated reference to a MoonMind or Omnigent workspace. MoonMind
resolves it into a daemon-visible mount source.

### 4.6 Image cache

The selected daemon's image store. Its scope is the backend daemon, not an
individual job, session, or workflow.

---

## 5. Architectural model

```text
Omnigent / managed session / workflow / API client
                        |
                        | MCP or HTTP container job request
                        v
               MoonMind API subsystem
               Docker Backend Service
          authn | authz | workspace resolution
                        |
                        | durable job command
                        v
                    Temporal
         timeout | cancellation | retry | evidence
                        |
                        v
             configured Docker backend
              existing system dockerd
                 |             |
                 |             +-- persistent image cache
                 +-- ephemeral owned workload container
```

The API owns authentication, authorization, validation, workspace resolution,
idempotent submission, and public status/control surfaces. It does not execute a
multi-hour build inside the HTTP request handler.

Temporal owns durable state transitions, timeout, cancellation, retry policy,
artifact publication, and cleanup coordination.

A trusted MoonMind worker receives the Docker endpoint and executes the resolved
launch plan. The requesting agent never receives that endpoint.

Managed sessions own agent continuity—turns, threads, epochs, and session
artifacts. Container jobs remain adjacent workload identity.

---

## 6. Declarative backend configuration

One backend is selected per deployment:

```yaml
dockerBackendService:
  enabled: true
  defaultBackendRef: system

  backends:
    system:
      kind: docker-engine
      endpointFrom: SYSTEM_DOCKER_HOST
      workspaceResolver: system-docker

  policy:
    mode: unrestricted
    allowRawDockerCli: false
```

Rules:

- unsupported backend kinds are startup errors;
- empty or unreachable endpoints fail readiness;
- callers cannot supply endpoint URLs, socket paths, or TLS material;
- the API and job schema remain stable if an operator changes the configured
  Docker Engine endpoint later;
- implementing another endpoint is optional and is not part of this design.

The abstraction is deliberately small:

```python
class DockerBackendAdapter(Protocol):
    async def inspect_image(self, image: str) -> ImageObservation: ...
    async def pull_image(
        self,
        image: str,
        auth: RegistryAuth | None,
    ) -> ImageObservation: ...
    async def run(self, request: ResolvedContainerJob) -> ContainerExecution: ...
    async def stop(self, container_id: str, grace_seconds: int) -> None: ...
    async def remove(self, container_id: str) -> None: ...
```

This is an endpoint boundary, not a fleet, scheduler, or daemon lifecycle
abstraction.

---

## 7. Agent-facing asynchronous tools

The normal MCP and HTTP surface is:

```text
container.submit
container.status
container.logs
container.artifacts
container.cancel
```

### 7.1 `container.submit`

Validates and authorizes a request, creates a durable job, and returns
immediately:

```json
{
  "jobId": "container-job:01J...",
  "status": "queued",
  "image": "mcr.microsoft.com/dotnet/sdk:8.0@sha256:..."
}
```

### 7.2 `container.status`

Returns a bounded snapshot such as `queued`, `resolving_workspace`,
`acquiring_image`, `starting`, `running`, `succeeded`, `failed`, `timed_out`,
`canceled`, or `cleanup_failed`.

### 7.3 `container.logs`

Returns bounded log pages or durable log references, never an unbounded daemon
stream in one response.

### 7.4 `container.artifacts`

Returns collected output references and publication diagnostics.

### 7.5 `container.cancel`

Requests idempotent cancellation. Temporal stops the owned container, captures
available evidence, and completes cleanup.

### 7.6 Raw Docker CLI

Docker CLI execution remains an explicitly gated internal escape hatch.
`container.run_docker` is not a normal Omnigent or managed-session MCP tool and
never grants its caller direct daemon access.

---

## 8. Container-job request contract

A backend-neutral request is shaped approximately as follows:

```json
{
  "image": "registry.example/image@sha256:...",
  "workspaceRef": {
    "kind": "omnigent-session",
    "sessionId": "conv_..."
  },
  "workdir": "/workspace",
  "command": ["program", "arg1"],
  "entrypoint": [],
  "environment": {},
  "cacheMounts": [],
  "networkMode": "none",
  "resources": {
    "cpu": "4",
    "memory": "8g",
    "shmSize": "1g"
  },
  "timeoutSeconds": 3600,
  "pullPolicy": "if-missing",
  "registryCredentialRef": null,
  "outputs": {}
}
```

The caller does not provide:

- a Docker endpoint;
- a daemon-visible host source path;
- Docker socket or data-root mounts;
- privileged mode, host namespaces, or devices;
- MoonMind ownership-label overrides.

MoonMind enriches every request with owner, workflow, run, step, session,
expiration, and idempotency metadata.

---

## 9. Durable job identity and state

A job has one stable `job_id`. An idempotency key is derived from caller identity,
workflow/run/step identity where present, and a caller request ID.

The durable job record stores request intent and compact observations; it does
not store registry secrets or unbounded logs.

Terminal states are:

```text
succeeded
failed
timed_out
canceled
```

Cleanup or artifact-publication failures are recorded separately and do not
silently rewrite primary workload success.

---

## 10. Workspace resolution

Workspaces are logical references, not raw host paths. Supported kinds include:

```text
moonmind-run
moonmind-session
omnigent-session
artifact-workspace
```

Resolution steps are:

1. authenticate the principal;
2. authorize access to the referenced run or session;
3. resolve the canonical workspace record;
4. map it to a source visible to the configured Docker daemon;
5. verify containment and deny symlink escapes;
6. create the destination mount and workdir;
7. run a visibility probe before image acquisition.

A failed probe stops the job before expensive image acquisition.

For host Docker, MoonMind maps a logical container path to the physical host bind
root or an approved named volume. The agent does not perform this translation.

Artifacts use a separate approved mount or spool. The workload may not select an
arbitrary artifact destination.

---

## 11. Image acquisition and cross-workflow reuse

### 11.1 Pull policy

Supported policy is:

```text
if-missing
always
never
```

`if-missing` is the default. Image presence is checked in the selected daemon,
not in the caller container.

### 11.2 Digest behavior

Digests are preferred for reproducibility. When a tag is permitted, the job
records the resolved digest. Subsequent jobs can use the same cached layers.

### 11.3 Per-image pull lock

MoonMind serializes acquisition by normalized registry/repository/reference so
concurrent requests for a missing 73 GB image do not initiate redundant pulls.
Waiters re-inspect after the owner finishes.

### 11.4 Private images

Registry credentials are secret references. The service resolves them into an
ephemeral Docker config, uses them only for the approved registry operation, and
removes the config afterward.

Authorization is checked on every run, including cache hits. A private image
being present in the daemon does not grant other users permission to execute it.

### 11.5 Image lifetime

Images survive job, session, and workflow completion. Deployment-level retention
may evict unused images under disk pressure while protecting images used by
active containers and operator-pinned digests.

---

## 12. Container security and resources

Structured jobs apply:

- `--privileged=false`;
- dropped Linux capabilities;
- `no-new-privileges`;
- no host PID, IPC, user, or network namespace;
- no arbitrary devices;
- no Docker socket, data-root, or host-root mounts;
- approved workspace, artifact, scratch, and cache mounts only;
- MoonMind-owned labels;
- explicit network mode;
- bounded CPU, memory, shared memory, process count, output, and timeout.

Callers may request resources only within deployment ceilings. They may not
weaken security defaults.

Network modes exposed to normal jobs are `none` and policy-controlled `bridge`.
A job may pull an image and then run the workload with networking disabled.

---

## 13. Lifecycle and cleanup

The canonical lifecycle is:

1. authenticate and authorize;
2. validate the request;
3. create or replay idempotent job identity;
4. resolve and probe the workspace;
5. inspect or acquire the image under the pull lock;
6. construct the non-overridable launch plan;
7. create and start the labeled container;
8. capture logs and state;
9. publish outputs and diagnostics;
10. remove job-scoped runtime objects.

Cancellation and timeout stop only the owned job container and preserve available
evidence when possible.

A recurring reconciler removes only labeled objects whose durable jobs are
terminal or expired. It refuses to act when durable ownership state is
unavailable.

Job cleanup never performs global image pruning. Image maintenance is a separate
deployment operation with disk watermarks, last-used observations,
active-container protection, minimum age, operator pins, and dry-run diagnostics.
On the system backend the safe default is to retain images rather than delete
unrelated operator or MoonMind service images.

---

## 14. Observability and durable evidence

Durable metadata includes:

```text
job_id
owner_principal
source_kind
source_session_id
workflow_id
run_id
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

Secrets, registry-auth payloads, and unbounded logs are excluded.

Metrics distinguish workspace resolution, image cache hits, pull time, container
startup, workload runtime, artifact collection, and cleanup.

---

## 15. Omnigent integration

Omnigent connects to MoonMind's authenticated MCP endpoint and discovers the
`container.*` tools. The Omnigent agent, host, runner, and shell do not receive a Docker socket or `DOCKER_HOST`.

For an Omnigent job:

1. Omnigent calls `container.submit` with its logical session workspace;
2. MoonMind authenticates the caller and maps it to the session;
3. MoonMind resolves the authorized writable worktree;
4. Temporal executes the job;
5. Omnigent reads status, logs, and artifacts;
6. the agent continues after terminal evidence is available.

Credentials are supplied through an operator-approved authentication boundary.
Session-uploaded bundles do not expand arbitrary server-side environment
variables.

---

## 16. Managed-session integration

MoonMind managed sessions use the same tools and job contract as Omnigent. A
managed runtime does not need a Docker CLI to run repository tests. Runtime
skills may wrap common requests, but the substrate remains generic.

Session capability may advertise:

```yaml
capabilities:
  containerJobs:
    available: true
    transport: moonmind-mcp
    backendKind: docker-engine
```

It must not advertise a session-local `DOCKER_HOST`.

---

## 17. Workload examples

### 17.1 .NET tests

```json
{
  "image": "mcr.microsoft.com/dotnet/sdk:8.0@sha256:...",
  "workspaceRef": {"kind": "omnigent-session", "sessionId": "conv_..."},
  "workdir": "/workspace",
  "command": ["dotnet", "test", "--logger", "trx;LogFileName=/artifacts/tests.trx"],
  "cacheMounts": [{"key": "nuget-v1", "target": "/root/.nuget/packages"}],
  "networkMode": "bridge",
  "timeoutSeconds": 3600
}
```

### 17.2 Unreal automation

```json
{
  "image": "ghcr.io/example/unreal-runner@sha256:...",
  "workspaceRef": {"kind": "omnigent-session", "sessionId": "conv_..."},
  "workdir": "/workspace",
  "command": ["bash", "-lc", "./tools/run_unreal_validation.sh"],
  "cacheMounts": [
    {"key": "unreal-ccache", "target": "/home/ue4/.ccache"},
    {"key": "unreal-ubt", "target": "/home/ue4/.config/Epic/UnrealBuildTool"},
    {"key": "unreal-ddc", "target": "/home/ue4/.cache/UnrealEngine"}
  ],
  "networkMode": "none",
  "timeoutSeconds": 14400
}
```

Both use the same backend and lifecycle. Toolchain-specific logic stays in the
image and repository script, not in MoonMind's backend core. No dedicated
toolchain pool is part of this design.

---

## 18. Readiness and failure classes

The service reports ready only when configuration parses, the backend kind is
supported, the endpoint is reachable, and Temporal submission is available.

Canonical failure classes include:

```text
backend_unavailable
invalid_request
permission_denied
workspace_not_found
workspace_not_visible
image_not_found
image_pull_timeout
image_pull_auth_failed
image_platform_mismatch
resource_limit_exceeded
container_start_failed
workload_failed
workload_timed_out
artifact_publication_failed
cleanup_failed
```

Auxiliary cleanup or publication failures are surfaced separately from primary
workload outcome.

---

## 19. Validation rules

The service fails closed unless all applicable rules pass:

1. The configured backend exists and uses a supported kind.
2. The endpoint comes from deployment configuration, not request input.
3. The daemon is reachable before readiness succeeds.
4. The caller is authenticated and authorized.
5. The workspace reference resolves to an authorized workspace.
6. The visibility probe succeeds before a missing large image is pulled.
7. The mount plan contains no caller-supplied arbitrary host path.
8. Docker socket, data-root, and host-root mounts are denied.
9. Privileged mode, host namespaces, and devices are denied.
10. Resources and timeout fit deployment ceilings.
11. Registry credentials are references resolved only at execution time.
12. Private-image authorization is checked on every run, including cache hits.
13. Job cleanup does not remove shared images.
14. Image maintenance protects active containers.
15. MoonMind ownership labels cannot be overridden.
16. Managed and Omnigent sessions do not receive Docker daemon authority.

---

## 20. Backend portability

The current and required target is `docker-engine` against the existing system
Docker host or proxy.

A future deployment may configure another persistent Docker Engine endpoint.
The same adapter, schema, Temporal workflow, and MCP tools remain in place. No
dedicated-daemon Compose service, lifecycle manager, cache migration, or second
implementation is required now.

A future non-Docker executor would require a distinct adapter kind. It is not
part of this contract.

---

## 21. Stable design rules

1. Agents request container jobs through MoonMind; they do not receive Docker
   authority.
2. The Docker Backend Service is part of the MoonMind API subsystem.
3. Temporal owns long-running durability, timeout, cancellation, and cleanup.
4. One deployment-selected daemon supplies the cross-workflow image cache.
5. Images are arbitrary permitted references acquired on demand.
6. Image lifetime is deployment-scoped, not workflow- or session-scoped.
7. Job cleanup removes job objects but not shared images.
8. Workspaces are logical authenticated references resolved by MoonMind.
9. The structured container contract is the normal public surface.
10. The current backend is the existing system Docker host or proxy.
11. A future endpoint switch is configuration behind the same adapter.
12. The core remains workload-agnostic.
13. Artifacts, job records, and bounded observations remain durable truth.
14. Per-session Docker daemons and graphs are not part of the desired state.

---

## 22. Final declarative contract

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
- The API owns request identity, policy, and the durable public surface.
- Temporal owns the long-running execution interval.
- The configured system Docker daemon runs jobs and retains downloaded images.
- Later workflows reuse those images automatically.
- No dedicated toolchain pool, daemon pool, or MoonMind Docker daemon is required.
