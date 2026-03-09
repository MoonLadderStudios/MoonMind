# Feature Specification: Temporal Migration Task 5.13 (Local Dev Bring-up & E2E Test)

**Feature Branch**: `071-temporal-migration-5-13`  
**Created**: 2026-03-09  
**Status**: Draft  
**Input**: User description: "Implement 5.13 from docs/Temporal/TemporalMigrationPlan.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001**: **Source**: `docs/Temporal/TemporalMigrationPlan.md` Section 5, Task 12 (Mapped from 4.13). **Summary**: Provide documented steps so a developer can run docker compose up and have Temporal and workers auto-start.
- **DOC-REQ-002**: **Source**: `docs/Temporal/TemporalMigrationPlan.md` Section 5, Task 12 (Mapped from 4.13). **Summary**: Write an end-to-end test (script or automated test) that creates a task, waits for worker execution, checks artifacts and UI status.
- **DOC-REQ-003**: **Source**: `docs/Temporal/TemporalMigrationPlan.md` Section 5, Task 12 (Mapped from 4.13). **Summary**: Verify rollback and clean state between runs.

*(Assumption: The prompt requests "5.13", but the plan tables only go up to "5.12". However, text references task "4.13" mapping to "12. Local Dev Bring-up Path & E2E Test". This spec assumes 5.13 was a typo for either 5.12 or 4.13, pointing to the Local Dev Bring-up and E2E testing phase.)*

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Local Development Bring-up (Priority: P1)

Developers need a seamless way to start the local Temporal environment and MoonMind workers so they can test workflow changes without manual setup steps.

**Why this priority**: Essential for team productivity and preventing "it works on my machine" issues.

**Independent Test**: Can be fully tested by running a single docker-compose command and verifying all services (Temporal, Postgres, workers) are healthy.

**Acceptance Scenarios**:

1. **Given** a fresh clone of the repository, **When** the developer runs `docker compose up`, **Then** Temporal Server, database, and all required worker fleets auto-start and begin polling.
2. **Given** a running local environment, **When** the developer stops and restarts the environment, **Then** state is preserved or cleanly reset based on documented flags.

---

### User Story 2 - Automated End-to-End Task Validation (Priority: P1)

The system requires an automated E2E test to prove that tasks are properly orchestrated by Temporal, from creation to final artifact generation, including UI status updates.

**Why this priority**: Necessary to validate the entire Temporal migration across all components (API, Workers, Temporal Server) in a production-like flow.

**Independent Test**: Can be tested by running the E2E test script against a running local environment.

**Acceptance Scenarios**:

1. **Given** a healthy Temporal environment, **When** the E2E test executes, **Then** it successfully submits a task via the API, waits for worker execution, and verifies final artifacts are produced.
2. **Given** a completed test run, **When** checking the task status, **Then** the UI/API reports the correct terminal state and artifact references.

---

### User Story 3 - Environment Rollback and State Cleaning (Priority: P2)

Developers and CI systems need to reset the Temporal state between test runs or roll back changes safely.

**Why this priority**: Ensures tests are repeatable and isolated.

**Independent Test**: Run a test, clean the state, and verify the next test run starts from a blank slate.

**Acceptance Scenarios**:

1. **Given** a populated Temporal environment, **When** the cleanup script/command is run, **Then** all previous workflows and data are cleared, resulting in a pristine state.

### Edge Cases

- What happens when a worker container crashes during the E2E test? (The test should fail gracefully or time out).
- How does the system handle concurrent E2E test executions? (They should use isolated task IDs or namespaces).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The local development environment MUST auto-start Temporal server, database, and worker fleets using `docker compose`. (Satisfies DOC-REQ-001)
- **FR-002**: An automated E2E test MUST be provided that submits a task via the MoonMind API and monitors its progress until completion. (Satisfies DOC-REQ-002)
- **FR-003**: The E2E test MUST verify that artifacts are correctly generated and retrievable after task execution. (Satisfies DOC-REQ-002)
- **FR-004**: The E2E test MUST query the API to verify the UI status aligns with the Temporal workflow state. (Satisfies DOC-REQ-002)
- **FR-005**: Scripts or documentation MUST be provided to easily reset/clean the Temporal environment state between test runs. (Satisfies DOC-REQ-003)
- **FR-006**: The deliverable MUST include production runtime code changes, such as actual test scripts or compose configurations, not just documentation. (Satisfies explicit user constraint)

### Key Entities

- **Docker Compose Configuration**: Defines the services and startup order.
- **E2E Test Script**: Python or shell script implementing the validation logic.
- **Temporal Workflow Execution**: The runtime instance of the task being validated.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Local environment bring-up takes a single command and less than 3 minutes to become fully healthy.
- **SC-002**: The E2E test script executes successfully against the local environment 100% of the time without manual intervention.
- **SC-003**: The clean-state procedure reliably resets the environment in under 1 minute.
- **SC-004**: No manual configuration of Temporal namespaces or worker startup commands is required by the developer.