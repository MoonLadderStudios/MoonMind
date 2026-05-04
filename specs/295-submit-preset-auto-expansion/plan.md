# Implementation Plan: Submit Preset Auto-Expansion

**Branch**: `295-submit-preset-auto-expansion` | **Date**: 2026-05-04 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/295-submit-preset-auto-expansion/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not be used because the current git branch is `apply-these-document-updates-6acb6f86`, which does not match the repository's Spec Kit feature-branch naming guard. This plan uses `.specify/feature.json` and the active feature directory as the source of truth.

## Summary

Implement the Create-page submit-time Preset auto-expansion path described by `docs/UI/CreatePage.md`: when the user explicitly clicks Create, Update, or Rerun with unresolved Preset steps, the page freezes a submission copy, expands Presets in authored order through the existing task-template expansion endpoint, replaces placeholders with generated executable Tool/Skill steps, validates the final executable-only payload, and submits normally. Current repo evidence shows manual Preset Expand/Apply and flat executable submission are present, and task contract validation already rejects non-executable `preset` step types. Missing work is the guarded submit-time convenience path, stale/duplicate response protection, non-mutating failure behavior, and targeted frontend plus task-contract regression coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | `frontend/src/entrypoints/task-create.tsx` currently returns "Expand Preset steps before submitting." for primary Preset steps and additional Preset steps. | Add submit-time expansion before payload construction for Create, Update, and Rerun. | unit + integration |
| FR-002 | missing | `handleSubmit` has one submit guard, but no expansion phase inside the submit attempt. | Model expansion and final submission as one guarded submit attempt with progress status. | unit + integration |
| FR-003 | implemented_unverified | Preset selection/detail loading paths do not call create endpoints in current code; tests cover manual expansion, not submit auto-expansion. | Add regression tests proving non-submit interactions do not create tasks or auto-expand. | unit |
| FR-004 | partial | Backend `TaskStepSpec` rejects `type: "preset"`; manual Apply submits Tool/Skill steps. Submit path still blocks unresolved Presets instead of expanding them. | Build final payload from expanded executable submission copy and assert no unresolved Preset payload remains. | unit + integration |
| FR-005 | partial | `expandPresetForDraft` and `applyPresetExpansionToDraft` provide manual semantics. No submit-time reuse exists. | Reuse or factor shared expansion mapping so submit-time output is contract-equivalent to manual Apply. | unit |
| FR-006 | partial | Manual expansion sends selected slug/version inputs and context. Submit-time intent and current submission context are not represented. | Submit unresolved Presets using selected key/version, current inputs, task context, attachment refs when available, and submit intent. | unit |
| FR-007 | implemented_unverified | Existing template lookup is key-driven and scoped, but no auto-submit test proves no inference from objective text. | Add tests that expansion uses authored Preset key and does not infer alternate presets. | unit |
| FR-008 | missing | Manual Apply replaces one selected Preset; no submit-time multi-Preset replacement exists. | Expand unresolved Presets in step order and splice generated steps into the frozen submission copy. | unit + integration |
| FR-009 | missing | Manual Apply mutates visible draft; submit path does not create a separate expanded copy. | Introduce frozen submission-copy flow that leaves visible draft unchanged unless submit succeeds or user manually applies. | unit |
| FR-010 | missing | `StepState` lacks submit-expansion transient state. | Add transient submit expansion status outside submitted task snapshot and avoid edit/rerun reconstruction dependency. | unit |
| FR-011 | implemented_verified | `frontend/src/entrypoints/task-create.test.tsx` covers manual Expand and editable generated steps. | Preserve manual behavior while adding submit-time path. | final regression |
| FR-012 | partial | Manual Apply preserves `source` provenance; task contract validates provenance without live lookup. Submit-time path missing. | Preserve provenance from expansion response when creating submit copy. | unit + integration |
| FR-013 | partial | Manual Apply handles generated steps and publish constraints through effective skill/template state; submit-time handling missing. | Apply warnings, capabilities, attachment mappings, and publish/merge constraints before final payload validation. | unit + integration |
| FR-014 | partial | Manual expansion failure is non-mutating; submit currently blocks unresolved Presets. | Block submission with Preset-scoped error when submit-time expansion fails or is ambiguous, with no side effect. | unit + integration |
| FR-015 | partial | `isSubmitting` guards final submission, but expansion has no request identity or stale-response handling. | Add request id or equivalent guard for duplicate clicks, cancellation, and stale expansion responses. | unit |
| FR-016 | partial | Attachment uploads and task attachment refs exist; no Preset retargeting rule exists for auto-expansion. | Resolve needed attachments before expansion and block ambiguous retargeting for manual review. | unit + integration |
| FR-017 | implemented_verified | `TaskStepSpec` rejects non-executable step types; `test_task_steps_reject_non_executable_step_types` covers `preset`. | Keep authoritative rejection intact. | final regression |
| FR-018 | missing | No expansion-success/final-submit-failure path currently exists for unresolved Presets. | Preserve original draft and optionally expose expanded review state after final submission failure. | unit |
| FR-019 | partial | Existing tests cover manual Expand/Apply, flat executable submission, and contract rejection. Missing submit-time scenarios. | Add focused frontend and task-contract tests for all listed cases. | unit + integration |
| SCN-001 | missing | Submit currently blocks unresolved Presets before create/update/rerun payload construction. | Add auto-expansion create/update/rerun submit path. | integration |
| SCN-002 | missing | No submit-time multi-Preset expansion exists. | Expand unresolved Presets in authored order and splice generated steps in relative position. | unit + integration |
| SCN-003 | partial | Manual expansion preserves provenance and warnings; submit-time path missing. | Preserve provenance and non-blocking warnings in submit feedback. | unit + integration |
| SCN-004 | partial | Manual expansion failure preserves draft; submit-time failure path missing. | Block side effects and show Preset-scoped error during submit-time failure. | integration |
| SCN-005 | implemented_unverified | Non-submit interactions appear non-submitting, but no submit-auto-expansion regression covers them. | Add verification test and conditional fallback guardrail if it fails. | unit |
| SCN-006 | partial | Existing attachment and publish/merge machinery exists; auto-expansion ambiguity handling missing. | Block ambiguous attachment retargeting and apply constraints before final validation. | unit + integration |
| SCN-007 | missing | No expansion-success/final-submit-failure path exists for unresolved Presets. | Preserve original draft and optionally expose expanded review copy on final submission failure. | unit + integration |
| SC-001 | missing | No successful unresolved-Preset submit path exists. | Verify successful submissions contain zero unresolved Presets. | integration |
| SC-002 | partial | Manual expansion failure preserves draft; submit-time failure path missing. | Verify failed auto-expansion creates no side effect and preserves draft. | integration |
| SC-003 | missing | No multi-Preset submit auto-expansion exists. | Verify three unresolved Presets preserve relative order after expansion. | unit |
| SC-004 | partial | Existing `isSubmitting` prevents duplicate final submissions after submit begins. Expansion-specific duplicate handling missing. | Verify duplicate submit clicks during expansion produce one side effect. | unit |
| SC-005 | partial | Some manual and backend cases exist. | Add coverage for create, update/rerun, failure, stale/cancel, attachment ambiguity, and authoritative rejection. | unit + integration |
| DESIGN-REQ-001 | partial | Manual Apply and backend flat executable behavior exist. Submit-time convenience missing. | Implement executable submission-copy expansion. | unit + integration |
| DESIGN-REQ-002 | missing | Submit currently validates/uploads/submits but does not auto-expand unresolved Presets. | Insert expansion after explicit submit click and before final payload construction. | unit + integration |
| DESIGN-REQ-003 | missing | No submit-expansion state exists. | Add transient state only. | unit |
| DESIGN-REQ-004 | partial | Manual expansion exists and must remain. | Share semantics with submit path. | unit |
| DESIGN-REQ-005 | partial | Existing expansion is scoped and key-driven. | Ensure submit path uses authored key/version/context only. | unit |
| DESIGN-REQ-006 | missing | No frozen submit copy or multi-Preset expansion. | Implement ordered non-mutating expansion copy. | unit + integration |
| DESIGN-REQ-007 | partial | Some publish/merge and attachment machinery exists. | Add auto-expansion warning, mapping, duplicate, and stale handling. | unit + integration |
| DESIGN-REQ-008 | partial | Final executable payload validation exists for applied steps. | Ensure auto-expanded submit copy follows same shape. | unit + integration |
| DESIGN-REQ-009 | missing | Canonical submit flow does not include Preset expansion. | Add full flow from freeze through final submit. | integration |
| DESIGN-REQ-010 | partial | Backend rejection exists; cancellation/stale and draft preservation missing. | Keep rejection and add stale/cancel/failure behavior. | unit |
| DESIGN-REQ-011 | partial | Manual preview failure copy exists. Submit failure copy missing. | Add Preset-scoped submit expansion error copy. | unit |
| DESIGN-REQ-012 | partial | Existing tests cover manual expansion/apply and provenance. | Add submit-time tests. | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: React, TanStack Query, FastAPI, Pydantic v2, existing task-template expansion service, existing Temporal task contract models  
**Storage**: Existing task input snapshots and artifact store only; no new persistent storage  
**Unit Testing**: Vitest for Create-page behavior; pytest for task contract validation  
**Integration Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for frontend scenario coverage; `./tools/test_integration.sh` for required hermetic integration_ci validation if backend/API boundaries change  
**Target Platform**: Mission Control web UI served by the MoonMind API service  
**Project Type**: Web application plus API-backed task submission surface  
**Performance Goals**: One explicit submit attempt should perform at most one expansion request per unresolved Preset and one final create/update/rerun side effect  
**Constraints**: No unresolved Preset step may reach final task execution; submit remains explicit; no direct browser calls to third-party providers; no new live preset runtime mode; preserve manual Preview/Apply  
**Scale/Scope**: One Create-page story covering unresolved Preset submission for create, update, and rerun modes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Plan Alignment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses existing MoonMind Preset expansion and task submission contracts rather than creating agent behavior. |
| II. One-Click Agent Deployment | PASS | No new external service or setup prerequisite. |
| III. Avoid Vendor Lock-In | PASS | Preset expansion remains MoonMind template behavior, not provider-specific logic. |
| IV. Own Your Data | PASS | Uses existing local artifact/task snapshot model and MoonMind APIs. |
| V. Skills Are First-Class and Easy to Add | PASS | Preserves Tool/Skill executable step model and Preset provenance. |
| VI. Replaceable Scaffolding | PASS | Factors submit-time convenience around existing contracts and tests. |
| VII. Runtime Configurability | PASS | No new hardcoded deployment setting; uses existing runtime/task context. |
| VIII. Modular and Extensible Architecture | PASS | Keeps changes in Create-page submission shaping and existing task-template/task-contract boundaries. |
| IX. Resilient by Default | PASS | Duplicate, stale, failed, and ambiguous expansion cases are explicit gates. |
| X. Facilitate Continuous Improvement | PASS | Submit feedback preserves recoverable errors and final outcomes. |
| XI. Spec-Driven Development | PASS | This plan follows the single-story spec and preserves traceability. |
| XII. Canonical Documentation Separation | PASS | Runtime implementation work stays in spec artifacts, with `docs/UI/CreatePage.md` used as source design. |
| XIII. Pre-Release Velocity | PASS | No compatibility aliases or linked-live Preset mode will be introduced. |
| Product and Operational Constraints | PASS | No secrets are introduced; Mission Control-visible errors are required. |

Post-design re-check: PASS. The Phase 1 artifacts below preserve the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/295-submit-preset-auto-expansion/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-submit-preset-auto-expansion.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

frontend/src/lib/
└── temporalTaskEditing.ts

moonmind/workflows/tasks/
└── task_contract.py

tests/unit/workflows/tasks/
└── test_task_contract.py

api_service/api/routers/
├── task_step_templates.py
└── executions.py
```

**Structure Decision**: Implement Create-page behavior in `frontend/src/entrypoints/task-create.tsx`, add Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx`, and preserve authoritative task-contract rejection in `moonmind/workflows/tasks/task_contract.py` with pytest coverage in `tests/unit/workflows/tasks/test_task_contract.py`. API router/service files are existing integration surfaces for expansion and submission; they should change only if frontend planning exposes a contract gap.

## Complexity Tracking

No constitution violations or justified complexity exceptions.
