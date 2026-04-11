# Feature Specification: Docker-Out-of-Docker Workload Contract

**Feature Branch**: `148-dood-workload-contract`
**Created**: 2026-04-10
**Status**: Draft
**Input**: User description: "Implement Phase 0 and Phase 1 using test-driven development of the DooD plan."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve the Phase 0 boundary contract (Priority: P1)

MoonMind engineers need the completed Docker-out-of-Docker Phase 0 documentation contract to remain stable while Phase 1 runtime models are added, so the new workload contract does not blur session containers, workload containers, runner profiles, or session-assisted workloads.

**Why this priority**: The Phase 1 contract must build on the already locked Phase 0 architecture and cannot safely proceed if the execution primitive or glossary regresses.

**Independent Test**: Run the existing DooD Phase 0 documentation-contract unit test and confirm the canonical DooD, session-plane, execution-model, and remaining-work tracker references still pass.

**Acceptance Scenarios**:

1. **Given** the Phase 0 documentation contract is present, **When** the Phase 1 contract code is added, **Then** the docs still state that Docker-backed workloads use ordinary executable tools and remain outside managed-session identity.
2. **Given** the DooD tracker contains future rollout phases, **When** maintainers review remaining implementation work, **Then** Phase 1 contract completion is visible without moving rollout checklists into canonical docs.

---

### User Story 2 - Validate workload requests before Docker exists (Priority: P1)

MoonMind engineers need a canonical validated workload request and result contract that can be constructed, rejected, or serialized without touching Docker, so later launcher and tool-path phases consume one authoritative payload shape.

**Why this priority**: Phase 1 exit criteria require a single validated workload request and rejection of invalid images, mounts, environment keys, and resource requests before Docker launch code is introduced.

**Independent Test**: Instantiate workload request and result objects against a curated runner profile and verify valid inputs are accepted while disallowed profile IDs, command shapes, environment overrides, resource requests, and workspace paths fail with clear validation errors.

**Acceptance Scenarios**:

1. **Given** a deployment-owned runner profile and a task workspace path under the allowed workspace root, **When** a workload request includes allowed environment overrides and resource overrides within the profile limits, **Then** MoonMind accepts the request and derives deterministic workload labels.
2. **Given** a workload request uses an unknown profile, an empty command, a path outside the allowed workspace root, or an unapproved environment key, **When** validation runs, **Then** MoonMind rejects the request before any Docker command can be built.
3. **Given** a workload result is produced by a future launcher, **When** it is serialized, **Then** it carries bounded execution metadata such as selected profile, labels, exit code, duration, stdout/stderr refs, diagnostics, and timeout/cancel status without embedding large log content.

---

### User Story 3 - Load deployment-owned runner profiles safely (Priority: P2)

MoonMind operators need a deployment-owned runner profile registry that accepts only curated workload shapes, so normal execution uses profile IDs instead of arbitrary image strings or mounts.

**Why this priority**: Runner profiles are the policy boundary between plans/session requests and Docker authority.

**Independent Test**: Load a profile registry from a local configuration file and verify schema validation rejects invalid image references, unsupported network policy, disallowed mount targets, malformed environment allowlists, excessive resource limits, and unsafe device policy.

**Acceptance Scenarios**:

1. **Given** a registry file with a valid lightweight profile, **When** MoonMind loads the registry, **Then** the profile can be selected by ID and exposes image, wrapper, workspace mount, optional cache mounts, environment allowlist, resource limits, timeout defaults, cleanup policy, network policy, and device policy.
2. **Given** a registry file includes a profile with an unpinned or malformed image, host networking, privileged device access, absolute host mounts, or invalid resource ceilings, **When** MoonMind loads the registry, **Then** loading fails with a deterministic policy error.
3. **Given** no deployment registry path is configured, **When** the registry is requested, **Then** MoonMind provides a safe empty/default registry behavior that does not silently allow arbitrary workload images.

### Edge Cases

- A session-assisted workload includes session metadata, but the metadata is grouping context only and must not make the workload a `MoonMind.AgentRun`.
- A request attempts to override secrets or provider auth variables that the profile did not explicitly allow.
- A request points `repo_dir` or `artifacts_dir` outside the task workspace root or uses symlink-style traversal.
- A profile asks for cache mounts or device access that are not deployment-owned and explicitly allowed.
- A resource override exceeds the selected profile's configured ceiling.
- A registry file is missing, empty, malformed, or contains duplicate profile IDs.
- A future launcher needs deterministic names and labels even when `tool_name` or `step_id` includes characters unsafe for Docker labels or names.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST preserve the Phase 0 DooD documentation contract while adding the Phase 1 workload data contract.
- **FR-002**: MoonMind MUST define canonical workload request, result, runner profile, and ownership metadata entities that can be validated and serialized without invoking Docker.
- **FR-003**: A workload request MUST include `profile_id`, `task_run_id`, `step_id`, `attempt`, `repo_dir`, `artifacts_dir`, command arguments, allowlisted environment overrides, timeout/resource overrides, and optional session association metadata.
- **FR-004**: Workload ownership metadata MUST include deterministic labels for `moonmind.kind=workload`, `moonmind.task_run_id`, `moonmind.step_id`, `moonmind.attempt`, `moonmind.tool_name`, and `moonmind.workload_profile`.
- **FR-005**: Request validation MUST reject unknown runner profiles, empty commands, invalid attempts, invalid timeout/resource overrides, environment keys not allowed by the selected profile, and workspace paths outside the allowed task workspace root.
- **FR-006**: Runner profiles MUST replace arbitrary free-form image strings in normal workload execution.
- **FR-007**: Runner profiles MUST define image, entrypoint/command wrapper, workspace mount contract, optional cache mounts, environment allowlist, network policy, resource profile, timeout defaults, cleanup policy, and optional device policy.
- **FR-008**: Runner profile validation MUST reject invalid or unapproved image references, unsupported network policy, privileged defaults, unsafe mount targets, invalid environment allowlists, and resource ceilings outside deployment policy.
- **FR-009**: MoonMind MUST provide a deployment-owned runner profile registry source that can load profiles from configuration and fails closed instead of allowing arbitrary images when configuration is absent or invalid.
- **FR-010**: Workload results MUST capture bounded execution metadata including selected profile, deterministic labels, status, exit code when available, started/completed timestamps, duration, timeout/cancel reason, artifact refs, and diagnostics without embedding large stdout/stderr contents.
- **FR-011**: Optional session association metadata (`session_id`, `session_epoch`, `source_turn_id`) MUST remain contextual and MUST NOT create or imply a new managed agent run.
- **FR-012**: The Phase 1 implementation MUST include automated unit tests proving valid requests are accepted and invalid images, mounts, environment keys, paths, and resource requests are rejected.

### Key Entities *(include if feature involves data)*

- **WorkloadRequest**: A validated control-plane request for one bounded workload container execution.
- **WorkloadResult**: A bounded summary of workload execution outcome and artifact references produced by a future launcher.
- **RunnerProfile**: A deployment-owned profile that defines the allowed image, command wrapper, workspace/cache mounts, environment keys, network/device policy, resource ceilings, timeout defaults, and cleanup behavior.
- **WorkloadOwnershipMetadata**: Deterministic identity and labels tying a workload to the producing task run, step, attempt, tool name, and runner profile.
- **Session Association Metadata**: Optional grouping context linking a workload request to a managed-session epoch and source turn without changing the workload lifecycle.
- **Runner Profile Registry**: A deployment-owned source of curated runner profiles used for profile lookup and policy validation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Existing Phase 0 documentation-contract validation still passes after Phase 1 changes.
- **SC-002**: A valid workload request can be constructed and serialized using a curated runner profile without touching Docker.
- **SC-003**: Unit tests reject invalid images, unsafe mounts, disallowed environment keys, invalid workspace paths, and excessive resource overrides.
- **SC-004**: Runner profile registry loading accepts a valid deployment-owned profile file and rejects malformed or unsafe profile definitions deterministically.
- **SC-005**: Workload request labels are deterministic and include all required `moonmind.*` ownership keys.
