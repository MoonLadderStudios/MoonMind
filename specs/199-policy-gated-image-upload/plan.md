# Implementation Plan: Policy-Gated Image Upload and Submit

**Branch**: `mm-380-b1b50fc8` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/199-policy-gated-image-upload/spec.md`

## Summary

Implement MM-380 by completing the Create page runtime behavior for policy-gated image attachment authoring and submission. The technical approach is to use the existing server-provided `attachmentPolicy`, Create page attachment state, artifact upload helpers, and task-shaped submission payloads while tightening UI validation, target-scoped failure messaging, image-specific labeling, and submit blocking. Validation uses focused Vitest coverage for Create page policy, validation, upload, failure, and payload behavior, plus final repository unit validation.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains relevant for existing backend contract tests but is not expected to change for this story  
**Primary Dependencies**: React, Vite/Vitest, Testing Library, existing FastAPI artifact and execution APIs  
**Storage**: Existing artifact metadata and execution task snapshots only; no new persistent storage  
**Unit Testing**: Vitest through `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and final `./tools/test_unit.sh`  
**Integration Testing**: Existing Create page test harness exercises browser-to-API payload behavior; no Docker-backed integration is planned for this UI story  
**Target Platform**: Mission Control browser UI served by FastAPI  
**Project Type**: Web application UI backed by existing API contracts  
**Performance Goals**: Validation and submit gating remain immediate for ordinary drafts within configured attachment limits  
**Constraints**: Browser must use MoonMind APIs only; local images must upload to the artifact system before create/edit/rerun submission; submitted task payloads carry structured refs, not image bytes; attachment target binding comes from explicit objective or step fields  
**Scale/Scope**: One Create page story covering attachment policy visibility, image validation, target-scoped failure handling, upload-before-submit, and canonical payload fields

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The Create page continues to submit MoonMind task payloads and does not introduce provider-specific image transport.
- **II. One-Click Agent Deployment**: PASS. No new services, secrets, or deployment prerequisites are introduced.
- **III. Avoid Vendor Lock-In**: PASS. Image inputs remain MoonMind artifact refs rather than provider-specific file handles.
- **IV. Own Your Data**: PASS. Local image bytes are uploaded to MoonMind's artifact system before execution submission.
- **V. Skills Are First-Class and Easy to Add**: PASS. Task skill and preset behavior remain compatible with existing Create page flows.
- **VII. Powerful Runtime Configurability**: PASS. The behavior is controlled by server-provided `attachmentPolicy`.
- **VIII. Modular and Extensible Architecture**: PASS. Work is scoped to the existing Create page entrypoint and tests.
- **IX. Resilient by Default**: PASS. Invalid, failed, incomplete, or uploading attachments block submit instead of starting partial executions.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. MM-380 input is preserved in spec artifacts and implementation tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime implementation artifacts live under `specs/` and `docs/tmp`; canonical docs are source requirements.
- **XIII. Pre-Release Compatibility Policy**: PASS. No compatibility aliases or hidden attachment retargeting are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/199-policy-gated-image-upload/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-image-upload.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

docs/tmp/jira-orchestration-inputs/
└── MM-380-moonspec-orchestration-input.md
```

**Structure Decision**: Use the existing Mission Control Create page entrypoint and colocated Vitest coverage. Backend schemas and artifact APIs already support structured `inputAttachments`, so backend code changes are not planned unless tests reveal a contract gap.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

## Managed Setup Note

`.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed branch name `mm-380-b1b50fc8` because the helper expects a branch like `001-feature-name`. Planning continued using `.specify/feature.json` and direct artifact inspection for `specs/199-policy-gated-image-upload`.
