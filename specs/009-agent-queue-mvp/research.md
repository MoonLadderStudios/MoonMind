# Phase 0: Research Findings

**Feature**: Agent Queue MVP (Milestone 1)  
**Branch**: `009-agent-queue-mvp`  
**Date**: 2026-02-13

## Queue Persistence Model

### Decision: Introduce a dedicated `agent_jobs` table backed by a new workflow module

Queue jobs will be stored in a first-class table with lifecycle fields and ownership metadata. The implementation will follow existing MoonMind layering: ORM model + repository + service + router.

### Rationale

- Milestone 1 explicitly requires a dedicated queue table and migration.
- Existing project conventions already separate persistence and orchestration logic.
- Keeping queue logic isolated avoids coupling to Speckit-specific workflow tables.

### Alternatives Considered

- Reuse existing workflow tables: rejected because queue semantics and state transitions are different.
- Store jobs as JSON blobs in an existing table: rejected because transactional claim semantics need explicit indexed columns.

## Claim Concurrency Strategy

### Decision: Use transactional claim with `FOR UPDATE SKIP LOCKED`

Claim logic will:
1. Requeue expired running jobs (or fail when attempt policy is exceeded).
2. Select the next eligible queued job ordered by `priority DESC, created_at ASC`.
3. Lock a single row with `FOR UPDATE SKIP LOCKED`.
4. Transition the selected job to `running` with `claimed_by` and `lease_expires_at`.

### Rationale

- Required by source document and aligns with Postgres queue best practices.
- Prevents duplicate claims under concurrent workers.
- Preserves deterministic scheduling and lease ownership.

### Alternatives Considered

- Optimistic update without row locking: rejected due race risk under concurrent claimers.
- External queue broker for Milestone 1: rejected as out of scope.

## Service Boundary and State Validation

### Decision: Keep transition rules in a service layer over repository methods

Repository methods perform DB operations; service methods enforce transition validity, ownership checks, and common error handling for API responses.

### Rationale

- Matches existing MoonMind architecture guidance in `docs/CodexTaskQueue.md`.
- Keeps router code thin and testable.
- Makes lifecycle rules reusable if MCP tools are added later.

### Alternatives Considered

- Put all transition logic in router handlers: rejected because it duplicates rules and complicates testing.

## API Contract and Authentication

### Decision: Add `/api/queue` REST router protected by standard auth dependency

Milestone 1 endpoints will be exposed under `/api/queue/jobs*` and use the same authentication dependency style as current routers (`get_current_user`).

### Rationale

- Required by source document and consistent with existing API patterns.
- Enables producer and worker clients with stable endpoint shapes.

### Alternatives Considered

- Use `/api/agent-jobs` prefix: valid alternative but deferred to keep docs alignment with examples.

## Test Strategy

### Decision: Add focused unit tests for transition correctness and concurrency behavior

Tests will cover:
- enqueue/claim/heartbeat/complete/fail transitions
- ownership and invalid transition errors
- concurrent claim safety and deterministic ordering behavior

### Rationale

- Milestone 1 requires unit coverage for state transitions and claim concurrency.
- Unit-level coverage gives fast feedback while still verifying core queue correctness.

### Alternatives Considered

- Integration-only coverage: rejected for slower feedback and harder race-condition isolation.
