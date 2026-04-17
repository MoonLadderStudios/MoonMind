# Implementation Plan: Preserve Attachment Bindings in Snapshots and Reruns

**Branch**: `196-preserve-attachment-bindings` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/196-preserve-attachment-bindings/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

MM-369 requires task edit and rerun flows to reconstruct attachment bindings from the authoritative task input snapshot. The implementation will extend the existing task input snapshot, Temporal task editing draft reconstruction, and Create-page edit/rerun submission paths so objective-scoped and step-scoped `inputAttachments` survive unchanged, persisted refs remain distinct from new local files, and missing binding data fails explicitly. Validation will use focused frontend unit coverage for draft reconstruction and Create-page behavior plus API/contract coverage for snapshot payloads and action availability.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Create-page behavior  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal artifact service, React, Vitest, existing task editing helpers  
**Storage**: Existing Temporal artifact metadata tables and original task input snapshot artifacts; no new persistent storage  
**Unit Testing**: pytest via `./tools/test_unit.sh`; Vitest via `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` for focused frontend iteration  
**Integration Testing**: pytest contract coverage against FastAPI app + sqlite-backed metadata, plus `./tools/test_integration.sh` for hermetic integration when Docker is available  
**Target Platform**: MoonMind API service and Mission Control Create/task detail surfaces  
**Project Type**: Web service with React frontend and Temporal workflow orchestration backend  
**Performance Goals**: Draft reconstruction remains linear in number of task steps plus submitted attachment refs; no binary attachment bytes are loaded for reconstruction  
**Constraints**: Do not infer target binding from artifact links, filenames, or metadata; do not embed image bytes in workflow payloads or histories; fail explicitly for incomplete snapshot binding data; preserve pre-release compatibility policy by removing unsupported hidden fallbacks rather than adding compatibility aliases  
**Scale/Scope**: One task-shaped execution snapshot with objective attachments and step attachments across the existing configured attachment limits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Uses existing task execution, artifact, and Temporal editing surfaces.
- II. One-Click Agent Deployment: PASS. No new external service dependency.
- III. Avoid Vendor Lock-In: PASS. Uses generic MoonMind attachment refs rather than provider-specific files.
- IV. Own Your Data: PASS. Attachment binding state remains in MoonMind-owned snapshots and artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. No runtime skill mutation.
- VI. Replaceability and Scientific Method: PASS. Behavior is defined by tests and contract evidence before implementation.
- VII. Runtime Configurability: PASS. Existing attachment policy and runtime configuration remain authoritative.
- VIII. Modular and Extensible Architecture: PASS. Changes stay at API snapshot and UI reconstruction boundaries.
- IX. Resilient by Default: PASS. Missing reconstruction state fails explicitly instead of silently dropping attachments.
- X. Facilitate Continuous Improvement: PASS. Failures surface concrete draft reconstruction reasons.
- XI. Spec-Driven Development: PASS. This plan follows a one-story spec with unit and integration evidence.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain source requirements; implementation notes stay in spec artifacts.
- XIII. Pre-release Compatibility Policy: PASS. Unsupported or incomplete internal payload shapes fail rather than compatibility-transforming attachment bindings.

## Project Structure

### Documentation (this feature)

```text
specs/196-preserve-attachment-bindings/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── attachment-binding-reconstruction.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/
    └── executions.py

frontend/
└── src/
    ├── lib/
    │   └── temporalTaskEditing.ts
    └── entrypoints/
        ├── task-create.tsx
        └── task-create.test.tsx

tests/
├── contract/
│   └── test_temporal_execution_api.py
└── unit/
    └── api/routers/test_executions.py
```

**Structure Decision**: Preserve authoritative binding data in the existing original task input snapshot and teach the existing frontend draft reconstruction path to hydrate persisted attachment refs into edit/rerun state. Backend coverage protects snapshot shape and action availability; frontend coverage protects user-visible edit/rerun behavior.

## Complexity Tracking

No constitution violations.
