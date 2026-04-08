# Implementation Plan: Step Ledger Phase 1

**Branch**: `137-step-ledger-phase1` | **Date**: 2026-04-07 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/137-step-ledger-phase1/spec.md`

## Summary

Implement Phases 0 and 1 of the step-ledger rollout by freezing the v1 step-ledger/progress contract and moving live per-step state ownership into `MoonMind.Run`. The workflow will initialize compact ledger rows from resolved plan metadata, maintain deterministic state transitions and run-scoped attempts during execution, expose query-safe latest-run ledger/progress snapshots, and keep Memo/Search Attribute mirrors compact.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, existing MoonMind workflow/plan contracts  
**Storage**: Temporal workflow state for compact ledger/progress; existing artifacts for large evidence; Memo/Search Attributes for compact execution summary only  
**Testing**: `pytest` via `./tools/test_unit.sh`; targeted workflow-boundary unit tests; existing Temporal integration workflow tests where useful  
**Target Platform**: Linux server workers running MoonMind Temporal workflows  
**Project Type**: Backend workflow/runtime implementation  
**Performance Goals**: Ledger/progress queries remain bounded and cheap for normal polling; no artifact hydration required for progress reads  
**Constraints**: Preserve workflow determinism and replay safety; keep large logs/diagnostics out of workflow state; do not ship Phase 2+ API/UI work in this change  
**Scale/Scope**: Phase 0 contract freeze and Phase 1 workflow-owned ledger only; no `/api/executions/{workflowId}/steps` route or Mission Control steps UI yet

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. This change strengthens orchestration state in `MoonMind.Run` without adding provider-specific behavior.
- **IV. Own Your Data**: PASS. Large logs and diagnostics remain in artifacts or task-run observability; the workflow stores only compact refs and summaries.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The phase is contract-first and pushes UI/API consumers to stable workflow/query contracts rather than ad hoc status strings.
- **IX. Resilient by Default**: PASS. Deterministic workflow-owned state plus boundary tests improve replay safety and unattended execution visibility.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Scope is explicitly limited to Phases 0 and 1 and captured in the spec, plan, tasks, and traceability docs.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical semantics remain in `docs/Temporal/StepLedgerAndProgressModel.md`; rollout-only work stays scoped here and in remaining-work trackers.
- **XIII. Pre-Release Delete, Don't Deprecate**: PASS. The phase adds the new canonical ledger model directly without introducing compatibility aliases for superseded internal state shapes.

## Project Structure

### Documentation (this feature)

```text
specs/137-step-ledger-phase1/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── step-ledger-query-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/schemas/temporal_models.py                         # MODIFY: progress + ledger/query contract models
moonmind/workflows/temporal/step_ledger.py                  # NEW: pure ledger/progress state helpers
moonmind/workflows/temporal/workflows/run.py                # MODIFY: initialize/update/query ledger state
tests/unit/workflows/temporal/test_step_ledger.py           # NEW: ledger state/reducer contract tests
tests/unit/workflows/temporal/workflows/test_run_step_ledger.py  # NEW: workflow-owned ledger lifecycle/query tests
tests/unit/api/routers/test_executions.py                   # MODIFY: progress model serialization coverage only if schema wiring changes
tests/integration/workflows/temporal/workflows/test_run.py  # MODIFY: latest-run query availability where targeted integration coverage adds value
```

**Structure Decision**: Keep pure ledger/progress state logic in a dedicated backend helper module so `MoonMind.Run` stays orchestration-focused. Keep externally consumable schema contracts in `moonmind/schemas/temporal_models.py` so later API phases can reuse the same models unchanged.

## Complexity Tracking

No constitution violations currently require mitigation. The only elevated risk is workflow contract compatibility for in-flight runs; mitigation is boundary-level coverage around query payload shapes and internal defaults rather than introducing compatibility aliases.
