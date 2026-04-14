# DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind

**Implementation tracking:** [`docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`](../tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md)
**Status:** Desired state
**Owners:** MoonMind Platform
**Last updated:** 2026-04-09

**Related:**
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
- [`docs/Tasks/SkillAndPlanContracts.md`](../Tasks/SkillAndPlanContracts.md)
- [`docs/Temporal/ArtifactPresentationContract.md`](../Temporal/ArtifactPresentationContract.md)
- [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)

---

## 1. Purpose

This document defines MoonMind's **Docker-out-of-Docker (DooD)** architecture for launching **specialized workload containers** through the MoonMind / Temporal control plane.

The main use case is **heavyweight, toolchain-specific work** that should not be baked into the generic MoonMind worker image or the Codex session container, for example:

- Unreal Engine build, cook, package, or test workloads
- Unity batchmode test or build workloads
- SDK- or distro-specific build/test images
- GPU- or driver-constrained validation jobs
- bounded helper containers needed to support a step's execution

The goal is to let MoonMind run these workloads **against the task workspace** while keeping:

- the **Codex managed session plane** focused on managed agent continuity
- the **Temporal control plane** as the owner of launch, policy, cancellation, and observability
- worker images generic and maintainable

This document replaces the older framing that treated DooD mainly as a sandbox-worker trick for arbitrary heavy commands. The updated model distinguishes **managed agent session containers** from **non-agent workload containers**. Phase 0 locks that contract here; remaining rollout work belongs in [`docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md`](../tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md).

---

## 2. Core decision

MoonMind should support specialized Docker workloads such as Unreal Engine runners, but they must remain **control-plane-launched workloads**, not ad hoc containers started directly by the Codex runtime.

The governing rules are:

1. **A Codex session container is not given unrestricted Docker authority by default.**
2. **Specialized Docker workloads are launched through MoonMind / Temporal as executable tools or curated workload activities.**
3. **A workload container is not a managed session and not a `MoonMind.AgentRun` unless it is itself a true agent runtime.**
4. **Session identity and workload identity remain separate.**
5. **Artifacts and bounded metadata remain authoritative; container state is never durable truth.**

In practice, this means a Codex-managed step may decide that it needs an Unreal toolchain, but the launch is still mediated by MoonMind. The session requests a tool-capability; a Docker-capable worker launches the workload container via the secure Docker proxy; MoonMind captures results as normal step outputs.

---

## 3. Scope and non-goals

### 3.1 In scope

This document defines:

- when MoonMind should launch a separate Docker workload container
- how those launches fit with the Codex managed session plane
- workspace, volume, artifact, timeout, and cleanup rules
- the policy boundary for runner images and privileged capabilities
- how specialized containers such as Unreal runners should be modeled

### 3.2 Out of scope

This document does **not** define:

- Kubernetes orchestration
- generic container marketplace semantics
- cross-task session reuse
- turning every workload container into a managed agent session
- raw Docker access from inside the Codex session container
- provider-native managed session protocols
- a permanent, cross-step workload-service framework beyond the bounded direction noted below

If MoonMind later needs reusable non-agent services with their own lifecycle across multiple steps, that should become a **sibling workload lifecycle**. It should not be forced into the Codex CLI managed session plane and should not be mislabeled as `MoonMind.AgentRun`.

---

## 4. Terminology

### 4.1 Session container

The **session container** is the task-scoped Codex container defined by [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md).

It owns:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

It is the continuity/performance cache for the Codex task-scoped managed session.

### 4.2 Workload container

A **workload container** is a separate Docker container launched to execute a specialized non-agent workload against the task workspace.

Examples:

- Unreal build/test image
- Unity editor CI image
- distro-specific compiler image
- bounded service container used only to support a step

A workload container is **not** the session container.

### 4.3 Runner profile

A **runner profile** is the curated MoonMind definition of an allowed workload-container shape.

A runner profile typically specifies:

- image reference
- entrypoint/command wrapper
- workspace mount contract
- optional cache volumes
- allowed environment variables
- resource profile
- network policy
- optional device policy (for example GPU access)

### 4.4 Session-assisted workload launch

A **session-assisted workload launch** happens when a managed session step decides it needs a specialized toolchain, but MoonMind launches the workload through the control plane instead of giving the session direct Docker daemon access.

---

## 5. Architectural layering

MoonMind should treat Docker-backed workloads as a third layer adjacent to, but distinct from, the managed session plane.

### 5.1 Orchestration layer

MoonMind / Temporal owns:

- workflow orchestration
- tool routing
- policy enforcement
- timeout and cancellation semantics
- artifact publication
- observability
- cleanup and orphan handling

### 5.2 Managed session plane

The Codex CLI managed session plane owns:

- the task-scoped Codex session container
- thread/turn lifecycle
- `clear_session`, `interrupt_turn`, and related session actions
- session continuity artifacts and bounded session metadata

The managed session plane does **not** become the generic owner of all other containers.

### 5.3 Docker workload plane

`DockerOutOfDocker` owns the MoonMind-side rules for launching **specialized workload containers** against the workspace.

It is responsible for:

- Docker proxy usage
- container launch arguments
- workspace/cache mounts
- workload labeling and identity
- stdout/stderr capture
- diagnostics capture
- cleanup

It does **not** own Codex thread lifecycle or session continuity semantics.

---

## 6. Supported container roles

### 6.1 Role A: task-scoped managed session container

The Codex managed session plane remains:

- Docker only
- one task-scoped session container per task
- one active Codex thread per session epoch
- continuity reused only within the same task

This role is governed by [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md), not by this document.

### 6.2 Role B: one-shot workload container

This is the default DooD role.

MoonMind launches a separate ephemeral container to perform one bounded specialized job, such as:

- `unreal.run_tests`
- `unreal.build_editor_target`
- `unity.run_batchmode_tests`
- `container.run_workload` using an approved runner profile

The container exits when the command completes and MoonMind returns a normal `ToolResult`.

### 6.3 Role C: bounded helper workload container

Some steps may require a non-agent helper container that stays alive long enough to support a bounded portion of execution, for example:

- an Unreal dedicated-server test target
- a temporary backing service for integration tests
- a driver- or simulator-specific helper runtime

This is allowed only when the lifecycle is still **bounded, owned, and observable by MoonMind**.

Rules:

- it remains a workload container, not a managed session
- it must have an owner execution reference and an explicit timeout / TTL
- it must have best-effort termination on step cancel, timeout, or task cancel
- it must not become a hidden long-lived background service

The initial implementation emphasis should remain **one-shot workload containers**. Bounded helper containers remain a later phase. They are a valid direction, but they do not change the separation between session-plane identity and workload identity.

---

## 7. Ownership and routing model

### 7.1 Primary rule

Specialized Docker workloads should enter MoonMind through the **tool execution path**, not by extending `agent_runtime.*` session verbs to mean “run an arbitrary container.”

### 7.2 Tool-path default

For normal specialized workloads, the preferred model is:

1. a plan step or session-assisted step invokes an executable tool
2. the tool resolves from the pinned tool registry snapshot
3. MoonMind routes the tool to a worker fleet with Docker workload capability
4. that worker launches the workload container via the Docker proxy
5. MoonMind publishes artifacts and returns a normal `ToolResult`

This keeps specialized workload containers in the **tool** world rather than pretending they are extra agent sessions.

The initial DooD execution primitive is `tool.type = "skill"`. `tool.type = "agent_runtime"` remains reserved for true long-lived agent runtimes rather than ordinary workload containers.

### 7.3 Curated activity exception

Some Docker-backed tools may justify a curated activity type later if they require stronger isolation, secrets handling, or hardware constraints.

Examples:

- GPU-bound Unreal packaging
- privileged simulator access
- dedicated credentialed registry access

That is consistent with MoonMind's hybrid execution model: the architectural boundary remains a control-plane tool/workload invocation even if the underlying activity type becomes more specialized.

### 7.4 Relationship to `agent_runtime`

True managed agent runtime execution and session supervision remain under `agent_runtime.*` and `MoonMind.AgentRun`.

A specialized Unreal build container is **not** automatically a true agent runtime merely because Docker is involved.

### 7.5 Current placement direction

The local repo currently makes the `agent_runtime` fleet the Docker-capable worker fleet. That is acceptable for the current shape because the platform already routes managed runtime execution there and the local compose wiring already gives that fleet Docker-proxy access and the shared workspace volume.

If isolation, hardware, egress, or scaling needs diverge materially, MoonMind may later introduce a dedicated Docker workload fleet and capability class. Until that split is justified, keep the queue model minimal.

---

## 8. Session-plane interaction model

### 8.1 What a Codex session may do

A Codex-managed session may:

- inspect the repository
- detect that a step requires a specialized toolchain
- request a MoonMind tool such as `unreal.run_tests`
- consume the returned artifacts/results
- continue the task-scoped managed session afterward

### 8.2 What a Codex session may not do by default

A Codex-managed session should **not** by default:

- mount the host Docker socket
- receive unrestricted `DOCKER_HOST` access
- create arbitrary containers directly on the daemon
- bypass Temporal for launches that affect workspace state or task progress

If MoonMind wants the session to request workload execution interactively, expose a **narrow MoonMind-owned capability surface**. Examples include:

- a tool invocation routed through normal plan execution
- an adapter-mediated control-plane request
- a brokered API surface owned by MoonMind

The exact transport is an implementation detail. The security boundary is not.

### 8.3 Session and workload lifecycle relation

Rules:

- `clear_session` affects the Codex thread/epoch model only
- `clear_session` does not redefine workload identity
- session artifacts remain session-plane artifacts
- workload completion artifacts remain workload outputs
- task cancellation should best-effort cancel both the session turn and any in-flight owned workload containers

### 8.4 Identity boundary

If a workload is launched from a session-assisted step, MoonMind may attach session metadata such as:

- `session_id`
- `session_epoch`
- `source_turn_id`

But those fields are **association metadata only**. They do not make the workload container part of session identity.

---

## 9. Workspace and volume contract

### 9.1 Shared workspace root

The canonical shared task workspace remains a named volume such as:

- `agent_workspaces` mounted at `/work/agent_jobs`

Recommended task layout:

- `run_root = /work/agent_jobs/<task_run_id>`
- `repo_dir = /work/agent_jobs/<task_run_id>/repo`
- `artifacts_dir = /work/agent_jobs/<task_run_id>/artifacts/<step_id>`
- `scratch_dir = /work/agent_jobs/<task_run_id>/scratch/<step_id>`

### 9.2 Mount rules

A workload container should receive only the mounts it needs.

Minimum common case:

- `agent_workspaces` mounted at `/work/agent_jobs`
- working directory set to the task repo directory

Optional additional mounts may include named caches such as:

- `unreal_ccache_volume`
- `unreal_ubt_volume`

### 9.3 Auth volume rule

Workload containers must **not** automatically inherit Codex / Claude / Gemini auth volumes just because they were requested by a managed session step.

Any auth or credential mount must be:

- explicitly declared by the runner profile
- justified by the workload
- scoped to the minimum required secret surface

### 9.4 Output discipline

Workload containers must write only to approved task paths and mounted caches.

They must not rely on:

- container-local home directories as durable output
- unnamed scratch layers as the only output location
- hidden background state outside MoonMind-owned mounts

---

## 10. Runner profile model

MoonMind should prefer **curated runner profiles** over free-form arbitrary image strings.

The old rule “any pullable image may be used per repository” is too permissive for the current control-plane design.

### 10.1 Minimum runner profile fields

A runner profile should define at least:

- `id`
- `image`
- `default_workdir`
- `entrypoint` or command wrapper
- required mounts
- allowed extra env vars
- resource profile
- network policy
- timeout defaults
- cleanup policy
- optional device policy

### 10.2 Example runner profile: Unreal Linux CI

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

### 10.3 Example runner profile: GPU-bound variant

A GPU-dependent variant must be explicit. GPU access must never be inferred merely because the image name suggests it.

```yaml
id: unreal-5_3-linux-gpu
kind: one_shot
image: ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3-gpu
workdir_template: /work/agent_jobs/${task_run_id}/repo
device_policy:
  gpu: required
resource_profile:
  cpu: "16"
  memory: "32g"
network_policy: none
```

### 10.4 Profile selection rule

Plans, tools, or session-assisted requests should refer to a **profile id** or other approved workload class, not to an unrestricted image string, except in tightly controlled operator/debug flows.

---

## 11. Execution contract

### 11.1 One-shot workload request shape

A one-shot workload invocation should carry a small structured request such as:

- `profile_id`
- `task_run_id`
- `step_id`
- `attempt`
- `workspace_root`
- `repo_dir`
- `artifacts_dir`
- `command` or command arguments
- `env_overrides` (only from allowlisted keys)
- `timeout_seconds`
- `resource_override` (within policy bounds)
- optional association metadata like `session_id` / `session_epoch`

### 11.2 Deterministic ownership labels

Every launched workload container should be labeled so MoonMind can trace and clean it up.

Recommended labels include:

- `moonmind.kind=workload`
- `moonmind.task_run_id=<task_run_id>`
- `moonmind.step_id=<step_id>`
- `moonmind.attempt=<attempt>`
- `moonmind.tool_name=<tool_name>`
- `moonmind.workload_profile=<profile_id>`
- optional `moonmind.session_id=<session_id>`
- optional `moonmind.session_epoch=<session_epoch>`

Recommended container name shape:

- `mm-workload-<task_run_id>-<step_id>-<attempt>`

### 11.3 Illustrative `docker run` shape

The launcher may construct a command similar to:

```bash
docker run --rm \
  --name "mm-workload-${TASK_RUN_ID}-${STEP_ID}-${ATTEMPT}" \
  --label "moonmind.kind=workload" \
  --label "moonmind.task_run_id=${TASK_RUN_ID}" \
  --label "moonmind.step_id=${STEP_ID}" \
  --label "moonmind.tool_name=unreal.run_tests" \
  --label "moonmind.workload_profile=unreal-5_3-linux" \
  --mount type=volume,src=agent_workspaces,dst=/work/agent_jobs \
  --mount type=volume,src=unreal_ccache_volume,dst=/work/.ccache \
  --mount type=volume,src=unreal_ubt_volume,dst=/work/ubt-cache \
  --workdir "/work/agent_jobs/${TASK_RUN_ID}/repo" \
  -e UE_PROJECT_PATH="MyGame.uproject" \
  ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3 \
  /bin/bash -lc "./Build/Scripts/run-ci-tests.sh"
```

This example is illustrative. The public contract is the MoonMind workload request, not the raw CLI string.

### 11.4 Exit contract

For one-shot workloads:

- exit code `0` means success
- non-zero exit means failure unless the tool contract explicitly treats certain codes specially
- stdout, stderr, diagnostics, and declared outputs must still be captured durably even on failure

### 11.5 Bounded helper contract

If MoonMind supports a bounded helper workload container, the control plane must still own:

- launch
- health check / readiness
- termination
- TTL
- artifact capture
- link-back to the owner step or step-group

This should be introduced as an explicit workload sub-contract, not as an informal “container happens to still be running” behavior.

---

## 12. Tool-contract integration

### 12.1 Preferred user-facing shape

Specialized Docker workloads should normally be exposed as executable tools such as:

- `unreal.run_tests`
- `unreal.build_target`
- `unity.run_batchmode_tests`
- `container.run_workload`

These remain ordinary executable tools from the point of view of plan execution.

### 12.2 Recommended modeling

Preferred order:

1. **Curated domain tool** when MoonMind understands the job well enough to expose a stable contract
2. **Generic workload tool** for controlled operator or advanced use cases
3. **Curated activity type** only when stronger isolation or hardware routing is required

### 12.3 Example `ToolDefinition`

```yaml
name: "unreal.run_tests"
version: "1.0.0"
type: "skill"
description: "Run Unreal Engine automated tests inside an approved workload container."
inputs:
  schema:
    type: object
    required: [repo_ref, project_path, test_command]
    properties:
      repo_ref: { type: string }
      project_path: { type: string }
      test_command: { type: string }
      profile_id: { type: string, default: "unreal-5_3-linux" }
outputs:
  schema:
    type: object
    required: [status]
    properties:
      status: { type: string }
      log_artifact_ref: { type: string }
      test_results_artifact_ref: { type: string }
executor:
  activity_type: "mm.skill.execute"
  selector:
    mode: "by_capability"
    requirements:
      capabilities:
        - "agent_runtime"
policies:
  timeouts:
    start_to_close_seconds: 7200
    schedule_to_close_seconds: 10800
  retries:
    max_attempts: 1
```

The exact capability name may change if MoonMind later introduces a dedicated Docker workload fleet, but the key architectural point remains: **this is a tool-backed workload invocation, not an extra managed session.**

---

## 13. Artifact and observability contract

### 13.1 Durable truth rule

Artifacts and bounded workflow metadata remain authoritative.

MoonMind must not depend on:

- container-local history
- container-local caches as audit truth
- daemon state as the only run record
- terminal scrollback as the only source of logs

### 13.2 Minimum outputs

Every workload invocation must produce durable evidence, including:

- command summary / invocation metadata
- stdout and stderr capture, or an equivalent durable log artifact set
- diagnostics / exit metadata
- declared output artifacts such as test reports, packages, or binaries

Execution detail surfaces should present workload evidence on the producing step. Runtime stdout, stderr, diagnostics, declared primary/summary outputs, and test/report artifacts are step outputs. Bounded workload metadata such as runner profile, image reference, status, exit code, duration, and timeout/cancel reason may be displayed alongside those artifact refs.

If the workload was launched from a managed-session-assisted step, `session_id`, `session_epoch`, and source-turn metadata may appear only as association context. The UI and API must not present the workload container itself as the managed session, and workload artifacts must not be grouped as session continuity artifacts by default.

### 13.3 Artifact classes

Artifact classes should align with the artifact contract:

- generic tool execution logs may use `output.logs`
- human-readable result summaries may use `output.summary`
- primary outputs may use `output.primary`
- runtime-supervised output may also publish `runtime.stdout`, `runtime.stderr`, or `runtime.diagnostics` when MoonMind's observability pipeline treats the workload as runtime-like operational output

The controlling rule is: **durable, retrievable outputs first; container-local output never first.**

### 13.4 Session association

If a workload was launched from a session-assisted step, related artifacts may carry `session_context` or equivalent association metadata so operators can understand the relationship.

However:

- workload artifacts are not session continuity artifacts by default
- `session.summary`, `session.step_checkpoint`, `session.control_event`, and `session.reset_boundary` remain the responsibility of the session plane

### 13.5 Observability guidance

When workload execution is long enough or important enough to merit richer observability, MoonMind may project it into the standard observability surfaces. That does not change the identity rule: a workload container still is not a managed session.

---

## 14. Security and policy controls

### 14.1 Docker access boundary

Use a controlled Docker proxy such as `docker-proxy`.

Do **not** mount the raw Docker socket into the session container as the default mechanism for specialized workload execution.

### 14.2 Image policy

Allowed images should come from:

- runner profiles
- registry allowlists
- controlled override paths for operators/debugging only

MoonMind should reject arbitrary unapproved images in normal task execution.

### 14.3 Mount policy

Allowed mounts must be explicit.

Defaults:

- shared task workspace volume
- declared cache volumes
- no host-path mounts except tightly controlled operator environments
- no automatic inheritance of unrelated auth volumes

### 14.4 Privilege policy

Default posture:

- no `--privileged`
- no host networking
- no device access unless explicitly required by profile
- no implicit GPU access
- no unrestricted env pass-through
- resource limits applied where supported

### 14.5 Secret handling

Secrets must be injected through explicit MoonMind policy, never by copying broad worker or session environment wholesale into the workload container.

### 14.6 Network policy

Runner profiles should declare whether networking is:

- disabled
- restricted internal-only
- restricted egress
- explicitly allowed to reach named internal or external services

Network allowance should be part of the runner profile, not an ad hoc per-command surprise.

---

## 15. Timeout, cancellation, and cleanup

### 15.1 Timeout ownership

Temporal-side policies own the wall-clock timeout. The workload launcher enforces those policies at the container boundary.

### 15.2 Cancel behavior

On task cancel, step cancel, or activity timeout, MoonMind should best-effort:

1. stop the container gracefully
2. escalate to kill if needed after a grace period
3. collect whatever logs/diagnostics remain available
4. record bounded failure / cancellation metadata
5. remove the container if policy says it is ephemeral

### 15.3 Orphan handling

MoonMind should be able to locate abandoned workload containers using deterministic names and labels.

A sweeper may safely remove orphaned containers when:

- the owning execution is terminal
- the TTL has expired
- no active ownership claim remains

### 15.4 Cleanup of caches vs outputs

Cleanup must preserve durable outputs while allowing cache reuse where policy permits.

This means:

- remove transient workload containers
- keep named caches such as Unreal ccache / UBT volumes when configured
- preserve artifacts and task workspace outputs under MoonMind-owned paths

---

## 16. Compose and runtime shape

### 16.1 Keep

The local development / deployment shape should keep:

- `docker-proxy` as the controlled Docker daemon access path
- `agent_workspaces` as the shared workspace volume
- specialized cache volumes such as Unreal cache volumes where needed

### 16.2 Current local direction

The current local repo shape already places Docker proxy access on the `temporal-worker-agent-runtime` service. That should be treated as the current Docker-capable fleet for DooD-backed workloads.

### 16.3 Important correction to older design

Do **not** describe the desired state as “add `DOCKER_HOST` to `temporal-worker-sandbox` so sandbox workers can launch heavy-build containers” unless the platform intentionally moves Docker workload capability there.

The current direction is:

- managed sessions and managed runtime supervision stay aligned with the `agent_runtime` execution boundary
- specialized workload launches use the Docker-capable worker fleet through MoonMind routing
- the Codex session container itself does not become the raw Docker launcher

### 16.4 Future fleet split

If specialized workloads require materially different:

- hardware
- resource envelopes
- secrets
- registry access
- concurrency limits
- network policies

then introduce a dedicated Docker workload fleet and capability class. Do not over-split queues before that is operationally justified.

---

## 17. Example flows

### 17.1 Unreal test run initiated from a Codex-managed task

1. Task is running in a task-scoped Codex managed session.
2. The session determines that the repository is an Unreal project.
3. It requests `unreal.run_tests`.
4. `MoonMind.Run` interprets that as an executable tool invocation.
5. A Docker-capable worker resolves the `unreal-5_3-linux` runner profile.
6. The worker launches the Unreal runner container through `docker-proxy`, mounting the shared workspace and allowed caches.
7. The container writes reports and build outputs under the task workspace.
8. MoonMind captures logs, diagnostics, and artifacts and returns a `ToolResult`.
9. The Codex managed session continues with the new evidence.

### 17.2 Generic profile-backed workload for advanced use

1. A plan node invokes `container.run_workload` with an approved `profile_id`.
2. MoonMind validates the profile and argument policy.
3. The worker launches the container through the Docker proxy.
4. Outputs are captured as durable artifacts.
5. The workflow treats the result as a normal tool result.

### 17.3 Bounded helper service

1. A step requires a temporary helper container.
2. MoonMind launches a bounded helper workload with explicit TTL and ownership metadata.
3. The step uses that helper during the bounded execution window.
4. MoonMind terminates the helper at step completion, cancellation, or timeout.

This still does not create a new managed session.

---

## 18. Design rules to keep stable

These rules should remain stable even as implementation details evolve:

1. **Codex managed session containers and specialized workload containers are different architectural roles.**
2. **The control plane launches specialized workload containers.**
3. **Session containers do not get unrestricted raw Docker access by default.**
4. **Use approved runner profiles, not arbitrary image strings, in normal execution.**
5. **Artifacts and bounded metadata remain authoritative.**
6. **Queue/fleet splitting should follow real isolation needs, not naming nostalgia.**
7. **A non-agent workload container is not a managed session merely because it is Docker-backed.**

---

## 19. Future refinements

Likely future work includes:

- dedicated Docker workload capability / worker fleet
- richer bounded helper-container contracts
- GPU and simulator scheduling policies
- stronger registry attestation / image provenance checks
- profile-aware cache lifecycle management
- better observability projections for long-running workload containers

Those refinements should preserve the core separation defined above.
