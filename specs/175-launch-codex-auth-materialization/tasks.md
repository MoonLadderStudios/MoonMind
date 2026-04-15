# Tasks: Launch Codex Auth Materialization

**Input**: `specs/175-launch-codex-auth-materialization/spec.md`

## Prerequisites

- Unit command: `./tools/test_unit.sh`
- Integration command: `./tools/test_integration.sh` when Docker is available

## Source Traceability

- DESIGN-REQ-005: T001, T004, T007
- DESIGN-REQ-006: T001, T002, T005, T006
- DESIGN-REQ-007: T002, T005, T006
- DESIGN-REQ-015: T001, T004, T006, T007
- DESIGN-REQ-016: T002, T005, T006, T007
- DESIGN-REQ-017: T001, T002, T006, T008

## Phase 1: Setup

- [X] T001 Inspect existing managed Codex adapter, controller, runtime, and tests for profile launch, explicit auth target, workspace volume, and Codex home behavior in `moonmind/workflows/adapters/codex_session_adapter.py`, `moonmind/workflows/temporal/runtime/managed_session_controller.py`, and `moonmind/workflows/temporal/runtime/codex_session_runtime.py`. (FR-001, FR-002, DESIGN-REQ-005, DESIGN-REQ-006)

## Phase 2: Foundational

- [X] T002 Confirm existing runtime seeding coverage for one-way auth copy and excluded entries in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`. (FR-005, DESIGN-REQ-007, DESIGN-REQ-016)

## Phase 3: Managed Codex OAuth Auth Materialization

Story summary: Launch a managed Codex session using a selected OAuth-backed Provider Profile while keeping durable auth storage separate from per-run Codex runtime state.

Independent test: Verify selected profile metadata produces an explicit auth target at launch, runtime rejects an auth target equal to Codex home, eligible auth entries seed one way, and Codex App Server uses the per-run `CODEX_HOME`.

- [X] T003 Add red-first adapter unit regression coverage for OAuth-backed Provider Profile launch metadata in `tests/unit/workflows/adapters/test_codex_session_adapter.py`. (FR-002, FR-003, SC-001)
- [X] T004 Add red-first runtime unit regression coverage rejecting `MANAGED_AUTH_VOLUME_PATH` equal to per-run Codex home in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`. (FR-004, SC-002)
- [X] T005 Confirm red-first failure for runtime equality validation before production change with targeted `./tools/test_unit.sh`. (FR-004, SC-002)
- [X] T006 Implement runtime boundary validation before auth seeding in `moonmind/workflows/temporal/runtime/codex_session_runtime.py`. (FR-004, DESIGN-REQ-006, DESIGN-REQ-015)
- [X] T007 Run targeted adapter and runtime unit tests for OAuth launch metadata, auth seeding, auth path rejection, and app-server `CODEX_HOME`. (FR-001 through FR-007, SC-001 through SC-004)
- [X] T008 Run broader unit test coverage for the three managed-session launch/runtime test files. (FR-001 through FR-007)
- [X] T009 Run integration verification or record exact local blocker. Blocked locally because `/var/run/docker.sock` is unavailable to `./tools/test_integration.sh`. (FR-007)
- [X] T010 Run `/speckit.verify` equivalent and record final verdict: `ADDITIONAL_WORK_NEEDED` only for unavailable Docker-backed integration execution; implementation and unit evidence are complete. (SC-001 through SC-004)
