# Feature Specification: Step Type Input Validation with Field-Addressable Errors

**Feature Branch**: `change-jira-issue-mm-574-to-status-in-pr-e1677d0e`
**Created**: 2026-05-09
**Status**: Draft
**Input**: Jira Orchestrate handoff for `MM-574`.

Preserved source traceability:

- Target Jira issue: `MM-574`
- Source Jira issue: `manual-mm-569-mm-574`
- Source design: `docs/Steps/StepTypes.md`
- Canonical source: synthesized from StepTypes.md section 10 (Validation Rules) because MM-574 is not accessible from the connected Atlassian workspace (gaudhammer.atlassian.net does not expose the MM project)
- Classification: single-story runtime feature request
- Resume decision: no existing Moon Spec feature directory preserved `MM-574`; Specify is the first incomplete stage

## Original Preset Brief (Synthesized)

MM-574 is STORY-006 in the `manual-mm-569-mm-574` batch, the final story in the StepTypes.md migration coverage set. The preceding stories covered:

- STORY-001 (MM-569): Phase 1 — UI terminology (rename step selector to "Step Type")
- STORY-002 (MM-570): Phase 2 — Schema-driven step editor forms
- STORY-003 (MM-571): Phase 3 — Draft model normalization (explicit `step.type`)
- STORY-004 (MM-572): Phase 4 — Preview and Apply Preset Steps
- STORY-005 (MM-573): Phase 5 — Compile executable steps into runtime plans

STORY-006 (MM-574) covers the remaining implementation gap: field-addressable step type validation errors (StepTypes.md section 10). The backend currently returns opaque error strings when step inputs fail validation. This story introduces a structured `ValidationFieldError` response model with `path`, `message`, and `code` fields so the Create page and API consumers can display targeted inline feedback for each invalid field.

## User Story — Field-Addressable Step Validation Errors

**Summary**: As a task author submitting a task with invalid step inputs, I receive structured validation errors that identify the exact step index, field path, and failure code so I can correct my submission without guessing which field or step is invalid.

**Goal**: The submission API and preset expansion service return validation errors in a consistent structured shape (`path`, `message`, `code`) for every step type violation, and the backend enforces all Tool, Skill, and Preset validation rules from StepTypes.md section 10 at submission time.

**Independent Test**: Submit a task with a Tool step missing a required input field and verify the API response contains a `validation_errors` array with at least one entry matching `{"path": "steps[0].tool.inputs.<field>", "message": "<readable message>", "code": "required"}`. The test is independent because it only requires the task submission endpoint and a catalog entry for the selected tool.

**Acceptance Scenarios**:

1. **Given** a task submission contains a Tool step with a missing required input, **When** the backend validates the step payload, **Then** the API returns a 422 response with a `validation_errors` array containing an entry with a non-empty `path`, `message`, and `code`.
2. **Given** a task submission contains a Skill step with inputs that fail the skill's input schema, **When** the backend validates the step payload, **Then** the API returns a structured error pointing to the exact failing input field path under `steps[idx].skill.inputs.<field>`.
3. **Given** a preset expansion request contains inputs that violate the preset's `inputSchema`, **When** the backend validates the expansion request, **Then** the expansion endpoint returns structured field errors pointing to the violating preset input path under `steps[idx].preset.inputs.<field>` and does not proceed to expansion.
4. **Given** a step payload passes all validation rules, **When** the submission is processed, **Then** no `validation_errors` are included and the submission proceeds normally.
5. **Given** multiple steps have independent validation failures, **When** the submission is processed, **Then** all field errors are collected and returned together rather than stopping at the first failure.
6. **Given** a Tool step's selected tool does not exist in the registry, **When** the backend validates the step, **Then** a structured error is returned with `code: "tool_not_found"` and a path targeting the tool identifier field.

### Edge Cases

- A Tool step that omits a required field nested inside an object input (e.g., `tool.inputs.jira_issue.key`) produces an error path of `steps[0].tool.inputs.jira_issue.key`.
- A Preset step where the preset catalog entry is missing or inactive produces `code: "preset_not_found"` before schema validation is attempted.
- An error that cannot be mapped to a specific field (e.g., a tool registry timeout) is returned with `path: "steps[idx]"` and `code: "validation_error"`.
- When submit-time expansion of a Preset step fails, errors from expansion are included in the same `validation_errors` array alongside other step errors.

## Assumptions

- Step type validation runs server-side at task submission time.
- Tool and Skill input schema resolution is available from the existing task template / capability catalog service.
- Preset expansion validation reuses the existing preset expansion service, adding structured error output.
- The existing `TaskContractError` class is extended or replaced with the structured error shape; existing string-only errors are updated to include field paths.
- Frontend rendering of field-level errors is out of scope for MM-574; this story defines and implements the backend error contract and API response shape. Frontend integration is a follow-on story.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Steps/StepTypes.md` section 10.1 | Every step must expose validation errors before submission succeeds. | In scope | FR-001, FR-005 |
| DESIGN-REQ-002 | `docs/Steps/StepTypes.md` section 10.2 | A Tool step is valid only when inputs validate against the tool schema; errors are field-addressable. | In scope | FR-002, FR-006 |
| DESIGN-REQ-003 | `docs/Steps/StepTypes.md` section 10.3 | A Skill step is valid only when skill inputs validate against the skill contract; errors are field-addressable. | In scope | FR-003 |
| DESIGN-REQ-004 | `docs/Steps/StepTypes.md` section 10.4 | A Preset step expansion is blocked when inputs fail validation; errors are field-addressable and expansion warnings are visible. | In scope | FR-004 |
| DESIGN-REQ-005 | `docs/Steps/StepTypes.md` section 10.4 (example) | Validation errors must carry `path`, `message`, and `code` fields using dot-bracket notation for nested inputs. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-006 | `docs/Steps/StepTypes.md` section 8.3 | When preset expansion fails at submit time, field-addressable errors are returned and the draft is preserved. | In scope | FR-004, FR-005 |
| DESIGN-REQ-007 | `docs/Steps/StepTypes.md` section 10.4 (example) | Downstream artifacts, verification output, commit text, and PR metadata MUST preserve MM-574, manual-mm-569-mm-574, and this preset brief. | In scope | FR-007 |

## Requirements

### Functional Requirements

- **FR-001**: The task submission API MUST return a structured `validation_errors` array when any step fails validation, where each entry contains a non-empty `path` (dot-bracket notation targeting the failing field), a human-readable `message`, and a machine-readable `code`.
- **FR-002**: Tool step validation MUST verify that inputs satisfy the tool's declared input schema and MUST return field errors with paths of the form `steps[{idx}].tool.inputs.{field}` when validation fails.
- **FR-003**: Skill step validation MUST verify that inputs satisfy the skill's declared input schema and MUST return field errors with paths of the form `steps[{idx}].skill.inputs.{field}` when validation fails.
- **FR-004**: Preset step expansion at submit time MUST validate inputs against the preset's `inputSchema` before expansion and MUST return field errors with paths of the form `steps[{idx}].preset.inputs.{field}` when inputs are invalid; expansion MUST NOT proceed for invalid preset inputs.
- **FR-005**: Validation MUST collect all field errors across all steps before returning, rather than stopping at the first error, so authors receive a complete picture of what needs to be corrected.
- **FR-006**: When a Tool or Skill selected in a step cannot be resolved in the registry, the validation response MUST include an error with `code: "tool_not_found"` or `code: "skill_not_found"` and a path targeting the selector field.
- **FR-007**: Downstream artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve target Jira issue `MM-574`, source issue `manual-mm-569-mm-574`, and this original synthesized preset brief.

### Key Entities

- **`ValidationFieldError`**: The structured error shape returned in `validation_errors` arrays. Contains `path` (string, dot-bracket notation), `message` (string, human-readable), and `code` (string, machine-readable, e.g., `"required"`, `"pattern"`, `"tool_not_found"`, `"skill_not_found"`, `"preset_not_found"`, `"validation_error"`).
- **`StepValidationResult`**: The aggregate result of validating all steps in a submission. Contains `is_valid` (bool) and `errors` (list of `ValidationFieldError`).
- **Tool/Skill Input Schema**: The `inputSchema` declared by each tool or skill in the capability catalog, used to validate `tool.inputs` or `skill.inputs` at submission time.
- **Preset Input Schema**: The `inputSchema` declared by a preset (e.g., in the seeded YAML), used to validate `preset.inputs` before expansion at submit time.

## Success Criteria

- **SC-001**: Unit tests demonstrate that a Tool step with a missing required input produces a `ValidationFieldError` with a `path` matching `steps[0].tool.inputs.<field>`, `code: "required"`, and a non-empty `message`.
- **SC-002**: Unit tests demonstrate that a Skill step with an invalid input produces a field error with the correct path pattern.
- **SC-003**: Unit tests demonstrate that a Preset step with invalid inputs is blocked from expansion and returns field errors with the correct path pattern.
- **SC-004**: Unit tests demonstrate that multiple validation failures across multiple steps are all collected and returned together.
- **SC-005**: Integration tests at the task submission boundary confirm that a submission with an invalid Tool step returns a 422 response with a `validation_errors` array.
- **SC-006**: Source traceability review confirms `MM-574`, `manual-mm-569-mm-574`, and DESIGN-REQ-001 through DESIGN-REQ-007 are preserved in MoonSpec artifacts.
