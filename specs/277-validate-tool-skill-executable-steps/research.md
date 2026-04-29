# Research: Validate Tool and Skill Executable Steps

## FR-001..FR-004 Tool step contract

Decision: Missing. Add explicit `type: tool` and `tool` payload support to API schema, catalog validation, save-from-task sanitization, and expansion preservation.
Evidence: `api_service/api/schemas.py` lacks `type` and `tool` fields for `TaskTemplateStepBlueprintSchema`; `api_service/services/task_templates/catalog.py` only validates `skill`; `api_service/services/task_templates/save.py` only allows `slug`, `title`, `instructions`, `skill`, and `annotations`.
Rationale: Tool steps must be deterministic typed operations and cannot be represented as generic skill steps.
Alternatives considered: Reusing `skill` for Tool operations was rejected because MM-557 requires Tool and Skill not to be interchangeable.
Test implications: Unit tests for create, save, and expand boundaries.

## FR-005..FR-007 Skill step contract

Decision: Partial. Keep existing skill-shaped validation, add explicit `type: skill`, preserve context/permissions/autonomy metadata in the Skill payload, and reject Tool-only fields for Skill steps.
Evidence: `catalog.py` validates `skill.id`, `skill.args`, and `skill.requiredCapabilities`; it drops other Skill contract metadata.
Rationale: Skill steps represent agentic work and need their own metadata without being forced through Tool semantics.
Alternatives considered: Allowing arbitrary Skill payload fields was rejected because executable steps should have bounded validation.
Test implications: Unit tests for explicit Skill acceptance and invalid Skill args/capabilities in create/save paths.

## FR-008..FR-010 Common Step Type validation

Decision: Missing. Add common Step Type normalization and fail-fast errors for unsupported Step Type or payload mismatch.
Evidence: Existing template validation validates `kind` for step/include but does not validate authored executable Step Type.
Rationale: The Step Type discriminator controls the expected type-specific payload.
Alternatives considered: Inferring Step Type from any payload was rejected except for existing skill-shaped templates, which remain Skill by default.
Test implications: Unit tests for unsupported type and mixed payload rejection.

## FR-011..FR-013 Jira examples

Decision: Missing. Add validation tests using Jira transition as Tool and Jira triage/implementation as Skill.
Evidence: `docs/Steps/StepTypes.md` section 9 defines this distinction; no service tests assert it.
Rationale: Jira examples are the acceptance proof that deterministic and agentic work remain separate.
Alternatives considered: Testing only generic tool names was rejected because the source design explicitly calls out Jira.
Test implications: Unit tests for accepted and rejected Jira-shaped examples.

## FR-014 Shell snippet rejection

Decision: Partial. Existing forbidden top-level keys reject runtime/repo/container fields, but command/script snippets are not forbidden.
Evidence: `_FORBIDDEN_STEP_KEYS` in `catalog.py` omits command/script/shell/bash fields; save service does not scan them because it drops unknown keys.
Rationale: Arbitrary shell snippets are not a Step Type. A typed Tool may represent command execution only when bounded inputs and policy metadata are present.
Alternatives considered: Rejecting every Tool with command-like input text was rejected because approved typed command tools can accept bounded inputs.
Test implications: Unit tests for top-level shell field rejection and accepted typed Tool policy metadata.
