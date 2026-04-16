# Implementation Plan: Merge Automation Visibility

**Branch**: `189-merge-automation-visibility` | **Date**: 2026-04-16 | **Spec**: `specs/189-merge-automation-visibility/spec.md`
**Input**: Single-story feature specification from `specs/189-merge-automation-visibility/spec.md`

## Summary

Implement MM-354 by projecting merge automation state into the parent run summary, writing durable merge automation artifacts from `MoonMind.MergeAutomation`, and rendering the state on Mission Control task detail. The test strategy uses focused workflow/unit tests for artifact and summary shapes plus frontend tests for the operator-visible panel.

## Technical Context

**Language/Version**: Python 3.12, TypeScript/React  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, FastAPI schemas, existing artifact activities, React/Vitest  
**Storage**: Existing Temporal artifact storage only; no new persistent database tables  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`, Vitest via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`  
**Integration Testing**: Existing hermetic integration runner `./tools/test_integration.sh`; no new compose service required  
**Target Platform**: Linux server/container runtime and Mission Control browser UI  
**Project Type**: Temporal workflow plus web UI  
**Performance Goals**: Keep workflow payloads compact and artifact writes bounded to one summary, one snapshot per gate evaluation, and one resolver artifact per attempt  
**Constraints**: Do not embed large provider payloads or secrets in workflow history; preserve parent-owned merge automation semantics; keep UI scoped to PR publishing  
**Scale/Scope**: One merge automation child workflow per PR-publishing parent run

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses existing Temporal workflows and artifact activities.
- II. One-Click Agent Deployment: PASS. No new external dependencies or services.
- III. Avoid Vendor Lock-In: PASS. Visibility surfaces are provider-neutral and compact.
- IV. Own Your Data: PASS. Artifacts remain in MoonMind-owned artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS. Does not alter skill runtime behavior.
- VI. Replaceable Scaffolding: PASS. Adds contract tests around observable behavior.
- VII. Runtime Configurability: PASS. Uses existing merge automation configuration.
- VIII. Modular Architecture: PASS. Workflow, parent summary, and UI boundaries remain separate.
- IX. Resilient by Default: PASS. Artifact write failures must not falsely change terminal outcomes.
- X. Continuous Improvement: PASS. Verification evidence will be recorded in `verification.md`.
- XI. Spec-Driven Development: PASS. Runtime changes follow this one-story spec.
- XII. Canonical Documentation Separation: PASS. Migration artifact lives under `specs/` and `docs/tmp`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases are introduced; optional payload additions preserve current invocation shape.

## Project Structure

### Documentation (this feature)

```text
specs/189-merge-automation-visibility/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── merge-automation-visibility.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/temporal_models.py
└── workflows/temporal/workflows/
    ├── merge_automation.py
    └── run.py

frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx

tests/unit/workflows/temporal/
├── test_run_parent_owned_merge_automation.py
└── workflows/test_merge_automation_temporal.py
```

**Structure Decision**: Modify the existing Temporal workflow, parent run summary, and task detail UI surfaces where merge automation behavior already lives.

## Complexity Tracking

No constitution violations.
