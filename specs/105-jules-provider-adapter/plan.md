# Implementation Plan: Jules Provider Adapter Runtime Alignment

**Branch**: `105-jules-provider-adapter` | **Date**: 2026-03-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/105-jules-provider-adapter/spec.md`

## Summary

Align Jules runtime behavior with the updated provider design by replacing step-chained Jules execution with one-shot bundle dispatch, making branch publication outcomes truthful, and preserving `sendMessage` only for clarification/resume exception paths. The implementation stays inside MoonMind orchestration layers (`worker_runtime.py`, `run.py`, `agent_run.py`, Jules activities/tests) so the Jules transport and shared adapter layers remain thin and reusable.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` currently allows `>=3.10,<3.14`)  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, httpx, pytest/pytest-asyncio  
**Storage**: Artifact-backed workflow metadata plus workflow-local state; no new database schema  
**Testing**: pytest via `./tools/test_unit.sh`; targeted integration coverage where needed  
**Target Platform**: Linux server workers under Docker Compose / Temporal  
**Project Type**: Single Python project centered on the `moonmind` package  
**Performance Goals**: Reduce Jules session churn and follow-up round trips by collapsing standard multi-step work into one provider session while keeping polling cadence and workflow overhead bounded  
**Constraints**: Temporal replay safety, no raw secrets in workflow history/logs, truthful publish semantics, no docs-only outcome for this runtime-intent feature, compatibility safety for in-flight workflow histories  
**Scale/Scope**: Existing Jules execution and integration workflow paths, plus workflow-boundary regression tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | **PASS** | Jules remains a provider-specific runtime behind MoonMind orchestration; no provider logic moves into a MoonMind-specific cognitive engine. |
| II. One-Click Agent Deployment | **PASS** | No deployment topology change; feature stays within existing Compose/worker flows. |
| III. Avoid Vendor Lock-In | **PASS** | Bundle compilation lives in generic orchestration layers, not in Jules transport internals. |
| IV. Own Your Data | **PASS** | Bundle manifests/results use MoonMind-controlled artifact/state surfaces. |
| V. Skills Are First-Class | **PASS** | No skill runtime changes. |
| VI. Thin Scaffolding, Thick Contracts | **PASS** | Consolidates runtime semantics behind a bundle/manifest contract instead of adding more provider-specific choreography. |
| VII. Runtime Configurability | **PASS** | Existing runtime gate and publish controls remain config-driven. |
| VIII. Modular Architecture | **PASS** | Changes target workflow helpers and Jules-specific activities without broad core rewrites. |
| IX. Resilient by Default | **PASS** | Plan adds workflow-boundary tests for truthful branch publication and replay-safe bundle dispatch. |
| X. Continuous Improvement | **PASS** | Result metadata becomes more explicit about incomplete bundles and verification failures. |
| XI. Spec-Driven Development | **PASS** | Implementation is anchored to this spec with `DOC-REQ-*` traceability. |
| XII. Canonical Docs vs Tmp | **PASS** | Canonical behavior comes from `docs/ExternalAgents/JulesAdapter.md`; migration/implementation detail stays in spec artifacts. |
| XIII. Delete, Don't Deprecate | **PASS** | Normal multi-step Jules session chaining will be removed rather than retained as a silent compatibility path for new executions. |

## Project Structure

### Documentation (this feature)

```text
specs/105-jules-provider-adapter/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   ├── jules-bundle-runtime-contract.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── worker_runtime.py                         # Current step expansion logic still generates sequential Jules nodes
├── activities/jules_activities.py           # Jules workflow activities, branch publish helper
├── activity_catalog.py                      # Existing Jules activity routing
└── workflows/
    ├── run.py                               # Current jules_session_id chaining + integration-stage send_message progression
    └── agent_run.py                         # External polling path, clarification exception path, result metadata

moonmind/workflows/adapters/
├── jules_agent_adapter.py                   # Shared adapter boundary, AUTO_CREATE_PR start behavior
└── jules_client.py                          # Thin provider transport; merge/base-update helpers

moonmind/schemas/
└── agent_runtime_models.py                  # Canonical request/result contracts used by workflows/adapters

tests/unit/workflows/temporal/
├── test_jules_merge_pr.py                   # Existing branch-merge helper tests
├── test_jules_activities.py                 # Jules activity tests
└── workflows/test_run_integration.py        # Existing multi-step send_message workflow tests to replace

tests/unit/workflows/temporal/workflows/
├── test_run_artifacts.py
└── test_run_integration.py

tests/integration/
└── test_jules_integration.py                # Real Jules sendMessage lifecycle test to update or retire
```

**Structure Decision**: Keep the implementation inside existing Temporal workflow and Jules adapter/activity modules. Add a dedicated bundle contract artifact under `specs/105-jules-provider-adapter/contracts/` rather than creating a new top-level runtime package.

## Research Summary

1. **Bundle at orchestration time, not in the provider adapter**: `JulesAgentAdapter` and `JulesClient` are already close to the desired thin-boundary design. The problematic step-chaining behavior lives in `worker_runtime.py` and `run.py`, so bundle compilation should happen after ordered plan nodes are known and before child/external execution starts.
2. **Remove `jules_session_id` as the standard execution path**: Both `run.py` and `agent_run.py` currently preserve and consume `jules_session_id` for normal step progression. That is the direct contradiction to the updated doc and should be deleted for standard flows.
3. **Keep clarification auto-answer in `MoonMind.AgentRun`**: The current auto-answer loop already lives in `agent_run.py`, which matches the updated design. It should be preserved as the only normal `sendMessage` exception path.
4. **Make branch publication a MoonMind-owned success gate**: `run.py` currently treats branch auto-merge as best-effort in the integration path. That needs to become a required success boundary when the caller requested `publishMode == "branch"`.
5. **Use workflow-boundary tests as the primary regression anchor**: The changed behavior crosses planner/orchestrator/adapters. Unit-only transport tests are insufficient; replay-safe workflow tests must prove one-shot bundling, no step chaining, and truthful branch publication failure mapping.

## Phase 0: Research Output

Research is captured in `research.md` and resolves the main design choices:

- where to bundle Jules work,
- how to encode bundle manifest metadata without bloating workflow history,
- how to preserve clarification-only `sendMessage`,
- how to map branch publication failures to MoonMind-owned outcomes.

## Phase 1: Design & Contracts

### Data Model Design

Create lightweight execution-side models/documented structures for:

- a synthetic Jules bundle node derived from ordered plan nodes,
- a bundle manifest describing original node IDs and compiled brief sections,
- a bundle result summary describing provider outcome, verification outcome, publish outcome, and incomplete checklist state.

### Contract Design

Document two contracts:

1. `contracts/jules-bundle-runtime-contract.md`
   - bundle eligibility rules,
   - consolidated brief shape,
   - bundle manifest metadata fields,
   - truthful result and branch-publication outcome rules.
2. `contracts/requirements-traceability.md`
   - one row per `DOC-REQ-*`,
   - mapped FRs,
   - implementation surfaces,
   - explicit validation strategy.

### Planned Implementation Surfaces

#### Change 1: Jules Bundle Compilation in Workflow Orchestration

**Primary files**: `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/worker_runtime.py`

- Replace standard multi-node Jules progression with deterministic bundle grouping for consecutive Jules-targeted execution nodes.
- Compile grouped work into one checklist-shaped brief and one bundle manifest.
- Remove `jules_session_id` propagation from normal execution.

#### Change 2: Preserve Clarification-Only Follow-Up Messaging

**Primary files**: `moonmind/workflows/temporal/workflows/agent_run.py`, `moonmind/workflows/temporal/activities/jules_activities.py`

- Keep `integration.jules.send_message` and auto-answer behavior only for clarification/intervention/resume flows.
- Ensure standard bundled execution never routes subsequent logical steps through `sendMessage`.

#### Change 3: Truthful Branch Publication and Result Ownership

**Primary files**: `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/activities/jules_activities.py`, `moonmind/workflows/adapters/jules_agent_adapter.py`

- Preserve AUTO_CREATE_PR start behavior for `pr`/`branch`.
- Make branch publication success contingent on PR extraction, optional base retarget, merge success, and any required MoonMind verification.
- Surface non-success outcomes when publication or verification fails.

#### Change 4: Result Metadata and Bundle Visibility

**Primary files**: `moonmind/workflows/temporal/workflows/agent_run.py`, `moonmind/workflows/temporal/workflows/run.py`

- Attach bundle manifest/result metadata to final result handling.
- Expose incomplete checklist or verification failures explicitly instead of relying on raw provider success alone.

#### Change 5: Boundary-Level Regression Coverage

**Primary files**: `tests/unit/workflows/temporal/workflows/test_run_integration.py`, `tests/unit/workflows/temporal/test_jules_merge_pr.py`, `tests/unit/workflows/temporal/test_jules_activities.py`, `tests/integration/test_jules_integration.py`

- Replace current multi-step send-message workflow expectations with one-shot bundle expectations.
- Add failure-path coverage for branch publication truthfulness and clarification-only `sendMessage` exception behavior.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | **PASS** | Design keeps bundling and verification in orchestration, not in provider cognition. |
| III. Avoid Vendor Lock-In | **PASS** | The bundle contract is orchestration-owned and can later support other providers with different policies. |
| VIII. Modular Architecture | **PASS** | Run/worker orchestration changes are isolated; transport remains thin. |
| IX. Resilient by Default | **PASS** | Truthful failure mapping plus workflow-boundary tests protect unattended execution. |
| XI. Spec-Driven Development | **PASS** | `DOC-REQ-*` mappings and bundle contract keep design traceable. |
| XIII. Delete, Don't Deprecate | **PASS** | Old normal-path chaining is removed instead of being left as a fallback for new runs. |

## Verification Plan

### Required Automated Coverage

Run unit/workflow tests via the canonical script:

```bash
./tools/test_unit.sh
```

Targeted additions/updates must cover:

1. Consecutive Jules plan nodes collapse into one bundled execution request.
2. Standard bundled execution no longer invokes `integration.jules.send_message`.
3. Clarification/auto-answer flows still invoke `integration.jules.send_message` when Jules asks for feedback.
4. Branch publish success requires merge completion.
5. Missing PR URL, base-update failure, merge rejection, and incomplete bundle verification map to non-success outcomes.
6. In-flight/replay-safe compatibility coverage for any changed workflow control-flow branch that can affect existing histories.

### Manual/Integration Validation

- Optional real-provider smoke check only after unit/workflow coverage passes, using a scratch repo and low-risk prompt.
- Confirm result metadata and summaries expose bundle identity and branch publication outcome clearly.

## Complexity Tracking

No constitution violations require special justification.
