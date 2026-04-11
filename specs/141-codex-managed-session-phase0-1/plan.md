# Implementation Plan: Codex Managed Session Phase 0 and Phase 1

**Branch**: `141-codex-managed-session-phase0-1` | **Date**: 2026-04-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/141-codex-managed-session-phase0-1/spec.md`

## Summary

Implement the Phase 0 and Phase 1 managed-session slice by aligning the canonical Codex session-plane documentation with the current production truth model and hardening `MoonMind.AgentSession` around typed workflow Updates, deterministic validators, and caller wiring that no longer depends on the generic mutating `control_action` signal. Runtime intent is required: deliverables include production code changes and validation tests, not docs/spec-only work.

## Technical Context

**Language/Version**: Python 3.12 in the managed agent environment; repository runtime remains Python-based Temporal workers
**Primary Dependencies**: Temporal Python SDK workflow handlers/validators, Pydantic managed-session schemas, FastAPI task-run router, MoonMind managed-session controller/store artifacts
**Storage**: Temporal workflow state, durable artifacts, bounded workflow metadata, and JSON-backed `ManagedSessionStore` records as the operational recovery index
**Testing**: pytest unit coverage through `./tools/test_unit.sh`; focused workflow/API/adapter tests during iteration
**Target Platform**: Docker Compose MoonMind deployment with Temporal workers and task-scoped managed Codex containers
**Project Type**: Backend workflow contract, runtime adapter wiring, API control routing, and canonical documentation alignment
**Performance Goals**: Preserve existing managed-session behavior while rejecting invalid mutations before activity execution and avoiding extra runtime round trips except where current epoch lookup is required
**Constraints**: Phase 0 and Phase 1 only; do not implement Phase 2 standalone steer runtime behavior, full cancel/terminate semantics split, Continue-As-New, observability/search-attribute rollout, replay CI, or worker versioning in this slice
**Scale/Scope**: One Codex task-scoped session workflow and direct callers: parent run workflow, agent-run adapter wiring, task-run session-control API, schemas, documentation, and unit tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan strengthens MoonMind's orchestration boundary for the existing Codex runtime instead of rebuilding Codex behavior.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependency or external service is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific behavior remains isolated in the managed Codex session boundary and adapter surfaces.
- **IV. Own Your Data**: PASS. The plan preserves artifact-backed operator/audit truth and clarifies the recovery role of the local session store.
- **V. Skills Are First-Class and Easy to Add**: PASS. No agent-skill runtime source, overlay, or materialization behavior changes are included.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The main work is contract hardening at the workflow/API boundary.
- **VII. Powerful Runtime Configurability**: PASS. No hardcoded runtime selection or new configuration precedence is introduced.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within existing schemas, workflow handlers, adapter callbacks, API routing, and tests.
- **IX. Resilient by Default**: PASS. Typed Update validators and current-epoch termination routing reduce ambiguous and stale workflow mutations.
- **X. Facilitate Continuous Improvement**: PASS. Validation artifacts and tests improve repeatability of future workflow-surface changes.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The current spec, plan, contracts, traceability, and tasks define the slice before or alongside implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical documentation remains declarative; phased rollout detail stays in Spec Kit artifacts.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The generic mutating signal is removed rather than retained as a compatibility alias.

## Project Structure

### Documentation (this feature)

```text
specs/141-codex-managed-session-phase0-1/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── agent-session-workflow-controls.md
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/
└── task_runs.py

docs/ManagedAgents/
└── CodexManagedSessionPlane.md

moonmind/schemas/
└── managed_session_models.py

moonmind/workflows/adapters/
└── codex_session_adapter.py

moonmind/workflows/temporal/
├── service.py
└── workflows/
    ├── agent_run.py
    ├── agent_session.py
    └── run.py

tests/unit/
├── api/routers/test_task_runs.py
└── workflows/
    ├── adapters/test_codex_session_adapter.py
    └── temporal/
        ├── test_temporal_service.py
        └── workflows/
            ├── test_agent_session.py
            └── test_run_codex_sessions.py
```

**Structure Decision**: Use the existing backend workflow, schema, adapter, API, and unit-test layout. No new package or service boundary is required for this Phase 0/1 contract-hardening slice.

## Phase 0: Research

Research output is captured in [research.md](./research.md). All planning unknowns are resolved:

- The near-term truth model is artifacts plus bounded workflow metadata for operator/audit truth, `ManagedSessionStore` for operational recovery, and container-local state as disposable cache.
- The production artifact publisher is the managed-session controller/supervisor path.
- The Phase 1 workflow mutation surface is typed Updates plus `attach_runtime_handles` as the remaining state-propagation Signal.
- `InterruptTurn` can be wired through existing controller/runtime activity support.
- Real end-to-end `steer_turn`, full cancel/terminate semantics, Continue-As-New, and deployment versioning remain out of scope for this slice.

## Phase 1: Design

- Data model: [data-model.md](./data-model.md)
- Workflow control contract: [contracts/agent-session-workflow-controls.md](./contracts/agent-session-workflow-controls.md)
- Source requirement traceability: [contracts/requirements-traceability.md](./contracts/requirements-traceability.md)
- Validation quickstart: [quickstart.md](./quickstart.md)

## Implementation Plan

1. Update the canonical managed-session plane doc so it distinguishes operator/audit truth, operational recovery index, disposable cache state, and the production artifact-publisher path.
2. Add or update managed-session workflow request schemas so typed control payloads carry the validation data needed at the workflow boundary.
3. Refactor `MoonMind.AgentSession` to initialize handler-visible state in `@workflow.init`, remove the generic mutating `control_action` signal, keep `attach_runtime_handles` as the Signal, and expose typed Updates with validators.
4. Wire `InterruptTurn` through the existing runtime activity surface and preserve the current deterministic behavior for runtime-unsupported steering.
5. Update parent workflow, adapter, API router, and service callers to target typed Update names and provide current `sessionEpoch` where validators require it.
6. Add or update unit tests for workflow validators, update handlers, caller routing, stale epoch handling, and API/service payloads.
7. Run focused tests, full unit verification, and the DOC-REQ traceability check.

## Verification Plan

### Automated Tests

1. Focused contract tests:

```bash
pytest \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py \
  tests/unit/workflows/adapters/test_codex_session_adapter.py \
  tests/unit/workflows/temporal/test_temporal_service.py \
  tests/unit/api/routers/test_task_runs.py \
  tests/unit/schemas/test_managed_session_models.py \
  -q --tb=short
```

2. Full local unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

3. Traceability gate:

```bash
python - <<'PY'
from pathlib import Path
import re
text = Path("specs/141-codex-managed-session-phase0-1/spec.md").read_text()
source_ids = sorted(set(re.findall(r"^- \*\*(DOC-REQ-\d+)\*\*:", text, re.M)))
fr_section = text.split("### Functional Requirements", 1)[1]
missing = [doc_id for doc_id in source_ids if not re.search(r"Maps: [^\n]*" + re.escape(doc_id), fr_section)]
raise SystemExit(1 if missing else 0)
PY
```

### Manual Validation

1. Review `docs/ManagedAgents/CodexManagedSessionPlane.md` and confirm the current production truth surfaces and publisher path are explicit.
2. Inspect `MoonMind.AgentSession` and confirm the generic mutating signal is absent, `attach_runtime_handles` remains the only Signal, and mutators use typed Updates.
3. Inspect caller surfaces and confirm parent/session/API controls target typed Update names with epoch-aware payloads.

## Complexity Tracking

No constitution violations require justification.

## Post-Design Constitution Check

PASS. Phase 1 design keeps the change scoped to existing workflow and adapter boundaries, includes production runtime code and validation tests, preserves artifact-owned operator truth, removes the superseded generic mutation surface, and maps every `DOC-REQ-*` to planned implementation and validation in [contracts/requirements-traceability.md](./contracts/requirements-traceability.md).
