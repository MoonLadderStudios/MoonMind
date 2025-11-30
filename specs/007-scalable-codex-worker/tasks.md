---
description: "Task list for Scalable Codex Worker feature"
---

# Tasks: Scalable Codex Worker

**Input**: Design documents from `/specs/007-scalable-codex-worker/`
**Prerequisites**: plan.md, spec.md, research.md
**Tests**: Manual verification via `quickstart.md` flows; Integration tests for worker startup.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

 - [x] T001 Verify feature branch `007-scalable-codex-worker` and context

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Define `codex_auth_volume` in `docker-compose.yaml` volumes section
- [x] T003 Remove legacy `celery-codex-0` service from `docker-compose.yaml`
- [x] T004 Remove legacy `codex_auth_0`, `codex_auth_1`, `codex_auth_2` volumes from `docker-compose.yaml`
- [x] T005 [P] Remove legacy script `celery_worker/scripts/codex_login_proxy.py` (if exists)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Deploy Dedicated Codex Worker (Priority: P1) 🎯 MVP

**Goal**: Deploy a dedicated, scalable worker service for Codex tasks.

**Independent Test**: Verify `celery_codex_worker` starts and listens on `codex` queue.

### Implementation for User Story 1

 - [x] T006 [US1] Add `celery_codex_worker` service to `docker-compose.yaml` (use `celery-worker` as template)
 - [x] T007 [US1] Configure `celery_codex_worker` environment in `docker-compose.yaml`: set `SPEC_WORKFLOW_CODEX_QUEUE=codex`
 - [x] T008 [US1] Configure `celery_codex_worker` command in `docker-compose.yaml` to run `celery -A celery_worker.speckit_worker worker ... -Q codex`
 - [x] T009 [US1] Manual Verification: Start service and check logs for queue subscription

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Persistent Authentication (Priority: P1)

**Goal**: Ensure worker can authenticate once and persist credentials across restarts.

**Independent Test**: Authenticate volume, restart worker, verify auth persists.

### Implementation for User Story 2

- [x] T010 [US2] Mount `codex_auth_volume` to `celery_codex_worker` at `/var/lib/codex-auth` in `docker-compose.yaml`
- [x] T011 [US2] Set `CODEX_VOLUME_NAME=codex_auth_volume` environment variable for `celery_codex_worker` in `docker-compose.yaml`
- [x] T012 [US2] Set `CODEX_VOLUME_PATH=/var/lib/codex-auth` environment variable for `celery_codex_worker` in `docker-compose.yaml`
- [ ] T013 [US2] Manual Verification: Perform auth flow in container, restart, and verify persistence

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Non-interactive Execution (Priority: P2)

**Goal**: Prevent worker from hanging on interactive prompts.

**Independent Test**: Verify worker fails fast or auto-approves instead of prompting.

### Implementation for User Story 3

- [x] T014 [US3] Set `CODEX_TEMPLATE_PATH=/app/api_service/config.template.toml` environment variable for `celery_codex_worker` in `docker-compose.yaml`
- [x] T015 [US3] Verify `api_service/config.template.toml` contains `approval_policy = "never"` (should exist)
- [x] T016 [US3] Manual Verification: Trigger a task requiring approval and ensure it proceeds or fails without prompt

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T017 [P] Documentation updates in `docs/CodexCliWorkers.md` reflecting new architecture
- [ ] T018 Clean up any unused environment variables in `docker-compose.yaml`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup
- **User Stories (Phase 3+)**: All depend on Foundational
- **Polish (Final Phase)**: Depends on all user stories

### User Story Dependencies

- **User Story 1 (P1)**: Independent after Foundational
- **User Story 2 (P1)**: Conceptually depends on US1 (service existence) but tasks are grouped logically. Best executed after US1.
- **User Story 3 (P2)**: Independent after Foundational, best executed after US1.

### Parallel Opportunities

- T005 (Script cleanup) can run parallel to Docker Compose edits.
- Documentation (T017) can be done anytime.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 & 2
2. Implement `celery_codex_worker` service (US1)
3. Verify queue connectivity

### Incremental Delivery

1. Add persistent auth volume (US2)
2. Enforce non-interactive policy (US3)
3. Polish and cleanup