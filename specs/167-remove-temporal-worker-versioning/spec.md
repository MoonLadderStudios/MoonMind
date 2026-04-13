# Feature Specification: Remove Temporal Worker Deployment Routing

**Feature Branch**: `167-remove-temporal-worker-versioning`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: Operator request to remove the Temporal worker-versioning system after tasks became stuck in `initializing` due to missing current-version routing.

## User Scenarios & Testing

### User Story 1 - Start Workers Without Deployment Routing (Priority: P1)

As a MoonMind operator, I want workers to poll their configured task queues directly so a fresh `docker compose up -d` deployment does not require Temporal Worker Deployment current-version administration.

**Independent Test**: Start the worker runtime in unit tests and assert the Temporal `Worker` is created without `deployment_config`, `build_id`, or worker-versioning flags.

**Acceptance Scenarios**:

1. **Given** the workflow worker starts, **When** the Temporal `Worker` is constructed, **Then** it polls `mm.workflow` directly without deployment routing configuration.
2. **Given** an activity worker starts, **When** the Temporal `Worker` is constructed, **Then** it polls its configured activity queue directly without deployment routing configuration.
3. **Given** an operator uses `.env-template`, **When** they review Temporal settings, **Then** there is no worker-versioning behavior setting to configure.

### User Story 2 - Preserve Replay Safety Gates (Priority: P2)

As a maintainer, I want durable workflow change safety to rely on replay tests, patch gates, and explicit cutover plans rather than worker deployment routing.

**Independent Test**: Run deployment-safety unit tests and assert sensitive workflow changes still require replay evidence and cutover topics.

## Requirements

- **FR-001**: The worker runtime MUST NOT import or construct Temporal Worker Deployment configuration.
- **FR-002**: The worker runtime MUST NOT pass `deployment_config`, `build_id`, or worker-versioning flags to Temporal `Worker`.
- **FR-003**: Runtime settings MUST NOT expose `TEMPORAL_WORKER_VERSIONING_DEFAULT_BEHAVIOR`.
- **FR-004**: Deployment-safety validation MUST NOT require or accept worker-versioning behavior as a gate.
- **FR-005**: Canonical docs and specs MUST describe direct task-queue polling and replay-safe rollout gates.

## Out of Scope

- Removing general build-id metadata used by Mission Control display.
- Removing `workflow.patched(...)` replay gates.
- Changing task queue names or worker fleet topology.
