# Implementation Plan: Protect Image Access and Untrusted Content Boundaries

**Branch**: `203-protect-image-access` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/203-protect-image-access/spec.md`

## Summary

MM-374 requires image byte access and image-derived text handling to stay inside MoonMind execution-owned security boundaries. Existing artifact authorization, task attachment validation, target-aware UI rendering, worker materialization, and runtime attachment injection already cover most of the boundary. The implementation will strengthen and verify the remaining runtime prompt contract by making image-derived context warnings explicit about system/developer/task instruction boundaries, while adding focused tests that prove browser access, worker access, exact refs, direct URL avoidance, and untrusted extracted-text handling remain enforced.

## Technical Context

**Language/Version**: Python 3.12 for artifact, worker, vision, and task-contract boundaries; TypeScript/React for Mission Control image link behavior  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async fixtures, Temporal artifact service, React, Vitest, existing Codex worker helpers, existing vision service  
**Storage**: Existing Temporal artifact metadata and artifact store; workspace-local `.moonmind/attachments_manifest.json` and `.moonmind/vision/*`; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifact_authorization.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/agents/codex_worker/test_worker.py` and focused Vitest via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`  
**Integration Testing**: `./tools/test_integration.sh` for required `integration_ci` coverage when Docker is available; focused vision context integration via `pytest tests/integration/vision/test_context_artifacts.py -q` if local dependencies allow  
**Target Platform**: MoonMind API service, Mission Control browser UI, managed worker containers, and per-run workspaces  
**Project Type**: Web control plane plus Python workflow/runtime services  
**Performance Goals**: Security checks and prompt warning rendering add no extra artifact-list requests and remain linear in prepared attachment count  
**Constraints**: Do not expose durable credentials to browsers; do not introduce direct Jira/provider/object-store browser routes; do not trust extracted image text as instructions; preserve attachment refs exactly; no live Jira sync; preserve MM-374 traceability  
**Scale/Scope**: One independently testable security boundary story spanning existing image artifact access and runtime context handling

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Strengthens MoonMind security boundaries around existing artifact and runtime orchestration.
- II. One-Click Agent Deployment: PASS. No new external services or required secrets.
- III. Avoid Vendor Lock-In: PASS. Keeps provider-specific image formats out of the control-plane contract.
- IV. Own Your Data: PASS. Image bytes remain MoonMind artifacts and are not exposed as durable third-party URLs.
- V. Skills Are First-Class and Easy to Add: PASS. Does not mutate skill sources or runtime skill snapshots.
- VI. Replaceability and Scientific Method: PASS. Adds focused tests for the security hypothesis before hardening behavior.
- VII. Runtime Configurability: PASS. Uses configured MoonMind artifact endpoints and existing service access boundaries.
- VIII. Modular and Extensible Architecture: PASS. Keeps authorization in artifact services, UI routing in Mission Control, and untrusted prompt wording in vision/worker helpers.
- IX. Resilient by Default: PASS. Unsupported or ambiguous attachment refs fail visibly or remain ungrouped instead of being rewritten.
- X. Facilitate Continuous Improvement: PASS. Verification evidence maps each security boundary to tests.
- XI. Spec-Driven Development: PASS. Artifacts preserve MM-374 and source design mappings before implementation.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain desired-state; implementation state lives in `specs/` and `docs/tmp/`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility transforms or aliases are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/203-protect-image-access/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── image-access-boundaries.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
├── agents/codex_worker/
│   └── worker.py
├── vision/
│   └── service.py
└── workflows/
    ├── tasks/
    │   └── task_contract.py
    └── temporal/
        └── artifacts.py

api_service/api/routers/
└── temporal_artifacts.py

frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx

tests/
├── integration/vision/
│   └── test_context_artifacts.py
└── unit/
    ├── agents/codex_worker/test_worker.py
    ├── workflows/tasks/test_task_contract.py
    └── workflows/temporal/test_artifact_authorization.py
```

**Structure Decision**: Keep behavior in the existing boundaries: artifact service authorization for byte access, Mission Control route selection for browser links, worker prepare for service-side materialization, `VisionService` for image-derived context text, and Codex worker prompt composition for runtime injection.

## Complexity Tracking

No constitution violations.
