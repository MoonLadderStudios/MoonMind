# Implementation Plan: Generate Target-Aware Vision Context Artifacts

**Branch**: `197-generate-target-aware-vision-context` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/197-generate-target-aware-vision-context/spec.md`

**Note**: This template is filled in by the `/moonspec-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

MM-371 requires runtime preparation to generate deterministic, target-aware image context artifacts for text-first agents. The implementation will extend the existing `moonmind.vision` service so callers can provide explicit objective and step attachment targets, render per-target Markdown context, and write `.moonmind/vision/image_context_index.json` with source attachment traceability and status metadata. Validation will use focused unit tests for target grouping, disabled/provider-unavailable status behavior, path stability, and source traceability plus an integration-style filesystem test that verifies artifact files are written to the desired workspace paths.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Existing `moonmind.vision` dataclass service, Pydantic v2 settings, standard-library `json` and `pathlib`  
**Storage**: Workspace-local generated files under `.moonmind/vision/`; no new persistent database storage  
**Unit Testing**: pytest via `./tools/test_unit.sh tests/unit/moonmind/vision/test_service.py` for focused iteration and `./tools/test_unit.sh` for final unit verification  
**Integration Testing**: pytest filesystem-bound integration coverage in `tests/integration/vision/test_context_artifacts.py`; full hermetic runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind managed runtime worker prepare stages on Linux workspaces  
**Project Type**: Python service/library within a larger web service and workflow orchestration system  
**Performance Goals**: Generation remains linear in number of attachment targets and source attachments; no image bytes are embedded in workflow payloads or generated index content  
**Constraints**: Preserve explicit objective versus step target meaning; do not infer target meaning from filenames or artifact links; disabled context generation must not block raw materialization/manifest work; derived summaries remain secondary to source refs; generated index must be deterministic for the same target set and config  
**Scale/Scope**: One task-shaped execution's objective target plus zero or more step targets, each with zero or more image attachments

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Extends MoonMind's runtime preparation support instead of creating a new agent behavior layer.
- II. One-Click Agent Deployment: PASS. Uses existing Python runtime and workspace files only.
- III. Avoid Vendor Lock-In: PASS. Keeps provider-specific caption/OCR behavior behind existing vision configuration and deterministic placeholders.
- IV. Own Your Data: PASS. Source refs and derived artifacts stay in the operator-controlled workspace.
- V. Skills Are First-Class and Easy to Add: PASS. Does not mutate agent skill sources or runtime skill materialization.
- VI. Replaceability and Scientific Method: PASS. Defines test-first behavior for deterministic artifacts and traceability.
- VII. Runtime Configurability: PASS. Honors existing vision context enabled/provider/OCR runtime configuration.
- VIII. Modular and Extensible Architecture: PASS. Keeps generation in `moonmind.vision` service boundaries.
- IX. Resilient by Default: PASS. Disabled/provider-unavailable states become explicit deterministic statuses.
- X. Facilitate Continuous Improvement: PASS. Generated index provides inspectable target/status evidence.
- XI. Spec-Driven Development: PASS. Implements one MoonSpec story with traceability to MM-371 and DESIGN-REQ-012.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain source requirements; implementation artifacts live under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. Adds a new internal service capability without compatibility aliases or hidden semantic transforms.

## Project Structure

### Documentation (this feature)

```text
specs/197-generate-target-aware-vision-context/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── vision-context-artifacts.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
└── vision/
    ├── __init__.py
    ├── service.py
    └── settings.py

tests/
├── unit/
│   └── moonmind/
│       └── vision/
│           └── test_service.py
└── integration/
    └── vision/
        └── test_context_artifacts.py
```

**Structure Decision**: Implement target-aware context rendering and artifact writing inside `moonmind.vision.service` because the existing service already owns deterministic attachment Markdown rendering and runtime vision configuration. Export any new public dataclasses through `moonmind.vision.__init__`. Keep tests close to the existing unit coverage and add one integration-style filesystem test for workspace output paths.

## Complexity Tracking

No constitution violations.
