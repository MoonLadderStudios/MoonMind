# Feature Specification: DooD Phase 5 Hardening

**Feature Branch**: `158-dood-phase5-hardening`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 5 using test-driven development of the MoonMind Docker-out-of-Docker strategy. Harden Docker-backed workload tools as a safe default platform capability while preserving the boundary that Codex managed session containers and specialized workload containers are different roles, specialized workload containers enter through the executable tool path first, Docker authority stays on control-plane-owned workers, runner profiles replace arbitrary images, and artifacts plus bounded workflow metadata remain authoritative. Phase 5 deliverables include runner-profile allowlists and registry policy; explicit rules for mounts, environment keys, network modes, device access, and secret injection; resource and concurrency controls for Docker-backed tools; orphan sweeper or janitor behavior; and audit-friendly logging or diagnostics for launch decisions and policy denials. Required hardening includes image provenance and registry allowlists, no-privileged default posture, no host networking by default, no implicit GPU or device access, no automatic inheritance of Codex, Claude, or Gemini auth volumes, per-profile and per-fleet concurrency guards so heavy Unreal jobs cannot starve normal managed-runtime work, cleanup of containers by ownership labels and TTL, and operator-facing denial reasons for unknown profile, disallowed env key, disallowed mount, resource request too large, and missing fleet capability. Required exit criteria are a default profile set that can pass security review, demonstrably reliable orphan cleanup, and visible bounded pressure for heavy-work queues. Runtime mode scope guard: Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deny Unsafe Workload Launches (Priority: P1)

An operator wants Docker-backed workload tools to reject unsafe or unauthorized workload requests before any workload container starts, so specialized workloads can be offered without giving managed session containers broad Docker authority.

**Why this priority**: This is the core safety requirement for making Docker-backed workload tools a default platform capability.

**Independent Test**: Submit workload requests that violate profile, image, mount, environment, network, resource, device, or secret-injection policy and verify each request is denied with no workload container launched.

**Acceptance Scenarios**:

1. **Given** a workload request names an unknown runner profile, **When** the tool is evaluated, **Then** the request is rejected before launch with an operator-facing unknown-profile reason.
2. **Given** a workload request includes an environment key outside the selected profile allowlist, **When** the tool is evaluated, **Then** the request is rejected before launch with a disallowed-env-key reason.
3. **Given** a runner profile or request attempts to use an unapproved mount, host networking, privileged execution, implicit device access, or inherited runtime auth volume, **When** it is evaluated, **Then** MoonMind rejects it as unsafe and does not start a workload container.
4. **Given** a workload requests resources above the selected profile maximum, **When** the request is evaluated, **Then** it is rejected with a resource-request-too-large reason.

---

### User Story 2 - Bound Heavy Workload Capacity (Priority: P2)

An operator wants heavy Docker-backed workloads, including Unreal-style jobs, to have explicit profile and fleet limits so they cannot starve normal managed-runtime work.

**Why this priority**: A safe launcher must protect platform capacity, not only container security.

**Independent Test**: Run workload requests until configured profile or fleet limits are reached and verify additional work is denied or held according to policy while unrelated managed-runtime capacity remains protected.

**Acceptance Scenarios**:

1. **Given** a runner profile has reached its allowed active workload limit, **When** another workload using that profile is requested, **Then** MoonMind prevents the launch and reports bounded capacity pressure.
2. **Given** the Docker-backed workload fleet has reached its allowed active workload limit, **When** another Docker-backed workload is requested, **Then** MoonMind prevents the launch and reports the fleet-capacity reason.
3. **Given** a heavy workload is denied by capacity policy, **When** an operator inspects the result, **Then** the denial is visible as a policy decision rather than an unexplained execution failure.

---

### User Story 3 - Clean Up Orphaned Workloads (Priority: P3)

An operator wants MoonMind to find and clean abandoned workload containers using durable ownership labels and TTL metadata, so failed, canceled, or interrupted workload runs do not accumulate operational risk.

**Why this priority**: Reliable cleanup is required before workload containers can be treated as a routine platform capability.

**Independent Test**: Create workload containers with ownership metadata and expired TTL values, run cleanup, and verify expired workload containers are removed while non-expired or unrelated containers remain untouched.

**Acceptance Scenarios**:

1. **Given** a workload container has MoonMind workload ownership labels and an expired TTL, **When** the sweeper runs, **Then** the container is removed and the cleanup count is recorded.
2. **Given** a workload container has not expired, **When** the sweeper runs, **Then** the container remains running or available for normal lifecycle handling.
3. **Given** a non-workload container exists on the same Docker host, **When** the sweeper runs, **Then** the container is not removed.

---

### User Story 4 - Audit Launch Decisions (Priority: P4)

An operator wants launch approvals, denials, cleanup actions, and pressure signals to be diagnosable from bounded metadata and artifacts, so security reviews and incident investigations do not require ad hoc log archaeology.

**Why this priority**: Auditability is necessary for operational trust but depends on the enforcement and cleanup behavior above.

**Independent Test**: Execute successful, denied, timed-out, and cleaned-up workload scenarios and verify each outcome includes bounded, operator-consumable decision metadata without leaking secrets.

**Acceptance Scenarios**:

1. **Given** a workload launch is approved, **When** an operator inspects the outcome, **Then** the selected profile, approved image reference, resource bounds, ownership labels, and launch status are visible.
2. **Given** a workload launch is denied, **When** an operator inspects the outcome, **Then** a stable denial reason and relevant non-secret details are visible.
3. **Given** cleanup removes expired workload containers, **When** an operator inspects cleanup diagnostics, **Then** the removed workload count and ownership basis are visible.

### Edge Cases

- A profile registry is missing, empty, duplicated, malformed, or references an image outside the allowed provenance policy.
- A request attempts to smuggle secrets through environment overrides, inherited runtime volumes, broad mounts, or session-container credentials.
- A request attempts privileged execution, host networking, GPU or device access, or resources beyond profile policy.
- A workload times out, is canceled, or loses its supervising worker before normal cleanup completes.
- Multiple heavy workloads are requested concurrently across the same profile or fleet.
- Cleanup sees malformed, missing, future, or expired TTL metadata on labeled workload containers.
- Operator diagnostics must remain useful while avoiding raw secrets, broad environment dumps, or unbounded log content.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST enforce runner-profile allowlists and registry policy before launching any Docker-backed workload.
- **FR-002**: MoonMind MUST reject workload requests that name unknown profiles, disallowed environment keys, disallowed mounts, excessive resources, missing workload fleet capability, or unsafe runtime options.
- **FR-003**: MoonMind MUST require workload image provenance to match an approved registry or image policy before launch.
- **FR-004**: MoonMind MUST default Docker-backed workload execution to a non-privileged posture.
- **FR-005**: MoonMind MUST reject host networking by default for Docker-backed workloads.
- **FR-006**: MoonMind MUST reject implicit GPU or device access unless an explicit approved policy allows it.
- **FR-007**: MoonMind MUST prevent Codex, Claude, Gemini, and other managed-runtime authentication volumes or credentials from being inherited automatically by workload containers.
- **FR-008**: MoonMind MUST enforce per-profile concurrency limits for Docker-backed workloads.
- **FR-009**: MoonMind MUST enforce a fleet-level capacity control for Docker-backed workloads so heavy jobs cannot starve normal managed-runtime work.
- **FR-010**: MoonMind MUST label workload containers with bounded ownership and expiration metadata sufficient for traceability and cleanup.
- **FR-011**: MoonMind MUST provide orphan cleanup behavior that removes expired MoonMind-owned workload containers without removing unrelated containers.
- **FR-012**: MoonMind MUST expose operator-facing diagnostics for launch approvals, policy denials, capacity pressure, and cleanup actions.
- **FR-013**: MoonMind MUST keep workload outputs and diagnostics in durable artifacts or bounded workflow metadata rather than treating workload containers as managed session continuity.
- **FR-014**: MoonMind MUST keep Docker-backed workload invocation on the executable tool path unless the workload is itself a true managed agent runtime.
- **FR-015**: MoonMind MUST preserve the separation between managed session identity and workload container identity.
- **FR-016**: Runtime deliverables MUST include production runtime code changes, not documentation-only or specification-only changes.
- **FR-017**: Runtime deliverables MUST include validation tests covering policy enforcement, concurrency limits, cleanup behavior, and operator-facing denial diagnostics.

### Key Entities *(include if feature involves data)*

- **Runner Profile**: A curated workload definition that identifies the approved workload class, image provenance, environment allowlist, mount policy, network posture, resource bounds, cleanup policy, device policy, and concurrency limit.
- **Workload Request**: A tool-originated request to run one Docker-backed workload against a task workspace, including the selected runner profile, command intent, ownership metadata, artifacts location, resource overrides, timeout, and optional session association.
- **Workload Result**: Bounded outcome metadata for a workload run, including status, timing, exit information, artifact references, selected profile, policy decision details, and cleanup diagnostics.
- **Policy Denial**: A structured decision that prevents launch and explains the non-secret reason, such as unknown profile, disallowed environment key, disallowed mount, excessive resource request, or missing fleet capability.
- **Workload Ownership Metadata**: Labels and bounded fields that tie a workload container to the producing task run, step, attempt, tool, profile, and expiration policy without making it a managed session.
- **Cleanup Record**: Operator-consumable evidence that an orphan sweep inspected workload ownership metadata and removed expired workload containers while leaving unrelated containers alone.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of requests that violate profile, image, mount, environment, network, device, secret-inheritance, or resource policy are rejected before container launch in validation tests.
- **SC-002**: 100% of policy denials covered by this feature expose a stable operator-facing reason and non-secret details.
- **SC-003**: Concurrent workload validation demonstrates that per-profile and fleet-level limits prevent additional heavy workload launches once configured capacity is reached.
- **SC-004**: Orphan cleanup validation removes all expired MoonMind-owned workload containers in the test set and removes zero unrelated or non-expired containers.
- **SC-005**: Successful and denied workload outcomes are diagnosable from durable artifacts or bounded metadata without relying on unbounded process logs.
- **SC-006**: Validation coverage includes production runtime behavior for launch policy, capacity control, and cleanup behavior; docs-only changes do not satisfy this feature.

## Assumptions

- The feature builds on the existing one-shot Docker-backed workload tool path rather than introducing bounded helper containers.
- Runner profiles remain deployment-owned for this phase; repo-authored profile overrides are out of scope unless a separate approval and policy model is introduced.
- Default behavior is fail-closed when a registry, profile, capability, or policy value is missing or unsupported.
- Session association metadata may be used for grouping, but workload containers must not become session identity carriers.
