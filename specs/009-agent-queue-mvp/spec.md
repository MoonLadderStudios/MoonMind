# Feature Specification: Agent Queue MVP (Milestone 1)

**Feature Branch**: `009-agent-queue-mvp`  
**Created**: 2026-02-13  
**Status**: Draft  
**Input**: User description: "Implement Milestone 1 of docs/CodexTaskQueue.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Queue and Inspect Jobs via REST (Priority: P1)

As an API client, I can create queue jobs and inspect their state so work can be scheduled for remote executors.

**Why this priority**: Without enqueue and inspection endpoints, no producer can submit work and no queue state can be monitored.

**Independent Test**: Create a job with `POST /api/queue/jobs`, then verify it is retrievable with `GET /api/queue/jobs/{jobId}` and listed by `GET /api/queue/jobs`.

**Acceptance Scenarios**:

1. **Given** an authenticated API caller and a valid create payload, **When** `POST /api/queue/jobs` is called, **Then** the API returns a persisted job with status `queued`.
2. **Given** existing jobs in multiple states, **When** `GET /api/queue/jobs` is called with filters, **Then** only matching jobs are returned in a deterministic order.

---

### User Story 2 - Worker Job Lifecycle Control (Priority: P1)

As an executor worker, I can claim, heartbeat, complete, and fail jobs so queue processing is safe and trackable.

**Why this priority**: Claim and state-transition endpoints are the execution core of the queue and are mandatory for Milestone 1.

**Independent Test**: Start with queued jobs, claim one via `POST /api/queue/jobs/claim`, renew lease with heartbeat, and transition to succeeded/failed.

**Acceptance Scenarios**:

1. **Given** multiple queued jobs, **When** a worker claims a job, **Then** the highest-priority, oldest queued job is moved to `running` with worker ownership and lease expiration set.
2. **Given** a running job claimed by a worker, **When** the same worker calls heartbeat, **Then** the lease expiration is extended.
3. **Given** a running job claimed by a worker, **When** that worker completes or fails the job, **Then** terminal state, finish time, and summary/error details are persisted.

---

### User Story 3 - Correct Concurrent Claim Behavior (Priority: P2)

As a platform operator, I need claim behavior to remain correct under concurrent workers so no job is double-claimed.

**Why this priority**: Concurrency safety is a core quality requirement for queue correctness and directly called out in Milestone 1.

**Independent Test**: Run concurrent claim requests against multiple queued jobs and verify each job is claimed by at most one worker using transactional locking.

**Acceptance Scenarios**:

1. **Given** two workers claiming at the same time, **When** claim transactions execute, **Then** no single queued job is returned to both workers.
2. **Given** running jobs with expired leases, **When** a claim operation executes, **Then** expired jobs are requeued (or failed per policy) before selecting the next job.

### Edge Cases

- Claim request has no eligible jobs after filters: API returns success with `job: null`.
- Worker attempts heartbeat/complete/fail for a job it does not own: API rejects with an authorization/ownership error.
- Worker attempts terminal transition on an already terminal job: API rejects with a state-transition error.
- Claim and lifecycle APIs receive unknown job IDs: API returns not found without mutating queue state.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/CodexTaskQueue.md:454`, `docs/CodexTaskQueue.md:456`): Milestone 1 MUST add an `agent_jobs` table and Alembic migration.
- **DOC-REQ-002** (Source: `docs/CodexTaskQueue.md:44`): `agent_jobs` MUST include identity, type, status, priority, payload, ownership/lease, attempts, summary/error, artifact path, and lifecycle timestamps.
- **DOC-REQ-003** (Source: `docs/CodexTaskQueue.md:48`): Queue status model MUST include `queued`, `running`, `succeeded`, `failed`, `cancelled`.
- **DOC-REQ-004** (Source: `docs/CodexTaskQueue.md:91`, `docs/CodexTaskQueue.md:99`): Claim operation MUST use atomic transactional selection with `FOR UPDATE SKIP LOCKED`.
- **DOC-REQ-005** (Source: `docs/CodexTaskQueue.md:93`): Claim operation MUST process expired leases before selecting the next queued job.
- **DOC-REQ-006** (Source: `docs/CodexTaskQueue.md:106`, `docs/CodexTaskQueue.md:117`): Queue behavior MUST be implemented through repository and service methods.
- **DOC-REQ-007** (Source: `docs/CodexTaskQueue.md:125`, `docs/CodexTaskQueue.md:460`): REST MVP MUST expose enqueue, claim, heartbeat, complete, fail, get, and list operations.
- **DOC-REQ-008** (Source: `docs/CodexTaskQueue.md:131`, `docs/CodexTaskQueue.md:136`): Queue REST API MUST live in a dedicated router under the queue API prefix and be mounted in the API service.
- **DOC-REQ-009** (Source: `docs/CodexTaskQueue.md:183`): Queue endpoints MUST follow existing MoonMind authentication dependency patterns.
- **DOC-REQ-010** (Source: `docs/CodexTaskQueue.md:461`): Milestone 1 MUST include unit tests for state transitions and claim concurrency with SKIP LOCKED semantics.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`): The system MUST persist queue jobs in a first-class `agent_jobs` datastore model with all MVP lifecycle fields and statuses.
- **FR-002** (`DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`): The system MUST provide transactional repository/service claim logic that reprocesses expired leases and atomically claims eligible queued jobs using SKIP LOCKED semantics.
- **FR-003** (`DOC-REQ-006`, `DOC-REQ-007`): The system MUST support lifecycle operations for enqueue, claim, heartbeat, completion, failure, get-by-id, and list with status transition validation.
- **FR-004** (`DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`): The API layer MUST expose queue lifecycle operations through a dedicated router mounted in the service and protected by standard authentication dependencies.
- **FR-005** (`DOC-REQ-010`): The implementation MUST include automated unit coverage for queue state transitions and concurrent claim safety.
- **FR-006**: Runtime deliverables MUST include production code changes plus validation tests; documentation-only outcomes do not satisfy this feature.

### Key Entities *(include if feature involves data)*

- **AgentJob**: Persistent queue job with scheduling metadata, lease ownership, lifecycle timestamps, and execution outcome fields.
- **AgentJobStatus**: Enumerated lifecycle value defining allowed queue transitions (`queued`, `running`, `succeeded`, `failed`, `cancelled`).
- **QueueClaimRequest**: Worker claim intent including worker identity, lease duration, and optional allowed job types.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A migration can be applied on a clean database and creates the `agent_jobs` table with all required MVP fields and status constraints.
- **SC-002**: All Milestone 1 queue REST endpoints are reachable and return schema-valid responses for success and validation failures.
- **SC-003**: Automated unit tests verify lifecycle transition correctness for enqueue, claim, heartbeat, complete, and fail paths.
- **SC-004**: Automated concurrency tests demonstrate no duplicate claims for the same job under concurrent claim calls and validate SKIP LOCKED behavior.
- **SC-005**: Unit tests for Milestone 1 pass through the project-standard unit test command.

## Assumptions

- Milestone 1 excludes artifact upload/download, MCP queue tools, remote worker daemon, and queue hardening from Milestones 2-5.
- Existing MoonMind auth and repository conventions remain authoritative for endpoint dependencies and persistence patterns.
