# Implementation Plan: Preview and Apply Preset Steps Into Executable Steps

**Branch**: `284-preview-apply-preset-executable-steps` | **Date**: 2026-04-29 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `/specs/284-preview-apply-preset-executable-steps/spec.md`

## Summary

MM-565 requires Preset steps to be selected from the step editor, previewed deterministically, applied into editable executable Tool and Skill steps, and rejected at submission time when unresolved. Repo inspection shows the related MM-558 implementation already added Create page preset preview/apply behavior in `frontend/src/entrypoints/task-create.tsx` and focused Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx`. This MM-565 plan preserves the newer Jira source request and treats delivery as verification-focused, with a contingency to patch the Create page if tests expose drift.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `task-create.tsx` renders Step Type `Preset` controls; tests cover step-editor preset preview/apply. | no code change planned | focused frontend unit/integration |
| FR-002 | implemented_verified | Preview uses the existing expand path and surfaces expand failures. | no code change planned | focused frontend unit/integration |
| FR-003 | implemented_verified | Preview failure test confirms failed expansion leaves draft unchanged before apply. | no code change planned | focused frontend unit/integration |
| FR-004 | implemented_verified | Preview state renders expansion warnings before apply. | no code change planned | focused frontend unit/integration |
| FR-005 | implemented_verified | Preview UI lists generated step titles and Step Types. | no code change planned | focused frontend unit/integration |
| FR-006 | implemented_verified | Apply preview replaces selected Preset placeholder with generated steps. | no code change planned | focused frontend unit/integration |
| FR-007 | implemented_verified | Applied generated steps remain editable in tests. | no code change planned | focused frontend unit/integration |
| FR-008 | implemented_verified | Generated executable Tool binding is preserved and submitted after apply. | no code change planned | focused frontend unit/integration |
| FR-009 | implemented_verified | Create submission blocks unresolved Preset steps. | no code change planned | focused frontend unit/integration |
| FR-010 | implemented_verified | Tests verify step-editor preset use without Task Presets management apply flow. | no code change planned | focused frontend unit/integration |
| FR-011 | implemented_unverified | Existing stale-preset/reapply tests cover explicit reapply messaging, but MM-565-specific update preview is not isolated. | verify existing reapply tests and record residual risk if no gap is exposed | focused frontend unit |
| SC-001 | implemented_verified | Focused Create page tests cover selecting Step Type `Preset`, configuring a preset, previewing generated steps, and applying expansion. | no code change planned | focused frontend unit/integration |
| SC-002 | implemented_verified | Focused Create page tests cover failed preset expansion and unresolved Preset submission blocking. | no code change planned | focused frontend unit/integration |
| SC-003 | implemented_verified | Focused Create page tests assert generated step titles, Step Types, and expansion warnings are visible before apply. | no code change planned | focused frontend unit/integration |
| SC-004 | implemented_verified | Focused Create page tests verify applied Tool steps submit with executable binding and generated steps remain editable. | no code change planned | focused frontend unit/integration |
| SC-005 | implemented_verified | Focused Create page tests verify step-editor preset use does not require Task Presets management. | no code change planned | focused frontend unit/integration |
| SC-006 | implemented_unverified | Existing stale/reapply messaging tests cover explicit reapply/update behavior; version-update-specific preview is adjacent existing behavior. | verify existing coverage and keep residual risk visible | focused frontend unit |
| DESIGN-REQ-006 | implemented_verified | Preset is an authoring-time state that can preview and apply. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-007 | implemented_verified | Preset use lives in the step editor. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-010 | implemented_verified | Preview before apply and replacement are implemented. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-011 | implemented_verified | Applied preset-generated Tool steps submit as executable Tool steps. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-017 | implemented_unverified | Validation/warnings/blocking are covered; explicit newer-version preview remains partially evidenced by stale reapply tests. | verify and document any remaining gap | focused frontend unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected to change for this story  
**Primary Dependencies**: React, TanStack Query, existing task-template catalog/detail/expand endpoints, Vitest and Testing Library  
**Storage**: Existing task draft state only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for focused managed runner; `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` for direct Vitest iteration  
**Integration Testing**: Create page Vitest render/submission coverage is the story integration boundary because it exercises UI state, mocked task-template API calls, and submit payload construction  
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend in the existing repository  
**Performance Goals**: Preview/apply reuses one explicit expansion request per preview and avoids background expansion on selection  
**Constraints**: Preserve existing task-template expansion endpoint, generated step mapping, and separation between preset management and preset use; unresolved Preset steps must not reach executable submission by default  
**Scale/Scope**: One task-authoring story for MM-565 focused on Create page Preset preview/apply behavior

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing preset expansion and task authoring contracts.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. Preset behavior is provider-neutral.
- IV. Own Your Data: PASS. Uses local draft state and MoonMind-owned preset APIs.
- V. Skills Are First-Class and Easy to Add: PASS. Generated Skill steps remain first-class editable steps.
- VI. Scientific Method: PASS. Existing red-first MM-558 tests are reused as evidence; MM-565 verification reruns focused tests.
- VII. Runtime Configurability: PASS. No hardcoded provider/runtime behavior added.
- VIII. Modular and Extensible Architecture: PASS. Behavior remains within existing Create page and task-template boundaries.
- IX. Resilient by Default: PASS. Unresolved preset submission is blocked before workflow execution.
- X. Facilitate Continuous Improvement: PASS. MM-565 artifacts preserve source traceability and verification evidence.
- XI. Spec-Driven Development: PASS. MM-565 spec and plan precede any MM-565 code changes.
- XII. Canonical Documentation Separation: PASS. Runtime implementation artifacts stay under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms planned.

## Project Structure

### Documentation (this feature)

```text
specs/284-preview-apply-preset-executable-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-preset-executable-steps.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
```

**Structure Decision**: This is primarily a frontend Create page story. Existing backend task-template expand services are reused; backend tests are only needed if focused verification exposes a backend contract gap.

## Complexity Tracking

No constitution violations.
