# DockerOutOfDocker Remaining Work

Source doc: [`docs/ManagedAgents/DockerOutOfDocker.md`](../../ManagedAgents/DockerOutOfDocker.md)
Status: Phases 0 through 7 complete
Last updated: 2026-04-12

## Phase checklist

- [x] Phase 0: Lock the contract and carve the boundary across the canonical DooD, session-plane, and execution-model docs.
- [x] Phase 1: Define the control-plane workload contract (`WorkloadRequest`, `WorkloadResult`, `RunnerProfile`, ownership metadata, validation rules).
- [x] Phase 2: Build the Docker workload launcher on the existing Docker-capable `agent_runtime` worker fleet.
- [x] Phase 3: Expose DooD through executable tools such as `container.run_workload` and `unreal.run_tests`.
- [x] Phase 4: Publish durable workload artifacts, live-log linkage, and optional session-association metadata without confusing workload identity with session identity.
- [x] Phase 5: Harden security, policy, concurrency control, and orphan cleanup.
- [x] Phase 6: Validate the architecture with the Unreal pilot runner profile and representative repository coverage.
- [x] Phase 7: Evaluate and implement bounded helper containers after one-shot workload containers are stable and well observed.

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
- Focused unit tests cover tool definition generation, raw Docker input rejection, input-to-request conversion, launcher invocation/result mapping, `MoonMind.Run` routing through `mm.tool.execute`, managed-session boundary preservation, and agent-runtime task-queue selection.

## Phase 4 completion notes

- Workload launcher publication writes bounded `runtime.stdout`, `runtime.stderr`, and `runtime.diagnostics` artifacts under the producing step artifacts directory.
- Diagnostics include runner profile, image ref, status, exit code, duration, cleanup policy, resource overrides, declared output refs, and missing declared outputs.
- Session association fields (`sessionId`, `sessionEpoch`, `sourceTurnId`) are carried as grouping metadata only and are not emitted as session continuity artifacts.
- Tool results expose workload metadata and artifact publication state for execution-detail projection without treating workload containers as managed sessions.
- Focused unit tests cover successful and failed artifact publication, partial publication failure, declared output linking, session association metadata, and run-ledger/API workload projection.

## Phase 5 completion notes

- Runner profile registries enforce an image registry allowlist, defaulting to curated registry hosts and permitting operator override through `MOONMIND_WORKLOAD_ALLOWED_IMAGE_REGISTRIES`.
- Workload profiles reject auth/credential/secret volumes for Codex, Claude, Gemini, and Anthropic runtimes, keeping managed-session auth volumes out of workload containers.
- Docker run construction now adds explicit no-privileged hardening with `--privileged=false`, `--cap-drop ALL`, and `--security-opt no-new-privileges`.
- Runner profiles carry a per-profile `maxConcurrency` limit, and the agent-runtime worker can apply a fleet-wide limit through `MOONMIND_DOCKER_WORKLOAD_FLEET_CONCURRENCY`.
- Workload containers receive a bounded `moonmind.expires_at` label and `DockerContainerJanitor.sweep_expired_workloads()` removes expired orphan containers.
- Workload policy denials include stable reason/details fields for operator-facing diagnostics and tool failure payloads.
- Focused unit tests cover image registry policy, denial metadata, auth-volume rejection, explicit Docker security flags, concurrency denial, and expired orphan sweeping.

## Phase 6 completion notes

- MoonMind ships a deployment-owned default workload profile registry at `config/workloads/default-runner-profiles.yaml` with the curated `unreal-5_3-linux` profile.
- The Unreal pilot profile pins `ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3`, mounts `agent_workspaces`, and uses `unreal_ccache_volume` plus `unreal_ubt_volume` as approved cache volumes.
- The profile keeps the Phase 5 safety posture: no host networking, no privileged launch, no implicit device access, bounded resources, and `maxConcurrency: 1`.
- Agent-runtime worker bootstrap loads the built-in profile registry when `MOONMIND_WORKLOAD_PROFILE_REGISTRY` is unset; operators can still set that env var to replace the registry with a deployment-owned file.
- `unreal.run_tests` now accepts `reportPaths` for primary, summary, and junit outputs, maps them to declared workload artifacts, and injects only allowlisted Unreal env keys.
- Representative unit coverage validates default profile loading, worker bootstrap behavior, curated command construction, invalid report path rejection, cache-volume launch args, and safe Docker posture.

### Unreal operator enablement path

1. Publish or mirror `ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3` according to deployment policy, or provide an operator registry file through `MOONMIND_WORKLOAD_PROFILE_REGISTRY`.
2. Ensure Docker named volumes exist for `agent_workspaces`, `unreal_ccache_volume`, and `unreal_ubt_volume`.
3. Keep the Docker-capable `agent_runtime` worker connected to the Docker proxy and carrying the `docker_workload` capability.
4. Invoke `unreal.run_tests` with `projectPath`, optional `target`/`testSelector`, and optional relative `reportPaths`; inspect runtime logs and reports under the step artifacts directory.

## Phase 7 completion notes

- Runner profiles can now declare `kind: bounded_service` as a separate helper lifecycle from default one-shot workload containers.
- Bounded helper profiles must define `helperTtlSeconds`, `maxHelperTtlSeconds`, and an explicit `readinessProbe`; helper requests must provide `ttlSeconds` within the selected profile limit.
- Helper ownership uses deterministic `mm-helper-...` container names and `moonmind.kind=bounded_service` labels, preserving the boundary that helpers are not `MoonMind.AgentRun` instances and do not carry managed-session identity.
- `DockerWorkloadLauncher.start_helper()` launches helpers detached, waits for bounded Docker exec readiness probes, and publishes bounded helper diagnostics and runtime logs under the step artifact directory.
- `DockerWorkloadLauncher.stop_helper()` performs explicit stop/kill/remove teardown for the bounded execution window.
- Executable tools `container.start_helper` and `container.stop_helper` route through the existing Docker workload tool bridge and Temporal `workload.run` activity boundary without granting Docker authority to managed-session containers.
- `DockerContainerJanitor.sweep_expired_helpers()` removes expired helper containers by `moonmind.kind=bounded_service` and `moonmind.expires_at` without touching fresh helpers or one-shot workload containers.
- Focused unit coverage validates helper profile/request policy, detached launch labels, readiness success/failure, bounded readiness diagnostics, multi-sub-step survival through the bounded window, executable tool start/stop mapping, Temporal activity dispatch, explicit teardown, and expired-helper sweeping.

## Guardrails to preserve during later phases

- Keep `tool.type = "skill"` as the initial execution primitive for Docker-backed workload launches.
- Keep the current Docker-capable `agent_runtime` fleet as the first workload host.
- Keep one-shot workload containers as the MVP lifecycle.
- Keep bounded helper containers as an explicit bounded-service lifecycle, not a hidden long-lived service model.
- Keep artifacts and bounded workflow metadata as durable truth.
