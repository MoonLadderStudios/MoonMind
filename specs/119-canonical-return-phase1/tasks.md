# Tasks: canonical-return-phase1

## Phase 1: Setup

- [x] T001 Verify test environment via `./tools/test_unit.sh`

## Phase 2: Foundational Activity Boundary Validation [US1]

**Goal**: Implement activity boundary validation helpers that strip/filter payload keys into Pydantic models.
**Test Criteria**: Validation logic correctly raises UnsupportedStatus and validates/strips payload keys.

- [x] T002 [US1] Create test cases in `tests/unit/schemas/test_agent_runtime_models.py` simulating boundaries and failing (Red) (DOC-REQ-001, DOC-REQ-006)
- [x] T003 [US1] Add `raise_unsupported_status` helper to `moonmind/schemas/agent_runtime_models.py` (DOC-REQ-002, DOC-REQ-003)
- [x] T004 [US1] Write test case asserting `raise_unsupported_status` throws exception and run test
- [x] T005 [US1] Add `build_canonical_start_handle` to `moonmind/schemas/agent_runtime_models.py` (DOC-REQ-002)
- [x] T006 [US1] Write test case asserting `build_canonical_start_handle` handles arbitrary provider fields and `external_id` (DOC-REQ-005)
- [x] T007 [US1] Add `build_canonical_status` mapping `providerStatus`, `normalizedStatus`, `externalUrl` to `metadata` (DOC-REQ-004)
- [x] T008 [US1] Write test case asserting `build_canonical_status` correctly filters metadata without poisoning workflow keys (DOC-REQ-005)
- [x] T009 [US1] Add `build_canonical_result` to `moonmind/schemas/agent_runtime_models.py` (DOC-REQ-002)
- [x] T010 [US1] Write test case asserting `build_canonical_result` enforces correct metadata schema


## Final Phase: Polish & Validation

- [ ] T011 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [ ] T012 Run `./tools/test_unit.sh` to ensure all tests are green
