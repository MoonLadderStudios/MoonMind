# Tasks: Auth-Profile and Rate-Limit Controls (081)

**Input**: Design documents from `/specs/081-auth-profile-controls/`
**Branch**: `081-auth-profile-controls`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel
- **[Story]**: User story (US1, US2, US3)

---

## Phase 1: Setup

**Purpose**: Wire existing infrastructure for Phase 5 additions.

- [ ] T001 Verify `AuthProfileManager` workflow is registered in worker entrypoint at `moonmind/workflows/temporal/worker_entrypoint.py` and `moonmind/workflows/temporal/workers.py` (DOC-REQ-002, DOC-REQ-003)
- [ ] T002 [P] Verify `managed_agent_auth_profiles` DB migration applied: `api_service/migrations/versions/202603140002_managed_agent_auth_profiles.py` (DOC-REQ-001, DOC-REQ-010)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: `auth_profile.list` activity implementation — required by `AuthProfileManager` at startup and blocks all adapter integration.

**⚠️ CRITICAL**: Must complete before Phase 3.

- [ ] T003 Implement `auth_profile.list` activity in `moonmind/workflows/temporal/activities/auth_profile_activity.py` — async activity that queries `managed_agent_auth_profiles` table and returns `{"profiles": [...]}` (DOC-REQ-010)
- [ ] T004 Register `auth_profile.list` activity handler in `moonmind/workflows/temporal/activity_runtime.py` (or appropriate activity registration file) (DOC-REQ-010)
- [ ] T005 Write unit tests for `auth_profile.list` activity in `tests/unit/workflows/temporal/test_auth_profile_activity.py` — mock DB session, verify correct shape returned (DOC-REQ-010)

**Checkpoint**: `auth_profile.list` implemented, tested, and registered.

---

## Phase 3: User Story 1 — Profile-Aware Managed Agent Execution (Priority: P1) 🎯 MVP

**Goal**: `ManagedAgentAdapter` resolves auth profile, shapes environment, and signals `AuthProfileManager` for slot lease/release, enforcing per-profile concurrency.

**Independent Test**: Run `test_managed_agent_adapter.py` with mock `AuthProfileManager` signals; verify env shaping outputs for both auth modes, concurrency guard, and profile_id-only payloads.

### Implementation for User Story 1

- [ ] T006 [US1] Create `moonmind/workflows/adapters/managed_agent_adapter.py` with `ManagedAgentAdapter` class skeleton implementing `AgentAdapter` protocol; `start()` returns a synthetic `AgentRunHandle` (stub — full subprocess launch is deferred to spec 073 Phase 4) (DOC-REQ-002)
- [ ] T007 [US1] Implement `_resolve_profile(execution_profile_ref)` method: fetch profile from registry/DB, fail-fast (non-retryable) if not found or disabled (DOC-REQ-002, FR-011)
- [ ] T008 [US1] Implement env shaping method `_shape_environment(profile: ManagedAgentAuthProfile) -> EnvironmentSpec` for OAuth mode: return `cleared_vars` (API key vars for runtime), `volume_mount_path` from `volume_ref` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009)
- [ ] T009 [US1] Implement env shaping for API-key mode: return key reference (not value) in `env_vars`, no volume mount, empty `cleared_vars` (DOC-REQ-009)
- [ ] T010 [US1] Implement slot lease in `ManagedAgentAdapter.start()`: signal `auth-profile-manager:<runtime_id>` with `request_slot`, wait for `slot_assigned` signal with resolved `profile_id` (DOC-REQ-003, DOC-REQ-004)
- [ ] T011 [US1] Implement `slot_assigned` signal wait loop **inside `ManagedAgentAdapter`**: the adapter signals `request_slot` then waits for `slot_assigned` signal using an asyncio-safe callback or stub mock in tests (do NOT create a separate `agent_run.py` for Phase 5 — full `MoonMind.AgentRun` workflow is out-of-scope until spec 073) (DOC-REQ-003)
- [ ] T012 [US1] Implement slot release: signal `release_slot` to `AuthProfileManager` on adapter completion and on error paths in `ManagedAgentAdapter` (DOC-REQ-003)
- [ ] T013 [P] [US1] Ensure `AgentRunHandle` returned by adapter contains only `profile_id` in metadata, never raw credential values (DOC-REQ-006)
- [ ] T014 [US1] Write unit tests in `tests/unit/workflows/adapters/test_managed_agent_adapter.py`:
  - Test OAuth env shaping: API-key vars cleared, volume_mount_path set (DOC-REQ-007, DOC-REQ-008)
  - Test API-key env shaping: no volume, key reference in env_vars (DOC-REQ-009)
  - Test fail-fast on unknown profile_id (FR-011)
  - Test fail-fast on disabled profile (FR-011)
  - Test slot request/release signal round-trips via mock (DOC-REQ-003)
  - Test `AgentRunHandle` contains no credential-like keys (DOC-REQ-006)

**Checkpoint**: Profile-aware execution with correct env shaping and slot management — independently testable via unit tests.

---

## Phase 4: User Story 2 — 429 Cooldown and Profile Failover (Priority: P1)

**Goal**: Adapter detects 429 response, signals `report_cooldown` to `AuthProfileManager`, queue drains to alt profile or waits.

**Independent Test**: Unit test: simulate 429 response, verify `report_cooldown` signal is sent with correct `profile_id` and `cooldown_seconds`; verify `AuthProfileManager` excludes cooled-down profile from assignment.

### Implementation for User Story 2

- [ ] T015 [US2] Implement 429 detection in `ManagedAgentAdapter`: detect `RESOURCE_EXHAUSTED` / HTTP 429 in result/error handling (DOC-REQ-005)
- [ ] T016 [US2] On 429 detection, signal `report_cooldown` to `AuthProfileManager` with `profile_id` and `cooldown_seconds` from profile's `cooldown_after_429` field (DOC-REQ-005)
- [ ] T017 [US2] On 429 cooldown signal, also signal `release_slot` so slot is freed (DOC-REQ-005, DOC-REQ-003)
- [ ] T018 [P] [US2] Write unit tests for 429 handling in `tests/unit/workflows/adapters/test_managed_agent_adapter.py`:
  - Test: 429 triggers `report_cooldown` signal with correct duration (DOC-REQ-005)
  - Test: `AuthProfileManager` stays at capacity during cooldown (verify existing manager tests or add new ones in `test_auth_profile_manager.py`)
  - Test: cooldown expiry re-enables profile (DOC-REQ-005)

**Checkpoint**: 429 cooldown signaling fully tested; `AuthProfileManager` correctly sidelines profile.

---

## Phase 5: User Story 3 — Credential Isolation and Secret Hygiene (Priority: P1)

**Goal**: Validate across all code paths that no credentials appear in durable state.

**Independent Test**: Inspect `AgentRunHandle`, `AgentExecutionRequest`, and mock Temporal signal payloads for absence of any key/token values.

### Implementation for User Story 3

- [ ] T019 [P] [US3] Add `_validate_no_credentials(env_spec: EnvironmentSpec)` helper to `ManagedAgentAdapter` — assert `env_vars` values contain no raw credential data (DOC-REQ-006)
- [ ] T020 [US3] Write unit test: assert that shaped env for OAuth and API-key modes contains no token/key values — only reference IDs and boolean flags (DOC-REQ-006)
- [ ] T021 [P] [US3] Run full test suite for `agent_runtime_models.py` — confirm `AgentExecutionRequest` validator rejects params with credential-like keys (DOC-REQ-006, existing functionality)

**Checkpoint**: Credential hygiene verified through automated tests.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T022 [P] Update `__all__` in `moonmind/workflows/adapters/__init__.py` to export `ManagedAgentAdapter`
- [ ] T023 Run `./tools/test_unit.sh` — full unit test suite must pass with no regressions
- [ ] T024 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` and confirm diff gate passes
- [ ] T025 [P] Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` Section 11 Phase 5 status to reflect implementation complete (optional doc update)

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: No dependencies — run immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS all story phases
- **Phase 3 (US1)**: Depends on Phase 2 — `auth_profile.list` must be implemented
- **Phase 4 (US2)**: Depends on Phase 3 — needs `ManagedAgentAdapter` base + slot management
- **Phase 5 (US3)**: Can run in parallel with Phase 4 (different files/concerns)
- **Phase 6 (Polish)**: Depends on Phases 3-5

## DOC-REQ Coverage Summary

| DOC-REQ | Implementation Tasks | Validation Tasks |
|---------|---------------------|-----------------|
| DOC-REQ-001 | T001, T002 | T005 (existing model validator tests) |
| DOC-REQ-002 | T006, T007 | T014 |
| DOC-REQ-003 | T010, T011, T012 | T014 |
| DOC-REQ-004 | T010 (keyed by profile_id) | T014 |
| DOC-REQ-005 | T015, T016, T017 | T018 |
| DOC-REQ-006 | T013, T019 | T014, T020, T021 |
| DOC-REQ-007 | T008 | T014 |
| DOC-REQ-008 | T008 | T014 |
| DOC-REQ-009 | T008, T009 | T014 |
| DOC-REQ-010 | T003, T004 | T005 |

## Implementation Strategy

### MVP First (Phase 1–3)
1. Complete Phase 1 (verify infrastructure)
2. Complete Phase 2 (auth_profile.list activity)
3. Complete Phase 3 (ManagedAgentAdapter + env shaping + slot management)
4. **VALIDATE**: run `test_managed_agent_adapter.py`

### Full Delivery
5. Complete Phase 4 (429 cooldown)
6. Complete Phase 5 (credential hygiene tests)
7. Complete Phase 6 (polish, diff validation)
