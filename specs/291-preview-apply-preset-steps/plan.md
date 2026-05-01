# Implementation Plan: Preview and Apply Preset Steps

**Branch**: `291-preview-apply-preset-steps` | **Date**: 2026-05-01 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/291-preview-apply-preset-steps/spec.md`

## Summary

MM-578 requires Preset steps to be selected from the step editor, configured, previewed with generated steps and warnings, applied into editable executable Tool and Skill steps, and rejected at submission time when unresolved. Repo inspection shows related MM-558/MM-565 implementation already added Create page preset preview/apply behavior in `frontend/src/entrypoints/task-create.tsx`; active MM-578 tests in `frontend/src/entrypoints/task-create.test.tsx` now preserve the story-specific evidence. This plan preserves the MM-578 Jira source request and treats delivery as verification-focused, with a contingency to patch the Create page if focused tests expose drift.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `task-create.tsx` renders Step Type `Preset`; active MM-578 tests cover step-editor preset selection and detail loading. | no code change planned | focused frontend unit/integration |
| FR-002 | implemented_verified | Preview uses existing task-template detail/expand paths; active MM-578 tests surface validation and expand failures. | no code change planned | focused frontend unit/integration |
| FR-003 | implemented_verified | Active MM-578 tests verify preview state renders generated step titles, Step Types, and warnings before apply. | no code change planned | focused frontend unit/integration |
| FR-004 | implemented_verified | Active MM-578 tests verify apply preview replaces the selected Preset placeholder with generated steps. | no code change planned | focused frontend unit/integration |
| FR-005 | implemented_verified | Active MM-578 tests verify applied generated steps remain editable. | no code change planned | focused frontend unit/integration |
| FR-006 | implemented_verified | Active MM-578 tests verify generated executable Tool binding is preserved after apply and unresolved Preset submission is blocked. | no code change planned | focused frontend unit/integration |
| FR-007 | implemented_verified | Active MM-578 tests verify step-editor preset use without Task Presets management apply flow. | no code change planned | focused frontend unit/integration |
| FR-008 | implemented_verified | Active MM-578 tests verify failed expansion leaves the draft unchanged with visible error. | no code change planned | focused frontend unit/integration |
| SC-001 | implemented_verified | Active MM-578 Create page tests cover selecting Step Type `Preset`, configuring a preset, previewing generated steps, and applying expansion. | no code change planned | focused frontend unit/integration |
| SC-002 | implemented_verified | Active MM-578 Create page tests cover failed preset expansion, stale detail handling, and unresolved Preset submission blocking. | no code change planned | focused frontend unit/integration |
| SC-003 | implemented_verified | Active MM-578 Create page tests assert generated step titles, Step Types, and expansion warnings are visible before apply. | no code change planned | focused frontend unit/integration |
| SC-004 | implemented_verified | Active MM-578 Create page tests verify applied Tool steps submit with executable binding and generated steps remain editable. | no code change planned | focused frontend unit/integration |
| SC-005 | implemented_verified | Active MM-578 Create page tests verify step-editor preset use does not require Task Presets management. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-004 | implemented_verified | Preset is an authoring-time state that can preview and apply. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-011 | implemented_verified | Preset use lives in the step editor. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-012 | implemented_verified | Preview before apply and replacement are implemented. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-013 | implemented_verified | Validation/warnings/blocking are covered by focused tests. | no code change planned | focused frontend unit/integration |
| DESIGN-REQ-019 | implemented_verified | Preset management is not required for step-editor preset use. | no code change planned | focused frontend unit/integration |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected to change for this story  
**Primary Dependencies**: React, TanStack Query, existing task-template catalog/detail/expand endpoints, Vitest and Testing Library  
**Storage**: Existing task draft state only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx` for the managed frontend unit path; `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` for focused direct Vitest iteration in managed workspaces whose paths may break npm's PATH-based binary lookup
**Integration Testing**: Create page Vitest render/submission coverage is the story integration boundary because it exercises UI state, mocked task-template API calls, and submit payload construction; run it through the same focused Vitest command and the managed dashboard-only wrapper
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend in the existing repository  
**Performance Goals**: Preview/apply reuses one explicit expansion request per preview and avoids background expansion on selection  
**Constraints**: Preserve existing task-template expansion endpoint, generated step mapping, and separation between preset management and preset use; unresolved Preset steps must not reach executable submission by default  
**Scale/Scope**: One task-authoring story for MM-578 focused on Create page Preset preview/apply behavior

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses existing preset expansion and task authoring contracts.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. Preset behavior is provider-neutral.
- IV. Own Your Data: PASS. Uses local draft state and MoonMind-owned preset APIs.
- V. Skills Are First-Class and Easy to Add: PASS. Generated Skill steps remain first-class editable steps.
- VI. Scientific Method: PASS. Existing MM-558/MM-565 red-first coverage established the behavior, and active MM-578 focused tests preserve story-specific evidence.
- VII. Runtime Configurability: PASS. No hardcoded provider/runtime behavior added.
- VIII. Modular and Extensible Architecture: PASS. Behavior remains within existing Create page and task-template boundaries.
- IX. Resilient by Default: PASS. Unresolved preset submission is blocked before workflow execution.
- X. Facilitate Continuous Improvement: PASS. MM-578 artifacts preserve source traceability and verification evidence.
- XI. Spec-Driven Development: PASS. MM-578 spec and plan precede any MM-578 code changes.
- XII. Canonical Documentation Separation: PASS. Runtime implementation artifacts stay under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden semantic transforms planned.

## Project Structure

### Documentation (this feature)

```text
specs/291-preview-apply-preset-steps/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-preset-preview-apply.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
```

**Structure Decision**: This is a frontend Create page story. Existing backend task-template expand services are reused; backend changes are only needed if focused verification exposes a backend contract gap.

## Complexity Tracking

No constitution violations.
