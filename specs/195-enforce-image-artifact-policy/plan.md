# Implementation Plan: Enforce Image Artifact Storage and Policy

**Branch**: `195-enforce-image-artifact-policy` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/195-enforce-image-artifact-policy/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

MM-368 requires task image inputs to stay artifact-backed and server-governed. The implementation will extend the existing Temporal artifact and task-shaped execution paths so attachment artifacts are validated by server policy at completion and execution submission, canonical `inputAttachments` refs are preserved in task parameters and snapshots, reserved input attachment namespaces are protected from worker impersonation, and disabled policy blocks image refs. Validation will use focused pytest unit coverage plus contract-style API coverage against the existing FastAPI/SQLAlchemy Temporal execution path.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for existing Create-page behavior  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal artifact service, existing React Create page  
**Storage**: Existing Temporal artifact metadata tables and configured artifact store; no new persistent storage  
**Unit Testing**: pytest via `./tools/test_unit.sh`, with focused pytest targets for iteration  
**Integration Testing**: pytest contract coverage against FastAPI app + sqlite-backed metadata, plus existing hermetic integration tier when needed  
**Target Platform**: MoonMind API service and managed-agent runtime containers  
**Project Type**: Web service with frontend submission client and Temporal workflow orchestration backend  
**Performance Goals**: Attachment validation should be linear in submitted attachment count and use compact metadata/signature checks before workflow start  
**Constraints**: Do not embed image bytes in payloads or Temporal history; do not introduce new storage tables; preserve pre-release compatibility policy by failing unsupported attachment shapes explicitly  
**Scale/Scope**: One task execution submit request with up to configured attachment count and total bytes; objective and step attachment refs only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Uses existing Temporal artifact and execution orchestration surfaces.
- II. One-Click Agent Deployment: PASS. No new external service dependency.
- III. Avoid Vendor Lock-In: PASS. Uses generic artifact refs and task attachment contracts, not provider file APIs.
- IV. Own Your Data: PASS. Image bytes remain in MoonMind-owned artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime mutation; task refs remain adapter-visible structured inputs.
- VI. Replaceability and Scientific Method: PASS. Behavior is contract-tested at API/service boundaries.
- VII. Runtime Configurability: PASS. Attachment policy uses existing server settings.
- VIII. Modular and Extensible Architecture: PASS. Validation lives at API/service boundaries.
- IX. Resilient by Default: PASS. Invalid inputs fail before workflow start.
- X. Facilitate Continuous Improvement: PASS. Validation errors are explicit and operator-visible.
- XI. Spec-Driven Development: PASS. This plan follows a one-story spec with tests.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Implementation notes stay in spec artifacts; canonical docs are not converted to migration logs.
- XIII. Pre-release Compatibility Policy: PASS. Unsupported shapes fail explicitly instead of compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/195-enforce-image-artifact-policy/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── image-attachment-policy.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/
    ├── executions.py
    └── temporal_artifacts.py

moonmind/
├── config/settings.py
└── workflows/temporal/artifacts.py

frontend/
└── src/entrypoints/
    ├── task-create.tsx
    └── task-create.test.tsx

tests/
├── contract/
│   └── test_temporal_execution_api.py
└── unit/
    ├── api/routers/test_executions.py
    ├── api/routers/test_temporal_artifacts.py
    └── workflows/temporal/test_artifacts.py
```

**Structure Decision**: Keep attachment validation in the existing artifact service and execution router boundaries. Preserve Create-page policy behavior unless tests reveal missing disabled-policy visibility.

## Complexity Tracking

No constitution violations.
