# Docker Backend Service

- **Owners:** MoonMind Platform

**Related:**

- [`ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md)
- [`CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`../ExternalAgents/ModelContextProtocol.md`](../ExternalAgents/ModelContextProtocol.md)
- [`../Omnigent/CombinedStackValidationAndRollback.md`](../Omnigent/CombinedStackValidationAndRollback.md)
- [`../Workflows/SkillAndPlanContracts.md`](../Workflows/SkillAndPlanContracts.md)
- [`../Workflows/GpuContainerContract.md`](../Workflows/GpuContainerContract.md)

---

## 1. Purpose

The **Docker Backend Service** is part of the MoonMind API subsystem. It accepts
governed container-job requests, records durable job identity, resolves
caller-authorized workspaces, and dispatches bounded execution against one
deployment-selected Docker backend.

It is the route for containerized repository work requested by Omnigent-hosted
sessions, MoonMind managed sessions, workflow tools and skills, the dashboard and
HTTP API, and authenticated MCP clients. Examples include .NET tests, MoonMind
Python tests, Unreal automation tests, JavaScript toolchains, linters, and
integration tests.

The requesting runtime never receives the Docker socket, a Docker API endpoint,
`DOCKER_HOST`, or raw daemon authority; it asks MoonMind to execute a typed job.
This keeps daemon authority (which is effectively host root) out of agent
runtimes. Per-session Docker-in-Docker is not a supported topology.

### 1.1 Session authority

Generic Omnigent hosts receive container-job authority only when the admitted
execution requires `docker`. The session capability binds the sandbox workspace
locator, workflow, Step Execution, runtime, and host lease. Its bearer is passed
on stdin into a read-only capability file, and only the file selector crosses
host, runner, and harness environments, so the secret never appears in process
environments. A run-owned shell profile carries the non-sensitive correlation
and file selectors for login shells and the projected `moonmind` executable,
which the `docker` tool binding delivers even when `gh` is not required.

The materialized workspace attachment owns repository access mode, and
container-job capabilities bind it as `workspaceReadOnly`. The scoped API applies
the signed restriction before persistence regardless of the caller's value, and
the worker enforces it on bind and volume-subpath mounts, so a read-only
repository stays read-only inside jobs; callers can only make access stricter.
Capability schema v3 requires this claim, and older capabilities fail closed
(they need a fresh host lease or session) because they cannot say which access
policy applies. Already admitted jobs keep their recorded behavior: an omitted
`spec.workspaceReadOnly` keeps the writable workspace and exact serialized
request.

### 1.2 Readiness and image lifetime

The selected daemon owns a persistent image store. Pulled and locally
provisioned images stay reusable by later jobs and workflows until
deployment-level retention removes them; image lifetime is independent of
workflow and session lifetime.

**Service readiness and optional workload-image readiness are separate.**
Planning admits the `docker` capability from the deployment's enabled, valid
container-job backend configuration; the planning worker does not need a local
socket or `DOCKER_HOST`. The trusted container-job worker checks endpoint
reachability and daemon compatibility before serving execution. Host
attestation checks each mounted tool's read-only mount, executable, and
manifest-declared `versionProbe`, recording pinned-digest drift as advisory.

Starting MoonMind does not build, pull, or validate an image merely because a
future workflow might use it. Image work begins when an admitted job needs the
image, or when an operator explicitly invokes the same provisioner to prewarm
it. This keeps ordinary startup fast and independent of optional toolchains.

---

## 2. Core decisions

1. **MoonMind owns container launches.** Agents request typed jobs; they do not
   control a daemon.
2. **The service is API-owned.** The API authenticates, authorizes, resolves
   logical workspaces, creates durable job identity, and exposes status and
   control. User authentication follows the single `AUTH_PROVIDER` selector in
   [AuthenticationContracts.md](../Security/AuthenticationContracts.md);
   machine credentials never become browser credentials.
3. **Temporal owns long-running execution**, so pulls and builds never hold an
   MCP or HTTP request open.
4. **One deployment-selected daemon is the backend**: the system Docker daemon
   through the configured Docker host or socket proxy. Callers cannot provide an
   endpoint, and the job contract is backend-neutral, so switching endpoints is
   configuration behind the same adapter. No dedicated daemon, daemon pool, or
   specialized worker pool is needed.
5. **Images are acquired on demand and reused.** Registry images are pulled by
   the first job that needs them. Local builds are allowed only for named,
   deployment-owned recipes, reused while their build key matches and their
   refresh deadline has not expired, and coalesced under a per-source,
   per-build-key lock.
6. **Job cleanup never removes shared images.** Container, scratch, network, and
   temporary credential lifecycles are job-scoped; image retention is separate.
7. **Workspaces are logical references.** Callers never provide host paths. A
   locator never resolves to the entire workspace store, and retained
   managed-session launch paths identify children of their configured root.
8. **The core is workload-agnostic.** Toolchain-specific commands and recipes
   live in deployment configuration or thin helpers, not backend branches.
9. **Artifacts and bounded job records are durable truth.** Daemon-local state
   is an execution cache.

Out of scope: a public arbitrary-image build API, caller-supplied build inputs
or host paths, startup image probes, toolchain-specific worker pools, direct
Docker access from agents, multiple daemons sharing one data root, Kubernetes
orchestration, a generic shell service, unowned detached services without a TTL,
and migrating cached images between daemons.

---

## 3. Terminology

- **Container job**: One bounded, owned execution against an image, with durable
  identity, explicit inputs, a finite timeout, and one terminal outcome. It is
  not a managed session or `MoonMind.AgentRun` unless the image hosts an agent.
- **Image source**: A deployment-owned rule for making an image available:
  `registry` (inspect and pull a permitted reference) or `local-build` (inspect
  and, only when stale or missing, build a named recipe).
- **Build key**: A digest of the normalized recipe, build arguments, and the
  content of the declared effective inputs — only files that can affect the
  target. Source mounted at run time does not invalidate a dependency image.
- **Fresh image**: A built image that records the desired build key, matches the
  worker platform, passes the recipe's validation when due, and is within its
  optional maximum age. A missing image is **cold**, not a startup failure.
- **Image cache**: The selected daemon's image store, scoped to the daemon rather
  than to a job, session, or workflow.

---

## 4. Architecture

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
                trusted worker
                        |
                        | ensure required image
                        |  - reuse fresh image
                        |  - pull permitted registry image
                        |  - build approved local recipe
                        v
             configured Docker backend
              existing system dockerd
                 |             |
                 |             +-- persistent image cache
                 +-- ephemeral owned workload container
```

The API handles admission and public status/control but never runs a long pull
or build in a request handler. Temporal owns state transitions, timeout,
cancellation, retry, provisioning coordination, artifact publication, and
cleanup. Only the trusted worker holds the Docker endpoint.

Code: `api_service/services/container_jobs.py` (service),
`api_service/api/routers/container_jobs.py` (HTTP),
`moonmind/schemas/container_job_models.py` (contract and observations),
`moonmind/workflows/temporal/workflows/container_job.py` and
`moonmind/workflows/temporal/container_job_backend.py` (execution), and
`moonmind/config/container_backend_settings.py` (configuration).

---

## 5. Backend and image-source configuration

One backend is selected per deployment. Local build recipes are named by the
deployment and are never caller input:

```yaml
dockerBackendService:
  enabled: true
  defaultBackendRef: system

  backends:
    system:
      kind: docker-engine
      endpointFrom: SYSTEM_DOCKER_HOST
      workspaceResolver: system-docker

  imageSources:
    moonmind-python-tests:
      kind: local-build
      image: moonmind-python-tests:local
      context: .
      dockerfile: api_service/Dockerfile
      target: test-runtime
      buildArgs:
        INSTALL_CODEX_CLI: "false"
        INSTALL_TEST_DEPS: "true"
      fingerprint:
        mode: declared-effective-inputs
        inputs: [.dockerignore, api_service/Dockerfile, api_service/docker/**/*,
                 api_service/config.template.toml, pyproject.toml, poetry.lock,
                 README.md, LICENSE, NOTICE]
      freshness:
        maxAge: deployment-configured
      validation:
        command: ["python", "-c", "import pytest"]
        networkMode: none

    # Other sources come from MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES, a JSON
    # array of {"sourceRef", "image", "pullPolicy", "registryCredentialRef"}
    # objects (registryCredentialRef optional).
    <deployment-declared>:
      kind: registry
      from: MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES
      pullPolicy: if-missing

  # Cache sources come from MOONMIND_CONTAINER_BACKEND_CACHE_SOURCES, a JSON
  # array of {"cacheRef", "volumeName", "target", "readOnly"} objects.
  cacheSources:
    <deployment-declared>:
      from: MOONMIND_CONTAINER_BACKEND_CACHE_SOURCES
      readOnly: false

  policy:
    mode: unrestricted
    allowRawDockerCli: false
```

Behavior:

- MoonMind owns exactly one image source (its `moonmind-python-tests` self-test
  image) and no cache source. Every project image, named volume, and cache path
  is deployment-declared, so generic surfaces name no consuming project. A caller
  selects a source by opaque ref, and an undeclared ref fails closed.
- Unsupported backend or source kinds are configuration errors, as is a
  declaration that is not a JSON array of objects, omits `sourceRef`/`image` or
  `cacheRef`, repeats or reuses a reserved ref, names a non-volume, or carries an
  unknown key. Unknown keys are rejected because omitted keys default to
  permissive values, so a misspelled `readOnly` or `pullPolicy` would otherwise
  silently weaken the requested policy.
- A cache `target` is validated by the same rules as a caller's
  `CacheMount.target` (one normalized absolute path, no empty or `..`
  segments). Sources are matched by exact target, so a target only the
  declaration accepts would be unselectable; it fails at configuration time.
- Each declaration variable is used exactly as supplied, never merged with the
  compose-derived default, and must reach both the API (which resolves
  `imageSourceRef` before creating job identity) and the worker.
- `resources.shmSize` is caller-owned and refused (not clamped) above
  `MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB` (default: the memory ceiling), so
  the realized value matches the request. An omitted value uses
  `MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB`, which is itself refused at
  configuration time above that ceiling.
- Local-build paths resolve from an operator-owned deployment root. Declared
  fingerprint inputs cover every file that can affect the target, and repository
  tests pin that relationship. Secrets are not accepted as build arguments.

The execution adapter is narrow:

```python
class DockerBackendAdapter(Protocol):
    async def inspect_image(self, image: str) -> ImageObservation: ...
    async def pull_image(self, image: str, auth: RegistryAuth | None) -> ImageObservation: ...
    async def build_image(self, recipe: ResolvedLocalImageRecipe) -> ImageObservation: ...
    async def run(self, request: ResolvedContainerJob) -> ContainerExecution: ...
    async def stop(self, container_id: str, grace_seconds: int) -> None: ...
    async def remove(self, container_id: str) -> None: ...
```

`build_image` is internal over a deployment-resolved recipe, not an agent-facing
build tool.

---

## 6. Agent-facing asynchronous tools

| Tool | Behavior |
| --- | --- |
| `container.submit` | Validates and authorizes, creates a durable job, and returns immediately with `jobId`, `status` (`queued`), and `image`. |
| `container.status` | Bounded snapshot: `queued`, `resolving_workspace`, `waiting_for_capacity`, `acquiring_image`, `building_image`, `starting`, `running`, `succeeded`, `failed`, `timed_out`, `canceled`, or `cleanup_failed`. |
| `container.logs` | Bounded log pages or durable log refs, never an unbounded daemon stream. Pull and build diagnostics are included without credentials or unrelated daemon state. |
| `container.artifacts` | Collected output refs and publication diagnostics. |
| `container.cancel` | Idempotent cancellation; Temporal stops the owned container or build, captures evidence, and cleans up. |

`container.run_docker` (raw Docker CLI) is an explicitly gated internal escape
hatch, not a normal Omnigent or managed-session MCP tool, and never grants its
caller daemon access.

Both transports call one API-owned `ContainerJobService`; neither executes
Docker nor waits for completion.

- HTTP: `POST /api/v1/container-jobs`, `GET /api/v1/container-jobs/{jobId}`,
  `GET .../{jobId}/logs`, `GET .../{jobId}/artifacts`, and
  `POST .../{jobId}/cancel`, all authenticated and owner-scoped.
- MCP: the five `container.*` tools go to the same service; `tools/list`
  advertises them only when the surface is enabled and the backend route is
  ready.
- `MOONMIND_CONTAINER_JOBS_ENABLED` gates the surface. The local Compose
  deployment enables it by default; non-Compose deployments default to off.
- The service can be ready while an optional source is cold; a cold recipe is
  advertised as provisionable.
- Errors use stable machine-readable codes shared by both transports.

---

## 7. Request contract

A backend-neutral request looks approximately like this (full model in
`moonmind/schemas/container_job_models.py`):

```json
{
  "image": "registry.example/image@sha256:...",
  "imageSourceRef": null,
  "workspaceRef": { "kind": "managed_runtime", "runtimeId": "rt_...", "agentRunId": "conv_..." },
  "workdir": "/workspace",
  "command": ["program", "arg1"],
  "entrypoint": [],
  "environment": {},
  "cacheMounts": [],
  "networkMode": "none",
  "resources": {
    "cpu": "4",
    "memory": "8g",
    "shmSize": "1g",
    "gpu": { "vendor": "nvidia", "count": "all", "capabilities": ["compute", "utility"] }
  },
  "timeoutSeconds": 3600,
  "pullPolicy": "if-missing",
  "registryCredentialRef": null,
  "outputs": {}
}
```

A request supplies either a permitted `image` or an approved `imageSourceRef`,
which resolves to an immutable provisioning plan before execution. The caller
does not provide a Docker endpoint, a host source path, build inputs, socket or
data-root mounts, privileged mode, host namespaces, arbitrary devices, or
ownership-label overrides. MoonMind adds owner, workflow, run, step, session,
expiration, idempotency, and resolved-image metadata.

`resources.gpu` is the only typed device resource: a supported vendor, a device
count, and optional bounded driver capabilities, absent for CPU-only jobs. It is
not device authority; the caller never supplies device paths, runtime sockets,
Docker flags, or privileged mode. See
[GPU Container Contract](../Workflows/GpuContainerContract.md).

The worker's `task.container.env` adapter sends the container-create JSON
through the configured Docker CLI's `system dial-stdio` tunnel. Values,
including multiline settings, travel on stdin and never enter process arguments
or the Docker client's environment. The CLI keeps deployment-owned connection,
context, and TLS settings. Unless `DOCKER_API_VERSION` is set, the worker asks
the daemon for its API version through that CLI before sending a versioned
request. Negotiation, creation, and attachment share the execution deadline;
after an uncertain create, cleanup reconciles the unique launch label before
removing a container.

### 7.1 Workspace filesystem boundary

Agent-written workspace directories are untrusted when the worker publishes logs
or prepares another launch. Artifact writes use fresh files and atomic
replacement under descriptor-pinned, non-symlink parents, and never truncate an
agent-selected symlink or hardlink target. Managed launchers apply the same
parent validation, and ownership transfer walks pinned directory handles without
following symlinks.

`collectGlobs` traversal never enters symlinked directories, including literal
glob prefixes, and has bounded entry and depth budgets plus the artifact-count
limit. Budget exhaustion is recorded as truncation; unsafe paths fail
publication with diagnostics rather than redirecting worker filesystem access.

---

## 8. Job identity and state

A job has one stable `job_id`. Its idempotency key derives from caller identity,
workflow/run/step identity where present, and a caller request ID. The durable
record stores request intent and compact observations, not registry or build
secrets or unbounded logs.

Terminal states are `succeeded`, `failed`, `timed_out`, and `canceled`.
Cleanup, image-validation, and artifact-publication failures are recorded
separately and do not rewrite primary workload success.

---

## 9. Workspace resolution

`workspaceRef` reuses the cross-runtime workspace locator in
`moonmind/schemas/workspace_locator_models.py`, with kinds `sandbox`,
`managed_runtime`, and `external_state`. Resolution authenticates the
principal, authorizes the referenced run or session, resolves the workspace
record, maps it to a source visible to the daemon, verifies containment and
denies symlink escapes, creates the destination mount and workdir, and runs a
visibility probe. A failed probe stops the job before expensive image
provisioning.

For host Docker, MoonMind maps a logical container path to the physical host
bind root or an approved named volume; the agent does not perform this
translation. Artifacts use a separate approved mount or spool.

---

## 10. Image acquisition, provisioning, and reuse

### 10.1 Registry pulls

Pull policies are `if-missing` (default), `always`, and `never`. Presence is
checked in the selected daemon, not the caller container. Digests are preferred;
when a tag is permitted, the job records the resolved digest.

Acquisition has a 1800s execution window and a 2100s scheduling/retry window,
independent of the workload timeout, so a short test request does not truncate a
cold Unreal pull. Retained Temporal histories keep their recorded acquisition
timeout through the `container-job-image-pull-budget-v1` patch.

### 10.2 Local-build freshness

Before building, the worker resolves the recipe and desired build key, inspects
the configured tag and its MoonMind build labels (`io.moonmind.image-source`,
`io.moonmind.build-key`, `io.moonmind.built-at`, `io.moonmind.recipe-version`),
verifies platform and freshness, and reuses a fresh image immediately. Only when
the image is missing or stale does it take the per-build-key lock, re-inspect
(another job may have finished), build if still needed, validate, and record the
digest.

The build key, not wall-clock age, is the primary correctness check. The
optional maximum age lets deployments refresh unchanged recipes so updated base
images and OS packages are not cached forever.

The fingerprint covers only effective inputs to the target rather than the
whole build context. For the MoonMind Python test image, ordinary source changes
are mounted at run time and do not rebuild the dependency image; changes to the
Dockerfile, lockfile, dependency manifest, test tooling, build arguments,
platform, or recipe version do. BuildKit layer caching stays enabled, so a
stale-key rebuild can reuse unchanged layers, and no build runs at all for a
fresh image.

### 10.3 Provisioning lock

Acquisition is serialized by normalized registry/repository/reference for
registry images, and by source name, platform, and build key for local recipes.
Waiters do not start redundant pulls or builds; they wait, re-inspect, and
continue or receive the same bounded failure evidence.

### 10.4 Prebuilt image override

`MOONMIND_PYTHON_TEST_IMAGE` may name a versioned prebuilt image that follows
registry pull policy and bypasses the local recipe; this suits deployments
without a trusted local build root. A Compose deployment may offer an explicit
preparation command or opt-in profile for prewarming, which calls the same
provisioner and is never a dependency of API or worker startup.

### 10.5 Private images

A registry image declaration may carry an opaque `registryCredentialRef`. The
API authorizes the resolved image and credential through
`MOONMIND_REGISTRY_CREDENTIAL_GRANTS` before persisting a job, and the worker
re-checks the match before inspecting or pulling; a declaration change while a
job is in flight fails closed. Callers submit only `imageSourceRef` and cannot
override image, pull policy, or credential. Declarations without a credential
pull anonymously.

For the Compose Unreal source, `MOONMIND_UNREAL_ENGINE_REGISTRY_CREDENTIAL_REF`
names a managed secret (for example `db://tactics-ghcr-pull`) holding a JSON
username and token, with a grant scoped to
`ghcr.io/moonladderstudios/tactics-ue-base`. A host's Docker Desktop credential
helper is not inherited by the worker container, and the worker never
substitutes the agent's GitHub source-control token. Enrollment is a deployment
operation: preserving the database, its secret encryption key, and
configuration lets worker recreations and updates reuse a `db://` credential.
Fresh deployments can use `env://`, `vault://`, or approved `exec://`
references instead (for example `env://TACTICS_REGISTRY_AUTH` injected by a
secret manager). Missing or unauthorized references fail closed rather than
searching other credentials.

A job carries only the non-sensitive reference. Authorization is enforced on
every run, including cache hits, because an image being present in the daemon
does not grant permission to run it. Credentials are resolved only inside a
trusted execution-time Activity, written to a per-job Docker config with
restrictive permissions, redacted from observations, and removed on every
outcome including orphan reconciliation. The default Unreal workload profile
mounts no pull credentials: its runner has no Docker endpoint, uses
`networkPolicy: none`, and its image is acquired by the host before start.

#### Managed-session GHCR pulls

Public runtime images are pulled anonymously. An explicit private `ghcr.io`
identity selects exactly one deployment configuration, in precedence order: the
`MOONMIND_GHCR_PULL_*_SECRET_REF` (or `WORKFLOW_` equivalent) SecretRef pair,
the deployment `GHCR_PULL_USER` + `GHCR_PULL_TOKEN` pair, or the same-named
managed-secret slug pair read in one store session.

When none is configured, the deployment's own GitHub credential (the
`repository-connection:git-default` connection, with `read:packages`)
authenticates the pull, because requiring a separate registry secret to pull an
image the single-user deployment itself declared is unnecessary setup.
`MOONMIND_GHCR_PULL_FROM_GITHUB_TOKEN_ENABLED=false` keeps the credentials
separate. This derivation applies only to `ghcr.io` repositories the deployment
declared — a managed-session or Omnigent image from configuration, a job's
`imageSourceRef`, or a direct `image` whose registry and repository exactly match
a declared source without its own `registryCredentialRef` — so a workflow cannot
borrow the deployment's identity to pull arbitrary packages. `GHCR_PULL_*`
plaintext is not read from agent-authored launch fields. Incomplete pairs,
unresolvable SecretRefs, rotation between paired reads, store outages, and denied
credentials fail at the registry boundary without falling back to another
identity, ambient Docker login, or anonymous pull.

### 10.6 Image lifetime and maintenance

Images survive job, session, and workflow completion, and job cleanup never
prunes images globally. The hourly `MoonMind.ManagedRuntimeWorkspaceCleanup`
workflow runs Docker storage maintenance on the trusted agent-runtime worker:
it removes day-old unused untagged images, stale build cache, and unreferenced
anonymous volumes, keeps tagged images (such as declared container-job images)
so later jobs reuse them, and escalates to all unused images and build cache
when the `/work/agent_jobs` filesystem stays above its critical watermark. Named
volumes and containers are never targets. [Managed Runtime
Cleanup](ManagedRuntimeCleanup.md) describes thresholds and
`MOONMIND_DOCKER_STORAGE_*` tuning.

`tools/cleanup-docker-space.sh` is the operator recovery path and works when the
Docker data root is full: inspection launches no diagnostic container, and
`--dry-run` only reads. It also prunes images that nested daemons in sidecars
labeled `moonmind.kind=session-docker-sidecar` report as unused.

---

## 11. Container security and resources

Structured jobs run with `--privileged=false`, dropped capabilities,
`no-new-privileges`, no host PID/IPC/user/network namespace, no Docker socket,
data-root, or host-root mounts, only approved workspace, artifact, scratch, and
cache mounts, MoonMind-owned labels, an explicit network mode, and bounded CPU,
memory, shared memory, process count, output, and timeout. The only device
access is the typed `resources.gpu` request, realized as the vendor's Docker
device request and bounded by a non-overridable deployment ceiling. Callers may
request resources within deployment ceilings but cannot weaken security
defaults.

### 11.1 Fixed limits

Limits are per-container ceilings enforced by Docker (`--cpus`, `--memory`,
`--shm-size`, `--pids-limit`, `--gpus`, and the job timeout), not reservations
from a calculated pool; there is no aggregate resource accounting, and aggregate
settings such as `MOONMIND_MACHINE_*` are not read. New requests carry explicit
positive limits (`require_explicit_resources`) with one fixed `memoryMiB`; a
zero `cpuMillis` never means unlimited. Both Python-test entrypoints submit 2
CPUs and 4 GiB.

Zero-valued and memory-range requests stay decodable so persisted jobs and
histories replay. An admitted but unstarted job with such values continues under
its own identity: when reconcile finds no container, the launch resolves a
deterministic successor (`legacy_fixed_successor_resources`: 2 CPUs for a zero
`cpuMillis`, the persisted `memoryMiB` as the fixed limit, other fields
preserved) without rewriting the serialized request, and a successor the ceiling
cannot admit fails closed for replanning.

Because fixed limits do not guarantee a configuration fits its host, deployments
size host count, job count, and per-container limits together.

### 11.2 Concurrency

`MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS` (default 1) caps container-job
containers launching or running at once. The backend-scoped capacity lock
serializes daemon enumeration and the following `docker start` across workers,
and the daemon's owned containers are the slot ledger. A retry never takes a
second slot, and a slot is released only after the container is confirmed
stopped or removed, so an uncertain launch never reads as free. Agent hosts use
a separate count (host leases), so an agent holding the last host slot can still
launch the test job it is waiting for.

Shortages put jobs in `waiting_for_capacity` on Temporal timers within the
original timeout ("Waiting for a container-job slot. The configured limit is
1."), keeping job identity and workspace instead of becoming a test failure; an
exhausted wait reports that testing did not start. Memory is not reclaimed from
agents with low CPU usage, since an idle process can hold live RAM.

Local image builds use the recipe's fixed Dockerfile, context, target, arguments,
network, timeout, output limit, and validation, so a caller cannot turn a
container job into a build job.

---

## 12. Lifecycle and cleanup

1. authenticate, authorize, and validate;
2. create or replay idempotent job identity;
3. resolve and probe the workspace;
4. resolve the permitted image or approved source;
5. inspect and, only when needed, pull or build under the provisioning lock;
6. validate and record the resolved digest;
7. construct the non-overridable launch plan;
8. create and start the labeled container;
9. capture logs and state, then publish outputs and diagnostics;
10. remove job-scoped runtime objects.

Cancellation and timeout stop only the owned build or container and keep
available evidence. A recurring reconciler removes only labeled job-scoped
objects whose jobs are terminal or expired. There is no "prepare every optional
image at startup" step.

---

## 13. Observability

Durable job metadata records ownership and correlation (owner, source session,
workflow, run, step), backend, the requested image or source ref, the image
observation (`ImageObservation` in `moonmind/schemas/container_job_models.py`:
resolved digest, cache presence and hit, build key, freshness at start,
`provisionAction` of `reuse`/`pull`/`build`/`none`, lock wait, pull and build
durations), container id, status, exit code, timing, workspace kind, log and
artifact refs, and cleanup status. Secrets, registry-auth payloads, build
secrets, and unbounded logs are excluded.

Registry pull output is redacted and kept as bounded stream tails, published
immediately and refreshed at most every 15s. The job projection links it while
acquisition runs, a later timeout cannot erase it, and diagnostic publication
failures do not replace the pull result.

Metrics separate workspace resolution, cache hits, freshness misses, lock waits,
pull, build, validation, container startup, workload runtime, artifact
collection, and cleanup.

---

## 14. Omnigent and managed-session integration

Omnigent and managed sessions use the same tools and contract. An agent runtime
does not need a Docker CLI for repository tests and does not advertise a
session-local `DOCKER_HOST`. The Omnigent side (startup path, Host Classes,
credential materializers, session control) is described in the
[Omnigent module entrypoint](../Omnigent/README.md).

The profile-bound Omnigent host receives a host-lease-scoped bearer capability
and a dependency-free `moonmind` CLI projection. The capability pins the exact
sandbox WorkspaceLocator, Step Execution owner, runtime, and host lease, and the
scoped endpoint rejects any other sandbox or correlation identity. The
dual-homed egress proxy exposes one control-plane route to the host,
`POST /mcp/container/tools/call`, and rejects every other API path. The host
never gets a Docker endpoint; the trusted container worker remains the only
Docker owner.

Session capability may advertise:

```yaml
capabilities:
  containerJobs:
    available: true
    transport: moonmind-mcp
    backendKind: docker-engine
  pythonContainerTests:
    available: true
    imageState: cold-or-ready
    provisioning: on-demand
```

`available: true` means the backend and provisioner are configured, not that an
optional image is built. Capability admission does not force an image from
`cold` to `ready` before the workflow needs it.

---

## 15. MoonMind Python test path

Managed agents run targeted Python verification with:

```bash
moonmind container python-tests tests/unit/path/test_file.py
```

### 15.1 Credentials

In `accounts`, `oidc`, and `header` modes, the managed-session credential
boundary supplies a scoped bearer as `MOONMIND_CONTAINER_JOBS_BEARER_TOKEN`; in
restricted local `disabled` mode the same shape is issued for the stable
persisted user behind a loopback-only bind or trusted ingress. Sessions never
receive a shared deployment-wide token. The token is signed by the session
launcher, expires with the bounded session lifetime, is accepted only by
`/mcp/container/tools/call`, travels over TLS/loopback and never in URLs, and is
never a browser session. Its claims bind owner, runtime, agent run, and session,
and submissions whose workspace or correlation differ fail before job creation.

A generic Omnigent host receives the token as a file in its lease-owned control
volume, minted for the launch policy's `timeoutSeconds`. Because the session can
run until the execute activity deadline, the realizer re-mints the same scope at
half the lifetime and atomically replaces the file; a failed renewal is logged
and retried after one heartbeat interval without ending the session. The CLI
re-reads the file per request, so long-running jobs poll with the current token.

A missing capability returns `401 container_capability_required` and an invalid
or expired one `401 invalid_container_capability`, distinct from the generic
user-auth codes.

### 15.2 Execution

The command derives the locator from the active authority (`managed_runtime`
for a managed session, the exact `sandbox` locator for an isolated Omnigent
host), submits durable work, polls `container.status`, and exits from the
terminal state.

The workflow uses a configured versioned prebuilt image if there is one;
otherwise it resolves the `moonmind-python-tests` recipe, reuses
`moonmind-python-tests:local` when its build key and freshness match, or builds
the `test-runtime` target once under the lock, validates `pytest`, and records
the digest. Tests then run against the managed workspace using that digest. The
workload is equivalent to:

```json
{
  "imageSourceRef": "moonmind-python-tests",
  "workdir": "/workspace",
  "command": ["bash", "-lc", "./tools/test_unit.sh --python-only -- \"$@\"", "moonmind-python-tests", "tests/unit"],
  "environment": [
    {"name": "MOONMIND_FORCE_LOCAL_TESTS", "value": "1"},
    {"name": "MOONMIND_PYTEST_JUNITXML", "value": "artifacts/pytest-unit.xml"},
    {"name": "PYTHONPATH", "value": "/workspace"}
  ],
  "networkMode": "bridge",
  "outputs": [{"name": "pytest-junit", "relativePath": "artifacts/pytest-unit.xml"}]
}
```

After provisioning, the launch uses the immutable digest and does not pull
again; `pullPolicy: never` applies only to that resolved launch and does not
replace the ensure-image step.

The worker mounts the `agent_workspaces` named volume with a constrained volume
subpath and never forwards its local `/work/agent_jobs/...` path to the host
daemon as a bind mount.

A missing or stale image is normal first-use state; only a failed or
misconfigured provisioner is an environment blocker. A build failure is not a
test assertion failure, and its evidence states the recipe, desired build key,
failure class, and bounded remediation guidance.

### 15.3 Generic managed-session jobs

Other portable Skills submit a workload-only JSON spec:

```bash
moonmind container run --spec /path/to/job.json --request-id stable-phase-id
```

The CLI replaces any supplied workspace locator with the admitted
managed-runtime identity and injects workflow, session, run, step, and
idempotency correlation. The spec may name an approved `imageSourceRef` and
`cacheRef` values but cannot choose endpoints, host paths, raw volume names, or
registry credentials.

The validated runtime profile decides workload capability:
`workloadMode=container-jobs` enables the scoped endpoint, `no-docker`
advertises no container tools, and `kubernetes-job` fails before launch unless a
Kubernetes backend is configured. Workflow text and runtime environment
overrides cannot elevate that decision.

The `tactics-unreal` source selects the deployment-approved Unreal image; its
default `pullPolicy=if-missing` lets the first admitted job provision a cold
cache through the coalesced acquisition path, and an operator may set `never`
when prewarming is required. The `unreal-ccache` and `unreal-ubt` cache refs
resolve to deployment-owned targets and owner-scoped volumes derived from the
configured base names, so jobs from the same principal reuse a cache while
another identity gets a different writable volume. The `tactics-test` Skill
routes its `run_dood_unreal_tactics.sh` entrypoint through
`moonmind container run` inside a managed session and keeps direct-Docker
behavior outside MoonMind, so the portable capability needs no MoonMind-only
fork.

---

## 16. Readiness and failure classes

The service is ready when configuration parses, the backend kind is supported,
the endpoint is reachable, worker routes are available, and Temporal submission
works. It does not require optional images. Image-source state is reported
separately as `ready`, `cold` (missing, provisioner configured), `stale`,
`provisioning`, or `blocked` (configuration or execution failed).

Failure classes (`ContainerJobFailureClass` in
`moonmind/schemas/container_job_models.py`) distinguish backend availability,
request validation and authorization, workspace resolution and visibility,
image source, pull, build, validation, platform, and use authorization, registry
credential resolution and cleanup, resource and GPU limits, container start,
workload failure or timeout, artifact publication, and cleanup. A cold image
never produces `backend_unavailable` by itself; the first job that needs it
moves through acquisition or build and receives the authoritative result.
