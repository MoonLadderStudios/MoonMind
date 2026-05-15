# Implementation Plan: Provider-Neutral Slash Command Previews

**Branch**: `run-jira-orchestrate-for-mm-685-show-pro-4537c34f` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:9f8378c1-5596-4d43-875b-8387e0bedb86/repo/specs/355-slash-command-previews/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not be used because the current managed branch name is not a numeric MoonSpec feature branch. The active feature directory was resolved from `.specify/feature.json` and existing `spec.md`.

## Summary

Deliver MM-685 by adding provider-neutral runtime slash-command preview behavior to the Mission Control Create page for objective and step instructions. Existing backend task snapshot normalization already detects and stores runtime command metadata, but the browser surface lacks preview state, declarative runtime command capability and hint data, unsupported-runtime warnings, and edit-mode restoration of stored command metadata. The implementation will expose a browser-safe runtime command preview catalog, add Create page preview parsing and rendering that preserves authored text, extend edit/rerun draft reconstruction to carry stored command metadata for preview, and verify behavior with frontend unit tests plus API/edit-boundary integration coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | `frontend/src/entrypoints/task-create.tsx` has instruction fields but no `runtimeCommand` preview search hits. | Add objective and step preview state derived from authored instructions and selected runtime. | unit + integration |
| FR-002 | missing | `api_service/api/routers/task_dashboard_view_model.py` exposes `supportedTaskRuntimes`, but no slash-command capability or hint catalog. | Expose browser-safe runtime command capability and hint metadata, then consume it in Create page preview labels. | unit + integration |
| FR-003 | missing | Backend treats unknown commands as opaque pass-through in `tests/unit/workflows/tasks/test_task_contract.py`; Create page has no matching preview. | Add unknown valid command preview as pass-through for slash-capable runtimes without warning language. | unit |
| FR-004 | missing | Runtime changes update model/profile state in `frontend/src/entrypoints/task-create.tsx`, but no preview recomputation exists. | Recompute preview from current instructions and runtime without mutating instruction state. | unit |
| FR-005 | missing | Backend records escaped slash metadata; Create page has no escaped-literal preview. | Add escaped literal preview state with no executable command chip. | unit |
| FR-006 | missing | Backend preserves whitespace-prefixed slash text as literal; Create page has no preview distinction. | Add preview parsing that ignores leading whitespace and inline slash text. | unit |
| FR-007 | missing | Backend marks path-like slash input malformed literal; Create page has no preview distinction. | Add path-like or malformed preview state as literal/warning without rewriting text. | unit |
| FR-008 | partial | Create page currently has runtime selection and no provider-specific slash markup, but it also lacks declarative preview behavior. | Keep rendering provider-neutral and source preview decisions from shared metadata rather than runtime-specific UI branches. | unit + integration |
| FR-009 | partial | `buildTemporalSubmissionDraftFromExecution()` restores instructions, but draft step/task types do not carry `runtimeCommand` metadata. | Extend edit/rerun draft models to carry stored runtime command metadata for preview only. | unit + integration |
| FR-010 | missing | Backend runtime command tests exist; frontend preview matrix and edit restoration tests are absent. | Add focused Vitest coverage for known, unknown, unsupported, escaped, whitespace, malformed, objective, step, runtime change, and edit-mode cases. | unit |
| FR-011 | implemented_unverified | `spec.md` preserves `MM-685` and the original preset brief. | Preserve traceability through plan, research, quickstart, tasks, implementation, verification, commit text, and PR metadata. | final verify |
| SCN-001 | missing | No Create page preview for `/review`. | Add known command preview with optional hint. | unit |
| SCN-002 | missing | No Create page preview for `/foo`. | Add opaque pass-through preview. | unit |
| SCN-003 | missing | No unsupported-runtime warning preview. | Add runtime capability recomputation and warning state. | unit |
| SCN-004 | missing | No escaped-literal preview. | Add escaped state. | unit |
| SCN-005 | partial | Edit mode restores instructions from task snapshots but not command metadata. | Preserve stored metadata for preview restoration, with re-detection only when absent. | unit + integration |
| SC-001 | missing | No frontend preview tests for known commands. | Add tests for task and step known command previews. | unit |
| SC-002 | missing | No frontend preview tests for unknown pass-through. | Add tests ensuring no warning/error language. | unit |
| SC-003 | missing | No preview recomputation tests on runtime changes. | Add tests preserving exact text across runtime switches. | unit |
| SC-004 | missing | No escaped preview tests. | Add tests for escaped literal state. | unit |
| SC-005 | missing | No unsupported-runtime preview tests. | Add tests for runtime without pass-through. | unit |
| SC-006 | implemented_unverified | `spec.md` and this plan preserve `MM-685`; final verification still pending. | Carry traceability into downstream artifacts and final verification. | final verify |
| DESIGN-REQ-001 | partial | Backend preserves authored text; Create page preview and declarative metadata are missing. | Add provider-neutral preview driven by capability/hint metadata. | unit + integration |
| DESIGN-REQ-002 | partial | Backend supports leading slash and escaped slash; Create page preview is missing. | Add preview states for detected and escaped text. | unit |
| DESIGN-REQ-003 | partial | Backend does not block unknown commands; Create page preview is missing. | Add opaque pass-through preview without local allowlist behavior. | unit |
| DESIGN-REQ-004 | partial | Backend distinguishes cases; Create page lacks equivalent preview. | Add browser-side preview classification aligned with backend semantics. | unit |
| DESIGN-REQ-005 | partial | Backend unknown command behavior exists; Create page preview missing. | Add unknown command pass-through UI state. | unit |
| DESIGN-REQ-006 | missing | No browser-safe slash capability or hint catalog in boot config. | Add declarative preview catalog to boot config and consume it in UI. | unit + integration |
| DESIGN-REQ-007 | missing | Create page behavior not implemented. | Render command status, optional hints, pass-through status, and unsupported warnings. | unit |
| DESIGN-REQ-008 | missing | Runtime changes do not affect preview because preview does not exist. | Recompute preview when runtime state changes. | unit |
| DESIGN-REQ-009 | partial | Edit mode restores instructions but not stored `runtimeCommand` metadata. | Preserve command metadata for preview restoration; re-detect only when absent. | unit + integration |
| DESIGN-REQ-010 | partial | Backend parser tests exist; frontend preview matrix missing. | Add Create page preview tests covering the required matrix. | unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control Create page; Python 3.12 for FastAPI boot payload and boundary tests  
**Primary Dependencies**: React, TanStack Query, FastAPI, Pydantic v2, existing task dashboard view model, existing Temporal task snapshot helpers  
**Storage**: No new persistent storage; preview state is derived from authored instructions, boot payload metadata, and existing task input snapshots  
**Unit Testing**: Vitest and Testing Library for frontend preview behavior; pytest for Python boot-payload/schema helpers when changed  
**Integration Testing**: Existing hermetic integration runner `./tools/test_integration.sh`, with focused API/edit-boundary tests where boot payload or task snapshot reconstruction is exposed  
**Target Platform**: Mission Control web UI served by FastAPI in the existing local-first MoonMind deployment  
**Project Type**: Full-stack web application with frontend Create page and Python API boot metadata  
**Performance Goals**: Preview updates must be immediate for normal instruction editing and runtime changes, with no network call per keystroke  
**Constraints**: Preserve authored instruction text exactly; do not hard-code provider-specific command markup in Create page; keep backend normalization authoritative at submit time; avoid new persistent storage  
**Scale/Scope**: One Create page preview story covering objective and step instructions, edit-mode preview restoration, and runtime changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Plan Evidence |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Preview remains provider-neutral and delegates execution semantics to runtime capabilities and adapters. |
| II. One-Click Agent Deployment | PASS | No new external services or required secrets. |
| III. Avoid Vendor Lock-In | PASS | Capability/hint data is runtime-declarative; no Codex or Claude markup in Create page. |
| IV. Own Your Data | PASS | Preview uses existing local boot payload and task input snapshots. |
| V. Skills Are First-Class and Easy to Add | PASS | No skill runtime contract changes; task composition remains compatible with existing presets and steps. |
| VI. Replaceable Scaffolding | PASS | Preview parsing is bounded and backed by tests; backend remains authoritative. |
| VII. Runtime Configurability | PASS | Runtime command support is exposed as configuration/capability metadata rather than hard-coded UI behavior. |
| VIII. Modular and Extensible Architecture | PASS | Changes are localized to boot metadata, Create page preview helpers, and edit draft reconstruction. |
| IX. Resilient by Default | PASS | Submit-time backend normalization remains authoritative; preview does not mutate saved task input. |
| X. Continuous Improvement | PASS | Traceability and final verification preserve `MM-685` and planned evidence. |
| XI. Spec-Driven Development | PASS | This plan follows `spec.md` and keeps all requirements visible. |
| XII. Canonical Docs vs Tmp | PASS | No canonical docs changes planned; implementation artifacts remain under `specs/355-slash-command-previews/`. |
| XIII. Pre-release Compatibility Policy | PASS | No compatibility aliasing or migration layer planned; existing internal contracts will be updated directly where needed. |

No constitution violations are present.

## Project Structure

### Documentation (this feature)

```text
specs/355-slash-command-previews/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-command-preview.md
└── tasks.md             # Created later by /speckit.tasks
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── task_dashboard_view_model.py

frontend/
└── src/
    ├── entrypoints/
    │   ├── task-create.tsx
    │   └── task-create.test.tsx
    └── lib/
        └── temporalTaskEditing.ts

moonmind/
└── workflows/
    └── tasks/
        └── task_contract.py

tests/
├── unit/
│   └── workflows/
│       └── tasks/
│           └── test_task_contract.py
└── integration/
    └── api/
        └── test_task_contract_normalization.py
```

**Structure Decision**: Use the existing Create page entrypoint for UI behavior, existing Temporal editing helper for edit/rerun draft reconstruction, and existing dashboard boot payload route for browser-safe runtime command preview metadata. Backend task contract normalization remains the authoritative submit boundary and should be reused or mirrored only through safe exported metadata.

## Complexity Tracking

No constitution violations or unusual complexity exceptions are required.
