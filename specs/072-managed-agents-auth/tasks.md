---
description: "Task list for Managed Agents Authentication"
---

# Tasks: Managed Agents Authentication

**Input**: Design documents from `/specs/001-managed-agents-auth/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Create documentation structure `specs/001-managed-agents-auth` [DOC-REQ-012]
- [x] T002 [P] Configure temporal worker settings if needed for singleton

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core DB schema that MUST be completed before story implementations

- [x] T003 Create `managed_agent_auth_profiles` SQLAlchemy model in `api_service/db/models.py` [DOC-REQ-010]
- [x] T004 Generate alembic migration for new auth profiles table in `api_service/migrations/versions/` [DOC-REQ-010]
- [x] T005 Create Pydantic schemas for auth profiles in `api_service/schemas/auth_profiles.py`
- [x] T006 Implement CRUD API router in `api_service/api/routers/auth_profiles.py`

---

## Phase 3: User Story 1 - Multi-Account Authentication Setup (Priority: P1)

**Goal**: DB representation and volume naming for admin auth setup

**Independent Test**: Ensure multiple profiles can be registered in DB and volume naming patterns apply.

### Tests for User Story 1

- [x] T007 [P] [US1] Integration test for CRUD router in `tests/integration/api/routers/test_auth_profiles.py` [DOC-REQ-010]

### Implementation for User Story 1

- [x] T008 [P] [US1] Update `tools/auth-gemini-volume.sh` to support distinct volume suffix provisioning [DOC-REQ-003]
- [x] T009 [P] [US1] Update `tools/auth-claude-volume.sh` to support distinct volume suffix provisioning [DOC-REQ-003]
- [x] T010 [P] [US1] Update `tools/auth-codex-volume.sh` to support distinct volume suffix provisioning [DOC-REQ-003]

---

## Phase 4: User Story 2 - Profile-Aware Execution and Rate Limiting (Priority: P1)

**Goal**: Dynamical Temporal assignment and rate limit cooldown logic

**Independent Test**: Complete pipeline execution with agent runs leasing slots from manager.

### Tests for User Story 2

- [x] T011 [P] [US2] Unit test for temporal singleton execution and queueing in `tests/unit/workflows/temporal/test_auth_profile_manager.py` [DOC-REQ-002, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-011, DOC-REQ-012]
- [x] T012 [P] [US2] Unit test for environment shaping adapter logic in `tests/unit/agents/base/test_adapter.py` [DOC-REQ-001, DOC-REQ-003, DOC-REQ-009]

### Implementation for User Story 2

- [x] T013 [US2] Implement `AuthProfileManager` workflow in `moonmind/workflows/temporal/workflows/auth_profile_manager.py` [DOC-REQ-002, DOC-REQ-004]
- [x] T014 [US2] Implement slot assignment and FIFO queuing logic in `auth_profile_manager.py` [DOC-REQ-005, DOC-REQ-006]
- [x] T015 [US2] Add cooldown 429 signal handler in `auth_profile_manager.py` [DOC-REQ-007, DOC-REQ-011]
- [x] T016 [US2] Add continue-as-new threshold logic in `auth_profile_manager.py` [DOC-REQ-008]
- [x] T017 [US2] Implement Signal contracts in `auth_profile_manager.py`
- [x] T018 [US2] Implement environment shaping (clearing API keys) in `moonmind/agents/base/adapter.py` [DOC-REQ-001]
- [x] T019 [US2] Implement dynamic volume mount path resolution for runtime launcher in `moonmind/agents/base/adapter.py` [DOC-REQ-009]

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T020 Run quickstart.md validating the pipeline works manually.
- [x] T021 Code cleanup and standard code quality formatting.
