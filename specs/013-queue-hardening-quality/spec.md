# Feature Specification: Agent Queue Hardening and Quality (Milestone 5)

**Feature Branch**: `013-queue-hardening-quality`  
**Created**: 2026-02-14  
**Status**: Draft  
**Input**: User description: "Implement Milestone 5 of docs/CodexTaskQueue.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enforce Trusted Worker Identity (Priority: P1)

As a queue operator, I need worker authentication and per-worker policy enforcement so only authorized workers can claim and mutate jobs they are allowed to run.

**Why this priority**: Untrusted workers can execute or mutate arbitrary jobs, so auth and policy enforcement are the highest-risk gap in Milestone 5.

**Independent Test**: Create one restricted worker identity and verify claim/heartbeat/complete/fail requests are accepted only with valid worker credentials and matching allowlists.

**Acceptance Scenarios**:

1. **Given** a worker request with invalid credentials, **When** it calls a worker mutation endpoint, **Then** the API rejects the request with an auth failure.
2. **Given** a worker identity with repository and job-type restrictions, **When** it claims jobs, **Then** jobs outside those restrictions are never assigned.
3. **Given** a queued job requiring capabilities, **When** a worker without required capabilities claims, **Then** the job is skipped and remains queued.

---

### User Story 2 - Retry with Backoff and Dead-Letter Handling (Priority: P1)

As an operator, I need retry scheduling and terminal dead-letter behavior so transient failures retry safely and permanently failing jobs stop cycling.

**Why this priority**: Without controlled retry behavior, queue reliability and worker efficiency degrade under failure conditions.

**Independent Test**: Fail a job as retryable multiple times and verify backoff delay is applied between attempts and final terminal state becomes dead-letter when attempts are exhausted.

**Acceptance Scenarios**:

1. **Given** a retryable failure with attempts remaining, **When** fail is recorded, **Then** the job requeues with a non-zero retry delay.
2. **Given** a retryable failure with no attempts remaining, **When** fail is recorded, **Then** the job transitions to dead-letter and is not claimable.
3. **Given** a queued job with a future retry timestamp, **When** workers claim jobs, **Then** the job is not returned until its retry time is reached.

---

### User Story 3 - Observe Job Events and Streaming-ish Logs (Priority: P2)

As a producer/operator, I need append-only job events and pollable log updates so I can monitor job lifecycle progress from another machine.

**Why this priority**: Cross-machine execution needs progress visibility to debug failures and track execution state.

**Independent Test**: Append events for a job, then poll events with cursor/after filters to retrieve incremental updates in order.

**Acceptance Scenarios**:

1. **Given** worker lifecycle transitions (claim, heartbeat, complete/fail), **When** operations run, **Then** corresponding event entries are persisted.
2. **Given** clients polling event feed with an `after` cursor, **When** new events are emitted, **Then** only newer events are returned in stable order.
3. **Given** event-level payload metadata, **When** events are read, **Then** message, level, and payload fields remain available for diagnostics.

### Edge Cases

- Worker token is valid but worker ID in request does not match the token-bound worker identity.
- Worker allows a job type but repository in job payload is missing or not in the worker allowlist.
- Job payload omits capability requirements; claim matching should default to eligible.
- Retry delay fields are malformed or missing in persisted jobs from earlier milestones.
- Event polling requests invalid timestamps, extreme limits, or jobs that do not exist.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/CodexTaskQueue.md:489`, `docs/CodexTaskQueue.md:491`): Milestone 5 MUST enforce worker authentication via worker tokens and/or OIDC/JWT-protected worker identity.
- **DOC-REQ-002** (Source: `docs/CodexTaskQueue.md:410`, `docs/CodexTaskQueue.md:415`): Dedicated worker token validation MUST map inbound token credentials to worker identity and permissions.
- **DOC-REQ-003** (Source: `docs/CodexTaskQueue.md:419`, `docs/CodexTaskQueue.md:420`): Server MUST enforce repository and job-type allowlists per worker credential.
- **DOC-REQ-004** (Source: `docs/CodexTaskQueue.md:97`, `docs/CodexTaskQueue.md:493`): Claiming logic MUST support job capability matching between queued jobs and worker capabilities.
- **DOC-REQ-005** (Source: `docs/CodexTaskQueue.md:63`, `docs/CodexTaskQueue.md:72`): System MUST persist append-only job events with level/message/payload fields.
- **DOC-REQ-006** (Source: `docs/CodexTaskQueue.md:494`): Queue API MUST provide a streaming-ish logs/events surface for incremental progress visibility.
- **DOC-REQ-007** (Source: `docs/CodexTaskQueue.md:495`): Queue lifecycle MUST support retries with backoff before jobs become claimable again.
- **DOC-REQ-008** (Source: `docs/CodexTaskQueue.md:495`): Queue lifecycle MUST support dead-letter behavior for retry-exhausted jobs.
- **DOC-REQ-009** (Source: `docs/CodexTaskQueue.md:93`, `docs/CodexTaskQueue.md:100`): Expired lease requeue/failure handling MUST remain atomic with claim selection semantics.
- **DOC-REQ-010** (Source: `docs/CodexTaskQueue.md:421`): Worker-authenticated artifact upload paths MUST continue enforcing artifact size/content validation constraints.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-002`): Worker mutation endpoints (`claim`, `heartbeat`, `complete`, `fail`, event append) MUST require a validated worker identity from either dedicated worker token or authenticated OIDC/JWT principal.
- **FR-002** (`DOC-REQ-002`, `DOC-REQ-003`): Worker credentials MUST resolve to policy fields (worker id, allowed repositories, allowed job types, capabilities) and requests that violate policy MUST be rejected or filtered from claim selection.
- **FR-003** (`DOC-REQ-004`, `DOC-REQ-009`): Claim selection MUST only return queued jobs whose required capabilities are satisfied and whose retry delay window has elapsed.
- **FR-004** (`DOC-REQ-007`, `DOC-REQ-008`): Retryable failures MUST schedule a delayed requeue with backoff while retry exhaustion transitions jobs to dead-letter terminal status.
- **FR-005** (`DOC-REQ-005`, `DOC-REQ-006`): System MUST persist append-only job events and expose list/poll APIs that allow incremental retrieval of event logs.
- **FR-006** (`DOC-REQ-009`): Lease-expiry recovery and claim mutation semantics MUST remain concurrency-safe and deterministic under concurrent worker claims.
- **FR-007** (`DOC-REQ-010`): Existing artifact upload validation (size constraints and safe content handling) MUST remain enforced for authenticated worker flows.
- **FR-008**: Runtime deliverables MUST include production code changes plus validation tests (docs/spec-only output is insufficient).

### Key Entities *(include if feature involves data)*

- **AgentWorkerToken**: Worker credential record containing token hash, worker identity, and policy constraints (allowed repositories, job types, capabilities).
- **AgentJobRetryState**: Job retry scheduling fields that control next claim eligibility and dead-letter transition.
- **AgentJobEvent**: Append-only event item with severity level, message text, optional payload metadata, and creation timestamp.
- **WorkerAuthContext**: Resolved identity/policy context attached to worker requests during queue mutations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Worker mutation endpoints reject invalid or unauthorized worker credentials with typed 401/403 responses.
- **SC-002**: Claim API only returns jobs matching worker repository/job-type allowlists and required capabilities in automated tests.
- **SC-003**: Retryable failures apply non-zero backoff delay and exhausted retries transition to `dead_letter` status in automated tests.
- **SC-004**: Job events are persisted and incrementally queryable by polling cursor/after filters in automated tests.
- **SC-005**: Unit tests covering hardening behavior execute through `./tools/test_unit.sh` in environments with pytest available.

## Assumptions

- Milestone 5 should extend existing Milestone 1-4 queue code paths rather than replacing queue or MCP APIs.
- Dedicated worker token enforcement is required for worker daemon credentials even when local dev auth provider is disabled.
- Capability requirements are expressed in job payload as `requiredCapabilities` (array of strings).
