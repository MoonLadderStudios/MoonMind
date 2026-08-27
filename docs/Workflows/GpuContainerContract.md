# GPU Container Contract

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-08-27
**Audience:** Contributors and runtime authors
**Authority:** Target semantics for caller-supplied GPU containers on MoonMind's container boundaries
**Owning Surface:** moonmind/workloads/, moonmind/schemas/container_job_models.py
**Related Docs:** [Skill and Plan Contracts](SkillAndPlanContracts.md), [Workflow Architecture](WorkflowArchitecture.md)
**Related Implementation:** `moonmind/workloads/gpu.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/schemas/container_job_models.py`, `moonmind/workflows/temporal/container_job_backend.py` (MoonLadderStudios/MoonMind#3777, MoonLadderStudios/MoonMind#3779)

MoonMind executes a caller-supplied NVIDIA GPU container through the same
container boundaries it uses for every other container. The GPU is a resource
the caller requests; it is not a workload type MoonMind recognizes.

## Scope

This contract is application-neutral. MoonMind does not qualify, name, or
special-case any game, engine, project image, project gate, project bundle, or
project skill. A consumer repository supplies its own image and command as
ordinary request data and may cite this qualification externally without adding
its semantics to MoonMind.

## Request contract

`WorkloadResourceOverrides.gpu` carries a `WorkloadGpuRequest`:

| Field | Values | Default |
|---|---|---|
| `vendor` | `nvidia` | `nvidia` |
| `count` | `all` or a positive integer | `all` |
| `capabilities` | one or more of `compute`, `compat32`, `graphics`, `utility`, `video`, `display` | absent (the vendor's default device capability set) |

An unsupported vendor, a non-positive count, an unknown capability, an empty
capability list, or an unknown field is rejected at request validation with an
actionable error and a stable class (see **Failure classification**). `count` is
validated before coercion, so `true`, `"2"`, and `2.0` are rejected rather than
realized as device counts the caller never declared. `capabilities` are bounded
vendor driver capability names -- never device paths, runtime sockets, or Docker
options -- and are deduplicated and canonically ordered, so one semantic request
always serializes to the same durable bytes and the same device request. An
omitted `capabilities` field stays absent from the serialized request, so a
request recorded before the field existed is byte-identical to the same request
made today. The GPU request travels on the same request as the caller's image,
command, entrypoint, workdir, environment, cache mounts, network mode, timeout,
and declared outputs.

This one request contract is shared by both container boundaries:

| Boundary | Field |
|---|---|
| Canonical container job (`container.run_job`) | `ContainerJobSpec.resources.gpu` |
| Unrestricted container launch (replay-only `container.run_container`) | `WorkloadResourceOverrides.gpu` |

Any repository skill can request a GPU through the canonical `container.run_job`
contract: it is the ordinary submission payload with one additional resource
field. The tool schema, the closed request model, the deployment ceiling, and
the container-job backend all carry the field, so a new plan never depends on
the replay-only launch path to reach a device.

### Moving an unrestricted GPU request to `container.submit`

The semantic GPU request is the same object on both routes, so the move changes
only the envelope around it. Nothing about the workload changes: the image
reference, the command argv, the declared-output relative paths, and the
artifact interpretation are carried across unmodified.

| Unrestricted field | Canonical field | Note |
|---|---|---|
| `resources.gpu` | `spec.resources.gpu` | The identical `WorkloadGpuRequest`; no translation |
| `image` | `spec.image` | Unchanged caller-owned reference |
| `command` / `entrypoint` | `spec.command` / `spec.entrypoint` | Unchanged argv |
| `repoDir` / `artifactsDir` / `scratchDir` (absolute host paths) | `spec.workspaceRef` (a logical #3147 locator) plus `spec.workdir` | The caller stops naming host paths; the workspace is mounted at `/workspace` |
| `declaredOutputs` (class to relative path) | `spec.outputs` (name and `relativePath`) | Same relative paths, resolved under the job workspace |
| `cacheMounts` (named volume plus target) | `spec.caches` (`cacheRef` plus target) | The deployment owns the volume; the caller names an approved cache ref |
| `envOverrides` | `spec.environment` | Sensitive values move to `secretRef` |
| `timeoutSeconds` | `spec.timeoutSeconds` | Unchanged |
| result metadata `workload.gpu` | `ContainerJobStatus.gpu` plus the terminal failure class | Bounded observation instead of launcher metadata |

Canonical submission additionally requires an `idempotencyKey` and a `source`
correlation, and returns a `jobId` to poll instead of a synchronous result.

## Authority boundaries

- **Canonical container-job path.** `ContainerJobSpec.resources.gpu` is admitted
  by the deployment's non-overridable device ceiling
  (`MOONMIND_CONTAINER_BACKEND_MAX_GPU_COUNT`) and then realized by the
  container-job backend as the vendor's Docker device request. An unset ceiling
  imposes no MoonMind bound, so host capability remains the only limit; a finite
  ceiling rejects both a higher count and the unbounded `all` with
  `gpu_count_unsupported` rather than silently clamping a billing-relevant
  value; `0` refuses every device request on that deployment. Before anything is
  created, the backend reports whether the selected daemon supports the
  requested resource at all: a vendor it cannot realize is refused with
  `gpu_vendor_unsupported`, and a daemon predating the device-request API
  (Docker Engine 19.03) is refused with `gpu_backend_unsupported` rather than
  running the caller's workload without the resource it asked for.
- **Unrestricted container path.** `container.run_container` under
  `workflow_docker_mode=unrestricted` realizes the GPU request as the vendor's
  Docker device request (`--gpus all`, or `--gpus <count>`). Request validation
  does not consult runner-profile device policy, because an unrestricted
  container has no runner profile.
- **Profile path.** A profile-backed `WorkloadRequest` carrying `resources.gpu`
  is denied with reason `unsupported_gpu_request`. `WorkloadDevicePolicy` grants
  no device access, and a profile must not gain device authority implicitly.
- **Raw Docker CLI path.** `container.run_docker` rejects `resources.gpu`: that
  request builds its own container arguments, so a MoonMind-realized device
  request would be silently ignored.
- **Disabled and profiles modes.** A GPU container request is denied at the
  dispatch boundary with `docker_workflows_disabled` or
  `docker_workflow_mode_forbidden` before any validation or launch.

MoonMind never selects the image, appends application commands, or branches on
what the workload does.

## Lifecycle and evidence

A GPU container follows generic container behavior without exception: the
workspace, artifacts, and scratch directories are bind-mounted at their own
absolute paths; caller-declared named cache volumes are mounted as requested;
stdout and stderr are captured bounded and redacted; `timeoutSeconds` and
cancellation stop and kill only the run-owned container; declared outputs are
collected by relative path under the authorized artifacts root, including
partial outputs after a timeout.

Job cleanup removes exactly the container MoonMind named and launched, and only
for a device-bearing unrestricted request or a profile whose cleanup policy asks
for it. A CPU-only unrestricted request keeps the retained-container semantics
its already recorded `workload.run` history was launched with, so a replayed or
retried in-flight attempt sees unchanged cleanup behavior and unchanged cleanup
metadata. Cleanup never removes the image and never removes named cache volumes,
so a later request reuses both. `docker run` keeps its default if-missing image
behavior.

Every unrestricted run records generic GPU observations under
`metadata.workload.gpu`: the caller's request, the realized device-request
arguments, and a launch failure classification. CPU-only requests record `null`
there and are otherwise unchanged.

Every canonical container job that requested a GPU records one bounded
`GpuObservation` in the durable job record (`container_jobs.gpu_observation_json`)
and projects it as `ContainerJobStatus.gpu`:

| Field | Meaning |
|---|---|
| `vendor`, `count`, `capabilities` | The caller's own semantic request, unchanged |
| `backendSupported` | Whether the selected daemon reported support for the requested resource |
| `launched` | Whether the container actually started carrying the device request |
| `failureClass` | The stable generic class when the resource was refused, otherwise absent |

The observation is request-identified: it always describes what the caller
submitted, so a job refused before the backend reported anything still projects
the resource it asked for. A refusal the backend can only reach after it
reported support -- `gpu_runtime_unavailable` and `gpu_resource_unavailable`, both
raised at container create or start -- reports `backendSupported = true`, because
the class itself is that evidence and the refusal is raised before an
observation can be returned. It deliberately carries no Docker device-request
structure, flag, device path, endpoint, driver version, or ownership label, so it
is safe to persist, to project to the caller, and to cross Temporal workflow
history. A CPU-only job has no observation at all, and its request, Temporal
payloads, and durable record are byte-identical to before this field existed.

Canonical job cleanup removes only the run-owned container. It never removes the
image and never removes a deployment-approved shared cache volume, so a device
count or capability change never invalidates a warm image or cache.

## Failure classification

### Canonical container-job classes

A canonical job refuses an unsupported GPU resource before the caller's workload
executes, with one stable generic class. No class names a vendor product, an
application, or a framework.

| Class | Raised when | Where |
|---|---|---|
| `gpu_request_invalid` | The GPU request is malformed: an unknown capability, an empty capability list, an unknown field, or a wrong JSON type | HTTP and MCP submission, before durable identity exists |
| `gpu_vendor_unsupported` | The requested vendor is not supported by the contract, or not realizable by the selected backend | Submission, then again at the trusted backend |
| `gpu_count_unsupported` | The device count is not a positive integer or `all`, or exceeds the deployment device ceiling | Submission, then again at the trusted backend |
| `gpu_backend_unsupported` | The selected daemon has no device-request API, or refused to select a device driver | Trusted backend, before or during container start |
| `gpu_runtime_unavailable` | The vendor container runtime is missing or failed to initialize | Trusted backend, at container start |
| `gpu_resource_unavailable` | The runtime is present but exposed no usable device | Trusted backend, at container start |

A submission refusal is classified from the request contract's own typed
refusal, not from an error string, so HTTP and MCP callers receive the identical
class and neither response echoes the rejected value. A launch refusal is
classified from the daemon's own diagnostic through the shared classifier below;
the diagnostic itself is never echoed, because it can name trusted host paths
and endpoints. Ordinary image, workspace, launch, execution, timeout,
cancellation, artifact, and cleanup failures keep their existing classes, and a
container that started and then exited nonzero remains `execution`.

### Shared launch-refusal evidence

Docker's refusal of the device request is reported distinctly from an ordinary
container process exit. On the unrestricted path a refusal carries `failureClass
= gpu_device_request_rejected` and one of these generic reasons, which the
canonical backend maps to `gpu_runtime_unavailable`, `gpu_resource_unavailable`,
or `gpu_backend_unsupported`:

| Reason | Meaning |
|---|---|
| `nvidia_runtime_unavailable` | The NVIDIA container runtime is missing or failed to initialize |
| `gpu_device_unavailable` | The runtime is present but exposed no usable device |
| `device_request_unsupported` | The daemon or client does not understand the device request |
| `gpu_device_request_rejected` | The daemon refused the device request for another reason |

The diagnostic is matched most-specific-first. The vendor stack reports a device
failure *through* its own tooling, so a host with a working runtime and no usable
device names both the generic `nvidia-container-cli` prefix and a specific device
condition; the specific condition decides the reason, and a bare vendor-stack
prefix is the last resort. Otherwise an operator would be directed at repairing a
runtime that already works instead of at device capacity.

A refusal is recognized only from objective launch-refusal evidence: stderr must
carry a vendor/runtime diagnostic. Where one `docker run` merges the launch phase
with the application's own exit, Docker must additionally report its own
launch-failure exit status (125); where the launch phase is its own command --
the canonical backend's container create and start -- that command's failure
already cannot be an application exit. A container that started and exited
nonzero carries no GPU failure class even when its own stderr names NVML,
`nvidia-container-cli`, or a device driver, because Docker forwards the
application's output verbatim. An unreachable Docker daemon, an unavailable or
unauthorized image, and a missing workspace bind are ordinary generic launch
failures, never GPU refusals.

## Qualification

Qualification runs in two layers.

**CPU-capable contract fixtures** (`tests/unit/workloads/`,
`tests/unit/workflows/temporal/test_gpu_container_dispatch.py`,
`tests/unit/workflows/temporal/test_container_job_gpu_resources.py`,
`tests/unit/api/test_container_job_gpu_transport.py`) run on any
runner against a synthetic Docker launcher and cover request validation,
serialization and dispatch, Docker command construction, workspace and output
mounts, logs, timeout, cancellation, cleanup, image and command preservation,
warm reuse, and the negative matrix. The canonical fixtures additionally cover
the durable round trip -- HTTP and MCP submission, idempotent replay, the durable
record, the trusted-worker projection, and the projected status observation --
the pre-start support report, each stable failure class, and the proof that one
semantic request realizes the identical device request on both container
boundaries.
`tests/unit/workloads/test_gpu_container_genericity_guard.py` statically fails
if a production GPU module or a qualification fixture acquires a game name, an
engine filename/argument/cache name, a project image constant, project
gate/scenario/proof/bundle parsing, or a repository-specific condition.

**Real NVIDIA integration**
(`tests/integration/workloads/test_nvidia_container_qualification_journey.py`)
runs a caller-supplied GPU workload on a deployment-owned GPU host through the
same trusted Docker boundary used in production, on both container boundaries:
the unrestricted `workload.run` route and the canonical container-job backend
driven through its real Activity sequence (workspace resolution, image
acquisition, create, start, observe, evidence publication, removal, cleanup).
The canonical leg asserts the resolved GPU observation, the collected declared
output, a surviving image, and that a device count above the deployment ceiling
is refused with `gpu_count_unsupported` before any workload runs. It is marked `requires_gpu`,
is excluded from required CI, and skips on a CPU-only runner with an explicit
environment reason. Preflight probes actual device availability with one bounded
device-bearing container, so a host with the NVIDIA runtime registered but no
usable device also skips explicitly instead of failing inside the caller's
workload. Every helper invocation targets the same configured Docker daemon the
workload ran on. Run it with `./tools/test_gpu_qualification.sh` on the GPU
host; the image, command, GPU count, workspace root, cache volume, and record
directory are supplied as test configuration and are not MoonMind product
settings. A command override receives the declared-output path in
`MOONMIND_GPU_QUALIFICATION_OUTPUT`; the journey declares that output only when
the command agreed to write it, so an ordinary override is not failed for a
missing fixture-specific file.

The journey publishes a compact generic `GpuQualificationRecord` carrying the
MoonMind revision, request schema version, image reference and digest, the
generic GPU request and realized device-request arguments, the Docker result,
declared-output checksums, and timestamps. Raw Docker endpoints, credentials,
and unrelated host environment never enter published evidence.

Record construction is evidence-bound, not request-bound:

- `deviceRequestArgs` comes from the executed result's `metadata.workload.gpu`
  observations. A result carrying no realized device request, or one realized
  through a different substrate, is rejected instead of recorded as if the
  device request had been honored.
- The result must belong to the requested container: `requestId`,
  `containerName`, `imageRef`, and the realized GPU request must all match the
  submitted request, so a concurrent or retried run's outcome can never be
  recorded under another request's identity.
- `moonmindRevision` requires an immutable identity (`MOONMIND_BUILD_SHA` or
  `MOONMIND_IMAGE_DIGEST`); there is no placeholder value, and the operator
  command resolves the checked-out revision on a source checkout.
- `imageDigest` is selected by matching repository, so a digest belonging to an
  alias repository is never attributed to the requested image reference.

Records are published to a durable root outside the ephemeral per-run
workspace (`MOONMIND_GPU_QUALIFICATION_RECORD_DIR`, default
`var/gpu_qualification`), and the operator command prints each published path,
so a successful qualification always leaves citable evidence behind.

Because the record and the request contract are generic, the same qualification
runs against both container boundaries without changing workload semantics: a
repository-owned skill moves from the unrestricted route to `container.submit`
by re-enveloping the request, not by rewriting its image, command, or artifact
interpretation.
