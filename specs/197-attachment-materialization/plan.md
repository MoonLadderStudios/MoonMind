# Implementation Plan: Materialize Attachment Manifest and Workspace Files

**Branch**: `197-attachment-materialization` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/197-attachment-materialization/spec.md`

**Note**: This template is filled in by the `/moonspec-plan` workflow. The repository setup script could not run in this managed branch because the active branch is `mm-370-8edeb6d2`, so artifacts were generated manually from `.specify/feature.json`.

## Summary

MM-370 requires prepare-time materialization for task input attachments. The implementation will extend the existing worker prepare stage so objective-scoped and step-scoped `inputAttachments` from the canonical task payload are downloaded through the trusted MoonMind API, written to deterministic target-aware workspace paths, and recorded in `.moonmind/attachments_manifest.json`. Validation will use unit coverage for path, manifest, stable step reference, and failure behavior plus worker prepare boundary coverage to prove attachments are present before runtime execution.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, httpx, existing Codex worker prepare stage, existing Temporal artifact download API  
**Storage**: Existing artifact store for source bytes; local per-job workspace files under `.moonmind/inputs/` and `.moonmind/attachments_manifest.json`; no new persistent storage  
**Unit Testing**: pytest via `./tools/test_unit.sh`, with focused pytest targets for iteration  
**Integration Testing**: pytest worker/activity boundary coverage in the required unit suite, plus `./tools/test_integration.sh` for hermetic integration when Docker is available  
**Target Platform**: MoonMind managed-agent worker containers and per-job workspaces  
**Project Type**: Python worker/runtime orchestration service  
**Performance Goals**: Materialization is linear in declared attachment count and streams or writes compact image bytes without embedding them in workflow history  
**Constraints**: Do not infer target binding from filenames or artifact metadata; do not embed image bytes in Temporal histories or task instruction text; fail explicitly for partial materialization; preserve MM-370 traceability  
**Scale/Scope**: One task-shaped execution payload with objective attachments and step attachments within existing configured attachment limits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Extends the existing worker prepare stage and artifact API rather than introducing a new runtime.
- II. One-Click Agent Deployment: PASS. No new external dependency.
- III. Avoid Vendor Lock-In: PASS. Uses generic MoonMind artifact refs and workspace files, not provider-specific file APIs.
- IV. Own Your Data: PASS. Artifact bytes remain in MoonMind-owned storage and per-job workspaces.
- V. Skills Are First-Class and Easy to Add: PASS. No runtime skill mutation.
- VI. Replaceability and Scientific Method: PASS. Behavior is covered by deterministic tests at the prepare boundary.
- VII. Runtime Configurability: PASS. Existing attachment policy and artifact API remain authoritative before prepare.
- VIII. Modular and Extensible Architecture: PASS. Materialization is isolated to prepare helpers and worker boundary wiring.
- IX. Resilient by Default: PASS. Partial materialization fails explicitly before runtime execution.
- X. Facilitate Continuous Improvement: PASS. Failures include concrete materialization diagnostics.
- XI. Spec-Driven Development: PASS. This plan follows a one-story MM-370 spec with traceable tests.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain source requirements; implementation notes stay in spec artifacts.
- XIII. Pre-release Compatibility Policy: PASS. Unsupported or invalid internal payload shapes fail rather than compatibility-transforming target bindings.

## Project Structure

### Documentation (this feature)

```text
specs/197-attachment-materialization/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── attachment-materialization.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
└── agents/codex_worker/
    └── worker.py

tests/
└── unit/
    └── agents/codex_worker/
        └── test_attachment_materialization.py
```

**Structure Decision**: Implement prepare-time attachment materialization in the existing Codex worker prepare boundary because that is where per-job workspace paths, `.moonmind` directories, logs, and pre-runtime setup are already controlled. Keep validation focused on helper behavior and prepare-stage boundary effects.

## Complexity Tracking

No constitution violations.
