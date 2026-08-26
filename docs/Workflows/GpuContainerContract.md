# GPU Container Contract

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-08-26
**Audience:** Contributors and runtime authors
**Authority:** Target semantics for caller-supplied GPU containers on MoonMind's container boundaries
**Owning Surface:** moonmind/workloads/, moonmind/schemas/container_job_models.py
**Related Docs:** [Skill and Plan Contracts](SkillAndPlanContracts.md), [Workflow Architecture](WorkflowArchitecture.md)
**Related Implementation:** `moonmind/workloads/gpu.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/schemas/container_job_models.py`, `moonmind/workflows/temporal/container_job_backend.py` (MoonLadderStudios/MoonMind#3777)

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

An unsupported vendor, a non-positive count, or an unknown field is rejected at
request validation with an actionable error. `count` is validated before
coercion, so `true`, `"2"`, and `2.0` are rejected rather than realized as
device counts the caller never declared. The GPU request travels on the same
request as the caller's image, command, entrypoint, workdir, environment, cache
mounts, network mode, timeout, and declared outputs.

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

## Authority boundaries

- **Canonical container-job path.** `ContainerJobSpec.resources.gpu` is admitted
  by the deployment's non-overridable device ceiling
  (`MOONMIND_CONTAINER_BACKEND_MAX_GPU_COUNT`) and then realized by the
  container-job backend as the vendor's Docker device request. An unset ceiling
  imposes no MoonMind bound, so host capability remains the gate; a finite
  ceiling rejects both a higher count and the unbounded `all` with
  `resource_limit_exceeded` rather than silently clamping a billing-relevant
  value; `0` refuses every device request on that deployment.
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

Every run records generic GPU observations under
`metadata.workload.gpu`: the caller's request, the realized device-request
arguments, and a launch failure classification. CPU-only requests record `null`
there and are otherwise unchanged.

## Failure classification

Docker's refusal of the device request is reported distinctly from an ordinary
container process exit. A refusal carries `failureClass =
gpu_device_request_rejected` and one of these generic reasons:

| Reason | Meaning |
|---|---|
| `nvidia_runtime_unavailable` | The NVIDIA container runtime is missing or failed to initialize |
| `gpu_device_unavailable` | The runtime is present but exposed no usable device |
| `device_request_unsupported` | The daemon or client does not understand the device request |
| `gpu_device_request_rejected` | The daemon refused the device request for another reason |

A refusal is recognized only from objective launch-refusal evidence: Docker
itself must report its own launch-failure exit status (125) *and* stderr must
carry a vendor/runtime diagnostic. A container that started and exited nonzero
carries no GPU failure class even when its own stderr names NVML,
`nvidia-container-cli`, or a device driver, because Docker forwards the
application's output verbatim. An unreachable Docker daemon, an unavailable or
unauthorized image, and a missing workspace bind are ordinary generic launch
failures, never GPU refusals.

## Qualification

Qualification runs in two layers.

**CPU-capable contract fixtures** (`tests/unit/workloads/`,
`tests/unit/workflows/temporal/test_gpu_container_dispatch.py`) run on any
runner against a synthetic Docker launcher and cover request validation,
serialization and dispatch, Docker command construction, workspace and output
mounts, logs, timeout, cancellation, cleanup, image and command preservation,
warm reuse, and the negative matrix.
`tests/unit/workloads/test_gpu_container_genericity_guard.py` statically fails
if a production GPU module or a qualification fixture acquires a game name, an
engine filename/argument/cache name, a project image constant, project
gate/scenario/proof/bundle parsing, or a repository-specific condition.

**Real NVIDIA integration**
(`tests/integration/workloads/test_nvidia_container_qualification_journey.py`)
runs a caller-supplied GPU workload on a deployment-owned GPU host through the
same trusted Docker boundary used in production. It is marked `requires_gpu`,
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

Because the record and the request contract are generic, this qualification is
rerunnable against a future canonical container path without changing workload
semantics.
