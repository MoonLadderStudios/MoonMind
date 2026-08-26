# Generic GPU Container Resources Contract

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-08-26
**Audience:** Contributors, skill authors, and operators
**Authority:** Target semantics for generic GPU resources on unrestricted container execution
**Owning Surface:** moonmind/schemas/workload_models.py, moonmind/workloads/
**Related Docs:** [Skill and Plan Contracts](SkillAndPlanContracts.md), [Docker Backend Service](../ManagedAgents/DockerBackendService.md)
**Related Implementation:** `UnrestrictedContainerRequest.resources.gpu`, `DockerWorkloadLauncher` (MoonLadderStudios/MoonMind#3775, epic #3774)

## 1. Purpose

A repository-owned workflow or skill can ask MoonMind to run a caller-owned
container with NVIDIA GPU resources. MoonMind executes the request through its
trusted Docker boundary without knowing what application runs inside the
container.

The operator switch is the existing deployment-owned mode:

```env
MOONMIND_WORKFLOW_DOCKER_MODE=unrestricted
```

No runner profile, trust level, approval, Host Class, device policy, project
adapter, image registry, or project-specific MoonMind setting is required.

## 2. Ownership boundary

The caller owns the image reference, the entrypoint, the command and its
arguments, environment values, the requested GPU resources, the
workspace-relative workdir, generic cache or named-volume requests, declared
outputs, the timeout, and the interpretation of exit codes, logs, gates, and
artifacts.

MoonMind owns validation of the generic request shape, resolution of the
current authorized workspace, dispatch through the trusted Docker worker, image
pull or reuse under existing unrestricted behavior, realization of the generic
GPU device request, logs, timeout, cancellation, cleanup, and declared-output
collection.

MoonMind does not select an image, inspect repository-specific files, append
application arguments, or parse application artifacts. It performs no
application-level GPU readiness check; a caller that wants one runs its own
diagnostic command inside its own image.

## 3. Request contract

`resources.gpu` on an unrestricted container request
(`container.run_container`) carries the versioned generic GPU resource model:

```json
{
  "image": "caller-owned-image:tag-or-digest",
  "command": ["caller-owned-command", "arg"],
  "resources": {
    "gpu": {
      "contractVersion": "v1",
      "vendor": "nvidia",
      "count": "all"
    }
  }
}
```

| Field | Values | Meaning |
|---|---|---|
| `contractVersion` | `v1` | Versioned wire shape, so the same request can be carried unchanged into the canonical container-job contract. |
| `vendor` | `nvidia` | Required. The only vendor supported in v1. |
| `count` | `all` or a positive integer | Defaults to `all`. |
| `capabilities` | subset of `compute`, `utility`, `graphics`, `video` | Optional NVIDIA driver capability selection. Omit it unless the image requires a specific set. |

Generic shared memory stays on the existing `resources.shmSize` field; GPU
support introduces no second spelling for it.

The request shape rejects unknown vendors, zero or negative counts,
non-numeric counts other than `all`, unknown capability values, unknown
contract versions, and any additional field. A caller-controlled Docker
endpoint, runtime socket, ownership labels, privileged mode, host namespaces,
and arbitrary device paths remain unreachable through this field.

`resources.gpu` exists only on the structured unrestricted container request:

- **Profile mode** keeps `WorkloadResourceOverrides` and its own
  `devicePolicy`. A profile-backed request that carries `resources.gpu` is a
  validation error, and no GPU-capable runner profile exists or is required.
- **Raw `container.run_docker`** commands are caller-composed. MoonMind never
  appends `--gpus` to them, so `resources.gpu` is a validation error there too.
  A caller that still uses the raw interim route writes `--gpus` in its own
  command and MoonMind forwards it unchanged.

## 4. Docker realization

The launcher translates the generic request into the Docker device request:

| Request | Launch argument | Engine API `DeviceRequest` |
|---|---|---|
| `{"vendor": "nvidia", "count": "all"}` | `--gpus all` | `{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}` |
| `{"vendor": "nvidia", "count": 2}` | `--gpus 2` | `{"Driver": "nvidia", "Count": 2, "Capabilities": [["gpu"]]}` |
| `{"vendor": "nvidia", "count": "all", "capabilities": ["compute", "utility"]}` | `--gpus driver=nvidia,count=all,"capabilities=compute,utility"` | `{"Driver": "nvidia", "Count": -1, "Capabilities": [["compute", "utility"]]}` |

GPU support adds no other launch authority. The non-overridable hardening
posture (`--privileged=false`, `--cap-drop ALL`, `no-new-privileges`), the
workspace and scratch binds, the caller's generic cache volumes, the network
mode, and the caller's image and argv are all unchanged.

## 5. Bounded observations

Each run records a generic, application-neutral observation. It contains
whether a GPU request was present, its vendor, requested count and
capabilities, and whether Docker accepted the request. Host driver details and
daemon configuration stay out of the public result.

Every run also records a generic launch classification derived only from
signals Docker itself exposes:

| `launchOutcome` | Condition |
|---|---|
| `succeeded` | Container process exited `0`. |
| `docker_request_rejected` | `docker run` exited `125`: the daemon refused to create the container. An unsupported device request, an unavailable NVIDIA runtime, and an absent GPU all surface here. |
| `container_start_failed` | Exit `126`: the caller's command could not be invoked. |
| `container_command_not_found` | Exit `127`. |
| `process_failed` | Any other non-zero container process exit code. |
| `timed_out` | The run exceeded `timeoutSeconds`. |
| `unknown` | No exit code was observed. |

Cancellation is distinguishable at the same boundary: it propagates as a
cancellation rather than a terminal result.

## 6. Lifecycle

GPU containers use the same unrestricted lifecycle as CPU-only containers:
bounded logs, timeout and cancellation support, run-owned container identity
that stays stable across a retry of the same attempt, and declared-output plus
glob-based collection that is preserved after failure, timeout, or
cancellation. Cleanup removes only the run-owned container and run-owned
temporary data; the image and any caller-requested shared cache volumes
survive job completion.

## 7. Out of scope

MoonMind core carries no project-specific cache, build-tool, image, or artifact
settings for GPU workloads; a repository skill requests generic cache volumes
using its own conventions. Profile-mode GPU support, non-NVIDIA GPU runtimes,
GPU scheduling, quotas, tenancy, trust levels, and approvals are not part of
this contract. Extending the same generic GPU resource request to the canonical
asynchronous container-job contract and backend is tracked separately by
MoonLadderStudios/MoonMind#3779; the `contractVersion` field exists so that
convergence carries the caller's request unchanged.
