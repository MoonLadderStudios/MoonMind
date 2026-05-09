# Tasks: Step Type Input Validation with Field-Addressable Errors

**Input**: Design documents from `/specs/333-step-type-input-validation/`

## Phase 1: Error Model and Validation Core

- [ ] T001 Add `ValidationFieldError` and `StepValidationResult` Pydantic models to `moonmind/workflows/tasks/task_contract.py`
- [ ] T002 Add `validate_step_inputs(steps, step_idx)` that resolves tool/skill/preset `inputSchema` and returns `list[ValidationFieldError]` with correct path prefixes
- [ ] T003 Add `code: "tool_not_found"`, `code: "skill_not_found"`, `code: "preset_not_found"` errors when selected capabilities cannot be resolved
- [ ] T004 Update step-level validation to collect all field errors across all steps before raising `TaskContractError`

## Phase 2: API Response Shape

- [ ] T005 Update `_invalid_task_request()` in `api_service/api/routers/executions.py` to include `validation_errors` array in 422 response when `TaskContractError` carries field errors
- [ ] T006 Wire preset input schema validation into submit-time expansion: validate `preset.inputs` against `inputSchema` before expansion and return `ValidationFieldError` entries on failure

## Phase 3: Tests

- [ ] T007 Add unit tests in `tests/unit/workflows/tasks/test_task_contract.py` for Tool step with missing required input → `ValidationFieldError` with correct path and `code: "required"`
- [ ] T008 Add unit tests for Skill step with invalid input → field error with correct path pattern
- [ ] T009 Add unit tests for Preset step with invalid inputs → blocked expansion and field errors with correct path pattern
- [ ] T010 Add unit tests proving multiple validation failures across multiple steps are all collected before returning
- [ ] T011 Add unit test for unknown tool identity → `code: "tool_not_found"` with path targeting selector field
- [ ] T012 Add integration test in `tests/integration/` (marked `integration_ci`) proving that a task submission with an invalid Tool step returns HTTP 422 with a `validation_errors` array

## Phase 4: Validation

- [ ] T013 Run `pytest tests/unit/workflows/tasks/test_task_contract.py -q` — all tests pass
- [ ] T014 Run `./tools/test_unit.sh` — full unit suite passes
