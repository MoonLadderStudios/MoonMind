# Implementation Tasks: Provider Profiles Phase 2

**Branch**: `110-provider-profiles-p2`
**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)

## Phase 1: Setup

- [x] T001 Initialize the Alembic migration file `moonmind/alembic/versions/[hash]_provider_profiles_phase2.py` mapping to DOC-REQ-004

## Phase 2: Foundational 

- [x] T002 Update `moonmind/models/provider_profiles.py` to add `default_model` and `model_overrides` mapping to DOC-REQ-001
- [x] T003 Update `moonmind/schemas/provider_profiles.py` to include new fields and validation logic blocking plaintext secrets mapping to DOC-REQ-002

## Phase 3: User Story 1 - Secure Provider Profile Configuration (P1)

Goal: Ensure `ManagedAgentProviderProfile` cannot persist raw secrets.

- [x] T004 [US1] Update `moonmind/services/provider_profile_service.py` to enforce schema boundaries and write new column values mapping to DOC-REQ-002
- [x] T005 [P] [US1] Write unit tests in `tests/integration/test_provider_profiles.py` ensuring exceptions are raised when raw secrets are passed mapping to DOC-REQ-002

## Phase 4: User Story 2 - OAuth Profile Registration to New Contract (P2)

Goal: Change OAuth finalization logic to write `ManagedAgentProviderProfile` instead of legacy structures.

- [x] T006 [US2] Update `moonmind/services/oauth_session_service.py` to route finalized authentications into the `ProviderProfileService` mapping to DOC-REQ-003
- [x] T007 [P] [US2] Delete legacy creation usages from `moonmind/services/auth_profile_service.py` mapping to DOC-REQ-003
- [x] T008 [P] [US2] Write integration tests in `tests/integration/test_oauth_session.py` validating correct model generation mapping to DOC-REQ-003

## Phase 5: User Story 3 - Database Migration (P3)

Goal: Deploy schema updates seamlessly.

- [x] T009 [US3] Finalize `moonmind/alembic/versions/[hash]_provider_profiles_phase2.py` with column schemas and upgrade logic for existing legacy Auth Profiles mapping to DOC-REQ-004
- [x] T010 [P] [US3] Write tests confirming alembic upgrade functionality mapping to DOC-REQ-004

## Independent Testing Validation

- [x] T011 Execute `./tools/test_unit.sh` mapping to DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004
