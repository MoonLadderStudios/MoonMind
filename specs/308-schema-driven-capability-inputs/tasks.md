# Tasks: Schema-Driven Capability Inputs

**Input**: Design documents from `/specs/308-schema-driven-capability-inputs/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/capability-input-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped around one user story: `Render Capability Inputs From Schema`.

**Source Traceability**: Covers FR-001 through FR-013, SCN-001 through SCN-006, SC-001 through SC-007, and in-scope DESIGN-REQ-001 through DESIGN-REQ-008 from `spec.md`. DESIGN-REQ-009 is preserved as out-of-scope context for later or existing specs.

**Requirement Status Summary**:

- Missing: FR-004, FR-006, FR-007, FR-010, FR-013, SCN-003, SCN-006, SC-002, SC-003, SC-006, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-008
- Partial: FR-001, FR-002, FR-003, FR-005, FR-008, FR-009, FR-012, SCN-001, SCN-002, SCN-004, SCN-005, SC-001, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006
- Implemented unverified: FR-011, SC-007, DESIGN-REQ-007
- Implemented verified: none

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/api/test_task_step_templates_service.py tests/unit/integrations/test_jira_tool_service.py tests/unit/mcp/test_jira_tool_registry.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Full required unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration suite if backend API/seed behavior changes require compose-backed proof: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when the task touches different files and has no dependency on incomplete tasks.
- Every task includes exact file paths and requirement, scenario, success, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Prepare focused test fixtures and preserve the current planning baseline before story work.

- [ ] T001 Confirm the active feature directory and planning baseline in specs/308-schema-driven-capability-inputs/plan.md before modifying code for FR-001 through FR-013
- [ ] T002 [P] Add reusable backend test fixture builders for schema-backed capability contracts in tests/unit/api/test_task_step_templates_service.py covering FR-001, FR-002, DESIGN-REQ-001
- [ ] T003 [P] Add reusable Create-page test fixtures for capability input schemas, UI schema widget hints, and safe Jira issue values in frontend/src/entrypoints/task-create.test.tsx covering FR-003, FR-006, FR-008

---

## Phase 2: Foundational

**Purpose**: Add the shared contract scaffolding that blocks red-first tests and implementation.

**CRITICAL**: No production implementation work begins until red-first tests in Phase 3 have been written and confirmed failing.

- [ ] T004 [P] Add backend schema/default redaction fixture data in tests/unit/api/test_task_step_templates_service.py covering FR-011, FR-012, SC-007, DESIGN-REQ-007
- [ ] T005 [P] Add trusted Jira validation/enrichment fixture data in tests/unit/integrations/test_jira_tool_service.py covering FR-009, FR-012, DESIGN-REQ-004, DESIGN-REQ-005
- [ ] T006 [P] Add frontend helper assertions for generated field paths, preserved draft values, and field-addressable errors in frontend/src/entrypoints/task-create.test.tsx covering FR-005, SCN-004, DESIGN-REQ-006

---

## Phase 3: Story - Render Capability Inputs From Schema

**Summary**: As a Create-page task author, I want presets and skills to render their required inputs from capability schemas so that I can configure Jira-backed and other schema-declared capabilities without one-off Create-page forms.

**Independent Test**: Register or seed a capability with input schema and UI schema, select it on the Create page, verify generated fields and metadata-driven `jira.issue-picker`, and confirm valid/invalid inputs are preserved and validated consistently.

**Traceability**: FR-001 through FR-013; SCN-001 through SCN-006; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-008

**Unit Test Plan**:

- Backend contract normalization, schema/default validation, field-addressable error shape, safe Jira issue value validation, and secret-default guardrails.
- Frontend schema renderer unit behavior for supported schema types, widget registry lookup, unsupported-widget handling, draft preservation, and generated field paths.

**Integration Test Plan**:

- Create-page preset and skill selection flows using schema/UI metadata.
- Jira issue picker selection by metadata, manual key preservation on lookup outage, and dependent action blocking on missing required values.
- Template catalog route/service output for normalized capability contracts.

### Unit Tests (write first)

- [ ] T007 [P] Add failing backend unit tests for normalized preset and skill `inputSchema`, `uiSchema`, and `defaults` serialization in tests/unit/api/test_task_step_templates_service.py covering FR-001, FR-002, DESIGN-REQ-001
- [ ] T008 [P] Add failing backend unit tests for schema-backed required fields, nested field paths, and field-addressable validation errors in tests/unit/api/test_task_step_templates_service.py covering FR-005, SCN-004, DESIGN-REQ-006
- [ ] T009 [P] Add failing backend unit tests for secret-like schema defaults and safe Jira issue values in tests/unit/api/test_task_step_templates_service.py covering FR-011, FR-012, SC-007, DESIGN-REQ-007
- [ ] T010 [P] Add failing Jira validation/enrichment boundary tests for safe issue key values and optional enrichment in tests/unit/integrations/test_jira_tool_service.py covering FR-009, FR-012, DESIGN-REQ-004, DESIGN-REQ-005
- [ ] T011 [P] Add failing MCP registry regression for schema/default secret safety in tests/unit/mcp/test_jira_tool_registry.py covering FR-011, DESIGN-REQ-007
- [ ] T012 [P] Add failing frontend unit tests for JSON Schema field rendering of strings, booleans, numbers, enums, arrays, objects, oneOf/anyOf, formats, descriptions, and defaults in frontend/src/entrypoints/task-create.test.tsx covering FR-003, FR-004, DESIGN-REQ-002
- [ ] T013 [P] Add failing frontend unit tests for allowlisted widget registry lookup, `uiSchema` widget hints, `x-moonmind-widget`, and unsupported-widget errors in frontend/src/entrypoints/task-create.test.tsx covering FR-006, FR-007, SCN-003, DESIGN-REQ-003
- [ ] T014 [P] Add failing frontend unit tests for reusable `jira.issue-picker` manual entry, outage preservation, safe value shape, and field-path errors in frontend/src/entrypoints/task-create.test.tsx covering FR-008, FR-012, SCN-005, SC-005, DESIGN-REQ-004, DESIGN-REQ-005

### Integration Tests (write first)

- [ ] T015 [P] Add failing template catalog service integration-style tests for capability detail responses with `inputSchema`, `uiSchema`, and `defaults` in tests/unit/api/test_task_step_templates_service.py covering FR-001, FR-002, SC-001, DESIGN-REQ-001
- [ ] T016 [P] Add failing startup seed test proving a seeded Jira Orchestrate-style preset can expose schema-backed Jira issue input metadata in tests/integration/test_startup_task_template_seeding.py covering FR-001, FR-007, SC-003, DESIGN-REQ-004
- [ ] T017 [P] Add failing Create-page integration test for preset schema rendering without preset-specific form branches in frontend/src/entrypoints/task-create.test.tsx covering FR-003, FR-010, SCN-001, SC-001, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008
- [ ] T018 [P] Add failing Create-page integration test for direct skill schema rendering through the same renderer path in frontend/src/entrypoints/task-create.test.tsx covering FR-003, SCN-002, SC-002, DESIGN-REQ-001
- [ ] T019 [P] Add failing Create-page integration test for missing `jira_issue.key` blocking preview, apply, or submit while preserving draft values in frontend/src/entrypoints/task-create.test.tsx covering FR-005, SCN-004, SC-004, DESIGN-REQ-006
- [ ] T020 [P] Add failing Create-page integration test for metadata-selected `jira.issue-picker` manual key entry when lookup is unavailable in frontend/src/entrypoints/task-create.test.tsx covering FR-007, FR-008, FR-009, SCN-003, SCN-005, DESIGN-REQ-004
- [ ] T021 [P] Add failing Create-page integration test proving a new fixture capability with supported schema and existing widgets renders without capability-ID-specific code in frontend/src/entrypoints/task-create.test.tsx covering FR-010, SCN-006, SC-006, DESIGN-REQ-001, DESIGN-REQ-008

### Red-First Confirmation

- [ ] T022 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/api/test_task_step_templates_service.py tests/unit/integrations/test_jira_tool_service.py tests/unit/mcp/test_jira_tool_registry.py` and confirm T007-T011 and T015-T016 fail for the intended missing behavior before implementation
- [ ] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T012-T014 and T017-T021 fail for the intended missing behavior before implementation

### Conditional Verification For Implemented-Unverified Rows

- [ ] T024 Run the new secret-safety and safe-value tests from T009, T011, and T014 first; skip fallback implementation tasks T025-T026 only if FR-011, SC-007, and DESIGN-REQ-007 already pass without production changes
- [ ] T025 Conditional on T024 failure, implement schema default redaction and secret-like value rejection in api_service/services/task_templates/catalog.py covering FR-011, SC-007, DESIGN-REQ-007
- [ ] T026 Conditional on T024 failure, implement frontend safe-default omission and safe Jira issue value submission guardrails in frontend/src/entrypoints/task-create.tsx covering FR-011, FR-012, SC-007, DESIGN-REQ-007

### Implementation

- [ ] T027 Extend task-template API request/response models for normalized `inputSchema`, `uiSchema`, and `defaults` in api_service/api/routers/task_step_templates.py covering FR-001, FR-002, DESIGN-REQ-001
- [ ] T028 Extend task-template catalog validation, serialization, and expansion input resolution for schema-backed capability inputs in api_service/services/task_templates/catalog.py covering FR-001, FR-002, FR-005, DESIGN-REQ-001, DESIGN-REQ-006
- [ ] T029 Update task-template persistence model usage or JSON payload handling for lossless schema/UI/default metadata in api_service/db/models.py covering FR-002, DESIGN-REQ-001
- [ ] T030 Update seeded Jira Orchestrate template metadata with schema-backed Jira issue input while preserving existing orchestration behavior in api_service/data/task_step_templates/jira-orchestrate.yaml covering FR-007, FR-008, DESIGN-REQ-004
- [ ] T031 Add backend safe Jira issue input validation/enrichment boundary using trusted Jira services in moonmind/integrations/jira/models.py covering FR-009, FR-012, DESIGN-REQ-004, DESIGN-REQ-005
- [ ] T032 Add or update frontend capability input contract types and draft state for schema-generated preset and skill inputs in frontend/src/entrypoints/task-create.tsx covering FR-001, FR-003, FR-008
- [ ] T033 Implement the schema-form renderer for supported schema constructs in frontend/src/entrypoints/task-create.tsx covering FR-003, FR-004, DESIGN-REQ-002
- [ ] T034 Implement the allowlisted widget registry and unsupported-widget fallback/error behavior in frontend/src/entrypoints/task-create.tsx covering FR-006, DESIGN-REQ-003
- [ ] T035 Implement reusable `jira.issue-picker` field behavior, manual key preservation, and safe value shape in frontend/src/entrypoints/task-create.tsx covering FR-007, FR-008, FR-012, SCN-005, DESIGN-REQ-004, DESIGN-REQ-005
- [ ] T036 Wire field-addressable backend/frontend validation errors to generated fields and block dependent preview/apply/submit actions when required values are missing in frontend/src/entrypoints/task-create.tsx covering FR-005, SCN-004, DESIGN-REQ-006
- [ ] T037 Wire preset and direct skill detail flows to the shared schema renderer instead of capability-specific input branches in frontend/src/entrypoints/task-create.tsx covering FR-003, FR-010, SCN-001, SCN-002, SCN-006, DESIGN-REQ-001, DESIGN-REQ-002
- [ ] T038 Remove or isolate stale capability-ID-specific Create-page branches made obsolete by schema-driven rendering in frontend/src/entrypoints/task-create.tsx covering FR-010, DESIGN-REQ-001, DESIGN-REQ-002

### Story Validation

- [ ] T039 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/api/test_task_step_templates_service.py tests/unit/integrations/test_jira_tool_service.py tests/unit/mcp/test_jira_tool_registry.py` and fix story-related failures in api_service/services/task_templates/catalog.py, api_service/api/routers/task_step_templates.py, api_service/db/models.py, moonmind/integrations/jira/models.py, or the targeted tests
- [ ] T040 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix story-related failures in frontend/src/entrypoints/task-create.tsx or frontend/src/entrypoints/task-create.test.tsx
- [ ] T041 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/integration/test_startup_task_template_seeding.py` and fix seed-related failures in api_service/data/task_step_templates/jira-orchestrate.yaml or tests/integration/test_startup_task_template_seeding.py
- [ ] T042 Validate the independent story path from specs/308-schema-driven-capability-inputs/quickstart.md and record any blocked command or environment prerequisite in specs/308-schema-driven-capability-inputs/quickstart.md

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable through the quickstart scenarios.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T043 [P] Review frontend/src/entrypoints/task-create.tsx for localized extraction opportunities after tests pass, keeping schema renderer and widget registry cohesive without unrelated Create-page rewrites
- [ ] T044 [P] Review api_service/services/task_templates/catalog.py for localized cleanup after tests pass, preserving existing template expansion semantics and avoiding unrelated task-template refactors
- [ ] T045 [P] Review specs/308-schema-driven-capability-inputs/contracts/capability-input-contract.md and specs/308-schema-driven-capability-inputs/data-model.md for drift against the final implemented contract
- [ ] T046 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and fix only story-related failures in frontend/src/entrypoints/task-create.tsx, frontend/src/entrypoints/task-create.test.tsx, api_service/services/task_templates/catalog.py, api_service/api/routers/task_step_templates.py, api_service/db/models.py, moonmind/integrations/jira/models.py, tests/unit/api/test_task_step_templates_service.py, tests/unit/integrations/test_jira_tool_service.py, tests/unit/mcp/test_jira_tool_registry.py, or tests/integration/test_startup_task_template_seeding.py
- [ ] T047 Run `./tools/test_integration.sh` if backend API or startup seed behavior changed in a way that requires compose-backed `integration_ci` proof, and record unavailable Docker or environment blockers in specs/308-schema-driven-capability-inputs/quickstart.md
- [ ] T048 Run `/moonspec-verify` after implementation and required tests pass, validating the completed branch against specs/308-schema-driven-capability-inputs/spec.md and the preserved MM-593 Jira preset brief

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 Setup has no dependencies.
- Phase 2 Foundational depends on Phase 1.
- Phase 3 Story depends on Phase 2 fixtures and must keep unit/integration tests before implementation.
- Phase 4 Polish and Verification depends on the story checkpoint and passing focused tests.

### Story Order

1. Write unit tests T007-T014.
2. Write integration tests T015-T021.
3. Confirm red-first failures with T022-T023.
4. Run implemented-unverified verification T024 and only execute fallback tasks T025-T026 if needed.
5. Implement backend contract/data behavior T027-T031.
6. Implement frontend schema renderer, widgets, Jira field, validation mapping, and branch removal T032-T038.
7. Validate focused Python, frontend, and seed behavior T039-T042.
8. Complete polish and final verification T043-T048.

### Parallel Opportunities

- T002-T006 can run in parallel because they add test fixtures in separate focused areas.
- T007-T014 can run in parallel where they touch different test sections/files.
- T015-T021 can run in parallel after fixture setup because they target distinct acceptance surfaces.
- T027-T031 backend implementation can proceed in parallel with T032-T038 frontend implementation only after red-first confirmation, provided shared contract decisions from T027 are coordinated.
- T043-T045 can run in parallel after focused tests pass.

## Parallel Example: Story Phase

```text
Task: "Add failing backend unit tests for normalized capability contracts in tests/unit/api/test_task_step_templates_service.py"
Task: "Add failing frontend unit tests for schema renderer and widget registry in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing Jira safe value tests in tests/unit/integrations/test_jira_tool_service.py"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational fixture tasks.
2. Write all unit tests and integration tests for the selected story.
3. Run focused Python and frontend commands to confirm the new tests fail for missing behavior.
4. Treat FR-011, SC-007, and DESIGN-REQ-007 as implemented-unverified: run verification tests first, then execute fallback implementation only if those tests fail.
5. Implement the backend normalized capability contract and safe Jira value validation.
6. Implement the frontend schema renderer, widget registry, Jira issue picker, field-addressable errors, and generic preset/skill wiring.
7. Run focused tests, quickstart validation, full unit verification, and final `/moonspec-verify`.

## Notes

- This task list covers exactly one story: `Render Capability Inputs From Schema`.
- Do not implement preview/apply, recursive expansion, flattened provenance, or submit-time auto-expansion beyond the minimal integration needed for configured capability input values; those are covered by existing separate specs.
- Commit after a logical red/green/refactor group once implementation begins.
