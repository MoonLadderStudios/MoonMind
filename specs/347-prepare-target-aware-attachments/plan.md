# Implementation Plan: Prepare-Time Target-Aware Attachment Materialization

**Branch**: `[347-prepare-target-aware-attachments]` | **Date**: 2026-05-13 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/347-prepare-target-aware-attachments/spec.md`

## Summary

Deliver `MM-648` by tightening existing target-aware attachment preparation so step-scoped attachments require stable step identity, prepared manifests expose stable materialization/status metadata, and regression coverage proves reorder, preset apply, and text edits cannot silently retarget attachments. The implementation builds on `moonmind/workflows/tasks/prepared_context.py`, `moonmind/agents/codex_worker/worker.py`, and existing workflow boundary tests from prior target-aware input work.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing artifact/vision/prepared-context helpers  
**Storage**: Existing artifact store and workspace-local `.moonmind/` prepared files only; no new persistent storage  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: pytest hermetic integration through `./tools/test_integration.sh` where needed  
**Target Platform**: MoonMind worker/runtime execution on Linux containers  
**Project Type**: Backend workflow/runtime library  
**Performance Goals**: Manifest generation remains linear in attachment count and avoids loading binary bytes into workflow-visible payloads  
**Constraints**: No binary bytes in workflow history; target binding must be explicit; fail fast on invalid preparation; no compatibility aliases for internal contracts  
**Scale/Scope**: One task with objective attachments and multiple step attachments across normalized/preset-derived step payloads

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - uses existing workflow/runtime boundaries and provider adapters.
- II. One-Click Agent Deployment: PASS - no new mandatory external service or secret.
- III. Avoid Vendor Lock-In: PASS - target-aware refs are provider-neutral.
- IV. Own Your Data: PASS - prepared files and context refs stay in operator-controlled artifact/workspace paths.
- V. Skills Are First-Class and Easy to Add: PASS - no skill source mutation or runtime skill contract changes.
- VI. Scientific Method / Tests Anchor: PASS - unit and workflow-boundary tests validate the contract.
- VII. Runtime Configurability: PASS - no hardcoded operator settings added.
- VIII. Modular Architecture: PASS - changes remain in prepared-context and worker preparation boundaries.
- IX. Resilient by Default: PASS - invalid attachments fail explicitly before step execution.
- X. Continuous Improvement: PASS - final evidence preserves `MM-648` traceability.
- XI. Spec-Driven Development: PASS - this plan follows `spec.md` and leads to `tasks.md`.
- XII. Canonical Documentation Separation: PASS - implementation notes stay in feature artifacts, not canonical docs.
- XIII. Pre-Release Velocity: PASS - unsupported ambiguous step attachment payloads fail fast rather than receiving compatibility fallback retargeting.

## Project Structure

### Documentation (this feature)

```text
specs/347-prepare-target-aware-attachments/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── prepared-attachment-manifest.md
├── quickstart.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/prepared_context.py
tests/unit/workflows/tasks/test_prepared_context.py
moonmind/agents/codex_worker/worker.py
tests/unit/agents/codex_worker/test_attachment_materialization.py
tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py
```

**Structure Decision**: Keep pure target-binding and compact workflow metadata in `moonmind/workflows/tasks/prepared_context.py`; keep actual file materialization and `.moonmind/attachments_manifest.json` writing in the Codex worker preparation boundary.

## Complexity Tracking

No constitution violations or additional complexity exceptions.
