# Implementation Plan: Create Page Merge Automation

**Branch**: `193-create-page-merge-automation` | **Date**: 2026-04-16 | **Spec**: `specs/193-create-page-merge-automation/spec.md`
**Input**: Single-story feature specification from `specs/193-create-page-merge-automation/spec.md`

## Summary

Implement MM-365 by adding a Create page merge automation option that is available only for ordinary PR-publishing tasks, submits the existing `mergeAutomation.enabled=true` request shape consumed by `MoonMind.Run`, and clears the option whenever publish mode or resolver-style skill selection makes it unavailable. The test strategy uses focused Vitest coverage for visibility, stale-state clearing, submitted payload shape, and resolver-skill exclusion, plus existing backend workflow tests that already validate `MoonMind.Run` merge automation consumption.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control, Python 3.12 for existing request/workflow contracts  
**Primary Dependencies**: React state and form controls, existing task creation endpoint, existing `MoonMind.Run` merge automation request parsing, Vitest, pytest  
**Storage**: No new persistent storage; merge automation remains an optional task creation payload field  
**Unit Testing**: Vitest through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`; existing Python unit suite through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: Hermetic integration runner `./tools/test_integration.sh` remains available; no new compose service required for this UI request-shape story  
**Target Platform**: Mission Control browser UI submitting to the existing FastAPI/Temporal create endpoint  
**Project Type**: Web UI with existing backend workflow contract  
**Performance Goals**: No additional network requests; merge automation visibility/state updates happen as local form state changes  
**Constraints**: Keep resolver-style tasks forced to `publish.mode=none`; preserve `publishMode=pr` and `task.publish.mode=pr`; do not change Jira Orchestrate preset behavior; do not introduce direct auto-merge semantics  
**Scale/Scope**: One Create page control and request-shape path for a single task submission

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Uses the existing `MoonMind.Run` and `MoonMind.MergeAutomation` workflow contract.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment dependencies.
- III. Avoid Vendor Lock-In: PASS. The UI only submits provider-neutral task configuration.
- IV. Own Your Data: PASS. Submitted configuration remains in MoonMind task payloads and artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. Resolver skill behavior is preserved and tested.
- VI. Replaceable Scaffolding: PASS. Adds focused request-shape tests around observable behavior.
- VII. Runtime Configurability: PASS. The option is runtime form configuration, not a hardcoded workflow fork.
- VIII. Modular Architecture: PASS. The Create page owns UI state; `MoonMind.Run` owns merge automation execution.
- IX. Resilient by Default: PASS. Stale enabled state is cleared when the option becomes unavailable.
- X. Continuous Improvement: PASS. Verification evidence will be recorded in `verification.md`.
- XI. Spec-Driven Development: PASS. Runtime changes follow this one-story spec.
- XII. Canonical Documentation Separation: PASS. Canonical Create page behavior is documented in `docs/UI/CreatePage.md`; migration artifacts stay under `specs/` and `docs/tmp`.
- XIII. Pre-Release Compatibility Policy: PASS. No aliases or compatibility transforms are introduced; the existing supported `mergeAutomation` field is submitted directly.

## Project Structure

### Documentation (this feature)

```text
specs/193-create-page-merge-automation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── create-page-merge-automation.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
docs/UI/
└── CreatePage.md

frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

tests/unit/workflows/temporal/
└── test_run_merge_gate_start.py
```

**Structure Decision**: Add the user-facing control and payload construction in the existing Create page entrypoint, test the browser request body in the existing Create page test file, and rely on existing `MoonMind.Run` merge automation unit tests for backend consumption unless a frontend submission gap exposes a backend defect.

## Complexity Tracking

No constitution violations.
