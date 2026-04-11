# Feature Specification: DooD Executable Tool Exposure

**Feature Branch**: `152-dood-executable-tools`  
**Created**: 2026-04-11  
**Status**: Implemented  
**Input**: Implement Phase 3 of the MoonMind Docker-out-of-Docker plan: expose Docker-backed workloads as executable tools, not managed agent sessions.

## User Scenarios & Testing

### User Story 1 - Execute Generic Workload Tool (Priority: P1)

As a MoonMind plan executor, I need `container.run_workload` to run through the normal `tool.type = "skill"` path so advanced controlled workloads can use runner profiles without exposing raw Docker authority to session containers.

**Independent Test**: Execute a plan node with `tool.type = "skill"` and `name = "container.run_workload"` and verify it routes to the `agent_runtime` task queue via `mm.tool.execute`.

### User Story 2 - Execute Curated Unreal Tool (Priority: P1)

As a MoonMind operator, I need `unreal.run_tests` to provide a stable domain contract that maps onto a curated Unreal runner profile.

**Independent Test**: Invoke `unreal.run_tests` inputs and verify the handler builds the curated Unreal command, validates the runner profile request, and returns a normal `ToolResult`.

## Requirements

- **FR-001**: MoonMind MUST define `container.run_workload` as an executable `tool.type = "skill"` tool requiring `docker_workload`.
- **FR-002**: MoonMind MUST define `unreal.run_tests` as an executable `tool.type = "skill"` tool requiring `docker_workload`.
- **FR-003**: Docker-backed workload tools MUST route to the existing `agent_runtime` fleet, not to managed-session launch or control verbs.
- **FR-004**: Tool handlers MUST convert tool inputs into validated `WorkloadRequest` payloads through the runner profile registry before invoking the Docker launcher.
- **FR-005**: The generic tool MUST NOT expose raw image, mount, device, or arbitrary Docker parameters.
- **FR-006**: Workload tool execution MUST return a normal `ToolResult` containing bounded workload result metadata.

## Success Criteria

- **SC-001**: `MoonMind.Run` can execute a Docker-backed workload plan step through `mm.tool.execute` on the agent-runtime task queue.
- **SC-002**: Focused unit tests cover tool definitions, routing, request conversion, launcher invocation, and normal `ToolResult` mapping.
