# Feature Specification: Provider Profiles Phase 2

**Feature Branch**: `110-provider-profiles-p2`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Implement docs/Tasks/SkillAndPlanContracts.md Phase 2"

## Source Document Requirements

- **DOC-REQ-001**: [Phase 2, Task A] Add `default_model` and `model_overrides` to the Provider Profile table. Ensure JSON/JSONB defaults are safe and indexes cover lookups.
- **DOC-REQ-002**: [Phase 2, Task B] Implement/finish Provider Profile CRUD service behavior. Ensure all operations use `ManagedAgentProviderProfile`. Validate `secret_refs`, `env_template`, `file_templates`, `clear_env_keys`, and `command_behavior`. Reject raw secrets.
- **DOC-REQ-003**: [Phase 2, Task C] Update OAuth registration and sync paths to create/update `ManagedAgentProviderProfile` instead of old models. Remove references to old non-provider profile classes.
- **DOC-REQ-004**: [Phase 2, Task D] Create Alembic migrations for missing Provider Profile columns, enum renames, and workflow type renames. Migrate legacy Auth Profile rows without introducing long-lived compatibility aliases.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Secure Provider Profile Configuration (Priority: P1)

Operators must be able to securely store and configure provider profiles without raw secrets being persisted to the database.

**Why this priority**: Correct data persistence and secret validation are foundational to the security architecture of the system.

**Independent Test**: Can be independently tested by attempting to save a profile with raw credentials (must fail) vs a profile with secure secret references (must succeed).

**Acceptance Scenarios**:

1. **Given** a new provider profile request with raw secrets, **When** the service layer processes it, **Then** validation fails and it is rejected.
2. **Given** a valid provider profile request with secret references and models, **When** the service layer saves it, **Then** the database correctly stores `default_model` and `model_overrides` alongside the secret refs.

---

### User Story 2 - OAuth Profile Registration to New Contract (Priority: P2)

When an OAuth session is finalized, the resulting profile must be directly persisted as a `ManagedAgentProviderProfile` rather than the legacy `AuthProfile`.

**Why this priority**: Registration paths are currently pointing to old data models, causing systemic inconsistency.

**Independent Test**: Can be tested by executing an OAuth registration flow and verifying the created database row.

**Acceptance Scenarios**:

1. **Given** a completed OAuth authentication flow, **When** the registration step executes, **Then** a `ManagedAgentProviderProfile` is correctly created/updated.

---

### User Story 3 - Database Migration (Priority: P3)

The system must seamlessly migrate existing provider profile rows and renamed enums/workflows using declarative Alembic migrations.

**Why this priority**: Required for deployment of these changes to existing environments.

**Independent Test**: Can be tested by running `alembic upgrade head` on a database pre-populated with legacy shapes.

**Acceptance Scenarios**:

1. **Given** an existing database with missing columns and legacy Auth Profile rows, **When** the new migrations run, **Then** columns (`default_model`, `model_overrides`) are added and existing logic is cleanly migrated without leaving dual-compatibility logic.

### Edge Cases

- What happens when an update targets a profile ID that is currently leased or executing?
- How does the system handle schema migration if a legacy Auth Profile row contains fundamentally incompatible data shapes?
- What happens if the `secret_refs` dictionary structure is malformed during a CRUD operation?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST map `DOC-REQ-001` by adding `default_model` and `model_overrides` fields to the underlying database table.
- **FR-002**: System MUST map `DOC-REQ-002` by extending the service tier to read and write only the updated `ManagedAgentProviderProfile` representation.
- **FR-003**: System MUST map `DOC-REQ-002` by enforcing validation checks on `secret_refs`, `env_template`, `file_templates`, `clear_env_keys`, and `command_behavior`.
- **FR-004**: System MUST map `DOC-REQ-002` by rejecting raw secret contents at the service domain boundary.
- **FR-005**: System MUST map `DOC-REQ-003` by ensuring OAuth login/profile binding creates proper `ManagedAgentProviderProfile` models instead of legacy concepts.
- **FR-006**: System MUST map `DOC-REQ-004` by supplying one-way Alembic migrations that finalize the DB schema changes and workflow enums.

### Key Entities

- **Provider Profile Model**: The durable, database representation of a managed runtime environment including its execution shape and secret references.
- **Service Layer**: The Python application logical boundary where domain validation ensures data integrity before database persistence.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of new profile registrations via OAuth map to `ManagedAgentProviderProfile`.
- **SC-002**: 100% of CRUD operations block raw secrets.
- **SC-003**: 0 backward-compatibility aliases or fallback models are retained for persisted records.
