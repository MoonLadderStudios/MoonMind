# Feature Specification: DooD Phase 7 Bounded Helper Containers

**Feature Branch**: `161-dood-bounded-helper-containers`
**Created**: 2026-04-12
**Status**: Draft
**Input**: Implement Phase 7 of the Docker-out-of-Docker strategy: optional bounded helper containers.

## User Scenarios & Testing

### User Story 1 - Launch a Bounded Helper (Priority: P1)

An executable tool step can start a non-agent helper container that stays alive for a bounded TTL and is tied to one owner step.

**Independent Test**: Validate a `bounded_service` profile and request, launch it detached, and verify deterministic helper identity, ownership labels, TTL labels, and no session identity substitution.

### User Story 2 - Wait for Readiness (Priority: P1)

MoonMind can prove the helper is usable before later sub-steps rely on it.

**Independent Test**: A helper profile with a readiness command performs bounded `docker exec` probes until success or reports unhealthy after retries.

### User Story 3 - Tear Down Explicitly (Priority: P1)

MoonMind can stop, kill, remove, and publish diagnostics for the helper at the end of the bounded execution window.

**Independent Test**: A helper is launched, used across multiple simulated sub-steps, then torn down; janitor cleanup also removes expired helpers by labels.

## Requirements

- **FR-001**: Runner profiles MAY declare `kind: bounded_service` for helper containers, separate from one-shot workload profiles.
- **FR-002**: Bounded helper profiles MUST define a TTL limit and a readiness contract.
- **FR-003**: Bounded helper requests MUST provide `ttlSeconds`; TTL MUST be positive and no greater than the profile maximum.
- **FR-004**: Helper containers MUST carry ownership labels, `moonmind.kind=bounded_service`, and `moonmind.expires_at`.
- **FR-005**: Helper containers MUST be launched detached and must not be treated as `MoonMind.AgentRun` or managed-session identity.
- **FR-006**: Helper readiness checks MUST be bounded by probe timeout, interval, and retry settings.
- **FR-007**: Helper teardown MUST stop, kill, and remove the container according to the profile cleanup policy.
- **FR-008**: Expired-helper sweeping MUST remove expired helpers while preserving fresh helpers and unrelated containers.
- **FR-009**: Diagnostics MUST include helper profile, image, readiness, TTL, ownership, and teardown metadata without environment values.

## Success Criteria

- **SC-001**: Invalid helper profiles or helper requests missing TTL/readiness are rejected before Docker launch.
- **SC-002**: A launched helper can survive across multiple simulated sub-step observations and is removed by explicit teardown.
- **SC-003**: Readiness success and readiness failure paths are covered by unit tests.
- **SC-004**: Expired helper janitor behavior is covered by unit tests.
