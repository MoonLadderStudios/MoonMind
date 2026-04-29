# Implementation Plan: Validate Tool and Skill Executable Steps

**Branch**: `277-validate-tool-skill-executable-steps` | **Date**: 2026-04-29 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/277-validate-tool-skill-executable-steps/spec.md`

## Summary

MM-557 requires executable step blueprints to validate Tool and Skill steps against distinct contracts before task templates are persisted or expanded. Current repo evidence shows `TaskTemplateCatalogService` validates instructions, includes, and skill payloads, while API schemas and save-from-task sanitization do not preserve explicit `type: tool` plus `tool` payloads. The implementation will extend the task template boundary to parse explicit Step Type, validate and normalize Tool payloads, preserve Skill metadata, reject mixed or shell-shaped executable steps, and add unit coverage using the existing async task template service tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | `TaskTemplateStepBlueprintSchema` has no `type` or `tool`; catalog ignores explicit Step Type | Add `type`/`tool` schema and catalog validation | unit + integration-boundary |
| FR-002 | missing | No Tool payload identifier validation | Require `tool.id` or `tool.name` | unit |
| FR-003 | missing | No Tool input object validation | Validate `tool.inputs`/`tool.args` object shape | unit |
| FR-004 | missing | Tool metadata is not preserved by schema/create/save paths | Preserve normalized Tool payload metadata | unit + integration-boundary |
| FR-005 | partial | Legacy Skill steps and `skill` payload validation exist | Add explicit `type: skill` validation and reject Tool-only fields | unit |
| FR-006 | implemented_unverified | `skill.args` and `requiredCapabilities` validation exists in catalog; save path only checks skill object | Add save-path coverage and keep validation evidence | unit |
| FR-007 | partial | Existing catalog preserves `id`, `args`, and `requiredCapabilities`; not context/permissions/autonomy metadata | Preserve allowed Skill metadata fields | unit |
| FR-008 | missing | Unsupported `type` values are ignored by schema/catalog | Reject unsupported Step Type values | unit |
| FR-009 | missing | Mixed Tool/Skill payloads are not detected | Reject selected-type payload mismatches | unit |
| FR-010 | partial | Template validation errors already surface, but not for Tool/Skill distinctions | Use existing `TaskTemplateValidationError` messages | unit |
| FR-011 | missing | No Tool example validation exists | Add Jira transition Tool acceptance test | unit |
| FR-012 | implemented_unverified | Skill-shaped examples are accepted | Add explicit Jira triage Skill acceptance test | unit |
| FR-013 | missing | Swapped/mixed Jira variants are not rejected | Add rejection tests | unit |
| FR-014 | partial | Some top-level forbidden keys exist, but `command`/`script` are not rejected and Tool policy exception is absent | Reject shell-shaped fields unless bounded Tool payload declares policy metadata | unit |
| SC-001..005 | missing | No MM-557 tests exist | Add targeted service tests and run focused/full unit suite | unit + final verify |
| DESIGN-REQ-003 | missing | Tool contract not represented in template validation | Add Tool step contract validation | unit |
| DESIGN-REQ-004 | partial | Skill contract partially represented | Preserve broader Skill metadata and explicit Step Type | unit |
| DESIGN-REQ-005 | partial | Existing errors surface, but Step Type-specific errors missing | Add Step Type-specific validation errors | unit |
| DESIGN-REQ-013 | missing | Jira Tool/Skill examples are not distinguished | Add Jira example tests | unit |
| DESIGN-REQ-017 | partial | Top-level forbidden keys exist but shell fields are incomplete | Add shell field rejection | unit |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React remains present but not expected for this backend validation story  
**Primary Dependencies**: Pydantic v2, FastAPI request models, SQLAlchemy async test fixtures, existing task template catalog/save services, pytest  
**Storage**: Existing task step template tables only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` for focused service coverage, then `./tools/test_unit.sh` for final verification when feasible  
**Integration Testing**: Existing async service tests cover the API/service boundary for create/save/expand without compose; hermetic compose integration is not required because no external services or workflow contracts change  
**Target Platform**: MoonMind API service and Mission Control task template persistence boundary  
**Project Type**: Backend service with API schema boundary  
**Performance Goals**: Validation remains linear in number of template steps and does not introduce network calls  
**Constraints**: Preserve checked-in seed template compatibility; do not introduce hidden fallbacks for Tool semantics; reject unsupported Step Type values fail-fast  
**Scale/Scope**: One executable step validation story for MM-557

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses task template service boundaries and typed tool concepts.
- II. One-Click Agent Deployment: PASS. No deployment changes.
- III. Avoid Vendor Lock-In: PASS. Step validation is provider-neutral.
- IV. Own Your Data: PASS. Uses existing local template persistence.
- V. Skills Are First-Class and Easy to Add: PASS. Skill remains a first-class executable Step Type.
- VI. Scientific Method: PASS. Test-first validation is planned.
- VII. Runtime Configurability: PASS. No hardcoded provider configuration added.
- VIII. Modular and Extensible Architecture: PASS. Changes stay within task template schema/service boundaries.
- IX. Resilient by Default: PASS. Invalid executable steps fail before execution.
- X. Facilitate Continuous Improvement: PASS. Tests and verification preserve evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, and tasks precede implementation.
- XII. Canonical Documentation Separation: PASS. Runtime work remains in specs/code/tests.
- XIII. Pre-release Compatibility Policy: PASS. Unsupported explicit Step Type values fail fast; legacy skill-shaped templates remain validated as existing Skill data rather than translated into Tool semantics.

## Project Structure

### Documentation (this feature)

```text
specs/277-validate-tool-skill-executable-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── executable-step-validation.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/schemas.py
api_service/services/task_templates/catalog.py
api_service/services/task_templates/save.py
tests/unit/api/test_task_step_templates_service.py
```

**Structure Decision**: The task template API/service boundary is the narrowest runtime surface where authored executable steps are persisted, saved from drafts, expanded from presets, and validated before execution.

## Complexity Tracking

No constitution violations.
