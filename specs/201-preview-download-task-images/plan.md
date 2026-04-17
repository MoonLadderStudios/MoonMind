# Implementation Plan: Preview and Download Task Images by Target

**Branch**: `201-preview-download-task-images` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/201-preview-download-task-images/spec.md`

## Summary

MM-373 adds target-aware image input review for task detail while preserving existing edit and rerun attachment durability. The implementation extends the task-detail artifact parsing and rendering path to recognize image artifacts with authoritative target metadata, group them by objective or step target, preview them through MoonMind artifact download endpoints, and keep metadata plus downloads visible on preview failure. Existing edit/rerun reconstruction and serialization tests cover unchanged persisted refs and persisted-vs-local attachment distinctions.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12/FastAPI routes remain unchanged  
**Primary Dependencies**: React, TanStack Query, Zod, Vitest, Testing Library, existing Temporal artifact API  
**Storage**: Existing Temporal artifact metadata and execution artifact links; no new storage  
**Unit Testing**: Vitest through `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`  
**Integration Testing**: Existing FastAPI/pytest artifact route coverage; no route contract change required for this story  
**Target Platform**: Browser-based Mission Control task detail, edit, and rerun surfaces  
**Project Type**: Web UI plus existing API contract consumption  
**Performance Goals**: Render target-aware image groups without additional network requests beyond the existing artifact list request  
**Constraints**: Use MoonMind-owned browser endpoints; do not infer target binding from filenames; preserve preview failure metadata; do not introduce hidden compatibility transforms  
**Scale/Scope**: One independently testable UI story for task image preview/download visibility

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The change consumes existing MoonMind artifact orchestration data and does not introduce a new agent or storage path.
- VII. Powerful Runtime Configurability: PASS. Browser endpoints continue to come from configured API base/routes.
- IX. Resilient by Default: PASS. Preview failure degrades to metadata plus download visibility.
- XII. Documentation State Separation: PASS. Spec artifacts capture implementation work; canonical desired-state docs are not rewritten for migration status.
- XIII. Pre-release Compatibility Policy: PASS. Unsupported target metadata is not silently transformed into inferred bindings.
- Testing Discipline: PASS. Focused UI tests cover the user-facing contract; existing edit/rerun tests cover persisted ref preservation.

## Project Structure

### Documentation (this feature)

```text
specs/201-preview-download-task-images/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-image-inputs.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-detail.tsx
├── task-detail.test.tsx
├── task-create.tsx
└── task-create.test.tsx

frontend/src/styles/
└── mission-control.css

api_service/api/routers/
└── temporal_artifacts.py
```

**Structure Decision**: Implement in the existing Mission Control entrypoints. The detail page consumes existing execution artifact metadata; edit/rerun behavior remains in `task-create.tsx` and `temporalTaskEditing.ts`.

## Complexity Tracking

No constitution violations.
