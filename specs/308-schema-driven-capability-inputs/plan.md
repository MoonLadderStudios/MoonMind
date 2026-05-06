# Implementation Plan: Schema-Driven Capability Inputs

**Branch**: `change-jira-issue-mm-593-to-status-in-pr-1cc41602` | **Date**: 2026-05-06 | **Spec**: [specs/308-schema-driven-capability-inputs/spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:f25c322e-d7b5-4f0e-b969-6e1325b0c861/repo/specs/308-schema-driven-capability-inputs/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but refused to run because the active branch is `change-jira-issue-mm-593-to-status-in-pr-1cc41602`, not a numeric MoonSpec branch. `.specify/feature.json` already points to `specs/308-schema-driven-capability-inputs`, so this plan uses that active feature directory directly.

## Summary

Build the schema-driven capability input foundation selected from MM-593: presets and skills expose normalized input contracts, the Create page renders those inputs from schema/UI metadata, `jira.issue-picker` is reusable metadata-driven UI, and validation errors/safe Jira values remain field-addressable and credential-safe. Repo analysis shows existing legacy task-template `inputs` support, governed tool `inputSchema` discovery, and trusted Jira tooling, but the selected story still needs code and tests for normalized preset/skill schemas, UI schema/widget handling, reusable Jira issue input values, and security regression evidence.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Tool discovery exposes inputSchema; task templates expose existing inputs lists; skill selection is mostly string IDs. | add normalized capability input contract for presets and skills, making the schema contract authoritative when present without adding compatibility aliases | unit + integration |
| FR-002 | partial | TaskStepTemplateVersion.inputs_schema stores simplified name/label/type/default fields; no uiSchema/defaults object round-trip. | add lossless schema/ui/default normalization and serialization tests | unit + integration |
| FR-003 | partial | Create page renders legacy template inputs and governed tool schema summaries, but not a shared generated field renderer for preset/skill schemas. | add schema-form renderer for selected preset and skill capability inputs | frontend unit + integration |
| FR-004 | missing | No evidence of generic JSON Schema field rendering for required/properties/items/enum/oneOf/anyOf/format across presets and skills. | implement supported schema subset with fallback behavior | frontend unit |
| FR-005 | partial | Template expansion validates required legacy inputs; no generic schema path field-addressable validation for nested fields. | add field-path validation model and frontend error mapping | unit + frontend integration |
| FR-006 | missing | No allowlisted widget registry for capability schemas; existing preset fields are type-specific legacy inputs. | add reusable widget registry keyed by widget metadata | frontend unit |
| FR-007 | missing | No jira.issue-picker widget selected from uiSchema or x-moonmind-widget. | add Jira issue picker widget registration and metadata resolution | frontend unit + integration |
| FR-008 | partial | Jira issue keys appear as text inputs in current Jira presets; no reusable picker value object. | add durable Jira issue input value shape and manual-entry preservation | frontend unit + integration |
| FR-009 | partial | Trusted Jira tools exist; schema-driven Jira input validation/enrichment is not attached to capability inputs. | add validation/enrichment service boundary using trusted Jira tooling or explicit deferred validation contract | unit + integration |
| FR-010 | missing | Tests currently mock specific preset inputs and legacy labels; adding supported schema fields still requires bespoke test setup. | prove a new capability renders from schema without capability-specific Create-page code | frontend integration |
| FR-011 | implemented_unverified | Jira tool auth is trusted-server-side; no story-specific regression proves schema defaults/logs/artifacts omit secrets. | add security regression coverage for schema defaults and safe values | unit + integration |
| FR-012 | partial | Jira tool responses are sanitized but capability input value contract is not defined for presets/skills. | define and validate safe Jira issue value contract | unit + integration |
| FR-013 | missing | No tests cover the full selected story across preset, skill, Jira widget, validation, unsupported widget, and new-capability behavior. | write required red-first tests before implementation | unit + integration |
| SCN-001 | partial | Legacy preset fields render from template inputs. | verify schema-driven preset field rendering and fill gaps | frontend integration |
| SCN-002 | partial | Skill selector exists; skill input schema rendering is not present. | verify skill schema rendering and fill gaps | frontend integration |
| SCN-003 | missing | No metadata-driven Jira widget. | test uiSchema and x-moonmind-widget select the Jira picker | frontend unit |
| SCN-004 | partial | Required legacy inputs are handled; nested field-addressable schema errors are missing. | test field-level blocking and preservation | unit + integration |
| SCN-005 | partial | Manual Jira key text fields exist in specific presets. | test manual entry in reusable Jira issue value object | frontend unit |
| SCN-006 | missing | No generic new-capability test. | add fixture capability with supported schema and no special branches | frontend integration |
| SC-001 | partial | Specific preset input tests exist. | verify schema-based seeded preset path | frontend integration |
| SC-002 | missing | No direct skill schema input rendering test. | add direct skill schema rendering test | frontend integration |
| SC-003 | missing | No metadata-based Jira widget selection. | add metadata-only widget selection tests | frontend unit |
| SC-004 | partial | Legacy required input behavior exists. | add schema field path blocking tests | unit + integration |
| SC-005 | partial | Text input preservation exists in drafts generally. | add Jira outage/manual-entry preservation test | frontend unit |
| SC-006 | missing | No new capability generic rendering proof. | add schema fixture test | frontend integration |
| SC-007 | implemented_unverified | Secret handling exists around trusted Jira tools; not schema default path. | add explicit secret-safety regression | unit + integration |
| DESIGN-REQ-001 | partial | Tool schema and legacy template inputs exist; normalized preset/skill contract does not. | add normalized capability input contract | unit + integration |
| DESIGN-REQ-002 | missing | No generic schema-form renderer for presets/skills. | add supported schema renderer | frontend unit + integration |
| DESIGN-REQ-003 | missing | No reusable widget registry for capability schemas. | add allowlisted widget registry | frontend unit |
| DESIGN-REQ-004 | partial | Jira keys are text inputs; no reusable picker. | add reusable Jira issue picker contract and UI behavior | frontend unit + integration |
| DESIGN-REQ-005 | partial | Trusted Jira tooling exists and Jira responses are sanitized, but the capability input value contract is not yet defined for presets and skills. | define the safe Jira issue value shape and validation/enrichment boundary | unit + integration |
| DESIGN-REQ-006 | partial | Legacy required validation exists; nested schema field paths missing. | add field-addressable validation shape and mapping | unit + integration |
| DESIGN-REQ-007 | implemented_unverified | Trusted Jira auth is server-side; no story-specific regression proves schema defaults, draft values, logs, artifacts, or agent-visible payloads omit credentials and secret-like values. | add explicit credential-isolation and secret-default regression coverage | unit + integration |
| DESIGN-REQ-008 | missing | Required story test suite absent. | write tests specified by quickstart | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Create page  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, React, TanStack Query, Vitest/Testing Library, pytest  
**Storage**: Existing `task_step_template_versions.inputs_schema` JSON and task draft/submission payloads; no new persistent tables planned  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- <pytest targets>` for Python; `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` or `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` for focused frontend  
**Integration Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for required local verification; `./tools/test_integration.sh` for hermetic integration if backend API/seed behavior changes require compose-backed verification  
**Target Platform**: MoonMind web control plane and Mission Control Create page  
**Project Type**: Full-stack web application with Temporal-backed task execution and managed runtime integrations  
**Performance Goals**: Capability schema rendering should add no blocking network calls beyond existing template/detail lookups; common Create-page interactions remain responsive for normal task drafts  
**Constraints**: No raw Jira credentials in schema defaults, logs, artifacts, workflow payloads, or agent-visible content; backend validation remains authoritative; no new persistent storage unless existing JSON payloads cannot safely represent the contract  
**Scale/Scope**: One independently testable story covering capability input contracts, schema rendering, widget registry, Jira issue input, and validation/security tests; preview/apply, recursive expansion, provenance, and submit auto-expansion stay in existing/later specs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses existing Create-page/control-plane contracts and trusted Jira tools; no new agent cognition layer. |
| II. One-Click Agent Deployment | PASS | Uses existing local frontend/backend test tooling and no required external SaaS for CI paths. |
| III. Avoid Vendor Lock-In | PASS | Jira is a reusable widget/integration; generic schema renderer applies to presets and skills beyond Jira. |
| IV. Own Your Data | PASS | Stores only safe input values and sanitized context in operator-controlled payloads. |
| V. Skills Are First-Class and Easy to Add | PASS | Aligns skill and preset input schemas so skills can declare inputs without custom UI. |
| VI. Replaceable Scaffolding | PASS | Adds thin contracts and tests around declarative schemas and widgets. |
| VII. Runtime Configurability | PASS | Capability behavior is metadata/config driven, not hardcoded constants. |
| VIII. Modular and Extensible Architecture | PASS | Work is bounded to catalog/input contracts, Create-page renderer/widget registry, and trusted Jira validation boundaries. |
| IX. Resilient by Default | PASS | Field-addressable validation and safe fallbacks keep drafts recoverable on lookup or validation failure. |
| X. Facilitate Continuous Improvement | PASS | Plan preserves verification evidence and clear follow-on task generation. |
| XI. Spec-Driven Development | PASS | `spec.md`, `plan.md`, and design artifacts exist before implementation. |
| XII. Canonical Documentation Separation | PASS | Migration/task details remain under `specs/308-*`; canonical docs are only source references. |

## Project Structure

### Documentation (this feature)

```text
specs/308-schema-driven-capability-inputs/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── capability-input-contract.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/task_step_templates.py
├── data/task_step_templates/*.yaml
├── db/models.py
└── services/task_templates/catalog.py

frontend/src/
└── entrypoints/
    ├── task-create.tsx
    └── task-create.test.tsx

moonmind/
├── integrations/jira/
├── mcp/jira_tool_registry.py
└── workflows/skills/

tests/
├── integration/test_startup_task_template_seeding.py
├── unit/api/test_task_step_templates_service.py
├── unit/integrations/test_jira_tool_service.py
└── unit/mcp/test_jira_tool_registry.py
```

**Structure Decision**: Use the existing full-stack Create-page and task-template catalog structure. Add backend contract normalization and validation near `api_service/services/task_templates/catalog.py` and `api_service/api/routers/task_step_templates.py`; add frontend schema rendering and widgets in `frontend/src/entrypoints/task-create.tsx` or extracted colocated helpers only if needed to keep the file maintainable; add tests beside existing task-template and Create-page tests.

## Complexity Tracking

No constitution violations are planned.
