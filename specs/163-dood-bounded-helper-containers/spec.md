# Feature Specification: DooD Bounded Helper Containers

**Feature Branch**: `163-dood-bounded-helper-containers`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 7 using test-driven development of the MoonMind Docker-out-of-Docker strategy: optional bounded helper containers. Support short-lived helper services without collapsing them into the managed session model. Deliver a separate bounded-helper contract for owned, TTL-bound helper containers, health/readiness checks, explicit ownership and teardown semantics, a separate workload kind such as helper or bounded_service, required owner step, TTL, readiness contract, stop/kill policy, artifact behavior, and end-to-end tests for helpers that survive across multiple sub-steps within one bounded execution window. Guardrails: do not silently permit indefinite helper lifetimes, and do not treat helper containers as session_id carriers or substitutes for MoonMind.AgentRun. Exit criteria: a temporary service workload can be launched, used, observed, and torn down without confusing the task/session model. Preserve the Docker-out-of-Docker architecture boundaries: Codex session containers and specialized workload containers are different roles; specialized workload containers enter through the executable tool path first; Docker authority stays on control-plane-owned workers; runner profiles replace arbitrary image strings; artifacts and bounded workflow metadata remain authoritative. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a Bounded Helper (Priority: P1)

A task step can request a temporary helper service as a controlled workload so later sub-steps in the same bounded window can use it without creating a managed agent session.

**Why this priority**: This establishes the core Phase 7 capability while preserving the boundary between session containers, workload containers, and true agent runs.

**Independent Test**: Submit a helper request with an approved runner profile, owner step, TTL, readiness contract, artifacts location, and command intent; verify the helper is accepted, assigned bounded ownership metadata, and becomes observable without receiving session identity.

**Acceptance Scenarios**:

1. **Given** an approved helper profile and a request with owner step, TTL, readiness contract, and artifacts location, **When** the request is validated, **Then** MoonMind records it as a bounded helper workload with explicit ownership and expiration metadata.
2. **Given** a helper request includes managed-session association fields, **When** MoonMind records helper metadata, **Then** the fields are used only as grouping context and never make the helper a session carrier or `MoonMind.AgentRun` substitute.
3. **Given** a helper request omits owner step, TTL, or readiness information, **When** the request is evaluated, **Then** MoonMind rejects it before launch with an operator-consumable reason.

---

### User Story 2 - Prove Helper Readiness (Priority: P1)

An operator needs MoonMind to confirm that a temporary helper is ready before dependent sub-steps proceed.

**Why this priority**: A helper that starts but is not ready can cause misleading downstream failures; readiness must be a first-class bounded outcome.

**Independent Test**: Launch a helper with a readiness contract and verify MoonMind reports ready only after the contract succeeds, and reports unhealthy when bounded retries are exhausted.

**Acceptance Scenarios**:

1. **Given** a helper has a readiness contract, **When** the helper becomes ready within the allowed probe window, **Then** MoonMind marks the helper usable and records readiness evidence.
2. **Given** a helper fails readiness checks until retries are exhausted, **When** the bounded probe window ends, **Then** MoonMind reports the helper as unhealthy and leaves a diagnosable result.
3. **Given** readiness checks produce logs or diagnostics, **When** MoonMind records the result, **Then** only bounded, non-secret metadata and artifacts are exposed.

---

### User Story 3 - Use and Tear Down the Helper Window (Priority: P1)

A bounded helper can remain available across multiple sub-steps in one execution window and is then explicitly torn down.

**Why this priority**: The feature exists to support short-lived service dependencies across more than one action, but must not become an indefinite background service.

**Independent Test**: Start a helper, simulate multiple dependent sub-steps using the same helper identity, then request teardown and verify stop/kill/remove behavior, final diagnostics, and no session-model confusion.

**Acceptance Scenarios**:

1. **Given** a helper is ready and within its TTL, **When** multiple dependent sub-steps run in the same bounded window, **Then** they refer to the same helper ownership record and not a managed-session identity.
2. **Given** the bounded window completes, **When** MoonMind tears down the helper, **Then** stop, kill, cleanup, and final artifact behavior follow the helper contract.
3. **Given** a task, step, or helper window is canceled or times out, **When** cleanup runs, **Then** MoonMind makes a best-effort teardown and records the outcome.

---

### User Story 4 - Sweep Expired Helpers (Priority: P2)

An operator needs abandoned helper containers to be removable by ownership and TTL metadata without affecting unrelated workloads.

**Why this priority**: Explicit teardown handles the normal path; TTL-based sweeping limits operational risk after worker interruption or abnormal exits.

**Independent Test**: Create helper and non-helper ownership records with expired and non-expired TTL values, run cleanup, and verify only expired MoonMind-owned helpers are removed.

**Acceptance Scenarios**:

1. **Given** a helper has expired ownership metadata, **When** the sweeper runs, **Then** MoonMind removes it and records the cleanup basis.
2. **Given** a helper has not expired, **When** the sweeper runs, **Then** MoonMind leaves it available for normal lifecycle handling.
3. **Given** an unrelated container or one-shot workload exists, **When** helper cleanup runs, **Then** it is not removed by helper cleanup rules.

### Edge Cases

- A helper request uses an unknown profile, a profile that is not helper-capable, or a profile with no readiness contract.
- A helper request asks for a TTL above the selected profile maximum or omits TTL entirely.
- A readiness check succeeds after one or more failures, times out, returns an unhealthy result, or emits large output.
- A helper becomes unhealthy after readiness succeeds but before all dependent sub-steps complete.
- The owner task, owner step, or bounded helper window is canceled while the helper is starting, ready, unhealthy, or tearing down.
- Cleanup sees malformed, missing, future, or expired TTL metadata.
- Helper metadata includes optional session association context but must not imply helper containers are session containers.
- Artifact publication partially fails during helper start, readiness, or teardown.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST support a bounded helper workload kind that is distinct from one-shot workload containers and true managed agent runtimes.
- **FR-002**: MoonMind MUST require every helper request to identify an owner task run, owner step, attempt, runner profile, artifacts location, TTL, readiness contract, and teardown policy before launch.
- **FR-003**: MoonMind MUST reject helper requests that omit required ownership, TTL, readiness, or artifacts fields before any helper starts.
- **FR-004**: MoonMind MUST enforce helper TTL limits from curated runner profiles and reject helper requests whose TTL exceeds the selected profile maximum.
- **FR-005**: MoonMind MUST expose helper ownership metadata that links the helper to the producing task step without making the helper a `session_id`, `session_epoch`, or `MoonMind.AgentRun` carrier.
- **FR-006**: MoonMind MUST evaluate bounded readiness checks and report ready, unhealthy, timeout, or canceled outcomes with operator-consumable diagnostics.
- **FR-007**: MoonMind MUST keep helpers available only within the declared bounded execution window and MUST NOT silently permit indefinite helper lifetimes.
- **FR-008**: MoonMind MUST support explicit helper teardown that stops, kills, removes, or otherwise cleans up the helper according to the approved helper policy.
- **FR-009**: MoonMind MUST perform best-effort helper teardown when the owner step, owner task, helper readiness, or helper window is canceled or times out.
- **FR-010**: MoonMind MUST publish bounded helper stdout, stderr, readiness, diagnostics, teardown metadata, and declared outputs as artifacts or bounded workflow metadata.
- **FR-011**: MoonMind MUST preserve the Docker-out-of-Docker boundary that helper containers enter through executable workload tooling, Docker authority stays on control-plane-owned workers, and arbitrary image strings are replaced by runner profiles.
- **FR-012**: MoonMind MUST ensure helper cleanup can remove expired helper containers by ownership and TTL metadata while preserving fresh helpers, one-shot workload containers, session containers, and unrelated containers.
- **FR-013**: MoonMind MUST present helper metadata so operators can tell which step launched the helper, whether it was associated with session context, and why it is ready, unhealthy, stopped, expired, or removed.
- **FR-014**: MoonMind MUST ensure helper metadata and artifacts do not expose prompts, transcripts, scrollback, credentials, raw secrets, or unbounded logs.
- **FR-015**: Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

### Key Entities *(include if feature involves data)*

- **Bounded Helper Workload**: A short-lived non-agent workload service owned by a task step, limited by TTL, readiness, and teardown policy.
- **Helper Runner Profile**: A curated profile that declares whether helper use is allowed, the permitted runtime shape, maximum TTL, readiness contract, resources, artifacts behavior, and cleanup policy.
- **Helper Request**: A task-step request to start a helper, including owner metadata, selected profile, TTL, readiness expectations, command intent, artifacts location, and optional session association context.
- **Helper Readiness Result**: Bounded evidence that the helper is ready or unhealthy, including status, attempts, timing, and non-secret diagnostic references.
- **Helper Teardown Result**: Bounded evidence that MoonMind attempted or completed helper stop, kill, removal, and artifact publication.
- **Helper Cleanup Record**: Operator-visible evidence that expired helper cleanup inspected ownership and TTL metadata and removed only eligible helper containers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of validation tests for missing helper owner, TTL, readiness contract, profile, or artifacts fields reject the request before launch.
- **SC-002**: A helper can be launched, marked ready, referenced by at least two simulated dependent sub-steps in the same bounded window, and torn down with final diagnostics in automated validation.
- **SC-003**: Readiness validation covers successful readiness, exhausted retries, timeout, and canceled readiness outcomes with bounded diagnostics.
- **SC-004**: Cleanup validation removes all expired MoonMind-owned helpers in the test set and removes zero fresh helpers, one-shot workload containers, session containers, or unrelated containers.
- **SC-005**: Operator-visible helper metadata distinguishes helper identity from managed-session identity in every successful, unhealthy, canceled, expired, and stopped result.
- **SC-006**: Runtime validation includes production code behavior and automated tests; docs-only or spec-only completion does not satisfy this feature.

## Assumptions

- Helper containers are optional Phase 7 capability and do not replace the default one-shot workload lifecycle.
- Helper runner profiles remain curated by deployment policy; general users do not provide arbitrary images, mounts, or device access.
- Session association metadata may group helper activity with a managed session step, but the helper remains outside the session lifecycle.
- Durable artifacts and bounded workflow metadata are the source of truth; container state is operational and disposable.
