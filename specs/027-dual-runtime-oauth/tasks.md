# Tasks: Dual OAuth Setup for Codex + Claude with Default Task Runtime

**Input**: Design documents from `/specs/027-dual-runtime-oauth/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Validation tasks are mandatory because this feature changes worker startup auth behavior and queue runtime normalization defaults.

**Organization**: Tasks are grouped by user story so each story can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no direct dependency)
- **[Story]**: User story label (`US1`, `US2`, `US3`)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish spec artifacts and baseline checks before runtime edits.

- [X] T001 Confirm quickstart validation flow in `specs/027-dual-runtime-oauth/quickstart.md` with two-command OAuth setup and unit-test verification.
- [X] T002 [P] Add feature contract details in `specs/027-dual-runtime-oauth/contracts/worker-runtime-auth-contract.md` for runtime preflight matrix and default runtime fallback.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared configuration surfaces required by all stories.

**⚠️ CRITICAL**: No user story implementation should begin until this phase is complete.

- [X] T003 Add Claude auth volume/default runtime env defaults to `.env-template` (`FR-001`, `FR-005`, `FR-008`, `FR-009`).
- [X] T004 [P] Wire Claude auth volume paths into `docker-compose.yaml` worker services (`FR-001`, `FR-008`, `FR-009`).
- [X] T005 Implement settings fields/validation for default task runtime and Claude auth paths in `moonmind/config/settings.py` plus unit coverage in `tests/unit/config/test_settings.py` (`FR-001`, `FR-005`, `FR-009`).

**Checkpoint**: Shared config is in place for runtime auth and default-runtime behavior.

---

## Phase 3: User Story 1 - Operators authenticate both CLIs in one environment (Priority: P1) 🎯 MVP

**Goal**: Operators can authenticate Codex and Claude independently with two scripts and persistent volumes.

**Independent Test**: Run both auth scripts, restart worker containers, and confirm persisted auth states remain usable.

### Implementation for User Story 1

- [X] T006 [US1] Add `tools/auth-claude-volume.sh` with login + status verification against worker container context (`FR-002`, `FR-008`).
- [X] T007 [US1] Keep `tools/auth-codex-volume.sh` behavior stable while documenting parallel Claude workflow in `README.md` (`FR-003`, `FR-008`).

### Validation for User Story 1

- [X] T008 [US1] Validate script behavior paths in docs/examples and ensure no regressions in existing Codex auth instructions (`FR-003`, `FR-008`, `FR-009`).

**Checkpoint**: Dual OAuth setup commands are documented and available.

---

## Phase 4: User Story 2 - Runtime preflight enforces required auth by worker mode (Priority: P1)

**Goal**: Worker startup checks enforce auth requirements by runtime mode (`codex`, `claude`, `universal`) with clear failure behavior.

**Independent Test**: Unit tests confirm expected preflight command sequence per runtime mode.

### Implementation for User Story 2

- [X] T009 [US2] Extend worker preflight in `moonmind/agents/codex_worker/cli.py` to perform Claude auth status checks for `claude` and `universal` modes while preserving Codex behavior (`FR-004`, `FR-009`).
- [X] T010 [US2] Update runtime/bootstrap env handling in `tools/start-codex-worker.sh` so default capability policy remains coherent when using multi-runtime worker modes (`FR-004`, `FR-008`, `FR-009`).

### Validation for User Story 2

- [X] T011 [P] [US2] Add/adjust preflight unit tests in `tests/unit/agents/codex_worker/test_cli.py` for codex-only, claude-only, and universal auth check sequencing and failure handling (`FR-004`, `FR-009`).

**Checkpoint**: Runtime-specific preflight auth behavior is fully tested.

---

## Phase 5: User Story 3 - Tasks without runtime use a configurable default (Priority: P2)

**Goal**: Queue task normalization uses configured default runtime only when runtime is omitted, preserving explicit runtime overrides.

**Independent Test**: Unit tests verify fallback to configured default and precedence of explicit payload runtime.

### Implementation for User Story 3

- [X] T012 [US3] Apply settings-driven default runtime in queue normalization logic in `moonmind/workflows/agent_queue/service.py` (`FR-005`, `FR-006`, `FR-007`).

### Validation for User Story 3

- [X] T013 [P] [US3] Add coverage in `tests/unit/workflows/agent_queue/test_service_hardening.py` for default runtime fallback and explicit override precedence (`FR-005`, `FR-006`, `FR-007`, `FR-009`).

**Checkpoint**: Default runtime fallback is deterministic and backward compatible.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and scope checks.

- [X] T014 Run `./tools/test_unit.sh` and capture pass/fail notes in `specs/027-dual-runtime-oauth/quickstart.md` (`FR-004`, `FR-005`, `FR-009`).
- [X] T015 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and resolve any blockers.
- [X] T016 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` before final delivery.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: starts immediately.
- **Phase 2 (Foundational)**: depends on Phase 1 and blocks user stories.
- **Phase 3 (US1)** and **Phase 4 (US2)**: depend on Phase 2.
- **Phase 5 (US3)**: depends on Phase 2 and can run after or alongside US2.
- **Phase 6 (Polish)**: depends on all implementation phases.

### Parallel Opportunities

- `T004` and `T005` can run in parallel after `T003`.
- `T011` can be developed in parallel while `T009`/`T010` are in progress.
- `T013` can run in parallel with late-stage docs updates once `T012` is complete.
