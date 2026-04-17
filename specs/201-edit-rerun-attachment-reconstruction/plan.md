# Implementation Plan: Edit and Rerun Attachment Reconstruction

**Feature ID**: `201-edit-rerun-attachment-reconstruction` | **Managed PR Branch**: `mm-382-8aa2c304` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/201-edit-rerun-attachment-reconstruction/spec.md`

## Summary

Implement MM-382 by validating and, where necessary, completing Create page edit/rerun reconstruction from the authoritative task input snapshot. The technical approach uses the existing Temporal task editing helper, Create page persisted attachment state, artifact-first upload path, and task-shaped execution payload contract. Current repository inspection shows the runtime behavior and focused tests already exist from earlier attachment-binding work, so implementation work for this spec is primarily traceability, verification, and final evidence against the MM-382 source brief.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control Create page behavior; Python 3.12 for API and contract tests  
**Primary Dependencies**: React, Vite/Vitest, Testing Library, FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal artifact service  
**Storage**: Existing Temporal artifact metadata tables and original task input snapshot artifacts; no new persistent storage  
**Unit Testing**: Vitest through `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`; pytest through `pytest tests/unit/api/routers/test_executions.py -q`; final `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: `pytest tests/contract/test_temporal_execution_api.py -q` for API contract evidence; `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind API service and Mission Control browser UI  
**Project Type**: Web service with React frontend and Temporal workflow orchestration backend  
**Performance Goals**: Draft reconstruction remains linear in task steps plus attachment refs; reconstruction must not load binary attachment bytes  
**Constraints**: Use runtime mode; do not infer target binding from filenames, artifact links, or metadata; do not silently drop attachments; local images upload before create/edit/rerun submission; preserve MM-382 in artifacts and PR metadata  
**Scale/Scope**: One Create page edit/rerun story covering task snapshots with objective and step attachments across existing configured attachment limits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Uses existing task execution, artifact, and Temporal editing surfaces.
- **II. One-Click Agent Deployment**: PASS. No new service, secret, or deployment prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Attachment refs remain MoonMind-owned structured data, not provider-specific file handles.
- **IV. Own Your Data**: PASS. Attachment binding state remains in MoonMind-owned task input snapshots and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No runtime skill mutation or skill storage behavior changes.
- **VI. Replaceability and Scientific Method**: PASS. Completion is based on focused unit and contract evidence.
- **VII. Runtime Configurability**: PASS. Existing attachment policy and runtime configuration remain authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. Scope stays within existing API snapshot and UI reconstruction boundaries.
- **IX. Resilient by Default**: PASS. Missing reconstruction state fails explicitly instead of silently dropping attachments.
- **X. Facilitate Continuous Improvement**: PASS. Failure cases produce actionable reconstruction blockers.
- **XI. Spec-Driven Development**: PASS. MM-382 is preserved as the canonical source in spec artifacts.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs are source requirements; implementation planning remains under `specs/` and Jira input under `docs/tmp/`.
- **XIII. Pre-release Compatibility Policy**: PASS. Unsupported internal payload shapes fail rather than being compatibility-transformed.

## Project Structure

### Documentation (this feature)

```text
specs/201-edit-rerun-attachment-reconstruction/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── edit-rerun-attachment-reconstruction.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
frontend/src/lib/temporalTaskEditing.ts
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
tests/unit/api/routers/test_executions.py
tests/contract/test_temporal_execution_api.py
docs/tmp/jira-orchestration-inputs/MM-382-moonspec-orchestration-input.md
```

**Structure Decision**: Use the existing original task input snapshot and Create page reconstruction path. Backend coverage protects snapshot shape and action availability; frontend coverage protects user-visible edit/rerun behavior and payload preservation.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

## Managed Setup Note

The active managed PR branch is `mm-382-8aa2c304`, while Moon Spec helper scripts expect feature-number branch names in git-derived contexts. This run therefore keeps the Moon Spec feature ID aligned to `201-edit-rerun-attachment-reconstruction` after scanning `specs/` and selecting the next global numeric prefix. In managed branches, run helper scripts with `SPECIFY_FEATURE=201-edit-rerun-attachment-reconstruction` so they resolve the numbered feature directory deterministically.
