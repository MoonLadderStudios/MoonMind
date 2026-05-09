# Implementation Plan: Preview and Apply Preset Steps

**Branch**: `run-jira-orchestrate-for-mm-572-preview-d49b5fcf` | **Date**: 2026-05-09 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story Jira Orchestrate handoff for `MM-572` / `STORY-004`

## Summary

`MM-572` asks Jira Orchestrate to handle the "Preview and apply Preset steps" story from the `manual-mm-569-mm-574` source set. This current task is the task creation/handoff step, so implementation is intentionally not run inline. The feature artifacts preserve the `MM-572` source and define the downstream runtime work that the existing Jira Orchestrate/MoonSpec workflow should resume.

Prior related MoonSpec features exist for this product area:

- `specs/278-preview-apply-preset-steps` (`MM-558`)
- `specs/284-preview-apply-preset-executable-steps` (`MM-565`)
- `specs/291-preview-apply-preset-steps` (`MM-578`)

Those artifacts may provide implementation evidence when the downstream implementation/verification step is authorized, but they do not replace this `MM-572` traceability record.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | pending_downstream_verification | `docs/Steps/StepTypes.md` defines `preset` as a Step Type. | Verify/create step-editor Preset selection. | Focused Create page tests |
| FR-002 | pending_downstream_verification | Existing task-template services are expected to own detail/expand validation. | Verify preset existence/version/input validation. | Focused UI/API tests as needed |
| FR-003 | pending_downstream_verification | Design requires deterministic expansion preview. | Verify generated title/type/warning preview. | Focused Create page tests |
| FR-004 | pending_downstream_verification | Design requires apply to replace Preset placeholder. | Verify replacement with concrete Tool/Skill steps. | Focused Create page tests |
| FR-005 | pending_downstream_verification | Design requires generated steps to remain ordinary editable steps. | Verify editability after apply. | Focused Create page tests |
| FR-006 | pending_downstream_verification | Design requires unresolved Preset steps not to execute by default. | Verify unresolved submission block and generated step validation. | Focused Create page tests |
| FR-007 | pending_downstream_verification | Design separates management from use. | Verify Presets management is not required for authoring apply. | Focused Create page tests |
| FR-008 | pending_downstream_verification | Design requires visible validation feedback. | Verify failed preview/apply leaves draft unchanged. | Focused Create page tests |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present in the repository  
**Primary Dependencies**: React, TanStack Query, existing task-template catalog/detail/expand endpoints, Vitest and Testing Library  
**Storage**: Existing task draft and submission payload state only; no new persistent storage planned  
**Unit Testing**: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: Create page render/submission tests exercise the UI state, mocked task-template API calls, and submit payload boundary  
**Target Platform**: Mission Control web UI  
**Project Type**: Web application frontend in the existing repository  
**Constraints**: Preserve task-template expansion endpoint semantics, generated step mapping, and separation between preset management and preset use  
**Current Step Boundary**: Handoff/spec artifact creation only; no implementation, Jira transitions, PR creation, or publish work in this step

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. This handoff points to existing Jira Orchestrate and task-template expansion flows.
- II. One-Click Agent Deployment: PASS. No deployment prerequisite changes.
- III. Avoid Vendor Lock-In: PASS. Preset behavior is provider-neutral.
- IV. Own Your Data: PASS. Uses repo-local spec artifacts and MoonMind-owned task state.
- V. Skills Are First-Class and Easy to Add: PASS. Generated Skill steps remain first-class executable steps.
- VI. Scientific Method: PASS. Downstream work is framed as testable verification and implementation.
- VII. Runtime Configurability: PASS. No hardcoded runtime/provider behavior added.
- VIII. Modular and Extensible Architecture: PASS. Work stays within Create page/task-template boundaries.
- IX. Resilient by Default: PASS. Runtime execution of unresolved Preset placeholders is explicitly disallowed.
- X. Facilitate Continuous Improvement: PASS. The handoff records source and next action.
- XI. Spec-Driven Development: PASS. `MM-572` spec and plan precede implementation.
- XII. Canonical Documentation Separation: PASS. Runtime delivery artifacts stay under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden transforms are introduced.

## Project Structure

```text
specs/331-preview-apply-preset-steps-mm-572/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-preset-preview-apply.md
├── tasks.md
├── checklists/
│   └── requirements.md
└── verification.md
```

## Complexity Tracking

No constitution violations.

## Current Step Outcome

This step creates `MM-572` MoonSpec/Jira Orchestrate handoff artifacts only. Downstream implementation remains pending for the existing Jira Orchestrate workflow.
