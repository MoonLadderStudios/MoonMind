# Tasks: Provider Profile Management and Readiness in Settings

**Input**: `specs/271-provider-profile-readiness-settings/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/provider-profile-readiness-api.md`
**Unit test command**: `./tools/test_unit.sh`
**Focused commands**: `pytest tests/unit/api_service/api/routers/test_provider_profiles.py -q`; `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`

## Source Traceability Summary

- `MM-541`: preserved in `spec.md`, this task file, and final verification evidence.
- `DESIGN-REQ-002`: Provider Profiles own launch semantics; Settings remains display/edit surface.
- `DESIGN-REQ-012`: Providers & Secrets exposes readiness and generic settings do not inline provider semantics.
- `DESIGN-REQ-025`: Provider profile editing, role-aware SecretRefs, OAuth metadata, and readiness diagnostics are visible.

## Phase 1: Setup

- [X] T001 Confirm active feature directory and existing artifact state in `specs/271-provider-profile-readiness-settings/spec.md`
- [X] T002 Create MoonSpec plan, research, data model, contract, and quickstart artifacts in `specs/271-provider-profile-readiness-settings/`

## Phase 2: Foundational

- [X] T003 Inspect existing provider profile API, service, UI, and tests in `api_service/api/routers/provider_profiles.py`, `api_service/services/provider_profile_service.py`, `frontend/src/components/settings/ProviderProfilesManager.tsx`, and existing provider profile tests
- [X] T004 Define a response-only readiness shape without new persistent storage in `api_service/api/routers/provider_profiles.py`

## Phase 3: Story - Manage Provider Profiles and Readiness

**Story summary**: Workspace admins manage provider profiles in Settings and see actionable readiness diagnostics while runtime launch semantics remain owned by Provider Profiles and runtime strategies.

**Independent test**: Open Settings -> Providers & Secrets, inspect and edit launch-relevant provider profile metadata, validate readiness diagnostics, and confirm diagnostics do not expose credentials.

### Unit Test Plan

- Backend response shape and readiness synthesis for ready, disabled, missing OAuth metadata, malformed/missing SecretRefs, provider validation state, and redaction.
- Frontend helper/rendering tests for readiness status, metadata display, role-aware SecretRef text, and sanitized failure display.

### Integration Test Plan

- Existing API route tests use ASGI + SQLite and cover the real FastAPI provider profile boundary.
- Existing React tests cover the Mission Control provider profile component boundary.

### Tests First

- [X] T005 [P] Add failing backend API tests for `readiness` response shape and disabled/OAuth/SecretRef blockers in `tests/unit/api_service/api/routers/test_provider_profiles.py` (FR-005, FR-006, FR-009, DESIGN-REQ-025)
- [X] T006 [P] Add failing backend redaction test for provider readiness diagnostics in `tests/unit/api_service/api/routers/test_provider_profiles.py` (FR-009)
- [X] T007 [P] Add failing UI tests for provider metadata and readiness display in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` (FR-002, FR-005, DESIGN-REQ-012)
- [X] T008 [P] Add failing UI test for role-aware SecretRef display without plaintext in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` (FR-004, FR-009, DESIGN-REQ-025)
- [X] T009 Run targeted failing tests with `pytest tests/unit/api_service/api/routers/test_provider_profiles.py -q` and `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`

### Implementation

- [X] T010 Add `ProviderReadinessCheck` and `ProviderProfileReadiness` Pydantic response models in `api_service/api/routers/provider_profiles.py` (FR-005)
- [X] T011 Implement readiness synthesis helper in `api_service/api/routers/provider_profiles.py` using existing profile fields, managed secret metadata, and sanitized provider validation metadata (FR-005, FR-009)
- [X] T012 Include readiness in provider profile list/get/create/update responses in `api_service/api/routers/provider_profiles.py` without adding launch behavior to Settings (FR-001, FR-005, DESIGN-REQ-002)
- [X] T013 Extend `ProviderProfile` and render helpers in `frontend/src/components/settings/ProviderProfilesManager.tsx` for readiness and launch-relevant metadata (FR-002, FR-005)
- [X] T014 Add role-aware SecretRef and OAuth/concurrency/cooldown display in `frontend/src/components/settings/ProviderProfilesManager.tsx` (FR-003, FR-004, DESIGN-REQ-025)
- [X] T015 Keep runtime strategy ownership unchanged by avoiding generic settings launch code and preserving manager payload boundaries in `api_service/services/provider_profile_service.py` (FR-007, FR-008)

### Story Validation

- [X] T016 Run targeted backend and frontend tests and confirm pass: `pytest tests/unit/api_service/api/routers/test_provider_profiles.py -q`; `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`
- [X] T017 Update task checkboxes for completed story tasks in `specs/271-provider-profile-readiness-settings/tasks.md`

## Final Phase: Polish And Verification

- [X] T018 Run `./tools/test_unit.sh` for final unit verification
- [X] T019 Run MoonSpec verification and write `specs/271-provider-profile-readiness-settings/verification.md`
- [X] T020 Preserve `MM-541` in final summary and commit metadata if a commit is created

## Dependencies And Execution Order

1. T003-T004 before tests and implementation.
2. T005-T009 before T010-T015.
3. T010-T012 before backend test pass.
4. T013-T014 before UI test pass.
5. T016-T020 after implementation.

## Parallel Examples

- T005/T006 and T007/T008 can be written in parallel because they touch separate test files.
- T010-T012 and T013-T014 can be implemented in parallel after the test expectations are known.

## Implementation Strategy

Keep the change response-only and diagnostic-only. Do not add persistent storage or move launch semantics into Settings. Readiness can summarize Settings-visible blockers but runtime strategies and ProviderProfileManager remain authoritative for actual launch behavior.
