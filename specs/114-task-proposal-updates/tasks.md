---
description: "Task list for feature implementation"
---

# Tasks: Task Proposal System Plan Phase 1 and Phase 3 Implementation

**Input**: Design documents from `/specs/114-task-proposal-updates/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), contracts/requirements-traceability.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure per implementation plan (Already exists)

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [ ] T002 Update `TaskProposalPolicy` in `moonmind/workflows/tasks/task_contract.py` to add `defaultRuntime` and validation. (Matches DOC-REQ-001)

## Phase 3: User Story 1 - Maintain execution history correctly (Priority: P1)

**Goal**: The system accurately routes global `TaskProposalPolicy` overrides when submitting a run, effectively determining what features to propose without losing runtime target tracking.

### Implementation for User Story 1

- [ ] T003 [P] [US1] Remove `agent_runtime` tool payload serialization inside `moonmind/workflows/tasks/proposals.py` (Matches DOC-REQ-003)
- [ ] T004 [US1] Normalize origin metadata to snake_case `origin.source = "workflow"` in `moonmind/workflows/tasks/proposals.py` and `moonmind/workflows/temporal/workflows/run.py` (Matches DOC-REQ-005)
- [ ] T005 [P] [US1] Modify `moonmind/workflows/temporal/workflows/run.py` to preserve raw `task.proposalPolicy` in `initialParameters` instead of flattened fields. (Matches DOC-REQ-004, DOC-REQ-008, DOC-REQ-009)
- [ ] T006 [P] [US1] Enforce workflow-level global proposal enable switch (`enable_task_proposals`) in `_run_proposals_stage` within `moonmind/workflows/temporal/workflows/run.py` (Matches DOC-REQ-007)
- [ ] T007 [US1] Stamp `defaultRuntime` onto candidate payloads within `proposal.submit` activity in `moonmind/workflows/tasks/proposals.py` (Matches DOC-REQ-010)

## Phase 4: User Story 2 - Complete API representations (Priority: P2)

**Goal**: Operators interacting with the API can see exact lifecycle connections representing whether a task successfully spawned a new `MoonMind.Run` or stalled.

### Implementation for User Story 2

- [ ] T008 [US2] Ensure finish summary data records requested, generated, submitted, and error outcomes consistently in `moonmind/workflows/temporal/workflows/run.py`. (Matches DOC-REQ-011)
- [ ] T009 [US2] Update proposal API schemas in `moonmind/api/models/proposals` and `moonmind/api/routes/proposals.py` to expose promotion linkage `promoted_execution_id` cleanly. Standardize payloads on CanonicalTaskPayload. (Matches DOC-REQ-002, DOC-REQ-006)

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T010 [P] Implement integration and regression tests for proposal lifecycle in `tests/integration/`
- [ ] T011 [P] Implement backward compatibility unit tests for in-flight tasks preserving the legacy `proposalTargets` parsing if present in `tests/unit/workflows/temporal/test_run_artifacts.py`.
- [ ] T012 Run the unit test suite `./tools/test_unit.sh`
- [ ] T013 Run integration tests `docker compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -q --tb=short"`
