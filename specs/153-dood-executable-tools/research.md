# Research: DooD Executable Tool Exposure

## Decision 1: Use `tool.type = "skill"` for Docker-backed workloads

- **Decision**: `container.run_workload` and `unreal.run_tests` remain executable tools with `tool.type = "skill"` and route through `mm.tool.execute` / `mm.skill.execute`.
- **Rationale**: The executable tool contract already provides registry-pinned definitions, input/output schemas, capability routing, retries, timeouts, and normal `ToolResult` handling. Docker-backed workload containers are one-shot side-effecting activities, not long-lived agent runtimes.
- **Alternatives considered**:
  - Use `tool.type = "agent_runtime"`: rejected because that path is reserved for true `MoonMind.AgentRun` child workflows and would blur workload identity with agent-runtime lifecycle.
  - Add a third plan tool type: rejected because it would expand the plan interpreter unnecessarily when the existing tool path already models side-effecting activity execution.

## Decision 2: Route `docker_workload` capability to the existing `agent_runtime` fleet

- **Decision**: Add `docker_workload` as a skill capability category that resolves to the existing Docker-capable `agent_runtime` task queue.
- **Rationale**: Phase 2 established the current `agent_runtime` fleet as the Docker-capable control-plane worker fleet. Reusing it keeps Docker authority out of managed session containers and avoids provisioning a new fleet before one-shot workloads are proven.
- **Alternatives considered**:
  - Route workloads to the sandbox fleet: rejected because the current DooD plan identifies `agent_runtime` as the existing Docker-capable fleet.
  - Add a new dedicated workload fleet now: rejected as unnecessary operational expansion for Phase 3.

## Decision 3: Add a workload tool bridge rather than extending session controllers

- **Decision**: Implement a thin workload tool bridge that converts tool inputs into `WorkloadRequest`, validates through `RunnerProfileRegistry`, calls `DockerWorkloadLauncher`, and maps `WorkloadResult` to `ToolResult`.
- **Rationale**: The bridge localizes tool-specific input shaping while preserving the existing launcher and profile registry as authoritative policy surfaces. Session controllers remain focused on managed runtime continuity.
- **Alternatives considered**:
  - Add generic Docker launch methods to the managed session controller: rejected because session containers and workload containers have different identities and lifecycles.
  - Call Docker directly from `MoonMind.Run`: rejected because workflow code must orchestrate only and avoid side effects.

## Decision 4: Keep the generic tool policy-gated and the Unreal tool curated

- **Decision**: `container.run_workload` accepts profile ID, workspace paths, command args, allowlisted env overrides, bounded resource/timeout overrides, and optional session metadata. `unreal.run_tests` accepts domain fields such as project path, target, and test selector, then maps them to the curated Unreal profile command.
- **Rationale**: The generic tool supports controlled advanced use without raw Docker surfaces, while the Unreal tool proves specialized domain ergonomics and reduces the need for users or sessions to understand runner internals.
- **Alternatives considered**:
  - Expose raw image/mount/device fields: rejected by the Phase 3 guardrails and security posture.
  - Only ship the generic tool: rejected because Phase 3 explicitly requires one curated domain tool.

## Decision 5: Validate with workflow and worker-boundary tests

- **Decision**: Test at the tool bridge, activity catalog, default registry generation, worker initialization, and `MoonMind.Run` routing boundaries.
- **Rationale**: The highest-risk behavior is not Docker process execution itself, which Phase 2 covers, but preserving the tool path and session/workload boundary when plans and managed sessions request Docker-backed tools.
- **Alternatives considered**:
  - Only test helper functions: rejected because capability routing and workflow invocation shape are compatibility-sensitive.
  - Require compose-backed Docker integration in Phase 3: rejected because Phase 2 already validates launcher behavior; Phase 3 focuses on executable-tool exposure.
