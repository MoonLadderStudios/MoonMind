# Tasks: Executions List and Facet API Support for Column Filters

**Input**: Design documents from `/specs/303-executions-list-facets/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/executions-list-facets.md

**Tests**: Unit tests and integration/contract tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around one story: server-authoritative task-list and facet API data for column filters.

**Source Traceability**: MM-590, DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-025.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/test_executions_temporal.py`
- Frontend unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`
- Contract tests: `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify the existing web-service/frontend test surfaces and feature artifacts.

- [X] T001 Verify active MoonSpec artifacts for MM-590 exist in specs/303-executions-list-facets/spec.md, specs/303-executions-list-facets/plan.md, specs/303-executions-list-facets/research.md, specs/303-executions-list-facets/data-model.md, specs/303-executions-list-facets/contracts/executions-list-facets.md, and specs/303-executions-list-facets/quickstart.md (FR-008, SC-007)
- [X] T002 Verify existing ignore/test setup covers Python, frontend, and generated artifacts in .gitignore, package-lock.json, pytest.ini, and frontend/vitest config before story edits

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared parsing and response contracts before route and UI behavior.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Add failing unit coverage for canonical list/facet query parsing, bounds, contradictory filters, blank modes, date ranges, and sort/facet validation in tests/unit/api/test_executions_temporal.py (FR-002, FR-007, DESIGN-REQ-020, DESIGN-REQ-025)
- [X] T004 Add failing schema coverage for execution facet response shape in tests/unit/api/test_executions_temporal.py (FR-004, DESIGN-REQ-019)
- [X] T005 Implement shared bounded filter parsing helpers and facet response models in api_service/api/routers/executions.py and moonmind/schemas/temporal_models.py (FR-002, FR-004, FR-007)
- [X] T006 Run `./tools/test_unit.sh tests/unit/api/test_executions_temporal.py` and confirm foundational tests pass after T005

**Checkpoint**: Query parsing and schema foundations are ready for list, facet, and UI work.

---

## Phase 3: Story - Server-Authoritative Column Filter Data

**Summary**: As the Tasks List UI, I want server-authoritative list and facet data for task column filters so operators see accurate results, counts, pagination, and popover values across the authorized task universe.

**Independent Test**: Call list and facet request surfaces with supported filters, sort, pagination, invalid combinations, and authorization-sensitive values; render the Tasks List with facet failure and confirm usable fallback behavior.

**Traceability**: FR-001 through FR-008, SC-001 through SC-007, DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-025

**Test Plan**:

- Unit: query parsing, structured validation, task-scope query construction, facet filter exclusion, response schema.
- Integration/Contract: `/api/executions` and `/api/executions/facets` request/response behavior at FastAPI boundary.
- Frontend: facet failure fallback notice and table usability.

### Unit Tests (write first)

- [X] T007 [P] Add failing unit test for list requests combining multiple filters and supported sort query construction in tests/unit/api/test_executions_temporal.py (FR-001, FR-003, SC-001, DESIGN-REQ-006)
- [X] T008 [P] Add failing unit test for facet query excluding the requested facet filter while retaining other active filters and owner/task scope in tests/unit/api/test_executions_temporal.py (FR-004, FR-006, SC-004, DESIGN-REQ-019, DESIGN-REQ-025)
- [X] T009 [P] Add failing unit test for system/all workflow values not appearing through facet task-scope queries in tests/unit/api/test_executions_temporal.py (FR-006, SC-006, DESIGN-REQ-025)
- [X] T010 Run `./tools/test_unit.sh tests/unit/api/test_executions_temporal.py` to confirm T007-T009 fail for the expected missing implementation

### Integration / Contract Tests (write first)

- [X] T011 [P] Add failing contract test for filtered/sorted paginated list response count and token behavior in tests/contract/test_temporal_execution_api.py (FR-001, FR-003, SC-001, SC-002, DESIGN-REQ-006)
- [X] T012 [P] Add failing contract test for `/api/executions/facets` dynamic values, counts, blank counts, and filter-exclusion behavior in tests/contract/test_temporal_execution_api.py (FR-004, SC-003, SC-004, DESIGN-REQ-019)
- [X] T013 [P] Add failing frontend test for facet fetch failure showing a current-page-values fallback notice while table rows remain usable in frontend/src/entrypoints/tasks-list.test.tsx (FR-005, DESIGN-REQ-019)
- [X] T014 Run `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` to confirm T011-T013 fail for the expected missing implementation

### Implementation

- [X] T015 Implement list route support for bounded `sort`, `sortDir`, `taskId`, `taskIdContains`, `titleContains`, `repoContains`, validated value-list bounds, blank modes, and date ranges in api_service/api/routers/executions.py (FR-001, FR-002, FR-003, FR-007, DESIGN-REQ-006, DESIGN-REQ-020)
- [X] T016 Implement `GET /api/executions/facets` in api_service/api/routers/executions.py using the same task-scope, owner-scope, active-filter parsing, count metadata, and structured validation as the list route (FR-004, FR-006, FR-007, DESIGN-REQ-019, DESIGN-REQ-025)
- [X] T017 Implement execution facet Pydantic response models in moonmind/schemas/temporal_models.py and wire them into the router response model (FR-004, DESIGN-REQ-019)
- [X] T018 Implement Tasks List facet fetch/fallback state and visible current-page-values notice in frontend/src/entrypoints/tasks-list.tsx (FR-005, DESIGN-REQ-019)
- [X] T019 Run `./tools/test_unit.sh tests/unit/api/test_executions_temporal.py`, `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py`, and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`; fix failures until the story passes end-to-end (FR-001-FR-007, SC-001-SC-006)

**Checkpoint**: The story is functional, covered by backend unit, API contract, and frontend unit tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T020 Review validation messages and UI copy for secret-safe, user-facing wording in api_service/api/routers/executions.py and frontend/src/entrypoints/tasks-list.tsx (FR-007, DESIGN-REQ-025)
- [X] T021 Update specs/303-executions-list-facets/tasks.md task statuses and preserve MM-590 traceability in final verification notes (FR-008, SC-007)
- [X] T022 Run `./tools/test_unit.sh` for full unit-suite verification, or record the exact blocker if the full suite cannot run in this managed environment
- [X] T023 Run `/speckit.verify` equivalent read-only verification against specs/303-executions-list-facets/spec.md, plan.md, tasks.md, source mappings, and test evidence (FR-008, SC-007)

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion and blocks story work.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on story tests passing.

### Within The Story

- T007-T009 unit tests and T011-T013 contract/frontend tests must be written before T015-T018 implementation.
- T010 and T014 confirm red-first failures before production code tasks.
- T015 and T016 share router code and must be sequenced carefully with T017 schema wiring.
- T018 frontend work can begin after the facet response contract from T017 is known.
- T019 validates the complete story before polish.

### Parallel Opportunities

- T007, T008, and T009 can be authored in parallel within the same test file only if coordinated to avoid overlapping edits.
- T011, T012, and T013 touch different files and can be authored in parallel.
- Backend schema work T017 can be prepared alongside router implementation T015/T016 if imports and response names are coordinated.

---

## Implementation Strategy

1. Preserve the MM-590 source brief and confirm feature artifacts.
2. Add failing backend validation/query tests and schema tests.
3. Implement shared parser/model foundations.
4. Add failing list, facet, and frontend fallback tests.
5. Implement list filters/sort, facet route, response schema, and Tasks List fallback notice.
6. Run targeted backend and frontend suites until green.
7. Run full unit verification where feasible.
8. Run final MoonSpec verification and record any remaining evidence gaps.

---

## Notes

- This task list covers exactly one story.
- Do not implement the complete future Google Sheets-like popover interaction beyond the visible fallback/error behavior required by MM-590.
- Do not expose system workflow browsing through `/tasks/list` or `/api/executions/facets`.
- Do not send display labels as canonical filter values.
- Preserve `MM-590` in verification, commit, and PR metadata.
