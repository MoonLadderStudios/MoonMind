# Implementation Plan: Remove Legacy Merge Automation Workflow

**Branch**: `193-remove-legacy-merge-automation` | **Date**: 2026-04-16 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/193-remove-legacy-merge-automation/spec.md`

## Summary

Remove the dead legacy activity-based `MoonMind.MergeAutomation` workflow class from `merge_gate.py`, keep `merge_gate.py` as a helper module for the active `merge_automation.py` workflow, and delete the now-unreachable `merge_automation.create_resolver_run` activity registration/runtime path. Validation is test-first: add/adjust tests that prove only the active workflow remains registered, helper behavior still works, active workflow launches `pr-resolver` through child `MoonMind.Run` with `publishMode=none`, and no live legacy activity references remain.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, pytest, existing MoonMind Temporal workflow helpers  
**Storage**: No new persistent storage  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: pytest workflow-boundary tests through `./tools/test_unit.sh` focused on Temporal workflow modules; compose-backed integration suite not required for this internal cleanup  
**Target Platform**: MoonMind worker containers on Linux  
**Project Type**: Python service and Temporal workflow orchestration  
**Performance Goals**: No additional runtime work; cleanup must not add workflow activity calls or resolver launches  
**Constraints**: Preserve active merge readiness semantics, preserve child `MoonMind.Run` resolver launch with `publishMode=none`, do not add compatibility aliases for deleted pre-release internals, keep raw credential data out of logs/artifacts  
**Scale/Scope**: One workflow family cleanup plus focused tests and documentation/input traceability

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The active implementation continues to orchestrate `pr-resolver` through `MoonMind.Run` instead of embedding merge logic.
- II. One-Click Agent Deployment: PASS. No deployment prerequisites or secrets change.
- III. Avoid Vendor Lock-In: PASS. Cleanup stays behind existing workflow/activity boundaries.
- IV. Own Your Data: PASS. No external data storage or credential exposure changes.
- V. Skills Are First-Class and Easy to Add: PASS. `pr-resolver` remains a skill launched through the existing run substrate.
- VI. Scientific Method / Tests Anchor: PASS. The work is driven by targeted unit and workflow-boundary tests.
- VII. Runtime Configurability: PASS. No hardcoded operator configuration is introduced.
- VIII. Modular Architecture: PASS. Shared helper functions remain isolated from the active workflow class.
- IX. Resilient by Default: PASS. Active child workflow cancellation and terminal outcome semantics remain covered.
- X. Continuous Improvement: PASS. Verification records grep and test evidence for future review.
- XI. Spec-Driven Development: PASS. This plan follows the MM-364 spec and creates task/verification artifacts before completion.
- XII. Canonical Documentation: PASS. Volatile orchestration input remains under `docs/tmp`; canonical docs are updated only if they imply both workflow paths are active.
- XIII. Pre-Release Compatibility: PASS. The legacy internal activity/workflow path is removed rather than wrapped with compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/193-remove-legacy-merge-automation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── merge-automation-cleanup.md
├── checklists/
│   └── requirements.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── activity_catalog.py
├── activity_runtime.py
├── worker_entrypoint.py
└── workflows/
    ├── merge_automation.py
    └── merge_gate.py

tests/unit/workflows/temporal/
├── test_merge_gate_workflow.py
└── workflows/
    └── test_merge_automation_temporal.py

docs/Tasks/
└── PrMergeAutomation.md
```

**Structure Decision**: Keep production changes inside the existing Temporal workflow and activity modules. Keep helper tests in `test_merge_gate_workflow.py`; move workflow-boundary coverage to the active `test_merge_automation_temporal.py`; remove legacy workflow tests that only validate the deleted activity-based path.

## Complexity Tracking

No constitution violations.
