# Tasks: Report-Aware Execution Projections

**Input**: Design documents from `specs/248-report-aware-execution-projections/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/execution-report-projection-contract.md`, `quickstart.md`

**Tests**: Unit tests and execution-API contract verification are REQUIRED. For MM-496, add failing coverage before production changes where the execution detail API currently omits the report projection, then implement the bounded projection path and rerun verification.

**Organization**: Tasks are grouped around the single MM-496 story: surface bounded report-aware projection data on execution detail responses using the existing report projection helper while explicitly deferring the dedicated report endpoint.

**Source Traceability**: MM-496; FR-001 through FR-008; acceptance scenarios 1-6; SC-001 through SC-006; DESIGN-REQ-013, DESIGN-REQ-022, DESIGN-REQ-024.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_executions.py`
- Execution API contract verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py`
- Hermetic integration escalation: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the planning artifacts and current implementation gaps for the single MM-496 story.

- [X] T001 Confirm `specs/248-report-aware-execution-projections/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/execution-report-projection-contract.md`, and `quickstart.md`
- [X] T002 Inspect current execution detail schema/materialization in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` before editing (FR-001 through FR-007)
- [X] T003 Inspect existing bounded projection helper coverage in `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, router coverage in `tests/unit/api/routers/test_executions.py`, and execution API contract coverage in `tests/contract/test_temporal_execution_api.py` before adding failures (FR-001 through FR-007)

---

## Phase 2: Foundational

**Purpose**: Confirm MM-496 reuses existing report projection helpers and requires no new storage or endpoint foundation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Confirm no database migration or new persistent storage is required because MM-496 remains a convenience read model over existing artifact links and execution detail materialization (FR-003, DESIGN-REQ-024)
- [X] T005 Confirm no new API route is required because MM-496 explicitly defers the dedicated report endpoint and only extends `/api/executions/{workflowId}` (FR-005, DESIGN-REQ-022)
- [X] T006 Confirm `build_report_projection_summary` in `moonmind/workflows/temporal/report_artifacts.py` remains the canonical projection helper to reuse from the execution detail path (FR-002, FR-007)

**Checkpoint**: Foundation ready - focused verification and bounded execution-detail implementation can now begin.

---

## Phase 3: Story - Expose Report-Aware Execution Detail Fields

**Summary**: As an API consumer, I want execution detail responses to expose canonical report refs and bounded counts so clients can build report-first behavior without artifact-guessing heuristics.

**Independent Test**: Request execution detail for a run with canonical report artifacts and verify the response includes bounded report projection data derived server-side from canonical report semantics, omits fabricated refs when no report exists, and preserves artifact-backed authorization-safe refs only.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-013, DESIGN-REQ-022, DESIGN-REQ-024

**Unit Test Plan**:

- Extend router-level unit coverage for execution detail projection presence, omission, and bounded metadata behavior in `tests/unit/api/routers/test_executions.py`.
- Reuse and tighten helper-level coverage in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` only if execution-detail integration reveals missing projection helper guarantees.

**Integration Test Plan**:

- Extend `tests/contract/test_temporal_execution_api.py` to assert the execution detail route returns the new `reportProjection` contract shape.
- Escalate to `./tools/test_integration.sh` only if implementation changes persistence, artifact-link query behavior, or API serialization beyond the existing contract boundary.

### Unit Verification Tests (write first)

- [X] T007 [P] Add failing router unit coverage in `tests/unit/api/routers/test_executions.py` asserting `/api/executions/{workflowId}` returns `reportProjection.hasReport`, `latestReportRef`, `latestReportSummaryRef`, `reportType`, `reportStatus`, and bounded count maps when canonical report data exists (FR-001, FR-002, SC-001, SC-002)
- [X] T008 [P] Add failing router unit coverage in `tests/unit/api/routers/test_executions.py` asserting no-report executions omit fabricated refs and bounded counts while still degrading safely (FR-006, SC-001)
- [X] T009 [P] Tighten `tests/unit/workflows/temporal/test_report_workflow_rollout.py` only if needed to cover any MM-496-specific helper behavior for bounded `finding_counts` and `severity_counts` metadata (FR-007, SC-003)

### Integration-Style Boundary Tests (write first)

- [X] T010 [P] Add failing contract coverage in `tests/contract/test_temporal_execution_api.py` asserting the execution detail response surfaces the bounded `reportProjection` object and no raw report payloads (FR-001, FR-003, FR-004, SC-004)
- [X] T011 [P] Add contract coverage or traceability notes asserting MM-496 explicitly defers the dedicated `/report` endpoint in favor of execution-detail summary fields (FR-005, SC-006)

### Red-First Confirmation

- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_report_workflow_rollout.py` and confirm the new MM-496 coverage fails before production changes
- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py` and confirm the new MM-496 execution-detail contract coverage fails before production changes

### Implementation Tasks

- [X] T014 Extend `moonmind/schemas/temporal_models.py` with the bounded `reportProjection` response model and camelCase fields required by MM-496 (FR-001, FR-003)
- [X] T015 Update `api_service/api/routers/executions.py` execution-detail materialization to derive the projection server-side from canonical report semantics using `build_report_projection_summary` (FR-001, FR-002, FR-006, FR-007)
- [X] T016 Ensure execution detail surfaces only compact artifact refs and bounded count metadata and does not bypass artifact authorization or preview/default-read behavior (FR-003, FR-004)
- [X] T017 Preserve the explicit dedicated-endpoint deferral decision in feature-local artifacts and avoid adding a new `/report` route in this story (FR-005)

### Story Validation

- [X] T018 Rerun the focused unit command from T012 until MM-496 verification passes
- [X] T019 Rerun the execution API contract command from T013 until MM-496 verification passes
- [X] T020 Run `rg -n "MM-496|DESIGN-REQ-013|DESIGN-REQ-022|DESIGN-REQ-024" specs/248-report-aware-execution-projections docs/tmp/jira-orchestration-inputs/MM-496-moonspec-orchestration-input.md` to verify traceability (FR-008, SC-005)
- [X] T021 Escalate to `./tools/test_integration.sh` only if MM-496 implementation crosses the hermetic integration boundary

**Checkpoint**: MM-496 execution detail projection behavior is implemented and independently testable, while the dedicated `/report` endpoint remains explicitly deferred.

---

## Phase 4: Polish And Verification

**Purpose**: Complete final validation and preserve story-level evidence without adding hidden scope.

- [X] T022 Update `quickstart.md` only if the executed MM-496 verification commands differ from the current plan
- [X] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` unless blocked by environment constraints
- [X] T024 Run `/moonspec-verify` equivalent for `specs/248-report-aware-execution-projections/` and produce the final verification artifact covering MM-496, FR-001 through FR-008, SC-001 through SC-006, and DESIGN-REQ-013, DESIGN-REQ-022, DESIGN-REQ-024

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on focused story verification completing.

### Within The Story

- T007-T011 must complete before red-first confirmation.
- T012-T013 must confirm failure before T014-T017 begin.
- T014-T017 implement the bounded execution-detail slice only.
- T018-T021 validate story completion after implementation.
- T024 depends on validation completion.

### Parallel Opportunities

- T007-T010 can run in parallel because they touch different verification files.
- T014 and T015 can be coordinated closely because the schema and router changes are tightly coupled.
- T020 can run in parallel with final verification preparation after T019 completes.

## Implementation Strategy

1. Confirm the MM-496 planning artifacts and current execution-detail/report-helper state.
2. Add failing router and execution-contract coverage for the missing projection fields.
3. Extend execution detail schemas and materialization using the existing bounded helper.
4. Rerun focused verification and escalate only if the change crosses the hermetic integration boundary.
5. Preserve MM-496 traceability and the explicit dedicated-endpoint deferral through final verification.
