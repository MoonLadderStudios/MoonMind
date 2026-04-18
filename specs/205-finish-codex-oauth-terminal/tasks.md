# Tasks: Finish Codex OAuth Terminal Flow

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/oauth-terminal-settings-flow.md](./contracts/oauth-terminal-settings-flow.md)
**Prerequisites**: Spec and plan gates passed for one runtime story preserving MM-402.
**Unit Test Command**: `./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py tests/unit/api_service/api/routers/test_oauth_sessions.py --ui-args frontend/src/components/settings/ProviderProfilesManager.test.tsx`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available.

## Source Traceability Summary

MM-402 is the canonical source request. Missing/partial rows: FR-001, FR-002, FR-004, FR-005, FR-008, FR-009, FR-014. Implemented-unverified rows: FR-003, FR-006, FR-007, FR-010, FR-011, FR-013. Implemented-verified rows: FR-012, FR-015.

## Phase 1: Setup

- [X] T001 Create MoonSpec feature artifacts under `specs/205-finish-codex-oauth-terminal/` preserving MM-402.
- [X] T002 Create plan, research, data model, contract, and quickstart artifacts in `specs/205-finish-codex-oauth-terminal/`.

## Phase 2: Foundational

- [X] T003 [P] Add failing provider registry unit expectations for Codex device-code bootstrap and interactive transport in `tests/unit/auth/test_oauth_provider_registry.py`. (FR-004, FR-009, DESIGN-REQ-004)
- [X] T004 [P] Add failing Codex verifier unit coverage for malformed auth material with sanitized output in `tests/unit/auth/test_volume_verifiers.py`. (FR-005, FR-013, DESIGN-REQ-006)
- [X] T005 [P] Add failing API/session coverage proving created Codex OAuth sessions start with `moonmind_pty_ws` in `tests/unit/api_service/api/routers/test_oauth_sessions.py`. (FR-002, FR-009)

## Phase 3: Story - Codex OAuth Terminal Enrollment

**Summary**: Settings starts and manages an end-to-end Codex OAuth terminal enrollment session.
**Independent Test**: Start a Codex OAuth profile Auth flow from Settings, observe terminal-session state, finalize verified auth material, and confirm Provider Profile update plus cleanup behavior.
**Traceability IDs**: FR-001 through FR-015, DESIGN-REQ-001 through DESIGN-REQ-008, SC-001 through SC-007.

### Unit Test Plan

Add targeted Vitest and pytest coverage before implementation for Settings Auth action/state, provider registry, verifier strength, and OAuth session transport.

### Integration Test Plan

Use existing OAuth terminal route/workflow tests for boundary coverage and run `./tools/test_integration.sh` when Docker is available. If Docker is unavailable, record the exact blocker during final verification.

- [X] T006 [P] Add failing Settings UI test for Auth action visibility and OAuth session creation in `frontend/src/components/settings/ProviderProfilesManager.test.tsx`. (FR-001, FR-002, SC-001)
- [X] T007 [P] Add failing Settings UI test for active status, cancel, retry, finalize, and Provider Profile query invalidation in `frontend/src/components/settings/ProviderProfilesManager.test.tsx`. (FR-006, FR-008, FR-011, FR-014)
- [X] T008 Run focused frontend and Python tests to confirm the new tests fail before implementation. (TDD red-first)
- [X] T009 Update Codex OAuth provider registry in `moonmind/workflows/temporal/runtime/providers/registry.py` to use `session_transport="moonmind_pty_ws"` and `bootstrap_command=["codex", "login", "--device-auth"]`. (FR-004, FR-009)
- [X] T010 Update auth runner/session test expectations for the new Codex bootstrap command in `tests/unit/auth/test_oauth_session_activities.py` and `tests/unit/services/temporal/runtime/test_terminal_bridge.py`. (FR-003, FR-004, SC-002)
- [X] T011 Update OAuth session creation in `api_service/api/routers/oauth_sessions.py` and/or `api_service/services/oauth_session_service.py` so Codex interactive sessions persist and send `moonmind_pty_ws` to the workflow. (FR-002, FR-009)
- [X] T012 Strengthen Codex verification in `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py` to validate safe usable-auth structure beyond file presence without returning secrets. (FR-005, FR-013, SC-003)
- [X] T013 Implement Settings OAuth session state, Auth/Cancel/Retry/Finalize actions, terminal launch handoff, status polling, and cache invalidation in `frontend/src/components/settings/ProviderProfilesManager.tsx`. (FR-001, FR-002, FR-006, FR-008, FR-011, FR-014, SC-004)
- [X] T014 Run focused tests and fix implementation until they pass. (SC-001 through SC-005)

## Final Phase: Polish And Verification

- [X] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification. (SC-001 through SC-007)
- [X] T016 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker blocker. (FR-014, SC-006)
- [X] T017 Run `/moonspec-verify` equivalent by auditing spec, tasks, code, and test evidence; write `specs/205-finish-codex-oauth-terminal/verification.md`. (FR-015, SC-007)

## Dependencies And Execution Order

T003-T007 are red-first tests and can be authored in parallel by file. T008 must run before implementation. T009-T013 implement the behavior after red-first confirmation. T014 validates focused tests. T015-T017 are final gates.

## Parallel Examples

- T003, T004, T005, T006, and T007 touch different test files and can be prepared independently.
- T009 and T012 touch different runtime modules, but T010 depends on T009 expectations.

## Implementation Strategy

Start with tests for missing and partial requirements, confirm red, then implement the smallest scoped changes in provider registry, OAuth session transport, verifier, and Settings UI. Do not add generic terminal access or new storage. Preserve MM-402 in verification output.
