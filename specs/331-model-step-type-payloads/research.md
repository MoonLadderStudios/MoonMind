# Research: Model Explicit Step Type Payloads and Validation

## Setup Script

Decision: Planning continues manually from `.specify/feature.json` because the helper script is blocked by the managed branch name.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed with `ERROR: Not on a feature branch. Current branch: run-jira-orchestrate-for-mm-569-model-ex-42b254e1`; `.specify/feature.json` points to `specs/331-model-step-type-payloads`.
Rationale: The active feature directory is explicit and already contains a passing single-story `spec.md`, so artifact generation can proceed without regenerating the feature.
Alternatives considered: Renaming branches or creating a new feature directory was rejected because it would break managed-run traceability and duplicate `MM-569`.
Test implications: none beyond verifying generated artifact paths.

## Current Implementation Evidence

Decision: Existing code is partial and should be extended rather than replaced wholesale.
Evidence: `moonmind/workflows/tasks/task_contract.py` defines executable task step validation and rejects `type: preset`; `api_service/services/task_templates/catalog.py` normalizes Tool and Skill template steps and validates preset input expansion; `frontend/src/entrypoints/task-create-step-type.test.tsx` covers Step Type UI selection and preset expansion; `tests/unit/workflows/tasks/test_task_contract.py` and `tests/unit/api/test_task_step_templates_service.py` cover several explicit Tool/Skill and mixed-payload cases.
Rationale: These surfaces already represent the intended boundaries: draft/UI, template expansion, and executable submission. The gap is consistency and coverage across all MM-569 requirements.
Alternatives considered: Creating a new validation subsystem was rejected because existing model/service boundaries already own the relevant behavior.
Test implications: unit + integration.

## FR-001 / DESIGN-REQ-012

Decision: Status is partial; add or consolidate validation for stable identity, title/label, Step Type, and exactly one matching sub-payload across draft/template/submission contexts.
Evidence: `TaskStepSpec` supports step IDs and executable Tool/Skill discriminators; `TaskTemplateCatalogService` normalizes missing `type` to Skill; Create-page tests cover Step Type UI. No single shared validation proof covers all required fields across Tool, Skill, and Preset contexts.
Rationale: The story requires a coherent model, not just isolated validation in one boundary.
Alternatives considered: Treating UI-only Step Type tests as sufficient was rejected because executable submission also needs contract-level proof.
Test implications: unit + integration.

## FR-002 / SC-002

Decision: Status is implemented_unverified; keep current behavior and add MM-569-specific invalid matrix tests.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` rejects preset/activity runtime step types, conflicting tool+skill payloads, and skill steps with non-skill tool payloads. `tests/unit/api/test_task_step_templates_service.py` rejects unsupported or mixed template step payloads.
Rationale: Existing coverage is strong but not explicitly mapped to every MM-569 mixed/missing class.
Alternatives considered: Marking implemented_verified was rejected because the current tests are MM-557/MM-559 scoped and do not cover missing type-specific payloads uniformly.
Test implications: unit.

## FR-003 / SC-003 / DESIGN-REQ-013

Decision: Status is partial; standardize or expose field-addressable validation output for Step Type errors.
Evidence: Preset input validation in `TaskTemplateCatalogService._resolve_inputs()` emits structured errors such as `preset.inputs.<name>`, while `TaskContractError` paths are currently message-based for many task step failures.
Rationale: MM-569 requires field-addressable validation errors before submission, so task-step errors need consistent path evidence where exposed through API boundaries.
Alternatives considered: Keeping plain error strings was rejected because the acceptance criteria call for field-addressable errors.
Test implications: unit + integration.

## FR-004 / DESIGN-REQ-014

Decision: Status is partial; close local Tool validation gaps and document unavailable service checks.
Evidence: `_normalize_tool_payload()` validates tool id/name, input object shape, requiredCapabilities shape, metadata passthrough, and command-like tool policy metadata. It does not prove authorization, registry availability, worker capability, retry policy, and side-effect policy checks for all tool classes.
Rationale: Some checks require services that may not exist for every local unit path; the validator should fail clearly when required local metadata is missing and degrade explicitly only where a service is unavailable by design.
Alternatives considered: Deferring all Tool validation to runtime was rejected because the spec requires pre-submission validation.
Test implications: unit.

## FR-005 / DESIGN-REQ-015

Decision: Status is partial; close local Skill validation gaps for resolution, runtime compatibility, context, allowed tools/permissions, and autonomy constraints.
Evidence: `_normalize_skill_payload()` validates skill id defaulting, args object shape, requiredCapabilities shape, and object shapes for context, permissions, autonomy, and runtime metadata. Active skill resolution and runtime compatibility proof are not complete at the task-template/task-submission boundary.
Rationale: The story requires validation where services are available, so the plan should add local checks and explicit degraded outcomes rather than silent acceptance.
Alternatives considered: Treating `skill.id` shape validation as complete was rejected because the spec names resolution and compatibility checks.
Test implications: unit.

## FR-006 / DESIGN-REQ-018

Decision: Status is partial; extend preset validation and generated-step coverage.
Evidence: `TaskTemplateCatalogService.expand_template()` validates inputs, recursive expansion, max step count, authored presets, generated steps, capabilities, warnings, and inactive-version warnings. Existing tests cover deterministic IDs, schema input errors, recursive presets, and step limits.
Rationale: MM-569 needs an explicit plan for active version, generated-step validation, visible warnings, deterministic expansion, and policy limits as one Preset validation story.
Alternatives considered: Relying on existing catalog tests only was rejected because they do not fully map to MM-569 requirements.
Test implications: unit + integration.

## FR-007 / SC-004

Decision: Status is implemented_unverified; add a focused integration boundary test.
Evidence: `TaskStepSpec._reject_forbidden_step_overrides()` rejects runtime `type: preset`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py` asserts expanded task steps are not preset.
Rationale: Behavior appears present, but MM-569 needs direct verification that executable submission rejects unresolved Preset steps unless an explicit linked-preset mode exists.
Alternatives considered: Marking verified from unit tests alone was rejected because the requirement is an executable submission boundary.
Test implications: integration.

## FR-008

Decision: Status is partial; add tests for failed expansion preserving inputs and visible validation errors.
Evidence: Create-page tests cover in-place expansion, stale async expansion isolation, and latest preset instructions. Catalog validation emits recoverable preset input errors.
Rationale: The requirement includes preservation after failed apply/submit expansion, which needs explicit UI/API evidence.
Alternatives considered: Treating happy-path expansion tests as sufficient was rejected because failure preservation is a separate behavior.
Test implications: unit + integration.

## FR-009 / DESIGN-REQ-021

Decision: Status is partial; keep legacy readers explicit while preventing new ambiguous emission.
Evidence: `TaskTemplateCatalogService._normalize_step_type()` defaults missing type to Skill, preserving legacy input; tests assert explicit and legacy skill steps are accepted.
Rationale: The migration requirement allows legacy reads but requires new authoring surfaces to emit normalized explicit shapes.
Alternatives considered: Removing legacy reads now was rejected because the spec explicitly allows migration readers.
Test implications: unit + integration.

## FR-010

Decision: Status is partial; align draft, submission, and executable validation around flat executable Tool/Skill payloads and provenance metadata.
Evidence: Runtime task contract preserves preset provenance and rejects unresolved Preset execution; template expansion emits concrete steps and authored preset metadata. Cross-boundary validation is still distributed.
Rationale: The acceptance criteria require accepted executable steps to be correct without live preset catalog lookup.
Alternatives considered: Re-expanding live presets at execution was rejected by the source design and Constitution pre-release clarity principle.
Test implications: integration.

## FR-011 / SC-006

Decision: Status is implemented_unverified; preserve traceability through all artifacts and final evidence.
Evidence: `spec.md` preserves `MM-569`, `manual-mm-569-mm-574`, the original preset brief, and DESIGN-REQ coverage IDs.
Rationale: Verification depends on comparing implementation evidence against the original Jira preset brief.
Alternatives considered: Storing only the issue key was rejected because final verification needs the original brief.
Test implications: final traceability review.

## Unit Test Strategy

Decision: Use focused pytest and Vitest unit tests before production changes, then full `./tools/test_unit.sh` before finalization.
Evidence: Existing unit targets include `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/api/test_task_step_templates_service.py`, and `frontend/src/entrypoints/task-create-step-type.test.tsx`.
Rationale: These are the fastest boundaries for red-first validation of step models, template validation, and Create-page behavior.
Alternatives considered: Starting with integration only was rejected because most validation gaps can be isolated faster at unit level.
Test implications: unit.

## Integration Test Strategy

Decision: Use hermetic integration tests for executable submission normalization and any API-visible structured validation behavior.
Evidence: Existing targets include `tests/integration/api/test_task_contract_normalization.py` and `tests/integration/temporal/test_task_shaped_submission_normalization.py`.
Rationale: FR-007, FR-010, and parts of FR-003 require proof at the API/Temporal submission boundary, not only model-level validation.
Alternatives considered: Provider verification tests were rejected because no third-party credentials are required.
Test implications: integration_ci.
