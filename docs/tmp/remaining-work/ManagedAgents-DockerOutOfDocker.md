# DockerOutOfDocker Remaining Work

Source doc: [`docs/ManagedAgents/DockerOutOfDocker.md`](../../ManagedAgents/DockerOutOfDocker.md)
Status: Phases 0 through 3 complete; Phases 4 through 7 pending
Last updated: 2026-04-11

## Phase checklist

- [x] Phase 0: Lock the contract and carve the boundary across the canonical DooD, session-plane, and execution-model docs.
- [x] Phase 1: Define the control-plane workload contract (`WorkloadRequest`, `WorkloadResult`, `RunnerProfile`, ownership metadata, validation rules).
- [x] Phase 2: Build the Docker workload launcher on the existing Docker-capable `agent_runtime` worker fleet.
- [x] Phase 3: Expose DooD through executable tools such as `container.run_workload` and `unreal.run_tests`.
- [ ] Phase 4: Publish durable workload artifacts, live-log linkage, and optional session-association metadata without confusing workload identity with session identity.
- [ ] Phase 5: Harden security, policy, concurrency control, and orphan cleanup.
- [ ] Phase 6: Validate the architecture with the Unreal pilot runner profile and representative repository coverage.
- [ ] Phase 7: Evaluate bounded helper containers only after one-shot workload containers are stable and well observed.

## Phase 0 completion notes

- Canonical glossary locked around `session container`, `workload container`, `runner profile`, and `session-assisted workload`.
- Session-plane doc now states that managed-session steps may invoke control-plane workload tools whose containers remain outside session identity.
- Execution-model doc now states that Docker-backed workload tools remain ordinary executable tools unless they launch a true managed agent runtime.
- A focused unit test guards the tracker reference and the agreed boundary wording.

## Phase 1 completion notes

- Canonical workload request/result, runner profile, and ownership metadata models are defined in code without invoking Docker.
- A deployment-owned runner profile registry can load JSON/YAML profile files and fails closed when no registry exists.
- Profile-aware validation rejects unknown profiles, unsafe images/mounts/network/device policy, disallowed env overrides, workspace paths outside the configured root, excessive resource overrides, and timeout overrides above profile limits.
- Focused unit tests cover valid request construction, deterministic `moonmind.*` labels, registry loading, fail-closed behavior, and policy denials.

## Phase 2 completion notes

- `DockerWorkloadLauncher` executes a profile-validated workload request through the configured Docker CLI / `DOCKER_HOST` path and returns bounded `WorkloadResult` metadata.
- Docker run construction is deterministic for container name, labels, workspace/cache mounts, approved artifacts directory reachability, workdir, network policy, env overrides, resource flags, entrypoint/wrapper, image, and command.
- Timeout and cancellation cleanup paths stop, kill, and remove ephemeral workload containers according to profile cleanup policy.
- `DockerContainerJanitor` supports `docker stop`, `docker kill`, `docker rm`, and orphan lookup by ownership labels.
- The Temporal activity catalog exposes `workload.run` as a separate `docker_workload` capability on the existing `agent_runtime` fleet rather than overloading managed-session verbs.
- Focused unit tests cover launcher argument construction, cleanup, orphan lookup, activity routing, and worker topology.

## Phase 3 completion notes

- The generated executable tool registry emits curated `ToolDefinition` payloads for `container.run_workload` and `unreal.run_tests` with `tool.type = "skill"` and `docker_workload` capability requirements.
- `docker_workload` skill capability routing resolves to the existing `agent_runtime` fleet and its task queue, keeping Docker-backed workloads outside `MoonMind.AgentRun` and managed-session verbs.
- The agent-runtime worker registers DooD skill handlers that convert tool inputs into validated `WorkloadRequest` payloads, resolve runner profiles through the deployment-owned registry, invoke `DockerWorkloadLauncher`, and return normal `ToolResult` payloads.
- `container.run_workload` exposes profile, workspace, command, env allowlist, timeout, resource, and optional session-association fields without raw image, mount, or device inputs.
- `unreal.run_tests` maps a stable domain contract (`projectPath`, optional target/test selector) onto the curated Unreal runner profile command.
- Focused unit tests cover tool definition generation, input-to-request conversion, launcher invocation, `MoonMind.Run` routing through `mm.tool.execute`, and agent-runtime task-queue selection.

## Guardrails to preserve during later phases

- Keep `tool.type = "skill"` as the initial execution primitive for Docker-backed workload launches.
- Keep the current Docker-capable `agent_runtime` fleet as the first workload host.
- Keep one-shot workload containers as the MVP lifecycle.
- Keep bounded helper containers as a later phase, not part of the MVP.
- Keep artifacts and bounded workflow metadata as durable truth.
