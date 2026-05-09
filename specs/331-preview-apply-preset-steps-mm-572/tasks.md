# Tasks: Preview and Apply Preset Steps

**Input**: Design documents from `/specs/331-preview-apply-preset-steps-mm-572/`  
**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/

**Current Step Boundary**: This task creation step creates handoff artifacts only. Downstream implementation, verification, Jira transitions, pull request creation, and publish work must run only when the existing Jira Orchestrate workflow authorizes those steps.

**Source Traceability**: `MM-572`, `STORY-004`, `manual-mm-569-mm-574`, FR-001..FR-008, SC-001..SC-005, DESIGN-REQ-001..DESIGN-REQ-006.

## Phase 1: Handoff Setup

- [X] T001 Create the `MM-572` MoonSpec feature directory using the next global spec number.
- [X] T002 Preserve target Jira issue `MM-572`, source story `STORY-004`, source Jira issue `manual-mm-569-mm-574`, and the original brief reference in `spec.md`.
- [X] T003 Record that this step does not run implementation inline and must defer implementation to the existing Jira Orchestrate workflow.

## Phase 2: Downstream Planning

- [ ] T004 Confirm whether prior related artifacts for `MM-558`, `MM-565`, or `MM-578` already satisfy the `MM-572` source requirements.
- [ ] T005 If implementation evidence is insufficient, add or update focused Create page tests for Preset selection, preview, apply, failure handling, and unresolved submission blocking.
- [ ] T006 If focused tests fail, update `frontend/src/entrypoints/task-create.tsx` within the existing task-template preview/apply boundaries.

## Phase 3: Downstream Verification

- [ ] T007 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`.
- [ ] T008 Run final MoonSpec verification against `MM-572` and record the verdict in `verification.md`.

## Dependencies & Execution Order

T001-T003 are complete in this task creation step. T004-T008 are intentionally pending for the downstream Jira Orchestrate implementation/verification step.
