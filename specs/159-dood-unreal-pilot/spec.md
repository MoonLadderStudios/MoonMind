# Feature Specification: DooD Unreal Pilot

**Feature Branch**: `159-dood-unreal-pilot`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 6 using test-driven development of the DooD plan: Unreal pilot and real repository validation. Deliver an `unreal-5_3-linux` runner profile, a MoonMind-maintained or pinned Unreal runner image policy, `unreal.run_tests` end-to-end execution shape for representative Unreal repositories, cache strategy using `unreal_ccache_volume` and `unreal_ubt_volume`, smoke/e2e fixtures, validation of workspace mounts, report collection, log capture, cancellation behavior, repeat execution with cache reuse, and operator documentation for enabling Unreal."

## User Scenarios & Testing

### User Story 1 - Enable the Curated Unreal Runner (Priority: P1)

Operators need a deployment-owned `unreal-5_3-linux` runner profile so the existing `unreal.run_tests` tool can run without accepting arbitrary image, mount, cache, or device input.

**Independent Test**: Load the default workload profile registry and verify it includes the pinned Unreal runner image, workspace mount, approved cache volumes, no network, no device access, resource bounds, timeout bounds, and expected env allowlist.

### User Story 2 - Run Unreal Tests Through a Stable Domain Contract (Priority: P1)

Plan authors need `unreal.run_tests` inputs for project path, target/test selector, report output paths, and required environment without exposing raw Docker controls.

**Independent Test**: Invoke the tool handler with Unreal inputs and verify the generated `WorkloadRequest` uses `unreal-5_3-linux`, builds the curated command, declares report/log artifacts, and passes only allowlisted Unreal env keys.

### User Story 3 - Preserve Artifact and Cache Semantics (Priority: P2)

Operators need repeated Unreal runs to reuse approved caches while durable reports and logs remain under the task artifacts directory.

**Independent Test**: Build Docker launch args for the Unreal profile and verify the workspace mount and cache volumes are present, the workdir stays under the workspace mount, report outputs stay under `artifactsDir`, and runtime stdout/stderr/diagnostics are still published on success or failure.

### Edge Cases

- `unreal.run_tests` omits a report path and must use deterministic default report paths.
- An Unreal input attempts an absolute or parent-relative report output path.
- A request overrides the Unreal profile with an unapproved profile.
- A deployment omits `MOONMIND_WORKLOAD_PROFILE_REGISTRY`; the built-in registry must still provide the curated Unreal profile.
- Cache volumes must never be treated as durable artifacts.

## Requirements

- **FR-001**: MoonMind MUST ship a deployment-owned default workload profile registry containing `unreal-5_3-linux`.
- **FR-002**: The `unreal-5_3-linux` profile MUST use a pinned non-`latest` image under an approved registry policy.
- **FR-003**: The Unreal profile MUST mount `agent_workspaces`, `unreal_ccache_volume`, and `unreal_ubt_volume` only as Docker named volumes.
- **FR-004**: The Unreal profile MUST deny host networking, privileged execution, implicit device access, and unmanaged auth volume inheritance.
- **FR-005**: `unreal.run_tests` MUST expose project path, optional target/test selector, optional report output paths, timeout/resources, declared outputs, and allowlisted Unreal env overrides.
- **FR-006**: `unreal.run_tests` MUST construct a curated command without raw image, mount, device, or arbitrary env controls.
- **FR-007**: `unreal.run_tests` MUST publish runtime stdout, stderr, diagnostics, and declared report artifacts as workload outputs.
- **FR-008**: The Unreal cache strategy MUST use approved cache volumes and MUST NOT treat cache contents as durable workflow truth.
- **FR-009**: Runtime deliverables MUST include production code/config changes and validation tests.

## Success Criteria

- **SC-001**: Default registry loading exposes exactly the curated Unreal pilot profile when no operator registry override is configured.
- **SC-002**: Unit coverage proves invalid report paths and disallowed env keys are rejected before launcher execution.
- **SC-003**: Unit coverage proves the generated Unreal Docker args include the workspace and cache mounts but no privileged, host-network, or device access.
- **SC-004**: Tool-bridge coverage proves `unreal.run_tests` returns normal `ToolResult` metadata with report output refs when the launcher returns a workload result.

## Assumptions

- Real Unreal Engine execution requires an operator-provided image compatible with licensing and local deployment policy; this phase pins the control-plane profile and command contract but does not build a binary image inside this repo.
- Integration against a full Unreal repository remains deployment/local-stack validation because CI cannot assume Unreal Engine assets or credentials.
