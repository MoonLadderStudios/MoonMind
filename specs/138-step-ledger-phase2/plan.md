# Implementation Plan: Step Ledger Phase 2

**Branch**: `138-step-ledger-phase2` | **Date**: 2026-04-08 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/138-step-ledger-phase2/spec.md`

## Summary

Implement the step-ledger evidence phase by carrying child-run lineage and canonical evidence refs into `MoonMind.Run` step rows without moving large logs or diagnostics into workflow state. The child `MoonMind.AgentRun` workflow and agent-runtime activities will enrich compact result metadata, and the parent workflow will deterministically group that metadata into `refs` and `artifacts` fields on the latest-run step ledger.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, existing managed-run/session runtime stores and artifact services  
**Storage**: Workflow state for compact step refs; managed-run/session stores and artifact storage for runtime evidence; artifact metadata for step-scoped linkage  
**Testing**: `pytest` via targeted unit/workflow tests and `./tools/test_unit.sh`  
**Target Platform**: Linux server workers running MoonMind Temporal workflows  
**Project Type**: Backend workflow/runtime implementation  
**Performance Goals**: Step-ledger queries remain bounded and artifact-body-free; evidence grouping does not require artifact hydration during workflow execution  
**Constraints**: Preserve replay determinism; keep API/UI rollout out of scope; do not add compatibility alias fields; do not store raw logs/diagnostics in workflow state  
**Scale/Scope**: Parent-child lineage, grouped evidence refs, and step-scoped artifact metadata only

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change improves orchestration lineage and evidence boundaries without adding provider-specific orchestration logic to the core workflow.
- **IV. Own Your Data**: PASS. Large logs and diagnostics stay in artifacts and managed-run/session stores, with the workflow carrying only compact refs.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work fills the frozen step-ledger contract rather than introducing new UI-specific evidence shapes.
- **IX. Resilient by Default**: PASS. Evidence grouping is derived from compact refs and deterministic metadata, preserving replay-safe workflow state.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This phase has its own feature artifacts and implementation tasks instead of extending Phase 1 informally.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical semantics stay in `docs/Temporal/StepLedgerAndProgressModel.md`; rollout-specific work is captured here.
- **XIII. Pre-Release Delete, Don't Deprecate**: PASS. The change fills existing canonical fields directly and does not add compatibility wrappers.

## Project Structure

### Documentation (this feature)

```text
specs/138-step-ledger-phase2/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── step-ledger-evidence-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/workflows/run.py                 # MODIFY: map child results into step refs/artifact slots
moonmind/workflows/temporal/step_ledger.py                   # MODIFY: merge structured refs/artifacts/checks into rows
moonmind/workflows/temporal/workflows/agent_run.py           # MODIFY: enrich child results with lineage/task-run metadata
moonmind/workflows/temporal/activity_runtime.py              # MODIFY: publish/fetch compact step-scoped observability metadata
tests/unit/workflows/temporal/test_step_ledger.py            # MODIFY: structured row merge coverage
tests/unit/workflows/temporal/workflows/test_run_step_ledger.py  # MODIFY: parent evidence/lineage coverage
tests/unit/workflows/temporal/test_agent_runtime_activities.py    # MODIFY: artifact metadata + managed-run metadata coverage
tests/unit/workflows/temporal/workflows/test_agent_run_jules_execution.py # MODIFY: child lineage metadata coverage
tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py # MODIFY: session-backed lineage/task-run metadata coverage
```

**Structure Decision**: Keep step-row mutation logic in the pure `step_ledger.py` helper and keep workflow-specific result grouping in `MoonMind.Run`. Runtime activities and `MoonMind.AgentRun` provide compact metadata only; they do not own parent step-row state.

## Complexity Tracking

The main risk is contract drift between parent workflow grouping and runtime metadata emission. Mitigation: add boundary tests on both sides and keep the parent grouping logic tolerant of missing optional refs while remaining fail-fast on unsupported shape assumptions.
