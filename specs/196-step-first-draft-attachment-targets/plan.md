# Implementation Plan: Step-First Draft and Attachment Targets

**Branch**: `mm-377-30189130` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/196-step-first-draft-attachment-targets/spec.md`

## Summary

Implement MM-377 by making the Create page draft explicitly target-aware for objective and step image inputs. The technical approach is to extend the existing React Create page attachment state so objective-scoped files are tracked separately from step-scoped files, step file ownership follows stable `step.localId` values through reorder/remove operations, uploaded attachment refs are submitted through structured `inputAttachments` fields only, and instruction text remains free of generated attachment markdown. Verification uses focused Vitest coverage for Create page authoring/submission plus the repository unit test runner.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for existing backend tests if payload contracts require server coverage
**Primary Dependencies**: React, Vite/Vitest, Testing Library, existing FastAPI execution/artifact APIs
**Storage**: Existing artifact metadata and execution payload storage only; no new persistent storage
**Unit Testing**: Vitest through `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and final `./tools/test_unit.sh`
**Integration Testing**: Existing Create page test harness exercises browser-to-API payload contracts; no Docker-backed integration required for this UI-only story
**Target Platform**: Mission Control browser UI served by FastAPI
**Project Type**: Web application UI with existing API contracts
**Performance Goals**: Attachment validation and step reorder remain immediate for ordinary task drafts within configured attachment limits
**Constraints**: Browser must call MoonMind APIs only; workflow payloads carry artifact refs and compact metadata, not image bytes; attachment targets must not be inferred from filenames or instruction text
**Scale/Scope**: One Create page story covering objective and step attachment target behavior

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change preserves MoonMind task submission and runtime adapter boundaries.
- **II. One-Click Agent Deployment**: PASS. No new services, secrets, or deployment prerequisites are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Attachment targets stay in MoonMind task payloads, not provider-specific message formats.
- **IV. Own Your Data**: PASS. Images remain MoonMind artifacts referenced by compact refs.
- **V. Skills Are First-Class and Easy to Add**: PASS. Step skill defaults and preset behavior remain compatible with existing template skills.
- **VII. Powerful Runtime Configurability**: PASS. Existing server-provided attachment policy remains the control for rendering and validation.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to the existing Create page entrypoint and tests.
- **IX. Resilient by Default**: PASS. Upload failures remain explicit and no partial execution is submitted after failed validation.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. MM-377 input is preserved in spec artifacts and implementation tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime implementation artifacts live under `specs/` and `local-only handoffs`, not canonical docs.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility aliases or hidden fallback semantics are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/196-step-first-draft-attachment-targets/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── create-page-attachment-targets.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

└── MM-377-moonspec-orchestration-input.md
```

**Structure Decision**: Use the existing Mission Control Create page entrypoint and colocated Vitest coverage. No backend schema or storage change is planned because the existing execution payload already supports task-level and step-level `inputAttachments`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
