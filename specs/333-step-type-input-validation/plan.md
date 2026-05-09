# Implementation Plan: Step Type Input Validation with Field-Addressable Errors

**Branch**: `change-jira-issue-mm-574-to-status-in-pr-e1677d0e` | **Date**: 2026-05-09 | **Spec**: [spec.md](./spec.md)

## Summary

Add a structured `ValidationFieldError` model and propagate field-addressable errors through task step validation so the submission API returns a `validation_errors` array with `path`, `message`, and `code` fields instead of opaque error strings.

The existing `TaskContractError` already carries a `diagnostic` field for structured context. The existing task submission endpoint already returns HTTP 422 on validation failures. This plan extends both to surface field paths in the response, adds collect-all-errors behavior across multiple steps, and wires preset input schema validation into the submit-time expansion path.

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. No agent behavior is created; this adds structured error output to existing validation boundaries.
- **II. One-Click Agent Deployment**: PASS. No new services or infrastructure dependencies.
- **III. Avoid Vendor Lock-In**: PASS. Validation is fully internal; no vendor-specific API changes.
- **IV. Own Your Data**: PASS. No data storage changes; errors are transient response objects.
- **V. Skills Are First-Class**: PASS. Skill step validation reuses the existing skill contract; errors are now field-addressable.
- **VI. Evolving Scaffolds**: PASS. The error model is a thin, stable contract. Existing string-only error messages are replaced (not shimmed).
- **VII. Runtime Configurability**: PASS. No new config options; validation behavior is deterministic.
- **VIII. Modular Architecture**: PASS. Changes are isolated to the task validation layer and the submission endpoint response shape.
- **IX. Resilient by Default**: PASS. Collect-all-errors behavior means callers receive complete feedback in a single round trip.
- **X. Continuous Improvement**: PASS. Structured error codes enable future analytics on common submission failures.
- **XI. Spec-Driven Development**: PASS. This plan, spec, and tasks track the change.
- **XII. Canonical Documentation Separation**: PASS. No migration backlog is added to canonical docs.
- **XIII. Pre-Release Compatibility**: PASS. The old opaque error string is replaced entirely; no backward-compat shim is introduced.

## Implementation Scope

### 1. Add `ValidationFieldError` and `StepValidationResult` models

**File**: `moonmind/workflows/tasks/task_contract.py`

Add two new Pydantic models alongside the existing `TaskContractError`. The `code` field is constrained by an `Enum` to keep error codes consistent and typo-resistant; the `errors` field uses `Field(default_factory=list)` to follow the existing convention in this module (see `moonmind/workflows/tasks/task_contract.py` line 1075).

The `path` field is rooted at the **request body** for the submission endpoint, so frontends can address fields directly from the submitted payload (e.g. `payload.task.steps[0].tool.inputs.issueKey`). The plan uses the `steps[i]...` shorthand below for brevity; the API contract documents the full root.

```python
class ValidationErrorCode(str, Enum):
    REQUIRED = "required"
    TYPE = "type"
    TOOL_NOT_FOUND = "tool_not_found"
    SKILL_NOT_FOUND = "skill_not_found"
    PRESET_NOT_FOUND = "preset_not_found"
    SCHEMA_VIOLATION = "schema_violation"

class ValidationFieldError(BaseModel):
    path: str                  # dot-bracket notation rooted at the request body, e.g. "payload.task.steps[0].tool.inputs.issueKey"
    message: str               # human-readable description
    code: ValidationErrorCode  # machine-readable error code

class StepValidationResult(BaseModel):
    is_valid: bool
    errors: list[ValidationFieldError] = Field(default_factory=list)
```

### 2. Add step input validation collectors

**File**: `moonmind/workflows/tasks/task_contract.py`

Add a new function `validate_step_inputs(steps, step_idx)` that:
- Resolves the tool/skill/preset `inputSchema` from the catalog (or existing annotation logic)
- Validates `step.tool.inputs` / `step.skill.inputs` / `step.preset.inputs` against the resolved schema
- Returns a list of `ValidationFieldError` with correct path prefixes: `steps[{idx}].tool.inputs.{field}` etc.
- Adds `code: "tool_not_found"` / `code: "skill_not_found"` / `code: "preset_not_found"` when the selected capability cannot be resolved

### 3. Collect errors across all steps before failing

**File**: `moonmind/workflows/tasks/task_contract.py` (task submission normalization path)

Update the existing step-level validation to collect all `ValidationFieldError` instances instead of raising `TaskContractError` on the first failure. Raise a single `TaskContractError` carrying the full `errors` list once all steps have been checked. Store the collected list on the existing `TaskContractError.diagnostic` field so the API layer can extract it from the standard error-handling pathway without a new sidecar attribute.

### 4. Update the 422 response shape

**File**: `api_service/api/routers/executions.py`

The current signature of `_invalid_task_request()` (line ~3236) only accepts a `message: str`. Extend the signature to accept an optional `validation_errors: list[ValidationFieldError] | None = None` parameter so callers can pass structured field errors through. Update all existing call sites to keep working with the message-only form.

When `validation_errors` is provided, include it as a `validation_errors` array in the 422 response body. Each entry uses paths rooted at the request body (e.g. `payload.task.steps[i]...`):

```json
{
  "code": "invalid_execution_request",
  "message": "Step validation failed: 2 field error(s).",
  "validation_errors": [
    {"path": "payload.task.steps[0].tool.inputs.issueKey", "message": "Field 'issueKey' is required.", "code": "required"},
    {"path": "payload.task.steps[1].skill.inputs.repository", "message": "Field 'repository' must be a string.", "code": "type"}
  ]
}
```

When no field errors are present (e.g., a contract-level failure), `validation_errors` is omitted.

### 5. Wire preset input schema validation into submit-time expansion

**File**: `moonmind/workflows/tasks/task_contract.py` (or preset expansion helper)

Before expanding an unresolved Preset step at submit time, validate the step's `preset.inputs` against the preset's declared `inputSchema`. Return `ValidationFieldError` entries with paths `steps[{idx}].preset.inputs.{field}` when validation fails, and skip expansion for that step.

## Validation

- `pytest tests/unit/workflows/tasks/test_task_contract.py -q`
- `pytest tests/integration/ -m integration_ci -q -k "submission or task_contract or step"` (focused)
- `./tools/test_unit.sh`

## Complexity Tracking

No cross-cutting changes; the scope is confined to `task_contract.py` and the submission endpoint response shape. No Temporal workflow or activity signature changes. No database schema changes.
