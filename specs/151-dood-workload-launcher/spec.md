# Feature Specification: Docker-Out-of-Docker Workload Launcher

**Feature Branch**: `151-dood-workload-launcher`  
**Created**: 2026-04-11  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 of the MoonMind Docker-out-of-Docker plan: build the Docker workload launcher on the existing Docker-capable agent_runtime worker fleet. The launcher must resolve runner profiles, construct deterministic docker run arguments, mount agent_workspaces and approved profile cache volumes, set the workdir to the task repo directory, capture stdout/stderr and exit diagnostics, enforce timeout/cancel cleanup, remove ephemeral containers, provide docker stop/kill/rm/orphan lookup utilities, and expose a docker_workload fleet capability without overloading managed-session verbs. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a Validated Workload Container (Priority: P1)

MoonMind operators need a validated workload request to launch one bounded workload container through the control plane, so specialized toolchain work can run against the task workspace without giving a managed session container direct Docker authority.

**Why this priority**: This is the minimum viable Phase 2 behavior. Without it, later executable tools cannot use runner profiles to perform specialized work.

**Independent Test**: Submit a valid workload request using an approved runner profile and verify that the workload runs against the expected task workspace, returns bounded execution metadata, and cleans up the container after completion.

**Acceptance Scenarios**:

1. **Given** a workload request has already passed runner-profile validation, **When** MoonMind launches it, **Then** the workload runs with the selected profile, task workspace, approved mounts, workdir, env overrides, timeout, and resource limits.
2. **Given** the workload exits successfully, **When** MoonMind records the result, **Then** the result includes the selected profile, deterministic workload identity, exit code, start and completion timestamps, duration, and bounded diagnostics.
3. **Given** the workload exits with a non-zero status, **When** MoonMind records the result, **Then** the failure is returned as workload execution metadata rather than being treated as a managed agent session failure.

---

### User Story 2 - Clean Up Timed-Out or Canceled Containers (Priority: P1)

MoonMind operators need workload containers to stop, terminate, and be removed when timeouts or cancellations occur, so routine failed workload runs do not leave orphan containers.

**Why this priority**: Timeout and cancellation cleanup is required before the platform can safely execute long-running specialized containers.

**Independent Test**: Run a workload that does not complete before its timeout and verify that MoonMind stops, terminates, removes, and reports the timed-out workload without leaving a routine orphan container.

**Acceptance Scenarios**:

1. **Given** a workload exceeds its configured timeout, **When** timeout handling runs, **Then** MoonMind records a timed-out result and performs stop, terminate, and removal cleanup according to profile policy.
2. **Given** a workload is canceled while running, **When** cancellation handling runs, **Then** MoonMind attempts bounded cleanup before releasing control back to the caller.
3. **Given** a previous run left labeled workload containers behind, **When** an operator or janitor searches by MoonMind ownership labels, **Then** matching orphan candidates can be identified for cleanup.

---

### User Story 3 - Route Workloads to the Docker-Capable Fleet (Priority: P2)

MoonMind engineers need Docker-backed workload execution to appear as a distinct workload capability on the current Docker-capable worker fleet, so workloads remain separate from managed-session lifecycle operations.

**Why this priority**: The architectural boundary is central to the DooD plan: workload containers are ordinary executable workloads, not new managed sessions.

**Independent Test**: Inspect worker routing metadata and verify that workload launch capability is advertised only by the Docker-capable fleet and uses a dedicated workload activity, not managed-session verbs.

**Acceptance Scenarios**:

1. **Given** MoonMind resolves worker topology, **When** it inspects the Docker-capable fleet, **Then** that fleet exposes a Docker workload capability alongside managed runtime capability.
2. **Given** MoonMind resolves non-Docker fleets, **When** it inspects forbidden capabilities, **Then** Docker workload execution is not advertised by those fleets.
3. **Given** a workload launch is requested, **When** MoonMind routes the operation, **Then** the route is distinct from managed-session launch, turn, interrupt, clear, and terminate operations.

---

### Edge Cases

- The selected profile is missing or invalid after the request was created.
- The task workspace or artifacts directory is unavailable at launch time.
- Approved cache volumes are declared by the profile but unavailable in the deployment.
- The workload command emits large stdout or stderr output.
- The workload process exits while timeout cleanup is also being attempted.
- The container has already disappeared before cleanup runs.
- Cancellation occurs while the workload is still starting.
- Labels or identifiers contain characters that are unsafe for container names.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST launch Docker-backed workload containers only from requests that have already been validated against an approved runner profile.
- **FR-002**: MoonMind MUST derive deterministic workload identity and ownership labels for each launch, including task run, step, attempt, tool name, and runner profile.
- **FR-003**: MoonMind MUST run workload containers against the task repository directory and approved artifacts directory associated with the request.
- **FR-004**: MoonMind MUST mount the shared task workspace and only profile-approved cache volumes for normal workload execution.
- **FR-005**: MoonMind MUST apply the selected profile and request limits for environment overrides, network access, timeout, resource controls, entry behavior, and command invocation.
- **FR-006**: MoonMind MUST capture bounded stdout, stderr, exit code, timing, timeout reason, selected profile, image reference, and diagnostics metadata for the workload result.
- **FR-007**: MoonMind MUST remove ephemeral workload containers after completion when the selected cleanup policy requires removal.
- **FR-008**: MoonMind MUST perform bounded stop and terminate cleanup when workload execution times out or is canceled.
- **FR-009**: MoonMind MUST provide an operator-usable cleanup lookup based on MoonMind workload ownership labels.
- **FR-010**: MoonMind MUST expose Docker workload execution as a distinct workload capability on the existing Docker-capable worker fleet.
- **FR-011**: MoonMind MUST NOT overload managed-session launch or session-control operations to mean generic workload container execution.
- **FR-012**: Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

### Key Entities *(include if feature involves data)*

- **Validated Workload Request**: A request that has passed runner-profile policy checks and contains workload identity, workspace locations, command, timeout, resource, and optional session association metadata.
- **Runner Profile**: The approved workload execution shape, including image reference, workspace mount contract, cache mounts, environment allowlist, resource limits, network policy, timeout defaults, and cleanup policy.
- **Workload Result**: Bounded metadata describing workload status, exit code, timing, diagnostics, output references, timeout or cancellation reason, and selected profile/image details.
- **Workload Container**: A one-shot specialized container launched by MoonMind for a bounded workload and explicitly separate from a managed session container.
- **Workload Cleanup Lookup**: A label-based mechanism for finding workload containers owned by a task run, step, attempt, tool, or runner profile.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid workload request can launch one container against the shared task workspace and return bounded execution metadata.
- **SC-002**: Successful and failed workload exits both produce a workload result with deterministic labels, timing, selected profile, and exit status.
- **SC-003**: Timeout and cancellation tests demonstrate that routine workload containers are stopped, terminated, and removed according to cleanup policy.
- **SC-004**: Worker topology validation shows Docker workload capability is routed to the existing Docker-capable worker fleet and not to non-Docker fleets.
- **SC-005**: Validation tests cover launch argument construction, cleanup behavior, orphan lookup, and worker routing.
