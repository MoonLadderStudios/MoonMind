# GPU Container Contract

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-08-26
**Audience:** Contributors and runtime authors
**Authority:** Target semantics for caller-supplied GPU containers on the unrestricted container boundary
**Owning Surface:** moonmind/workloads/
**Related Docs:** [Skill and Plan Contracts](SkillAndPlanContracts.md), [Workflow Architecture](WorkflowArchitecture.md)
**Related Implementation:** `moonmind/workloads/gpu.py`, `moonmind/workloads/docker_launcher.py` (MoonLadderStudios/MoonMind#3777)

MoonMind executes a caller-supplied NVIDIA GPU container through the same
unrestricted container boundary it uses for every other container. The GPU is a
resource the caller requests; it is not a workload type MoonMind recognizes.

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

An unsupported vendor, a non-positive count, a non-integer count, or an unknown
field is rejected at request validation with an actionable error. The GPU
request travels on the same request as the caller's image, command, entrypoint,
workdir, environment, cache mounts, network mode, timeout, and declared outputs.

Any repository skill can produce this request shape: it is the ordinary
`container.run_container` payload with one additional resource field.

## Authority boundaries

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

Job cleanup removes exactly the container MoonMind named and launched. It never
removes the image and never removes named cache volumes, so a later request
reuses both. `docker run` keeps its default if-missing image behavior.

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

A container that started and exited nonzero carries no GPU failure class. An
unreachable Docker daemon, an unavailable or unauthorized image, and a missing
workspace bind are ordinary generic launch failures, never GPU refusals.

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
environment reason. Run it with `./tools/test_gpu_qualification.sh` on the GPU
host; the image, command, GPU count, workspace root, and cache volume are
supplied as test configuration and are not MoonMind product settings.

The journey publishes a compact generic `GpuQualificationRecord` carrying the
MoonMind revision, request schema version, image reference and digest, the
generic GPU request and realized device-request arguments, the Docker result,
declared-output checksums, and timestamps. Raw Docker endpoints, credentials,
and unrelated host environment never enter published evidence.

Because the record and the request contract are generic, this qualification is
rerunnable against a future canonical container path without changing workload
semantics.
