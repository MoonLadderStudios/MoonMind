# Docker Backend Service

- **Status:** Desired state
- **Owners:** MoonMind Platform
- **Last updated:** 2026-07-22
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
and dispatches bounded execution against one deployment-selected Docker backend.

The service is the canonical route for containerized repository work requested
by:

- Omnigent-hosted sessions;
- MoonMind managed sessions;
- workflow tools and skills;
- the dashboard and HTTP API;
- authenticated MCP clients.

Examples include .NET tests, MoonMind Python tests, Unreal automation tests,
JavaScript toolchains, linters, and integration tests.

The requesting runtime never receives the Docker socket, a Docker API endpoint,
`DOCKER_HOST`, or raw daemon authority. It asks MoonMind to execute a typed job.
The per-session Docker-in-Docker topology is not a supported desired state or
compatibility path.

The selected daemon owns a persistent image store. Pulled and locally provisioned
images remain reusable by later jobs and workflows until deployment-level image
retention removes them. Image lifetime is independent of workflow and session
lifetime.

**MoonMind service readiness and optional workload-image readiness are separate.**
Starting MoonMind must not build, pull, or validate an image merely because a
future workflow might use it. Image work begins when an admitted job actually
requires that image, or when an operator explicitly invokes the same provisioner
to prewarm it.

---

## 2. Core decisions

1. **MoonMind owns container launches.** Agents request typed container jobs;
   they do not control a Docker daemon.
2. **The service is API-owned.** The API authenticates callers, authorizes the
   request, resolves logical workspaces, creates durable job identity, and
   exposes status and control surfaces.
3. **Temporal owns long-running execution.** Submission returns a stable job
   identifier. Pulls and builds never hold an MCP or HTTP request open.
4. **One configured daemon is the current backend.** The required implementation
   uses the existing system Docker daemon through MoonMind's configured Docker
   host or socket proxy.
5. **Backend selection is deployment-owned.** Callers cannot provide a Docker
   endpoint. The public job contract is backend-neutral.
6. **Optional images are acquired on demand.** A missing permitted registry image
   is pulled by the first job that needs it; later jobs reuse it.
7. **Deployment-owned local image recipes are provisioned on demand.** A local
   build is allowed only for a named, operator-configured recipe. Callers cannot
   submit arbitrary Dockerfiles, build contexts, build arguments, or secrets.
8. **Compose startup does not prepare test images.** Ordinary `docker compose up`
   starts MoonMind without building or running a Python-test image and without
   making an agent-runtime worker depend on such a build.
9. **Fresh images are reused.** Before a local build, MoonMind inspects the daemon
   for an image whose recorded build key matches the desired recipe and whose
   configured refresh deadline has not expired.
10. **Provisioning is coalesced.** A per-source, per-build-key lock prevents
    concurrent jobs from pulling or building the same image redundantly.
11. **Job cleanup never removes shared images.** Container, scratch, network, and
    temporary credential lifecycles are job-scoped. Image retention is separate.
12. **Workspaces are logical references.** Callers do not provide daemon-visible
    host paths. MoonMind resolves an authorized workspace into a mount plan.
13. **The structured container contract is the normal public interface.** Docker
    CLI execution remains an explicitly gated internal escape hatch.
14. **The core remains workload-agnostic.** Toolchain-specific commands and local
    image recipes live in deployment configuration or thin helpers, not backend
    branches for Python, .NET, Unreal, Unity, or Node.
15. **No dedicated MoonMind Docker daemon is required today.** This design does
    not add a daemon service, daemon pool, or specialized worker pool.
16. **Artifacts and bounded job records are durable truth.** Daemon-local state is
    an execution cache, not the system of record.

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
- on-demand pulls and deployment-owned local builds;
- deterministic local-build freshness and cross-workflow reuse;
- private-registry authorization;
- mount, network, resource, and cleanup policy;
- Omnigent and managed-session integration;
- backend portability without implementing a second backend.

### 3.2 Out of scope

This document does not define:

- a public arbitrary-image build API;
- caller-supplied Dockerfiles, build contexts, or daemon-visible source paths;
- an automatic build, pull, or test-image probe on ordinary MoonMind startup;
- a Docker daemon pool or dedicated MoonMind workload daemon;
- an Unreal-specific, Python-specific, or .NET-specific worker pool;
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

### 4.4 Image source

A deployment-owned rule for making an image available. Supported source kinds in
this design are:

- `registry`: inspect and pull a permitted image reference;
- `local-build`: inspect and, only when stale or missing, build a named local
  recipe owned by the deployment.

The public container-job request selects an image or an approved image-source
alias. It never defines a new source.

### 4.5 Build key

A deterministic digest of the normalized local-build recipe, build arguments,
and the content of the declared effective input set. The input set contains only
files that can affect the selected target, not the entire repository. Source-only
changes mounted into the workload at run time do not invalidate a dependency
image.

### 4.6 Fresh image

A locally built image is fresh when:

1. it records the desired build key;
2. its platform matches the worker platform;
3. it passes the recipe's bounded validation command, when validation is due;
4. its optional deployment-configured maximum age has not expired.

A missing image is **cold**, not a MoonMind startup failure.

### 4.7 Image cache

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

The API owns authentication, authorization, validation, workspace resolution,
idempotent submission, and public status/control surfaces. It does not execute a
long pull or build inside the request handler.

Temporal owns durable state transitions, timeout, cancellation, retry policy,
image-provisioning coordination, artifact publication, and cleanup coordination.

A trusted MoonMind worker receives the Docker endpoint and executes the resolved
provisioning and launch plan. The requesting agent never receives that endpoint.

The normal MoonMind startup path checks the API, worker routes, Temporal, the
configured Docker endpoint, and required authority boundaries. It does not walk
optional image sources and does not invoke their build or validation commands.

---

## 6. Declarative backend and image-source configuration

One backend is selected per deployment. Optional local build recipes are named by
the deployment and remain inaccessible as arbitrary caller input:

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
        inputs:
          - .dockerignore
          - api_service/Dockerfile
          - api_service/docker/**
          - api_service/config.template.toml
          - pyproject.toml
          - poetry.lock
          - README.md
          - LICENSE
          - NOTICE
      freshness:
        maxAge: deployment-configured
      validation:
        command: ["python", "-c", "import pytest"]
        networkMode: none

  policy:
    mode: unrestricted
    allowRawDockerCli: false
```

Rules:

- unsupported backend or image-source kinds are configuration errors;
- empty or unreachable Docker endpoints fail service readiness;
- a missing optional image does not fail service or worker readiness;
- local-build paths are resolved from an operator-owned deployment root, never
  from caller-provided absolute paths;
- the declared fingerprint inputs must cover every file that can affect the
  selected target, and repository tests must pin that relationship;
- secrets are not accepted as local-build arguments in this contract;
- callers cannot supply endpoint URLs, socket paths, TLS material, Dockerfiles,
  build contexts, build arguments, or validation commands;
- an operator may configure a prebuilt registry image instead of a local recipe;
- implementing another endpoint is optional and is not part of this design.

The Docker execution adapter remains narrow:

```python
class DockerBackendAdapter(Protocol):
    async def inspect_image(self, image: str) -> ImageObservation: ...
    async def pull_image(
        self,
        image: str,
        auth: RegistryAuth | None,
    ) -> ImageObservation: ...
    async def build_image(
        self,
        recipe: ResolvedLocalImageRecipe,
    ) -> ImageObservation: ...
    async def run(self, request: ResolvedContainerJob) -> ContainerExecution: ...
    async def stop(self, container_id: str, grace_seconds: int) -> None: ...
    async def remove(self, container_id: str) -> None: ...
```

`build_image` is an internal operation over a deployment-resolved recipe. It is
not exposed as an agent-facing Docker build tool.

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

Validates and authorizes a request, creates a durable job, and returns immediately:

```json
{
  "jobId": "container-job:01J...",
  "status": "queued",
  "image": "mcr.microsoft.com/dotnet/sdk:8.0@sha256:..."
}
```

### 7.2 `container.status`

Returns a bounded snapshot such as `queued`, `resolving_workspace`,
`acquiring_image`, `building_image`, `starting`, `running`, `succeeded`, `failed`,
`timed_out`, `canceled`, or `cleanup_failed`.

### 7.3 `container.logs`

Returns bounded log pages or durable log references, never an unbounded daemon
stream in one response. Pull and build diagnostics are included as bounded job
evidence without exposing credentials or unrelated daemon state.

### 7.4 `container.artifacts`

Returns collected output references and publication diagnostics.

### 7.5 `container.cancel`

Requests idempotent cancellation. Temporal stops the owned container or build,
captures available evidence, and completes cleanup.

### 7.6 Raw Docker CLI

Docker CLI execution remains an explicitly gated internal escape hatch.
`container.run_docker` is not a normal Omnigent or managed-session MCP tool and
never grants its caller direct daemon access.

### 7.7 Transport surface and readiness

Both transports call one API-owned `ContainerJobService`; neither executes Docker
nor waits for terminal completion.

- HTTP: `POST /api/v1/container-jobs` (submit),
  `GET /api/v1/container-jobs/{jobId}` (status), `GET .../{jobId}/logs`,
  `GET .../{jobId}/artifacts`, and `POST .../{jobId}/cancel`. All are
  authenticated and owner-scoped.
- MCP: the five `container.*` tools are dispatched to the same service, and
  `tools/list` advertises them only when the surface is enabled and the backend
  route is ready.
- Feature gate: `MOONMIND_CONTAINER_JOBS_ENABLED`. The canonical local Compose
  deployment enables it by default; non-Compose deployments retain a fail-closed
  default and must opt in explicitly.
- The service can be ready while a configured optional image source is cold. A
  cold local recipe is advertised as provisionable, not unavailable.
- Errors use stable machine-readable codes shared by both transports.

---

## 8. Container-job request contract

A backend-neutral request is shaped approximately as follows:

```json
{
  "image": "registry.example/image@sha256:...",
  "imageSourceRef": null,
  "workspaceRef": {
    "kind": "managed_runtime",
    "runtimeId": "rt_...",
    "agentRunId": "conv_..."
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

A request supplies either a permitted `image` or a deployment-approved
`imageSourceRef`. An image-source reference resolves to an immutable provisioning
plan before execution. The caller does not provide:

- a Docker endpoint;
- a daemon-visible host source path;
- a Dockerfile, build context, build target, or build argument;
- Docker socket or data-root mounts;
- privileged mode, host namespaces, or devices;
- MoonMind ownership-label overrides.

MoonMind enriches every request with owner, workflow, run, step, session,
expiration, idempotency, and resolved-image metadata.

---

## 9. Durable job identity and state

A job has one stable `job_id`. An idempotency key is derived from caller identity,
workflow/run/step identity where present, and a caller request ID.

The durable job record stores request intent and compact observations; it does
not store registry secrets, build secrets, or unbounded logs.

Terminal states are:

```text
succeeded
failed
timed_out
canceled
```

Cleanup, image-validation, or artifact-publication failures are recorded
separately and do not silently rewrite primary workload success.

---

## 10. Workspace resolution

Workspaces are logical references, not raw host paths. The `workspaceRef` field
reuses the canonical cross-runtime workspace locator in
`moonmind/schemas/workspace_locator_models.py`. Supported kinds are:

```text
sandbox
managed_runtime
external_state
```

Resolution steps are:

1. authenticate the principal;
2. authorize access to the referenced run or session;
3. resolve the canonical workspace record;
4. map it to a source visible to the configured Docker daemon;
5. verify containment and deny symlink escapes;
6. create the destination mount and workdir;
7. run a visibility probe before a pull or build.

A failed probe stops the job before expensive image provisioning.

For host Docker, MoonMind maps a logical container path to the physical host bind
root or an approved named volume. The agent does not perform this translation.
Artifacts use a separate approved mount or spool.

---

## 11. Image acquisition, local provisioning, and reuse

### 11.1 Registry pull policy

Supported registry pull policies are:

```text
if-missing
always
never
```

`if-missing` is the default. Image presence is checked in the selected daemon,
not in the caller container. Digests are preferred for reproducibility. When a
tag is permitted, the job records the resolved digest.

### 11.2 Local-build freshness

Before invoking a local build, the trusted worker:

1. resolves the approved recipe and computes its desired build key;
2. inspects the configured image tag and recorded MoonMind build labels;
3. verifies platform and freshness metadata;
4. reuses the image immediately when it is fresh;
5. acquires the per-build-key lock only when the image is missing or stale;
6. re-inspects after acquiring the lock because another job may have completed
   provisioning;
7. invokes the bounded build only when the second inspection still requires it;
8. validates the result and records the resolved image digest.

The canonical labels are conceptually:

```text
io.moonmind.image-source
io.moonmind.build-key
io.moonmind.built-at
io.moonmind.recipe-version
```

The build key, not wall-clock age alone, is the primary correctness check. An
optional maximum age lets deployments refresh unchanged recipes so updated base
images and operating-system packages are not cached forever.

### 11.3 Efficient invalidation

The fingerprint covers the effective inputs to the selected target. It must not
hash the whole repository merely because the repository is the Docker build
context. For the MoonMind Python test image, normal Python source changes are
mounted at run time and do not require rebuilding the dependency image. Changes
to the Dockerfile, lockfile, dependency manifest, test-runtime tooling, build
arguments, platform, or recipe version do require a new build key.

BuildKit layer caching remains enabled. A stale-key rebuild may still reuse
unchanged layers, but no build command is invoked at all for a fresh image.

### 11.4 Provisioning lock

MoonMind serializes acquisition by normalized image source and desired key:

- registry images use normalized registry/repository/reference;
- local recipes use source name, platform, and build key.

Concurrent waiters do not start redundant pulls or builds. They wait for the
owner, re-inspect, and then continue or receive the same bounded provisioning
failure evidence.

### 11.5 Prebuilt image override

Operators may configure `MOONMIND_PYTHON_TEST_IMAGE` as a versioned prebuilt image.
That source follows registry pull policy and bypasses the local recipe. A prebuilt
image is preferred for deployments that do not mount a trusted local build root.

A local Compose deployment may retain an explicit preparation command or opt-in
Compose profile for operators who want prewarming. It must call the same
provisioner and must never be a dependency of ordinary API or worker startup.

### 11.6 Private images

A job request carries a non-sensitive `registryCredentialRef` only; it never
carries a username, token, password, or Docker auth blob. Private-image
authorization is enforced on every run before either a cache hit or pull is
accepted. A private image being present in the daemon does not grant another user
permission to execute it.

Registry credentials are resolved only inside a trusted execution-time Activity,
materialized in a per-job Docker config with restrictive permissions, redacted
from observations, and removed on success, failure, cancellation, timeout, and
orphan reconciliation.

### 11.7 Image lifetime

Images survive job, session, and workflow completion. Deployment-level retention
may evict unused images under disk pressure while protecting images used by
active containers and operator-pinned digests. Job cleanup never performs global
image pruning.

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

Local image builds use separate deployment policy. Their Dockerfile, context,
target, arguments, network access, timeout, output limit, and validation command
are fixed by the approved recipe. A caller cannot turn a container job into a
build job.

---

## 13. Lifecycle and cleanup

The canonical lifecycle is:

1. authenticate and authorize;
2. validate the request;
3. create or replay idempotent job identity;
4. resolve and probe the workspace;
5. resolve the permitted image or approved image source;
6. inspect and, only when necessary, pull or build under the provisioning lock;
7. validate and record the resolved image digest;
8. construct the non-overridable launch plan;
9. create and start the labeled workload container;
10. capture logs and state;
11. publish outputs and diagnostics;
12. remove job-scoped runtime objects.

Cancellation and timeout stop only the owned build or job container and preserve
available evidence when possible.

A recurring reconciler removes only labeled job-scoped objects whose durable jobs
are terminal or expired. Image maintenance is a separate deployment operation.

There is intentionally no lifecycle step equivalent to "prepare every optional
image during MoonMind startup."

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
image_source_ref
image_digest
image_present_at_start
image_cache_hit
image_build_key
image_fresh_at_start
image_provision_action
image_provision_waited_on_lock
image_pull_duration_seconds
image_build_duration_seconds
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

`image_provision_action` is one of `reuse`, `pull`, `build`, or `none`. Secrets,
registry-auth payloads, build secrets, and unbounded logs are excluded.

Metrics distinguish workspace resolution, image cache hits, freshness misses,
lock waits, pull time, build time, validation time, container startup, workload
runtime, artifact collection, and cleanup.

---

## 15. Omnigent and managed-session integration

Omnigent and MoonMind managed sessions use the same tools and job contract. An
agent runtime does not need a Docker CLI to run repository tests and must not
advertise a session-local `DOCKER_HOST`.

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

`available: true` means the backend and approved provisioner are configured. It
does not mean an optional image has already been built. Capability admission must
not force the image from `cold` to `ready` before the workflow determines that
Python tests are needed.

---

## 16. MoonMind Python test path

Managed agents run targeted Python verification with:

```bash
moonmind container python-tests tests/unit/path/test_file.py
```

The command derives the canonical `managed_runtime` locator from the active
session, submits durable work, polls `container.status`, and exits from the
authoritative terminal state.

The Python-test workflow resolves the configured source before starting the test
container:

1. If a versioned prebuilt image is configured, inspect and pull it according to
   deployment policy.
2. Otherwise resolve the deployment-owned `moonmind-python-tests` local recipe.
3. Reuse `moonmind-python-tests:local` when its build key and freshness metadata
   match.
4. If it is missing or stale, build the `test-runtime` target once under the
   provisioning lock, validate `pytest`, and record the resulting digest.
5. Run the requested tests against the managed workspace using that resolved
   digest.

The workload is equivalent to:

```json
{
  "imageSourceRef": "moonmind-python-tests",
  "workdir": "/workspace",
  "command": [
    "bash",
    "-lc",
    "./tools/test_unit.sh --python-only -- \"$@\"",
    "moonmind-python-tests",
    "tests/unit"
  ],
  "environment": [
    {"name": "MOONMIND_FORCE_LOCAL_TESTS", "value": "1"},
    {"name": "MOONMIND_PYTEST_JUNITXML", "value": "artifacts/pytest-unit.xml"},
    {"name": "PYTHONPATH", "value": "/workspace"}
  ],
  "networkMode": "bridge",
  "outputs": [
    {"name": "pytest-junit", "relativePath": "artifacts/pytest-unit.xml"}
  ]
}
```

After provisioning, the resolved container launch uses the immutable image digest
and does not pull again. `pullPolicy: never` is valid only for that resolved launch;
it is not a substitute for the preceding ensure-image step.

The trusted worker mounts the deployment's `agent_workspaces` named volume with a
constrained volume subpath. It never forwards the worker-local
`/work/agent_jobs/...` path to the host daemon as a bind mount.

A missing or stale local image is normal first-use state. Only a failed or
misconfigured provisioner is an environment blocker. Build failure is not a test
assertion failure, and the returned evidence must state the recipe, desired build
key, failure class, and bounded remediation guidance.

---

## 17. Workload examples

### 17.1 .NET tests

```json
{
  "image": "mcr.microsoft.com/dotnet/sdk:8.0@sha256:...",
  "workspaceRef": {
    "kind": "managed_runtime",
    "runtimeId": "rt_...",
    "agentRunId": "conv_..."
  },
  "workdir": "/workspace",
  "command": ["dotnet", "test", "--logger", "trx;LogFileName=/artifacts/tests.trx"],
  "networkMode": "bridge",
  "timeoutSeconds": 3600
}
```

### 17.2 Unreal automation

```json
{
  "image": "ghcr.io/example/unreal-runner@sha256:...",
  "workspaceRef": {
    "kind": "managed_runtime",
    "runtimeId": "rt_...",
    "agentRunId": "conv_..."
  },
  "workdir": "/workspace",
  "command": ["bash", "-lc", "./tools/run_unreal_validation.sh"],
  "networkMode": "none",
  "timeoutSeconds": 14400
}
```

All workloads use the same backend and lifecycle. Toolchain-specific logic stays
in images, repository scripts, and approved source configuration, not in the
backend core.

---

## 18. Readiness and failure classes

The Docker Backend Service reports ready only when configuration parses, the
backend kind is supported, the endpoint is reachable, required worker routes are
available, and Temporal submission is available.

Service readiness does **not** require optional images to exist. Image-source
state is reported separately:

```text
ready          fresh image exists
cold           image is missing but provisioner is configured
stale          image exists but requires refresh
provisioning   a pull or build is in progress
blocked        provisioning configuration or execution failed
```

Canonical failure classes include:

```text
backend_unavailable
invalid_request
permission_denied
workspace_not_found
workspace_not_visible
image_source_not_found
image_not_found
image_pull_timeout
image_pull_auth_failed
image_build_not_configured
image_build_inputs_unavailable
image_build_timeout
image_build_failed
image_validation_failed
image_platform_mismatch
image_use_denied
credential_unresolved
repository_scope_mismatch
registry_auth_failed
credential_cleanup_failed
resource_limit_exceeded
container_start_failed
workload_failed
workload_timed_out
artifact_publication_failed
cleanup_failed
```

A cold image never produces `backend_unavailable` by itself. The first requesting
job transitions through acquisition or build and receives the authoritative
result.

---

## 19. Validation rules

The service fails closed unless all applicable rules pass:

1. The configured backend exists and uses a supported kind.
2. The endpoint comes from deployment configuration, not request input.
3. The daemon is reachable before service readiness succeeds.
4. Optional image presence is not a startup-readiness requirement.
5. The caller is authenticated and authorized.
6. The workspace reference resolves to an authorized workspace.
7. The visibility probe succeeds before an expensive pull or build.
8. The mount plan contains no caller-supplied arbitrary host path.
9. Docker socket, data-root, and host-root mounts are denied.
10. Privileged mode, host namespaces, and devices are denied.
11. Resources and timeout fit deployment ceilings.
12. Registry credentials are references resolved only at execution time.
13. Private-image authorization is checked on every run, including cache hits.
14. A local build references a named deployment-owned recipe.
15. The caller cannot override recipe paths, target, arguments, validation, or
    freshness policy.
16. The desired build key is recorded and checked before local image reuse.
17. Concurrent provisioning is coalesced by source and desired key.
18. Job cleanup does not remove shared images.
19. Image maintenance protects active containers.
20. MoonMind ownership labels cannot be overridden.
21. Managed and Omnigent sessions do not receive Docker daemon authority.
22. Ordinary Compose startup does not invoke optional image provisioning.

---

## 20. Stable design rules

1. Agents request container jobs through MoonMind; they do not receive Docker
   authority.
2. The Docker Backend Service is part of the MoonMind API subsystem.
3. Temporal owns long-running durability, timeout, cancellation, provisioning,
   and cleanup coordination.
4. One deployment-selected daemon supplies the cross-workflow image cache.
5. Registry images and approved local recipes are provisioned on demand.
6. Optional image readiness is not MoonMind service readiness.
7. `docker compose up` does not build a test image unless an operator explicitly
   selects an opt-in preparation action.
8. Freshness is deterministic and based on the effective build inputs, with an
   optional bounded refresh age.
9. Concurrent requests do not duplicate pulls or builds.
10. Image lifetime is deployment-scoped, not workflow- or session-scoped.
11. Job cleanup removes job objects but not shared images.
12. Workspaces are logical authenticated references resolved by MoonMind.
13. The structured container contract is the normal public surface.
14. Local-build recipes are deployment-owned and are not arbitrary agent input.
15. The current backend is the existing system Docker host or proxy.
16. A future endpoint switch is configuration behind the same adapter.
17. The core remains workload-agnostic.
18. Artifacts, job records, and bounded observations remain durable truth.
19. Per-session Docker daemons and graphs are not part of the desired state.

---

## 21. Final declarative contract

```yaml
dockerBackendService:
  owner: moonmind-api
  durability: temporal

  startup:
    checksBackend: true
    checksWorkerRoutes: true
    provisionsOptionalImages: false

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
    arbitraryBuildExposedToAgents: false
    rawDockerCliExposedToAgents: false

  backend:
    selectedBy: deployment
    currentKind: docker-engine
    currentEndpoint: system-docker-host
    futureEndpointSwitchAllowed: true
    dedicatedMoonMindDaemonRequiredNow: false

  images:
    registryAcquisition: on-demand
    localRecipeAcquisition: on-demand
    defaultPullPolicy: if-missing
    localFreshness: build-key-and-optional-max-age
    provisioningLock: per-source-and-desired-key
    cacheScope: selected-daemon
    reusableAcrossWorkflows: true
    removeOnJobEnd: false

  pythonTests:
    sourceSelectedBy: deployment
    prebuiltRegistryImageAllowed: true
    localRecipeAllowed: true
    buildOnComposeStartup: false
    buildOnFirstRequiredTestWhenMissingOrStale: true
    reuseFreshBuild: true

  workspaces:
    callerProvidesLogicalRef: true
    callerProvidesHostPath: false
    resolvedByMoonMind: true
    visibilityProbeBeforeProvisioning: true

  security:
    dockerSocketInAgent: false
    dockerHostInAgent: false
    privilegedJobs: false
    arbitraryHostMounts: false
    arbitraryBuildContextFromCaller: false
    privateImageAuthorizationPerRun: true

  cleanup:
    containers: job-scoped
    scratch: job-scoped
    images: deployment-retention
```

Mental model:

- MoonMind starts its API, workers, Temporal routes, and Docker authority boundary.
- It does not speculate about which optional toolchains a future workflow may use.
- A workflow that actually needs an image asks MoonMind to ensure it.
- MoonMind reuses a fresh image, pulls a permitted image, or builds an approved
  local recipe once under a lock.
- The configured daemon retains the image for later workflows.
- No test-image build belongs on the ordinary Compose startup critical path.
