# Tasks: Skill Runtime Observability and Verification

**Input**: `specs/209-skill-runtime-observability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/skill-runtime-observability.md`, `quickstart.md`
**Prerequisites**: Specify and plan artifacts complete; no unresolved clarification markers.
**Unit Test Command**: `./tools/test_unit.sh`
**Focused Backend Test Commands**: `python -m pytest tests/unit/api/routers/test_executions.py tests/unit/services/test_skill_materialization.py -q`
**Focused UI Test Command**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
**Integration Test Command**: `./tools/test_integration.sh` if implementation crosses compose-backed or Temporal worker boundaries.

## Source Traceability

- Original request: MM-408 Jira preset brief preserved in `spec.md`.
- Story: Inspect Skill Runtime Evidence.
- Acceptance scenarios: SCN-001 task detail/API evidence, SCN-002 projection collision diagnostics, SCN-003 lifecycle intent, SCN-004 boundary tests.
- In-scope source IDs: DESIGN-REQ-010, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020.
- Requirement statuses from `plan.md`: implemented_unverified (FR-001, FR-006), partial (FR-002, FR-003, FR-004, FR-005, FR-008, FR-010, FR-011, DESIGN-REQ-010, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020), missing (FR-009), implemented_verified (FR-007, FR-012).

## Phase 1: Setup

- [X] T001 Verify active feature artifacts exist in `specs/209-skill-runtime-observability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/skill-runtime-observability.md`, and `quickstart.md`.
- [X] T002 Confirm the focused backend and UI test commands from `specs/209-skill-runtime-observability/quickstart.md` are available in the current environment.

## Phase 2: Foundational

- [X] T003 Inspect current execution detail schema and serializer in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` for existing `resolvedSkillsetRef`, `taskSkills`, and input parameter metadata paths.
- [X] T004 Inspect current task-detail skill UI in `frontend/src/components/skills/SkillProvenanceBadge.tsx` and `frontend/src/entrypoints/task-detail.tsx` to identify the minimum safe payload extension.

## Phase 3: Story - Inspect Skill Runtime Evidence

**Story Summary**: Operators and maintainers can inspect compact skill runtime evidence and lifecycle intent without full skill body leakage.
**Independent Test**: Simulate skill-enabled execution detail payloads, projection failures, and lifecycle metadata, then verify API/UI output and boundary tests.
**Traceability IDs**: FR-001 through FR-012, SCN-001 through SCN-004, SC-001 through SC-005, DESIGN-REQ-010, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020.

### Unit Test Plan

- API serialization tests for `skillRuntime` metadata and no body leakage.
- Existing materialization tests retained for projection diagnostics.
- Lifecycle metadata tests for proposal/schedule/rerun intent where supported by current payload helpers.

### Integration/UI Test Plan

- Task-detail UI test rendering selected versions, provenance, materialization mode, paths, and refs from `skillRuntime`.
- UI test confirming missing metadata remains stable and no full skill body appears.

### Tests First

- [X] T005 [P] Add failing API unit test for compact `skillRuntime` serialization from execution params in `tests/unit/api/routers/test_executions.py` covering FR-002, FR-003, FR-004, FR-006, SCN-001, DESIGN-REQ-010, and DESIGN-REQ-018.
- [X] T006 [P] Add failing API unit test for lifecycle skill intent metadata in `tests/unit/api/routers/test_executions.py` covering FR-008, FR-009, FR-010, SCN-003, and DESIGN-REQ-019.
- [X] T007 [P] Add failing UI test for rich skill provenance rendering in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-002, FR-003, FR-004, SCN-001, and SC-001.
- [X] T008 [P] Add or confirm projection diagnostic no-body regression coverage in `tests/unit/services/test_skill_materialization.py` covering FR-007 and SCN-002.
- [X] T009 [P] Add or confirm boundary verification coverage for exact snapshot replay and repo-skill input without in-place mutation in `tests/unit/services/test_skill_materialization.py` or the closest existing activity-boundary test covering FR-011, SCN-004, and DESIGN-REQ-020.

### Red-First Confirmation

- [X] T010 Run `python -m pytest tests/unit/api/routers/test_executions.py tests/unit/services/test_skill_materialization.py -q` and confirm new backend tests fail for missing `skillRuntime` and lifecycle metadata before production changes.
- [X] T011 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` and confirm the new UI test fails before frontend implementation.

### Implementation

- [X] T012 Add compact skill runtime Pydantic models to `moonmind/schemas/temporal_models.py` covering FR-002, FR-003, FR-004, FR-006, DESIGN-REQ-010, and DESIGN-REQ-018.
- [X] T013 Implement skill runtime metadata extraction in `api_service/api/routers/executions.py` from existing execution params, task payload, and materialization-shaped metadata without dereferencing full skill bodies, covering FR-002, FR-003, FR-004, FR-006, SCN-001, and SC-001.
- [X] T014 Implement lifecycle skill intent extraction in `api_service/api/routers/executions.py` using available selector and `resolvedSkillsetRef` metadata, covering FR-008, FR-009, FR-010, SCN-003, and DESIGN-REQ-019.
- [X] T015 Extend task-detail parsing in `frontend/src/entrypoints/task-detail.tsx` to accept `skillRuntime` while preserving existing `resolvedSkillsetRef` and `taskSkills` behavior, covering FR-002 and FR-003.
- [X] T016 Update `frontend/src/components/skills/SkillProvenanceBadge.tsx` to render compact selected versions, provenance, materialization mode, visible path, backing path, manifest ref, prompt-index ref, lifecycle intent, and diagnostics without full body content, covering FR-002 through FR-005 and SCN-001 through SCN-003.
- [X] T017 Conditional fallback: if T009 exposes missing exact-snapshot or repo-input boundary behavior, update `moonmind/services/skill_materialization.py` or the closest activity-boundary implementation to preserve snapshot reuse evidence and repo input non-mutation, covering FR-011 and DESIGN-REQ-020.

### Story Validation

- [X] T018 Run focused backend tests `python -m pytest tests/unit/api/routers/test_executions.py tests/unit/services/test_skill_materialization.py -q` and record PASS/FAIL evidence for FR-001 through FR-012.
- [X] T019 Run focused UI tests `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` and record PASS/FAIL evidence for SCN-001 through SCN-003.
- [X] T020 Verify `rg -n "MM-408|DESIGN-REQ-010|DESIGN-REQ-018|DESIGN-REQ-019|DESIGN-REQ-020" specs/209-skill-runtime-observability` preserves traceability for FR-012 and SC-005.

## Final Phase: Polish and Verification

- [X] T021 Review changed API/UI payloads for secret-like strings and full skill body leakage in `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py`, `frontend/src/components/skills/SkillProvenanceBadge.tsx`, and `frontend/src/entrypoints/task-detail.tsx`.
- [X] T022 Run final unit verification with `./tools/test_unit.sh`.
- [X] T023 Run `./tools/test_integration.sh` only if implementation changed compose-backed or Temporal worker boundary behavior; otherwise document why it was not required.
- [X] T024 Run `/speckit.verify` via `moonspec-verify` against `specs/209-skill-runtime-observability/spec.md` after implementation and tests pass.

## Dependencies and Execution Order

1. T001-T004 complete setup and context.
2. T005-T009 create tests before production code.
3. T010-T011 confirm red-first behavior.
4. T012-T017 implement only after red-first confirmation.
5. T018-T020 validate the story.
6. T021-T024 complete final verification.

## Parallel Opportunities

- T005, T007, T008, and T009 can be drafted in parallel because they touch different test concerns.
- T012 and T015 can proceed in parallel after red-first confirmation because they touch backend schema and frontend parsing separately, but T016 depends on the final `skillRuntime` shape from T012/T015.
- T020 can run in parallel with focused test validation after implementation is complete.

## Implementation Strategy

Use the existing `resolvedSkillsetRef` and `taskSkills` fields as compatibility anchors while adding a compact `skillRuntime` object for richer evidence. For partial requirements, add tests first, implement the smallest serializer/UI extension, and keep materialization behavior unchanged unless verification exposes a gap. For implemented-verified rows FR-007 and FR-012, preserve existing evidence and cover them in final verification rather than adding unnecessary implementation work.

## Execution Notes

- T010 red-first backend evidence: `python -m pytest tests/unit/api/routers/test_executions.py tests/unit/services/test_skill_materialization.py -q` failed before implementation with missing `taskSkills` selector normalization and missing `skillRuntime`.
- T011 red-first UI evidence: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx` was blocked by npm PATH bin lookup in the managed workspace path containing `:`; direct local Vitest invocation was used for focused UI verification after implementation.
- T017 fallback was not needed; existing materialization tests already cover selected snapshot input, selected-only projection, incompatible source path non-mutation, and no body leakage.
- T018 evidence: `python -m pytest tests/unit/api/routers/test_executions.py tests/unit/services/test_skill_materialization.py -q` passed with 97 tests.
- T019 evidence: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx` passed with 71 tests.
- T022 evidence: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` passed with 3621 Python tests, 1 xpassed, 16 subtests, and 71 targeted UI tests.
- T023 integration decision: not run because implementation stayed in execution-detail serialization, existing materialization unit boundaries, and task-detail rendering; no compose-backed or Temporal worker boundary behavior changed.
