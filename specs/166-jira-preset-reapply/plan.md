# Implementation Plan: Jira Preset Reapply Signaling

**Branch**: `166-jira-preset-reapply` | **Date**: 2026-04-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/166-jira-preset-reapply/spec.md`

## Summary

Add runtime Create page safety signaling for Jira imports that interact with applied task presets. The implementation will preserve the existing Jira browser and import paths, mark applied preset instructions as needing explicit reapply when Jira changes them, avoid hidden rewrites of expanded preset steps, and warn when a Jira import targets a still-template-bound step that will become manually customized.

## Technical Context

**Language/Version**: TypeScript with React 19 for Mission Control UI; Python 3.12 backend remains unchanged for this phase.  
**Primary Dependencies**: React, TanStack Query, Vite/Vitest, Testing Library; existing Create page preset, Jira browser, and step update state.  
**Storage**: Client-side Create page draft state only. No persisted task payload, Jira provenance, or backend storage changes.  
**Testing**: Vitest/Testing Library for Create page behavior; frontend typecheck; repo unit wrapper for dashboard and full unit verification.  
**Target Platform**: Mission Control Create Task page served by MoonMind API and rendered in browser.  
**Project Type**: Web application feature in an existing frontend entrypoint with no new backend endpoint.  
**Performance Goals**: Reapply and conflict signaling must be synchronous UI-state updates with no additional network requests after existing Jira issue detail loading.  
**Constraints**: Jira remains optional and additive; manual task creation must continue if Jira fails; browser clients must use MoonMind-owned Jira data only; task submission payload shape must remain unchanged; selected orchestration mode is runtime, so deliverables must include production code and validation tests.  
**Scale/Scope**: One shared Jira browser, one selected import target at a time, one preset reapply-needed state, and one template-bound step warning path.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature adjusts Create page authoring behavior and does not introduce a new agent runtime or cognitive engine.
- **II. One-Click Agent Deployment**: PASS. Jira remains optional and feature-gated; no new mandatory service or secret is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Jira remains an optional instruction source copied into MoonMind-native task fields.
- **IV. Own Your Data**: PASS. Imported text remains in operator-controlled MoonMind draft/task data; no browser-to-Jira credential path is introduced.
- **V. Skills Are First-Class and Easy to Add**: PASS. Step instructions and skill controls remain the canonical execution units; the feature does not create a parallel skill model.
- **VI. Design for Deletion / Thick Contracts**: PASS. The feature is contained to explicit UI state transitions and documented by a small UI action contract.
- **VII. Powerful Runtime Configurability**: PASS. Jira controls continue to render only through the existing runtime config gate.
- **VIII. Modular and Extensible Architecture**: PASS. Work stays in the Create page UI boundary and reuses existing preset and step update behavior.
- **IX. Resilient by Default**: PASS. Jira failures remain local to the browser and do not block manual authoring or Create.
- **X. Facilitate Continuous Improvement**: PASS. Validation tests capture the exact reapply and conflict signaling regressions this feature prevents.
- **XI. Spec-Driven Development**: PASS. This plan is derived from `spec.md` and will be followed by generated tasks before implementation.
- **XII. Canonical Documentation Separation**: PASS. No canonical docs are changed; this plan stays under `specs/`.
- **XIII. Pre-Release Velocity**: PASS. Implementation should update the live Create page behavior directly without compatibility aliases or obsolete duplicate UI paths.

## Project Structure

### Documentation (this feature)

```text
specs/166-jira-preset-reapply/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-jira-preset-reapply.yaml
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx
```

**Structure Decision**: Implement the runtime behavior in `frontend/src/entrypoints/task-create.tsx` because the Create page already owns Jira browser state, preset objective state, applied preset state, step state, and template-detachment behavior. Add validation in `frontend/src/entrypoints/task-create.test.tsx`; no backend router or runtime config changes are planned for this phase.

## Phase 0 Research

See [research.md](./research.md).

## Phase 1 Design

See [data-model.md](./data-model.md), [quickstart.md](./quickstart.md), and [contracts/create-page-jira-preset-reapply.yaml](./contracts/create-page-jira-preset-reapply.yaml).

## Post-Design Constitution Check

- **Runtime intent**: PASS. The design requires production Create page code changes and validation tests.
- **Submission compatibility**: PASS. The task submission contract remains unchanged; only authored field text and UI state change.
- **Security boundary**: PASS. Jira credentials and raw Jira APIs remain outside the browser; the feature uses existing MoonMind Jira browser data.
- **Failure isolation**: PASS. Jira browser failures remain local and manual task creation remains available.
- **Spec traceability**: PASS. Each functional requirement maps to reapply signaling, explicit reapply action state, template-bound warning, import permission, or validation coverage.

## Complexity Tracking

No constitution violations require justification.
