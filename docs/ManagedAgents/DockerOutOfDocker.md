# DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind

**Implementation tracking:** Open rollout and backlog notes for this surface live in [`../tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`](../tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md). Other implementation handoffs may still live in MoonSpec artifacts (`specs/<feature>/`) or local-only files (for example `artifacts/`), not as migration checklists in canonical `docs/`.
**Status:** Desired state
**Owners:** MoonMind Platform
**Last updated:** 2026-04-23

**Related:**

* [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
* [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
* [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
* [`docs/Tasks/SkillAndPlanContracts.md`](../Tasks/SkillAndPlanContracts.md)
* [`docs/Temporal/ArtifactPresentationContract.md`](../Temporal/ArtifactPresentationContract.md)
* [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)

---

## 1. Purpose

This document defines MoonMind’s **Docker-out-of-Docker (DooD)** architecture for launching **specialized workload containers** through the MoonMind / Temporal control plane.

The DooD system supports three explicit deployment modes:

* `disabled`
* `profiles`
* `unrestricted`

These modes govern whether workflows may use Docker-backed workload tools, and if so, whether they may use only approved runner profiles or may also launch arbitrary runtime containers and Docker CLI workloads.

The DooD architecture exists to support heavyweight, toolchain-specific, or environment-specific work that must not be baked into the generic MoonMind worker image or the Codex managed session container, including:

* Unreal Engine build, cook, package, or test workloads
* Unity batchmode test or build workloads
* .NET, SDK, distro, or toolchain specific build/test workloads
* load test containers and benchmark runners
* bounded helper containers needed to support a step’s execution
* deployment-gated arbitrary runtime containers in trusted environments

The goals are:

* keep the **Codex managed session plane** focused on managed agent continuity
* keep the **Temporal control plane** as the owner of launch, policy, cancellation, audit, and observability
* keep worker images generic and maintainable
* support both curated and unrestricted Docker-backed workloads without giving the normal session container raw Docker authority

This document replaces older framing that treated DooD primarily as a profile-only specialized workload system. The desired state includes both:

* a stable **profile-backed** path for normal execution
* a deployment-gated **unrestricted** path for trusted environments that need arbitrary runtime containers or Docker CLI operations

---

## 2. Core decisions

MoonMind adopts the following governing rules for Docker-backed workloads:

1. **Specialized Docker workloads are control-plane-launched workloads.**
 They are not ad hoc containers started directly by the Codex runtime.

2. **Docker workflow access is governed by an explicit deployment mode.**
 The canonical modes are `disabled`, `profiles`, and `unrestricted`.

3. **A Codex session container is not given unrestricted Docker authority by default.**
 This remains true even when the deployment mode is `unrestricted`.

4. **The profile-backed path remains the normal execution path.**
 `container.run_workload`, `container.start_helper`, and `container.stop_helper` remain profile-validated and deployment-curated.

5. **Arbitrary runtime containers are first-class only in `unrestricted` mode.**
 They are exposed through a structured MoonMind tool contract rather than by widening the profile-backed contract.

6. **Raw Docker CLI is an unrestricted escape hatch, not the normal arbitrary-container contract.**
 The public unrestricted container interface is `container.run_container`. The public unrestricted Docker CLI interface is `container.run_docker`.

7. **Session identity and workload identity remain separate.**
 A workload container is not a managed session and not a `MoonMind.AgentRun` unless it is itself a true agent runtime.

8. **Artifacts and bounded metadata remain authoritative.**
 Container-local state, daemon state, and terminal scrollback are not durable truth.

9. **Invalid mode values fail fast.**
 Unsupported Docker mode values are configuration errors and must prevent startup.

10. **The DooD core remains domain-agnostic.**
 Unreal, dotnet, load test, and similar workloads are modeled as profiles, curated wrappers, or unrestricted requests. The DooD core does not contain workload-specific branching.

---

## 3. Scope and non-goals

### 3.1 In scope

This document defines:

* the Docker workflow permission model
* the architectural separation between managed sessions and workload containers
* the tool surface for profile-backed and unrestricted Docker execution
* workspace, mount, artifact, timeout, and cleanup rules
* the policy boundary for runner profiles and unrestricted execution
* the audit and observability contract for Docker-backed workloads

### 3.2 Out of scope

This document does **not** define:

* Kubernetes orchestration
* a generic container marketplace
* cross-task session reuse
* raw Docker access from inside the Codex session container
* provider-native managed session protocols
* a general-purpose arbitrary shell execution surface
* a permanent unrestricted detached service framework spanning multiple steps without explicit MoonMind lifecycle ownership

If MoonMind later needs reusable non-agent services with their own lifecycle across multiple steps, that becomes a sibling workload lifecycle. It does not collapse the distinction between the managed session plane and the Docker workload plane.

---

## 4. Terminology

### 4.1 Session container

The **session container** is the task-scoped Codex container defined by `CodexManagedSessionPlane`.

It owns session continuity state such as:

* `session_id`
* `session_epoch`
* `container_id`
* `thread_id`
* `active_turn_id`

It is the continuity and performance cache for the task-scoped managed session.

### 4.2 Workload container

A **workload container** is a separate Docker container launched to execute a specialized non-agent workload against the task workspace.

Examples include:

* an Unreal test runner
* a Unity build image
* a distro-specific build/test container
* a load test container
* a bounded helper service container
* an unrestricted runtime-selected container

A workload container is **not** the session container.

### 4.3 Runner profile

A **runner profile** is MoonMind’s curated definition of an approved workload-container shape.

A runner profile typically specifies:

* image reference
* entrypoint or command wrapper
* workspace mount contract
* optional cache volumes
* allowed environment variables
* resource profile
* network policy
* optional device policy
* timeout defaults
* cleanup policy

### 4.4 Profile-backed workload

A **profile-backed workload** is a DooD execution that resolves through an approved runner profile and validates through the runner profile registry.

### 4.5 Unrestricted container request

An **unrestricted container request** is a trusted, deployment-gated DooD execution that supplies the image and runtime container shape at execution time through the `container.run_container` tool.

### 4.6 Unrestricted Docker CLI request

An **unrestricted Docker CLI request** is a trusted, deployment-gated DooD execution that runs a Docker CLI command through the configured Docker host or proxy via the `container.run_docker` tool.

### 4.7 Session-assisted workload launch

A **session-assisted workload launch** occurs when a managed session step determines that a specialized toolchain is required, but MoonMind launches the workload through the control plane instead of granting the session direct Docker daemon access.

---

## 5. Architectural layering

MoonMind treats Docker-backed workloads as a dedicated workload plane adjacent to, but distinct from, the managed session plane.

### 5.1 Orchestration layer

MoonMind / Temporal owns:

* workflow orchestration
* tool routing
* policy enforcement
* timeout and cancellation semantics
* artifact publication
* observability
* cleanup and orphan handling

### 5.2 Managed session plane

`CodexManagedSessionPlane` owns:

* the task-scoped Codex session container
* thread and turn lifecycle
* `clear_session`, `interrupt_turn`, and related session actions
* session continuity artifacts and bounded session metadata

The managed session plane does not become the generic owner of all other containers.

### 5.3 Docker workload plane

`DockerOutOfDocker` owns MoonMind-side rules for launching specialized workload containers against the workspace.

It is responsible for:

* Docker proxy usage
* mode-aware permission enforcement
* container launch arguments
* workspace and cache mounts
* workload labeling and identity
* stdout and stderr capture
* diagnostics capture
* cleanup
* audit metadata

It does not own Codex thread lifecycle or session continuity semantics.

### 5.4 Logical execution capability

All DooD-backed tools are routed through the logical MoonMind capability:

* `docker_workload`

The current deployment may satisfy `docker_workload` on the `agent_runtime` fleet. A dedicated Docker workload fleet may be introduced later if hardware, isolation, egress, or scaling requirements justify the split.

The logical capability is stable. The physical fleet assignment is an implementation detail.

---

## 6. Docker workflow permission modes

### 6.1 Configuration surface

The canonical configuration surface is:

```text
MOONMIND_WORKFLOW_DOCKER_MODE=disabled|profiles|unrestricted
```

Mode behavior is normalized at settings load time.

Rules:

* default mode is `profiles`
* unsupported values are startup errors
* task instructions cannot change the mode
* the mode is deployment-owned, not planner-owned and not task-owned

If a legacy boolean alias is temporarily supported during migration, it maps only as follows:

* `MOONMIND_WORKFLOW_DOCKER_ENABLED=false` → `disabled`
* `MOONMIND_WORKFLOW_DOCKER_ENABLED=true` → `profiles`

No runtime behavior may depend on the legacy boolean after settings normalization.

### 6.2 `disabled`

`disabled` means:

* no workflow or task may use Docker-backed workload tools
* `container.run_workload` is denied
* `container.start_helper` is denied
* `container.stop_helper` is denied
* `container.run_container` is denied
* `container.run_docker` is denied

This preserves the existing “Docker workflows disabled” posture.

### 6.3 `profiles`

`profiles` is the default safe mode.

It means:

* workflows may use Docker-backed workloads only through approved runner profiles
* `container.run_workload` is allowed and remains profile-validated
* `container.start_helper` and `container.stop_helper` are allowed and remain profile-validated
* curated Docker-backed tools such as `unreal.run_tests` remain available
* `container.run_container` is denied
* `container.run_docker` is denied

Raw images, raw Docker flags, and arbitrary Docker CLI are not part of this mode.

### 6.4 `unrestricted`

`unrestricted` is the deployment-gated mode for trusted environments.

It means:

* all `profiles` behavior remains available
* `container.run_container` is available for arbitrary runtime containers
* `container.run_docker` is available for Docker CLI execution through the configured Docker host or proxy
* unrestricted usage is explicit in audit and result metadata
* unrestricted capability exists only because the deployment enables it; task instructions alone cannot enable it

Unrestricted mode does **not** change the session-plane security boundary. Normal agent and session containers still do not receive direct raw Docker socket access.

### 6.5 Tool exposure and runtime enforcement

Tool exposure and runtime enforcement follow the same mode model.

Rules:

* `disabled`: Docker-backed tools are omitted from the default registry snapshot and denied at runtime if invoked directly
* `profiles`: profile-backed DooD tools are included; unrestricted tools are omitted and denied at runtime if invoked directly
* `unrestricted`: profile-backed and unrestricted DooD tools are included

Runtime enforcement remains authoritative even when tool registration and planner exposure are mode-aware.

---

## 7. Supported container roles

### 7.1 Role A: task-scoped managed session container

The Codex managed session plane remains:

* Docker only
* one task-scoped session container per task
* continuity reused only within the same task

This role is governed by `CodexCliManagedSessions.md`, not by this document.

### 7.2 Role B: one-shot profile-backed workload container

This is the default DooD role.

MoonMind launches a separate ephemeral container to perform one bounded specialized job, such as:

* `unreal.run_tests`
* `unreal.build_target`
* `container.run_workload` using an approved runner profile

The container exits when the command completes and MoonMind returns a normal tool result.

### 7.3 Role C: bounded helper workload container

Some steps require a non-agent helper container that remains alive long enough to support a bounded execution window.

Examples:

* an Unreal dedicated-server test target
* a temporary integration-test backing service
* a simulator or driver-specific helper runtime

Rules:

* helper containers remain workload containers, not managed sessions
* helper lifecycle is explicit and bounded
* helper ownership is attached to a step or step-group
* helper containers remain profile-backed in the desired state
* unrestricted mode does not introduce a general detached arbitrary-helper contract

### 7.4 Role D: one-shot unrestricted runtime container

This role is available only in `unrestricted` mode.

MoonMind launches a separate ephemeral container from a runtime-selected image through the `container.run_container` tool.

This contract is intended for trusted environments that need arbitrary runtime container selection without changing worker images or pre-registering every image as a runner profile.

### 7.5 Role E: unrestricted Docker CLI execution

This role is available only in `unrestricted` mode.

MoonMind runs a Docker CLI command through the `container.run_docker` tool on the trusted Docker-capable worker plane.

This contract exists for advanced use cases such as:

* `docker compose`
* `docker build`
* `docker pull`
* `docker inspect`
* other daemon-mediated Docker operations not represented by the structured container contract

This contract is explicitly Docker CLI only. It is not a generic arbitrary shell execution surface.

---

## 8. Ownership and routing model

### 8.1 Primary rule

Specialized Docker workloads enter MoonMind through the **tool execution path**.

MoonMind does not extend `agent_runtime.*` session verbs to mean “run an arbitrary container.”

### 8.2 Tool-path default

The default execution model is:

1. a plan step or session-assisted step invokes an executable tool
2. the tool resolves from the pinned tool registry snapshot
3. MoonMind routes the tool to a worker fleet with `docker_workload` capability
4. the worker launches the workload through the configured Docker host or proxy
5. MoonMind publishes artifacts and returns a normal tool result

### 8.3 Executor contract

All DooD-backed tools are modeled as executable tools with:

* `tool.type = "skill"`
* `executor.activity_type = "mm.tool.execute"`
* selector mode by capability
* required capability `docker_workload`

This applies to:

* curated domain tools
* `container.run_workload`
* `container.start_helper`
* `container.stop_helper`
* `container.run_container`
* `container.run_docker`

### 8.4 Relationship to `agent_runtime`

True managed agent runtime execution and session supervision remain under `agent_runtime.*` and `MoonMind.AgentRun`.

A Docker-backed workload is not a managed session merely because Docker is involved.

The current deployment may satisfy `docker_workload` on the `agent_runtime` worker fleet. That does not change the identity boundary.

### 8.5 Domain-agnostic core

The DooD core is generic.

It does not contain hardcoded branches for load test, Unreal, dotnet, or similar workload families. Domain-specific behavior is represented through:

* curated tools
* runner profiles
* unrestricted container requests
* unrestricted Docker CLI requests

---

## 9. Session-plane interaction model

### 9.1 What a Codex session may do

A Codex-managed session may:

* inspect the repository
* determine that a specialized toolchain is required
* request a MoonMind tool such as `unreal.run_tests`, `container.run_workload`, or `container.run_container`
* consume the returned artifacts and metadata
* continue the task-scoped managed session afterward

### 9.2 What a Codex session may not do by default

A Codex-managed session must not, by default:

* mount the raw host Docker socket
* receive unrestricted `DOCKER_HOST` access
* create arbitrary containers directly on the daemon
* bypass Temporal for launches that affect workspace state or task progress

This remains true in `unrestricted` mode. Unrestricted mode expands what the control plane may launch, not what the session container may launch directly.

### 9.3 Session and workload lifecycle relation

Rules:

* `clear_session` affects the Codex session lifecycle only
* `clear_session` does not redefine workload identity
* session artifacts remain session-plane artifacts
* workload artifacts remain workload outputs
* task cancellation best-effort cancels both the session turn and any in-flight owned workload containers

### 9.4 Association metadata

If a workload is launched from a session-assisted step, MoonMind may attach association metadata such as:

* `session_id`
* `session_epoch`
* `source_turn_id`

These are association fields only. They do not make the workload container part of session identity.

---

## 10. Workspace and volume contract

### 10.1 Shared workspace root

The canonical shared task workspace remains a named volume such as:

* `agent_workspaces` mounted at `/work/agent_jobs`

Recommended layout:

* `run_root = /work/agent_jobs/<task_run_id>`
* `repo_dir = /work/agent_jobs/<task_run_id>/repo`
* `artifacts_dir = /work/agent_jobs/<task_run_id>/artifacts/<step_id>`
* `scratch_dir = /work/agent_jobs/<task_run_id>/scratch/<step_id>`

### 10.2 Path ownership

All DooD tools operate on MoonMind-owned task paths.

Rules:

* `repoDir` must resolve under the workspace root
* `artifactsDir` must resolve under the workspace root
* `scratchDir`, when present, must resolve under the workspace root
* declared outputs must resolve under `artifactsDir`

Invalid paths are rejected before launch.

### 10.3 Mount rules

A workload container receives only the mounts it needs.

Minimum common case:

* `agent_workspaces` mounted at `/work/agent_jobs`
* working directory set to the task repository or explicit workload workdir under the workspace

Additional mounts may include:

* deployment-approved named cache volumes
* deployment-approved helper-specific volumes declared by a runner profile

The structured unrestricted container contract does not expose arbitrary host-path mounts in the desired state.

### 10.4 Auth volume rule

Workload containers must not automatically inherit Codex, Claude, Gemini, or other auth volumes merely because they were requested by a managed session step.

Any auth or credential mount must be:

* explicitly declared by a runner profile, or
* explicitly provisioned by MoonMind policy for the unrestricted execution plane

### 10.5 Output discipline

Workload containers must write only to approved task paths and mounted caches.

They must not rely on:

* container-local home directories as durable output
* unnamed scratch layers as the only output location
* hidden background state outside MoonMind-owned mounts

---

## 11. User-facing tool surface

### 11.1 Tool inventory

The desired-state DooD tool surface is:

* curated domain tools such as `unreal.run_tests`
* `container.run_workload`
* `container.start_helper`
* `container.stop_helper`
* `container.run_container`
* `container.run_docker`

### 11.2 `container.run_workload`

`container.run_workload` is the generic profile-backed one-shot workload tool.

It is available in:

* `profiles`
* `unrestricted`

It is denied in:

* `disabled`

It requires an approved `profileId` and validates through the runner profile registry.

It remains generic and does not gain unrestricted fields such as:

* raw image strings
* arbitrary host-path mounts
* arbitrary Docker daemon flags
* unrestricted privilege flags

Minimum request shape:

* `profileId`
* `taskRunId`
* `stepId`
* `attempt`
* `repoDir`
* `artifactsDir`
* `command`
* `envOverrides`
* `timeoutSeconds`
* `declaredOutputs`
* optional report publication fields

### 11.3 `container.start_helper` and `container.stop_helper`

`container.start_helper` and `container.stop_helper` are the profile-backed helper lifecycle tools.

They are available in:

* `profiles`
* `unrestricted`

They are denied in:

* `disabled`

They remain profile-validated and bounded. Unrestricted mode does not transform them into a generic arbitrary detached-container framework.

### 11.4 `container.run_container`

`container.run_container` is the first-class unrestricted arbitrary-container tool.

It is available only in:

* `unrestricted`

It is denied in:

* `disabled`
* `profiles`

This tool allows a trusted workflow to select the runtime image at execution time. The image:

* need not be known at MoonMind worker build time
* need not be pre-registered as a runner profile
* remains subject to MoonMind workspace, artifact, timeout, and audit boundaries

Minimum request shape:

* `image` (required)
* `taskRunId`
* `stepId`
* `attempt`
* `repoDir`
* `artifactsDir`
* `scratchDir`
* `entrypoint`
* `command`
* `workdir`
* `envOverrides`
* `cacheMounts`
* `networkMode`
* `resources`
* `timeoutSeconds`
* `declaredOutputs`
* `report` / report publication fields

Desired-state boundaries for `container.run_container`:

* arbitrary runtime image selection is allowed
* arbitrary command and entrypoint are allowed
* workspace and deployment-approved named caches are allowed
* arbitrary host-path mounts are not exposed by this contract
* `--privileged` is not exposed by this contract
* host networking is not exposed by this contract
* implicit device access is not exposed by this contract
* automatic auth-volume inheritance is not allowed

The structured unrestricted contract is intentionally broad enough for arbitrary runtime containers while remaining narrower than raw Docker CLI.

### 11.5 `container.run_docker`

`container.run_docker` is the advanced unrestricted Docker CLI tool.

It is available only in:

* `unrestricted`

It is denied in:

* `disabled`
* `profiles`

Minimum request shape:

* `command` (required)
* `taskRunId`
* `stepId`
* `attempt`
* `repoDir`
* `artifactsDir`
* `timeoutSeconds`
* `envOverrides`
* `declaredOutputs`
* `report` / report publication fields

Validation rules:

* `command[0]` must be `"docker"`
* `["docker", "compose", ...]` is allowed
* arbitrary shell is not implied and is not allowed through this tool

This tool exists for advanced Docker operations that are not represented by `container.run_container`.

### 11.6 Planner and registry behavior

The runtime planner and tool registry preserve all DooD-backed tools as `tool.type = "skill"` nodes.

Mode-aware registry exposure rules apply:

* `disabled`: no DooD tools in the default registry
* `profiles`: curated and profile-backed DooD tools only
* `unrestricted`: curated, profile-backed, and unrestricted DooD tools

Runtime mode enforcement remains authoritative even when the registry is mode-aware.

---

## 12. Runner profile model

MoonMind prefers curated runner profiles for normal execution.

### 12.1 Minimum runner profile fields

A runner profile defines at least:

* `id`
* `image`
* `kind`
* `default_workdir`
* `entrypoint` or command wrapper
* required mounts
* optional cache volumes
* allowed extra environment variables
* resource profile
* network policy
* timeout defaults
* cleanup policy
* optional device policy

### 12.2 Example runner profile: Unreal Linux CI

```yaml
id: unreal-5_3-linux
kind: one_shot
image: ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3
workdir_template: /work/agent_jobs/${task_run_id}/repo
entrypoint: ["/bin/bash", "-lc"]
required_mounts:
 - type: volume
 source: agent_workspaces
 target: /work/agent_jobs
optional_mounts:
 - type: volume
 source: unreal_ccache_volume
 target: /work/.ccache
 - type: volume
 source: unreal_ubt_volume
 target: /work/ubt-cache
env_allowlist:
 - UE_PROJECT_PATH
 - UE_TARGET
 - UE_MAP
 - CI
 - CCACHE_DIR
resource_profile:
 cpu: "8"
 memory: "16g"
 shm_size: "2g"
network_policy: none
device_policy: none
timeout_seconds: 7200
cleanup:
 remove_container_on_exit: true
 kill_grace_seconds: 30
```

### 12.3 Example runner profile: .NET SDK

```yaml
id: dotnet-sdk-8
kind: one_shot
image: mcr.microsoft.com/dotnet/sdk:8.0
workdir_template: /work/agent_jobs/${task_run_id}/repo
entrypoint: ["/bin/bash", "-lc"]
required_mounts:
 - type: volume
 source: agent_workspaces
 target: /work/agent_jobs
env_allowlist:
 - CI
 - DOTNET_CLI_HOME
resource_profile:
 cpu: "4"
 memory: "8g"
network_policy: default
timeout_seconds: 3600
cleanup:
 remove_container_on_exit: true
 kill_grace_seconds: 15
```

### 12.4 Profile selection rule

Normal execution refers to a **profile id**, not an unrestricted image string.

This rule applies in both:

* `profiles`
* `unrestricted`

Unrestricted mode expands the available tool surface. It does not weaken the meaning of `container.run_workload`.

---

## 13. Execution model and launch semantics

### 13.1 Shared execution plane

All DooD-backed tools execute on the trusted MoonMind worker plane with `docker_workload` capability.

The worker plane:

* uses the configured Docker host or proxy
* captures stdout, stderr, and diagnostics
* writes runtime evidence under the task artifacts directory
* collects declared outputs
* applies timeout and cancellation policies
* emits audit metadata

### 13.2 Launch classes

MoonMind resolves DooD requests into one of three launch classes:

* profile-backed workload launch
* unrestricted container launch
* unrestricted Docker CLI launch

These classes share the same artifact, timeout, audit, and observability pipeline.

### 13.3 Profile-backed workload launch

A profile-backed launch resolves:

* runner profile
* mount contract
* environment allowlist
* network policy
* resource bounds
* timeout policy
* cleanup policy

MoonMind then launches the container through the configured Docker host or proxy.

### 13.4 Unrestricted container launch

An unrestricted container launch resolves:

* runtime-selected image
* entrypoint and command
* workspace paths
* deployment-approved cache mounts
* network mode
* resource overrides
* timeout
* declared outputs
* report publication settings

MoonMind then launches the container through the configured Docker host or proxy and captures the result using the same durable evidence pipeline as profile-backed workloads.

### 13.5 Unrestricted Docker CLI launch

An unrestricted Docker CLI launch runs the provided Docker command on the trusted worker plane with the configured Docker host or proxy in the environment.

Rules:

* the command is executed as a Docker CLI invocation, not as a general shell contract
* stdout, stderr, and diagnostics are captured
* artifacts are written under the task artifacts directory
* declared outputs and report publication use the same semantics as other DooD tools

### 13.6 Deterministic ownership labels

Every MoonMind-launched workload container must be labeled so the platform can trace and clean it up.

Recommended labels include:

* `moonmind.kind=workload`
* `moonmind.task_run_id=<task_run_id>`
* `moonmind.step_id=<step_id>`
* `moonmind.attempt=<attempt>`
* `moonmind.tool_name=<tool_name>`
* `moonmind.docker_mode=<profiles|unrestricted>`
* `moonmind.workload_access=<profile|unrestricted_container|unrestricted_docker_cli>`
* `moonmind.workload_profile=<profile_id>` when profile-backed
* `moonmind.session_id=<session_id>` when associated
* `moonmind.session_epoch=<session_epoch>` when associated

Recommended container name shape:

* `mm-workload-<task_run_id>-<step_id>-<attempt>`

### 13.7 Exit contract

For one-shot workloads:

* exit code `0` means success
* non-zero exit means failure unless the specific tool contract defines special handling
* stdout, stderr, diagnostics, and declared outputs are still captured durably on failure where available

### 13.8 Report publication

Declared report outputs use the same artifact collection and report publication semantics across:

* `container.run_workload`
* `container.run_container`
* `container.run_docker`

A declared primary report may publish as `report.primary` when configured.

### 13.9 Helper lifecycle

Profile-backed helper containers remain explicitly owned by MoonMind.

The control plane owns:

* launch
* readiness
* termination
* TTL
* artifact capture
* ownership metadata

This is an explicit lifecycle contract, not an emergent side effect of a container remaining alive.

---

## 14. Artifact, audit, and observability contract

### 14.1 Durable truth rule

Artifacts and bounded metadata remain authoritative.

MoonMind does not depend on:

* container-local history
* daemon state as the only run record
* terminal scrollback as the only log source
* background container state as the only evidence of task progress

### 14.2 Minimum durable outputs

Every DooD invocation produces durable evidence, including:

* invocation summary
* stdout capture or equivalent durable log artifact
* stderr capture or equivalent durable log artifact
* diagnostics and exit metadata
* declared outputs
* report bundles when configured

### 14.3 Audit metadata

DooD results include bounded audit metadata such as:

* `dockerMode`
* `workloadAccess`
* `unrestrictedContainer`
* `unrestrictedDocker`
* `profileId`
* `image`
* `commandSummary`
* `taskRunId`
* `stepId`
* `attempt`
* `dockerHost`
* `startedAt`
* `completedAt`
* `durationSeconds`
* `status`
* `exitCode`
* `artifactPublication`
* `reportPublication`

Rules:

* unrestricted execution must be obvious from metadata
* `dockerHost` is recorded in normalized or redacted form
* raw secret values are never recorded in metadata

### 14.4 Artifact classes

Artifact classes align with the artifact contract:

* `output.logs`
* `output.summary`
* `output.primary`
* `runtime.stdout`
* `runtime.stderr`
* `runtime.diagnostics`

The controlling rule is durable, retrievable outputs first.

### 14.5 Session association

If a workload is launched from a session-assisted step, related artifacts may carry session association metadata.

However:

* workload artifacts are not session continuity artifacts by default
* the workload container is not presented as the managed session
* session-plane artifacts remain the responsibility of the session plane

---

## 15. Security and policy controls

### 15.1 Deployment gating

Docker workflow mode is a deployment setting.

Rules:

* tasks cannot enable `unrestricted`
* planners cannot elevate the deployment mode
* unrestricted capability exists only because the deployment explicitly allows it

### 15.2 Docker access boundary

MoonMind uses a controlled Docker host or proxy.

Rules:

* the normal session container does not receive the raw Docker socket
* the normal session container does not receive unrestricted `DOCKER_HOST`
* Docker-backed workloads run on the trusted MoonMind worker plane

### 15.3 Image policy

Image policy depends on mode.

In `profiles`:

* images come from runner profiles only

In `unrestricted`:

* arbitrary images may be selected at runtime through `container.run_container`
* arbitrary Docker CLI operations may reference arbitrary images through `container.run_docker`
* registry access, egress, and daemon policy remain deployment-owned concerns

### 15.4 Mount policy

Mount policy depends on contract.

For `container.run_workload` and helper tools:

* mounts are profile-defined

For `container.run_container`:

* workspace mounts are MoonMind-defined
* additional mounts are limited to deployment-approved named caches

For `container.run_docker`:

* Docker CLI may request broader daemon features
* such usage is explicitly unrestricted and must be audited as such
* MoonMind does not synthesize hidden mount grants on behalf of the request

### 15.5 Privilege, device, and network policy

Default posture:

* no implicit `--privileged`
* no implicit host networking
* no implicit device access
* no implicit GPU access
* no unrestricted environment pass-through
* resource limits applied where supported

Profiles may explicitly grant device or GPU access.

The structured unrestricted container contract does not expose privileged or host-networking flags in the desired state.

### 15.6 Secret handling and redaction

Secrets are injected only through explicit MoonMind policy.

Rules:

* broad worker or session environment is never copied wholesale into a workload container
* stdout, stderr, and diagnostics pass through existing redaction helpers
* secret-looking environment values are never logged raw
* secret-looking metadata values are redacted before publication

### 15.7 Deterministic failure codes

Mode and contract failures are deterministic.

Examples:

* `invalid_docker_workflow_mode`
* `docker_workflows_disabled`
* `unrestricted_container_disabled`
* `unrestricted_docker_disabled`
* `invalid_docker_command`

Profile validation failures remain explicit and deterministic.

---

## 16. Timeout, cancellation, and cleanup

### 16.1 Timeout ownership

Temporal-side policies own the wall-clock timeout. The workload launcher enforces those policies at the container boundary.

### 16.2 Cancel behavior

On task cancel, step cancel, or activity timeout, MoonMind best-effort:

1. stops the container gracefully
2. escalates to kill after a grace period when needed
3. collects remaining logs and diagnostics where available
4. records bounded failure or cancellation metadata
5. removes ephemeral containers when policy requires it

### 16.3 Cleanup of profile-backed and unrestricted structured containers

MoonMind directly owns cleanup for containers launched through:

* `container.run_workload`
* `container.start_helper`
* `container.run_container`

Rules:

* one-shot containers are removed on exit when configured as ephemeral
* bounded helpers are terminated on completion, cancellation, timeout, or TTL expiry
* orphan sweepers may remove labeled owned containers after terminal execution and TTL expiry

### 16.4 Cleanup of unrestricted Docker CLI resources

MoonMind does not attempt to infer or remove arbitrary resources created by `container.run_docker`.

Rules:

* cleanup responsibility belongs to the Docker command unless the tool contract or wrapper injects explicit ownership labels
* best-effort sweeper cleanup may target resources that carry deterministic MoonMind ownership labels
* MoonMind does not claim automatic lifecycle ownership of arbitrary CLI-created resources that it cannot reliably identify

### 16.5 Caches versus outputs

Cleanup must preserve durable outputs while allowing configured cache reuse.

This means:

* transient containers are removed
* approved named caches may persist
* artifacts and task workspace outputs remain under MoonMind-owned paths

---

## 17. Compose and runtime shape

### 17.1 Keep

The runtime shape keeps:

* a controlled Docker host or proxy such as `docker-proxy`
* `agent_workspaces` as the shared workspace volume
* specialized named cache volumes where needed

### 17.2 Current logical placement

The logical `docker_workload` capability may currently be satisfied by the `agent_runtime` worker fleet.

This is the current Docker-capable execution boundary for DooD-backed workloads.

### 17.3 Important boundary

The desired state is not “give the session container raw Docker access.”

The desired state is:

* session containers remain managed-session containers
* workload launches remain control-plane-launched
* unrestricted execution expands the control-plane tool surface, not the session-side Docker authority surface

### 17.4 Future fleet split

If Docker-backed workloads require materially different:

* hardware
* resource envelopes
* secrets
* registry access
* concurrency limits
* network policies

then MoonMind may introduce a dedicated Docker workload fleet and capability class.

Queue and fleet splitting follow operational need, not naming nostalgia.

---

## 18. Example flows

### 18.1 Unreal test run in `profiles` mode

1. A task runs in a task-scoped Codex managed session.
2. The session determines that the repository is an Unreal project.
3. It requests `unreal.run_tests`.
4. MoonMind resolves the tool to the `unreal-5_3-linux` runner profile.
5. A `docker_workload` worker launches the workload through the configured Docker host or proxy.
6. The container writes reports and build outputs under the task workspace.
7. MoonMind captures logs, diagnostics, and artifacts and returns a normal tool result.
8. The Codex managed session continues with the new evidence.

### 18.2 Dynamic dotnet container in `unrestricted` mode

A trusted workflow selects the runtime image at execution time without changing worker images and without pre-registering the image as a runner profile.

```json
{
 "name": "container.run_container",
 "type": "skill",
 "inputs": {
 "image": "mcr.microsoft.com/dotnet/sdk:8.0",
 "repoDir": "/work/agent_jobs/task-123/repo",
 "artifactsDir": "/work/agent_jobs/task-123/artifacts/test-step",
 "command": ["bash", "-lc", "dotnet test MySolution.sln --logger trx"],
 "declaredOutputs": {
 "trx": "TestResults/results.trx"
 }
 }
}
```

### 18.3 Dynamic load test container in `unrestricted` mode

A trusted workflow selects a runtime load test image at execution time and writes outputs under the task workspace.

```json
{
 "name": "container.run_container",
 "type": "skill",
 "inputs": {
 "image": "ghcr.io/example/loadtest-runner:latest",
 "repoDir": "/work/agent_jobs/task-456/repo",
 "artifactsDir": "/work/agent_jobs/task-456/artifacts/loadtest",
 "command": ["bash", "-lc", "./run-loadtest.sh --out /work/agent_jobs/task-456/artifacts/loadtest"],
 "declaredOutputs": {
 "summary": "summary.json",
 "logs": "logs/output.log"
 }
 }
}
```

### 18.4 Docker Compose stack in `unrestricted` mode

A trusted workflow uses the Docker CLI escape hatch for compose-driven behavior.

```json
{
 "name": "container.run_docker",
 "type": "skill",
 "inputs": {
 "command": ["docker", "compose", "-f", "infra/docker-compose.yml", "up", "--abort-on-container-exit"],
 "repoDir": "/work/agent_jobs/task-789/repo",
 "artifactsDir": "/work/agent_jobs/task-789/artifacts/compose-step",
 "declaredOutputs": {
 "composeLog": "compose.log"
 }
 }
}
```

---

## 19. Stable design rules

The following rules remain stable as implementation details evolve:

1. **Codex managed session containers and Docker workload containers are different architectural roles.**
2. **The control plane launches Docker-backed workloads.**
3. **Session containers do not receive unrestricted raw Docker authority by default.**
4. **`disabled`, `profiles`, and `unrestricted` are the only supported Docker workflow modes.**
5. **`container.run_workload` remains profile-backed.**
6. **Arbitrary runtime containers are exposed through `container.run_container`, not by widening `container.run_workload`.**
7. **Raw Docker CLI is exposed through `container.run_docker`, not through an implicit shell contract.**
8. **Artifacts and bounded metadata remain authoritative.**
9. **Normal execution uses curated runner profiles; unrestricted execution is explicit and auditable.**
10. **Unrestricted mode expands the control-plane tool surface, not the session-side Docker authority boundary.**
11. **The DooD core remains domain-agnostic and does not hardcode load test, Unreal, dotnet, or similar workload families.**
12. **Queue and fleet splitting follow real isolation and operational requirements.**

---

## 20. Future refinements

Likely future work includes:

* a dedicated Docker workload fleet and capability class
* richer bounded helper-container contracts
* labeled resource conventions for better `container.run_docker` cleanup
* stronger registry attestation and image provenance checks
* profile-aware cache lifecycle management
* GPU and simulator scheduling policies
* richer observability projections for long-running workload containers

These refinements preserve the architectural separation defined above.
