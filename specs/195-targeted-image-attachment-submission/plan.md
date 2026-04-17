# Implementation Plan: Targeted Image Attachment Submission

**Branch**: `195-targeted-image-attachment-submission` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/195-targeted-image-attachment-submission/spec.md`

## Summary

Task-shaped execution submissions must preserve objective-scoped and step-scoped image attachment refs as structured `inputAttachments` data from the Create page through `/api/executions` into `MoonMind.Run` initial parameters and the original task input snapshot. The implementation will add typed attachment-ref validation to the canonical task contract, normalize task-level and step-level attachment refs in the execution router, reject raw image bytes/data URLs and filename-derived target shortcuts, and prove the behavior with unit and contract tests.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for existing Create page tests if frontend behavior changes  
**Primary Dependencies**: Pydantic v2, FastAPI, SQLAlchemy async session fixtures, existing Temporal execution router/service, React/Vitest test harness  
**Storage**: Existing Temporal execution records and artifact-backed original task input snapshots; no new persistent tables  
**Unit Testing**: pytest through targeted Python tests and `./tools/test_unit.sh` for final verification  
**Integration Testing**: pytest contract coverage for `/api/executions`; existing frontend Vitest coverage for Create page attachment submission when needed  
**Target Platform**: MoonMind API service and dashboard runtime  
**Project Type**: Web service plus dashboard frontend  
**Performance Goals**: Attachment ref normalization is linear in submitted refs and bounded by existing task/step limits  
**Constraints**: Do not embed image bytes or data URLs in workflow payloads or Temporal histories; do not use legacy queue attachment routes as canonical; preserve in-flight Temporal compatibility by adding optional fields only  
**Scale/Scope**: One task-shaped submit request with objective-level refs and up to the existing step limit for step-level refs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The story preserves MoonMind orchestration contracts and does not recreate agent behavior.
- II. One-Click Agent Deployment: PASS. No new external service or required secret is introduced.
- III. Avoid Vendor Lock-In: PASS. Attachment refs are provider-neutral artifact references.
- IV. Own Your Data: PASS. Image bytes remain behind MoonMind artifact APIs and refs are carried through owned workflow payloads.
- V. Skills Are First-Class and Easy to Add: PASS. No skill registration or runtime-specific skill behavior changes.
- VI. Replaceable Scaffolding: PASS. Behavior is protected by contract tests around stable boundaries.
- VII. Runtime Configurability: PASS. Existing attachment policy remains configuration-driven; this story does not hardcode new policy values.
- VIII. Modular and Extensible Architecture: PASS. Validation belongs in task contract/router boundaries.
- IX. Resilient by Default: PASS. Snapshot preservation supports edit/rerun recovery and avoids large workflow history payloads.
- X. Facilitate Continuous Improvement: PASS. Verification evidence will be produced through tests and Moon Spec tasks.
- XI. Spec-Driven Development: PASS. This plan follows the single-story spec.
- XII. Canonical Documentation Separation: PASS. Runtime work is specified under `specs/`; no canonical docs are rewritten as backlog.
- XIII. Pre-release Compatibility Policy: PASS. Optional additive fields preserve current callers; unsupported malformed attachment refs fail fast.

## Project Structure

### Documentation (this feature)

```text
specs/195-targeted-image-attachment-submission/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-input-attachments.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/
└── task_contract.py

api_service/api/routers/
└── executions.py

tests/unit/workflows/tasks/
└── test_task_contract.py

tests/unit/api/routers/
└── test_executions.py

tests/contract/
└── test_temporal_execution_api.py

frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx
```

**Structure Decision**: Use the existing task contract and execution router boundary. Frontend files are listed because the Create page already owns attachment uploads and may receive targeted test coverage, but production changes are expected to stay backend-focused unless tests reveal a UI payload gap.

## Complexity Tracking

No constitution violations.
