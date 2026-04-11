# Feature Specification: DooD Executable Tool Exposure

**Feature Branch**: `153-dood-executable-tools`  
**Created**: 2026-04-11  
**Status**: Draft  
**Input**: User description: "Implement Phase 3 of the MoonMind Docker-out-of-Docker plan in runtime mode. Expose Docker-backed workloads as ordinary executable tools, not agent sessions. Add tool definitions for container.run_workload and unreal.run_tests. Tool selector capability requirements must route these tools to Docker-capable execution on the existing agent_runtime fleet. Tool execution must load the pinned ToolDefinition, validate inputs, resolve a runner profile, call the workload launcher, and return a normal ToolResult. Planning and managed-session flows must let a Codex session request the tool through the control plane without giving the session container unrestricted Docker authority. Do not expose raw image, mount, device, or arbitrary env parameters to general plans by default. Keep tool.type = agent_runtime reserved for true long-lived agent runtimes. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a Generic Workload Tool (Priority: P1)

As a plan executor, I need a controlled `container.run_workload` tool so an approved plan step can request a Docker-backed workload through MoonMind without directly controlling Docker images, mounts, devices, or unrestricted environment variables.

**Why this priority**: This is the minimum viable Phase 3 behavior. Without the generic workload tool, one-shot workload containers cannot be invoked through the normal executable tool path.

**Independent Test**: Execute a plan step whose tool is `container.run_workload` and verify the step is treated as an executable tool, validates its request against an approved runner profile, launches through the workload launcher, and returns a normal tool result.

**Acceptance Scenarios**:

1. **Given** a pinned tool registry contains `container.run_workload`, **When** a plan step invokes that tool, **Then** MoonMind validates the step inputs and resolves an approved runner profile before any workload launch is attempted.
2. **Given** a valid generic workload tool request, **When** MoonMind executes the step, **Then** the workload is launched through the control-plane workload launcher and the step receives a normal tool result.
3. **Given** a plan attempts to provide raw image, mount, device, or non-allowlisted environment data, **When** MoonMind validates the tool request, **Then** the request is rejected before workload launch.

---

### User Story 2 - Run Curated Unreal Tests (Priority: P1)

As an operator running specialized repository checks, I need an `unreal.run_tests` tool with a stable domain-focused contract so Unreal test runs can use curated runner profiles without exposing the generic Docker surface to normal plans.

**Why this priority**: The Unreal use case motivates the Docker-out-of-Docker strategy. A curated domain tool proves the tool path can support specialized workloads while preserving policy boundaries.

**Independent Test**: Invoke `unreal.run_tests` with a repository workspace, artifacts location, project path, and test selector, then verify MoonMind maps the request to the curated Unreal runner profile and returns a normal tool result.

**Acceptance Scenarios**:

1. **Given** a plan step invokes `unreal.run_tests`, **When** required inputs are present, **Then** MoonMind derives a workload request for the curated Unreal runner profile.
2. **Given** Unreal test execution succeeds or fails, **When** the workload completes, **Then** the producing plan step receives a normal tool result containing bounded workload metadata.
3. **Given** the Unreal tool request omits required project or workspace information, **When** MoonMind validates inputs, **Then** the request fails clearly before workload launch.

---

### User Story 3 - Preserve Managed Session Boundaries (Priority: P2)

As a managed Codex session user, I need sessions to request workload tools through the control plane so the session container can trigger specialized work without receiving unrestricted Docker authority or becoming the workload identity.

**Why this priority**: The core architecture boundary requires session containers and workload containers to remain different roles. Tool exposure must not accidentally convert workload execution into a managed agent session.

**Independent Test**: Execute a managed-session-assisted plan step that requests a workload tool and verify the step routes through the control-plane tool path, not through managed-session launch or session-control operations.

**Acceptance Scenarios**:

1. **Given** a Codex-managed task requests a Docker-backed workload tool, **When** MoonMind evaluates the step, **Then** the session request is handled as a control-plane tool invocation.
2. **Given** a Docker-backed workload starts from a session-assisted step, **When** operators inspect execution metadata, **Then** the workload remains associated with the producing step and is not represented as the session container.
3. **Given** a plan uses `tool.type = "agent_runtime"`, **When** MoonMind evaluates it, **Then** that path remains reserved for true long-lived agent runtime execution rather than generic Docker workload launch.

### Edge Cases

- The pinned tool registry does not contain one of the Docker-backed workload tools requested by a plan.
- A workload tool declares a runner profile that is unknown, disabled, or no longer valid.
- A tool request includes raw Docker image, mount, device, or arbitrary environment parameters.
- A managed-session-assisted request omits optional session association metadata.
- A workload exits with a non-zero status, times out, or is canceled.
- A plan attempts to route Docker-backed workload execution through `tool.type = "agent_runtime"` instead of the executable tool path.
- The Docker-capable fleet is unavailable or lacks the required workload capability.

### Assumptions

- Phase 1 workload request and runner profile validation contracts already exist and remain authoritative for profile policy decisions.
- Phase 2 workload launching already exists and can launch, bound, clean up, and report one-shot workload containers.
- The initial Docker-capable execution host is the existing control-plane-owned worker fleet that already has Docker workload authority.
- Rich artifact publication, live-log presentation, and broader operational hardening remain later phases unless needed to validate the Phase 3 tool path.
- Managed session metadata, when present, is grouping context only and does not make a workload container part of session identity.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST expose `container.run_workload` as an executable tool that uses the ordinary tool path.
- **FR-002**: MoonMind MUST expose `unreal.run_tests` as an executable tool with a stable domain-focused input and output contract.
- **FR-003**: Docker-backed workload tools MUST declare capability requirements that route execution to a Docker-capable control-plane worker fleet.
- **FR-004**: Tool execution MUST load the pinned tool definition before executing a Docker-backed workload step.
- **FR-005**: Tool execution MUST validate inputs before resolving or launching a workload.
- **FR-006**: Tool execution MUST resolve an approved runner profile before invoking the workload launcher.
- **FR-007**: Tool execution MUST call the control-plane workload launcher and return a normal tool result.
- **FR-008**: MoonMind MUST NOT expose raw image, mount, device, or arbitrary environment parameters to general plans by default.
- **FR-009**: MoonMind MUST keep `tool.type = "agent_runtime"` reserved for true long-lived agent runtimes.
- **FR-010**: Managed-session-assisted workload requests MUST flow through the control-plane tool path without granting unrestricted Docker authority to the session container.
- **FR-011**: Workload tool results MUST include bounded metadata sufficient for the producing step to determine success, failure, timeout, or cancellation.
- **FR-012**: Required deliverables include production runtime code changes, not docs-only changes.
- **FR-013**: Required deliverables MUST include validation tests covering tool definition loading, input validation, runner profile resolution, launcher invocation, capability routing, and managed-session boundary preservation.

### Key Entities *(include if feature involves data)*

- **Docker-Backed Workload Tool**: An executable tool that represents a controlled request to launch a bounded workload container through MoonMind.
- **Generic Workload Tool Request**: Inputs for `container.run_workload`, including a runner profile reference, workspace locations, command arguments, allowed environment overrides, timeout/resource overrides, and optional session association metadata.
- **Unreal Test Tool Request**: Inputs for `unreal.run_tests`, including workspace locations, project path, optional target or test selector, and optional bounded execution overrides.
- **Runner Profile**: An approved deployment-owned workload execution profile that determines the container image, workspace contract, resource limits, network posture, environment allowlist, and cleanup behavior.
- **Tool Result**: The normal executable tool response containing bounded workload status, exit metadata, and output references where available.
- **Session-Assisted Workload**: A workload tool invocation requested from a managed-session step where session metadata is grouping context only, not workload identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A plan step invoking `container.run_workload` completes through the ordinary executable tool result path with no managed agent run created for the workload.
- **SC-002**: A plan step invoking `unreal.run_tests` maps to a curated runner profile and returns bounded workload status metadata.
- **SC-003**: Capability routing sends Docker-backed workload tools only to Docker-capable execution and not to non-Docker worker fleets.
- **SC-004**: Attempts to provide raw Docker images, mounts, devices, or arbitrary environment data are rejected before launch.
- **SC-005**: A managed Codex session can request a workload tool through MoonMind while the session container remains without unrestricted Docker authority.
- **SC-006**: Validation tests prove registry loading, input validation, runner profile resolution, launcher invocation, normal tool result mapping, and session/workload boundary preservation.
