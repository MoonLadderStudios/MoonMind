# Implementation Plan: Temporal Task Draft Reconstruction

**Branch**: `161-temporal-task-drafts` | **Date**: 2026-04-12 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/work/agent_jobs/mm:e407d769-fdd6-4c71-907a-f2134715e8ed/repo/specs/161-temporal-task-drafts/spec.md`

## Summary

Implement the runtime Phase 2 slice for Temporal-native task editing by making the shared task submission page mode-aware and able to reconstruct a reviewable draft from a supported `MoonMind.Run` execution. The implementation extends the existing Mission Control create-task surface, reuses the execution detail read contract and capability flags added in the previous phase, centralizes route/mode/draft helpers, reads immutable historical input artifacts when needed, and fails closed for unsupported workflow types, missing capabilities, unreadable artifacts, and incomplete drafts. Submission remains out of scope for this phase except for preventing edit/rerun modes from accidentally using create or queue-era behavior.

## Technical Context

**Language/Version**: TypeScript/React frontend, Python 3.12 backend schemas/API contracts  
**Primary Dependencies**: React, TanStack Query, Vitest, FastAPI/Pydantic execution models, Temporal execution read APIs, artifact download APIs  
**Storage**: Existing Temporal execution projections and immutable artifact storage; no new persistence tables  
**Testing**: Vitest for frontend helper/page behavior, `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`, and `./tools/test_unit.sh` for required final unit verification  
**Target Platform**: MoonMind Mission Control served by the API service in the existing Docker Compose deployment  
**Project Type**: Single repository with frontend entrypoints, shared frontend helpers, backend execution/artifact APIs, and unit tests  
**Performance Goals**: Edit/rerun page load performs one execution detail read and only performs one artifact read when inline task instructions are absent; create mode must not add extra execution-detail reads  
**Constraints**: Runtime implementation required; `MoonMind.Run` only; feature-flag gated; no queue fallback; no historical artifact mutation; unsupported or incomplete reconstruction fails explicitly; recurring schedule and unsupported controls hidden outside create mode; no submit/update behavior beyond preventing accidental create submission in edit/rerun modes  
**Scale/Scope**: One shared `/tasks/new` surface, one supported workflow type, first-slice prefill fields, local mocked fixtures and unit tests; no proposal editing, recurring schedule editing, legacy queue jobs, or non-`MoonMind.Run` workflows

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature uses Temporal executions as the editable object and does not introduce a separate task editing engine.
- **II. One-Click Agent Deployment**: PASS. No new services, secrets, deployment prerequisites, or external dependencies are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Draft reconstruction is based on MoonMind execution data and portable artifact JSON, not a specific agent vendor.
- **IV. Own Your Data**: PASS. Source execution data and artifacts remain in operator-controlled MoonMind storage.
- **V. Skills Are First-Class and Easy to Add**: PASS. Primary skill and template state are preserved as draft data rather than hardcoded workflow behavior.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a thin UI/helper layer around existing contracts with focused tests.
- **VII. Powerful Runtime Configurability**: PASS. Edit/rerun modes remain gated by the existing runtime-visible `temporalTaskEditing` feature flag.
- **VIII. Modular and Extensible Architecture**: PASS. Mode resolution and draft reconstruction are centralized in a frontend helper and consumed by the shared page.
- **IX. Resilient by Default**: PASS. Capability flags are validated before draft use, failures are explicit, and no unsafe fallback path is introduced.
- **X. Facilitate Continuous Improvement**: PASS. Explicit error states and fixture coverage make reconstruction failures diagnosable.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan is generated from the feature spec and keeps runtime validation tied to requirements.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. This work produces spec artifacts under `specs/` and does not alter canonical docs with migration checklists.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The feature refuses queue-era params/routes instead of preserving compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/161-temporal-task-drafts/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   └── temporal-task-drafts.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
frontend/
└── src/
    ├── entrypoints/
    │   ├── task-create.tsx
    │   ├── task-create.test.tsx
    │   ├── task-detail.tsx
    │   └── task-detail.test.tsx
    └── lib/
        └── temporalTaskEditing.ts

api_service/
└── api/
    └── routers/
        ├── executions.py
        ├── temporal_artifacts.py
        └── task_dashboard_view_model.py

moonmind/
└── schemas/
    └── temporal_models.py
```

**Structure Decision**: Use the existing Mission Control task-create entrypoint and the existing Temporal execution/artifact read APIs. Add or extend only the small shared frontend helper for route, mode, and draft-contract logic so later submit phases can reuse the same surface.

## Phase 0: Research Summary

Research is captured in [research.md](research.md). Decisions:

1. Keep `/tasks/new` as the single create/edit/rerun review surface and resolve mode from canonical query parameters.
2. Treat backend capability flags as required validation gates for edit and rerun draft display.
3. Reconstruct drafts from execution detail first and read immutable input artifacts only when required for missing inline instructions.
4. Fail closed for unsupported workflow types, missing capabilities, unreadable artifacts, and missing instructions.
5. Keep submit/update behavior out of Phase 2 except for preventing edit/rerun modes from using create or queue-era submission paths.

## Phase 1: Design Outputs

- [data-model.md](data-model.md): Defines submit page mode, source execution, capability set, submission draft, input artifact reference, and template state.
- [contracts/temporal-task-drafts.openapi.yaml](contracts/temporal-task-drafts.openapi.yaml): Captures the existing execution detail and artifact download surfaces consumed by Phase 2 and the page-mode contract exposed in the frontend.
- [quickstart.md](quickstart.md): Provides deterministic validation commands for the runtime implementation.

## Implementation Strategy

### 1. Mode and Route Plumbing

- Add a canonical mode-resolution helper that returns create, edit, or rerun mode and enforces rerun-over-edit precedence.
- Keep existing route helpers for `/tasks/new`, edit, and rerun targets.
- Ensure create mode remains unchanged and does not load execution detail.

### 2. Execution Detail Loading and Gating

- In edit/rerun modes, load the Temporal execution detail for the requested workflow identifier using the existing execution read path.
- Refuse any workflow type other than `MoonMind.Run`.
- Validate edit mode against `actions.canUpdateInputs` and rerun mode against `actions.canRerun`.
- Respect `features.temporalDashboard.temporalTaskEditing` before attempting edit/rerun reconstruction.

### 3. Draft Reconstruction

- Implement a single `buildTemporalSubmissionDraftFromExecution(execution, artifactInput?)` helper that maps execution detail into the shared form state.
- Populate runtime, provider profile, model, effort, repository, starting branch, target branch, publish mode, task instructions, primary skill, and applied template state where available.
- Prefer inline execution instructions and read the referenced input artifact only when inline instructions are absent.
- Treat missing instructions as an incomplete draft error.

### 4. Shared Form Behavior

- Update page title and primary CTA by mode: create, edit, rerun.
- Hide schedule controls outside create mode.
- Prevent edit/rerun mode from accidentally submitting through create semantics until Phase 3/4 add update submission.
- Show explicit operator-readable errors for feature-disabled, unsupported type, missing capability, unreadable artifact, malformed artifact, and incomplete draft states.

### 5. Validation

- Add focused Vitest coverage for mode precedence, draft reconstruction, edit prefill, rerun artifact-backed prefill, unsupported workflow errors, and hidden schedule controls.
- Preserve existing detail-page route helper tests.
- Run TypeScript typecheck and the full unit runner before completion.

## Post-Design Constitution Re-Check

- **I. Orchestrate, Don't Recreate**: PASS. The design remains Temporal-native and does not create a parallel queue editing model.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependencies are added.
- **III. Avoid Vendor Lock-In**: PASS. Runtime/provider/model fields are data copied from existing execution state.
- **IV. Own Your Data**: PASS. Artifacts are read from MoonMind-controlled storage and never mutated.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill and template state stay data-driven.
- **VI. The Bittersweet Lesson**: PASS. The helper/page split keeps the slice replaceable and testable.
- **VII. Powerful Runtime Configurability**: PASS. The feature flag remains the rollout control.
- **VIII. Modular and Extensible Architecture**: PASS. New logic is scoped to helper and submit page boundaries.
- **IX. Resilient by Default**: PASS. Unsupported and incomplete states fail explicitly.
- **X. Facilitate Continuous Improvement**: PASS. Tests and error copy cover diagnostic failure classes.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Plan, research, data model, contracts, and quickstart align to the spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Migration details remain in spec artifacts, not canonical docs.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No legacy queue aliases or fallbacks are planned.

## Complexity Tracking

No constitution violations or complexity exceptions.
