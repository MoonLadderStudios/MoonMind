# Implementation Plan: Expose Image Diagnostics and Failure Evidence

**Branch**: `203-expose-image-diagnostics` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/203-expose-image-diagnostics/spec.md`

**Note**: This plan was generated manually from the MoonSpec workflow because the generic template path is not present in this managed workspace. `.specify/feature.json` points at this feature directory.

## Summary

MM-375 requires runtime diagnostics for image-input upload, validation, prepare download, and image context generation. The implementation will extend existing image materialization and vision-context boundaries so they emit compact target-aware diagnostic events, expose attachment manifest and generated context paths in prepared task diagnostics, and preserve step target identity for failures. Validation will use focused unit tests for event payloads, evidence path discovery, target binding, and failure cases, plus existing integration-style vision artifact coverage.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Existing Codex worker prepare stage, existing `moonmind.vision` service, Pydantic v2 models where already present, standard-library `json` and `pathlib`  
**Storage**: Existing per-job workspace artifacts under `.moonmind/`; task context diagnostics under job artifacts; no new persistent database storage  
**Unit Testing**: pytest via `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py tests/unit/moonmind/vision/test_service.py tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/tasks/test_task_contract.py` for focused iteration and `./tools/test_unit.sh` for final unit verification  
**Integration Testing**: Existing filesystem-bound vision artifact tests in `tests/integration/vision/test_context_artifacts.py`; full hermetic runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind managed runtime worker containers and per-job workspaces  
**Project Type**: Python worker/runtime orchestration service within the MoonMind control plane  
**Performance Goals**: Diagnostic generation remains linear in attachment and target count; diagnostics contain compact metadata and paths only  
**Constraints**: Preserve authoritative target bindings; never infer target meaning from filenames, attachment order, UI heuristics, or raw workflow history; do not expose raw image bytes or credentials in diagnostics; preserve MM-375 traceability  
**Scale/Scope**: One task-shaped execution payload with objective attachments and zero or more step attachments within existing configured attachment limits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Extends existing worker and vision-service boundaries instead of adding a competing runtime.
- II. One-Click Agent Deployment: PASS. No new external service or secret is required.
- III. Avoid Vendor Lock-In: PASS. Diagnostics use MoonMind target metadata and artifact paths, not provider-specific file APIs.
- IV. Own Your Data: PASS. Evidence remains in operator-controlled task artifacts and workspace files.
- V. Skills Are First-Class and Easy to Add: PASS. Does not mutate skill sources or runtime skill materialization.
- VI. Replaceability and Scientific Method: PASS. Adds objective test evidence for diagnostic events and paths.
- VII. Runtime Configurability: PASS. Honors existing vision context enabled/provider configuration and reports disabled status explicitly.
- VIII. Modular and Extensible Architecture: PASS. Keeps upload/prepare/context diagnostics at existing service boundaries.
- IX. Resilient by Default: PASS. Failure diagnostics are explicit and compact.
- X. Facilitate Continuous Improvement: PASS. Diagnostics provide operator-readable evidence for future troubleshooting.
- XI. Spec-Driven Development: PASS. Implements one MoonSpec story with traceability to MM-375 and DESIGN-REQ-019.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain source requirements; implementation notes stay under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. Adds explicit internal diagnostics without compatibility aliases or hidden target transforms.

## Project Structure

### Documentation (this feature)

```text
specs/203-expose-image-diagnostics/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── image-diagnostics.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
├── agents/codex_worker/
│   └── worker.py
├── workflows/tasks/
│   └── task_contract.py
└── vision/
    └── service.py

api_service/
└── api/routers/
    └── temporal_artifacts.py

tests/
└── unit/
    ├── agents/codex_worker/
    │   └── test_attachment_materialization.py
    ├── api/routers/
    │   └── test_temporal_artifacts.py
    ├── moonmind/vision/
    │   └── test_service.py
    └── workflows/tasks/
        └── test_task_contract.py
```

**Structure Decision**: Add compact diagnostic event helpers to existing upload, task-contract validation, prepare materialization, and vision context service boundaries because those boundaries already own target-aware attachment metadata, evidence paths, and failure handling.

## Complexity Tracking

No constitution violations.
