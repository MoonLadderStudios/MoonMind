# Implementation Plan: Show Attachment and Recovery Diagnostics By Target

**Branch**: `329-show-attachment-recovery-diagnostics-by-target` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:1f8186c7-7b34-49ec-bf2f-fee2e3d290af/repo/specs/329-show-attachment-recovery-diagnostics-by-target/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed runtime branch `run-jira-orchestrate-for-mm-635-show-att-48c11b06` is rejected by the helper's `###-feature-name` branch-name guard. Planning continued from `.specify/feature.json`, which points at this feature directory.

## Summary

Task detail already has generic diagnostics panels, step artifacts, failed-step Resume actions, related-run links, and preserved-step ledger rows, while the worker and vision services already produce target-aware attachment and context artifacts. The remaining story is to expose a bounded, target-aware diagnostics contract on task detail so operators can see attachment ownership, manifest/generated context refs, recovery provenance, and failure phases without reading raw workflow history. The implementation should add verification tests first, then backend/UI contract changes where existing projections are partial.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/api/routers/executions.py` preserves snapshot attachment refs; `frontend/src/entrypoints/task-detail.tsx` lacks target-grouped attachment display | add target-grouped attachment diagnostics projection and task-detail rendering | unit + integration |
| FR-002 | missing | no target-empty/populated distinction found in task-detail UI | add explicit empty/populated target states | unit + integration |
| FR-003 | partial | step artifacts and runtime diagnostics refs render generically; image docs require manifest refs | surface manifest refs in target diagnostics | unit + integration |
| FR-004 | partial | `moonmind/vision/service.py` writes target context index; task detail does not expose generated context refs by target | project generated context refs into task detail diagnostics | unit + integration |
| FR-005 | partial | worker materialization records target-aware diagnostics, but task detail does not render target failure ownership | expose affected target for attachment failures | unit + integration |
| FR-006 | partial | docs and worker events mention prepare phases; task detail shows raw diagnostics text only | normalize and render bounded upload/validation/materialization/context generation phases | unit + integration |
| FR-007 | partial | step ledger distinguishes steps, but not current-step attachment context | add current-step attachment context section distinct from unrelated inputs | unit + integration |
| FR-008 | implemented_unverified | execution projection has `resume`; task detail has failed-step Resume and related-run tests | add/extend verification for resumed execution provenance in target diagnostics context | unit + integration |
| FR-009 | implemented_unverified | `StepLedgerRowSchema.preservedFrom` renders source workflow/run/attempt; workflow tests assert preserved rows | strengthen task-detail test for preserved prior-step reuse display | unit + integration |
| FR-010 | partial | backend exposes disabled reasons; task detail does not present the requested failed Resume phase taxonomy | add failed Resume phase diagnostics using bounded labels | unit + integration |
| FR-011 | partial | architecture docs list subsystem boundaries; task detail lacks a specific boundary-preserving diagnostics contract | include contract-owned labels/refs without redefining subsystem internals | unit |
| FR-012 | partial | generic diagnostics panels still require raw text inspection for many cases | add structured diagnostics summary before raw logs/history | unit + integration |
| FR-013 | implemented_unverified | `spec.md` preserves MM-635 and the original brief | preserve traceability in plan, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SC-001 | partial | target ownership exists at worker/vision layers, not task detail | validate every displayed attachment metadata item has owning target | unit + integration |
| SC-002 | partial | phase evidence exists in docs/events, not structured detail display | validate each failure shows one target and one phase | unit + integration |
| SC-003 | partial | context refs exist in vision index, not operator detail | validate target-owned manifest/context refs | unit + integration |
| SC-004 | implemented_unverified | related runs and preserved rows exist | verify source execution and preserved prior steps are visible | unit + integration |
| SC-005 | missing | no failed Resume phase display taxonomy found | validate failed Resume phase labels | unit + integration |
| SC-006 | implemented_unverified | `spec.md` and this plan preserve MM-635 and source IDs | preserve in later artifacts and final evidence | final verify |
| DESIGN-REQ-023 | partial | `docs/Tasks/TaskArchitecture.md` lines 200-204 and 660-671; partial worker/UI evidence | implement target-aware metadata, refs, target failure, and phase display | unit + integration |
| DESIGN-REQ-024 | partial | `docs/Tasks/TaskArchitecture.md` lines 671-689; partial Resume and ledger evidence | finish current-step context, Resume provenance, failure phase, and boundary contract | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, Temporal Python SDK, React, TanStack Query, Zod, Vitest, Testing Library, pytest  
**Storage**: Existing Temporal execution records, task input snapshot artifacts, Temporal artifact metadata/content, and existing step ledger/projection payloads; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh`; focused frontend iteration with `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`; focused backend pytest through `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`  
**Integration Testing**: `./tools/test_integration.sh`; targeted hermetic coverage should include existing integration-ci surfaces for vision context artifacts and workflow resume where feasible  
**Target Platform**: MoonMind Mission Control web UI backed by FastAPI and Temporal execution projections  
**Project Type**: Full-stack web application and orchestration control plane  
**Performance Goals**: Task detail should remain scan-friendly; diagnostics summaries must be bounded and avoid loading raw workflow history for normal inspection  
**Constraints**: Preserve artifact authorization/redaction behavior; do not embed large diagnostics or artifact bodies in execution projections; use refs and compact metadata; keep Resume and attachment subsystem semantics owned by their existing contracts  
**Scale/Scope**: One task-detail story covering objective targets, step targets, generated context refs, attachment failure phases, resumed execution provenance, and failed Resume phase labels

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Extends existing Mission Control projections and artifact refs rather than replacing workflow runtimes. |
| II. One-Click Agent Deployment | PASS | No new mandatory external service or secret. |
| III. Avoid Vendor Lock-In | PASS | Uses generic task targets, artifacts, and diagnostics metadata. |
| IV. Own Your Data | PASS | Keeps evidence in MoonMind-owned execution/artifact stores and passes refs to the UI. |
| V. Skills Are First-Class and Easy to Add | PASS | No skill runtime changes. |
| VI. Delete/Replaceable Scaffolding | PASS | Adds bounded contracts and tests around existing surfaces. |
| VII. Runtime Configurability | PASS | Uses existing feature/config patterns; no hardcoded provider coupling. |
| VIII. Modular and Extensible Architecture | PASS | Keeps projection, schema, and UI responsibilities separate. |
| IX. Resilient by Default | PASS | Uses compact refs and explicit degraded/failure states. |
| X. Facilitate Continuous Improvement | PASS | Improves operator diagnostics and final evidence. |
| XI. Spec-Driven Development | PASS | Planning starts from `spec.md` and preserves traceability. |
| XII. Canonical Documentation Separation | PASS | Implementation tracking stays in this spec directory. |
| XIII. Pre-Release Velocity | PASS | Plan should update active contracts directly, not add compatibility aliases. |

## Project Structure

### Documentation (this feature)

```text
specs/329-show-attachment-recovery-diagnostics-by-target/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-detail-target-diagnostics.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/executions.py

moonmind/
├── schemas/temporal_models.py
├── vision/service.py
└── agents/codex_worker/worker.py

frontend/src/
├── entrypoints/task-detail.tsx
└── entrypoints/task-detail.test.tsx

tests/
├── unit/api/routers/test_executions.py
├── unit/agents/codex_worker/test_attachment_materialization.py
├── unit/moonmind/vision/test_service.py
└── integration/vision/test_context_artifacts.py
```

**Structure Decision**: This is a full-stack Mission Control feature. Backend work belongs in execution projection/schema boundaries, UI work belongs in task detail, and tests should cover both projection contracts and rendered operator behavior.

## Complexity Tracking

No constitution violations requiring complexity exceptions.
