# Implementation Plan: Submit Discriminated Executable Payloads

**Branch**: `279-submit-discriminated-executable-payloads` | **Date**: 2026-04-29 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/279-submit-discriminated-executable-payloads/spec.md`

## Summary

Task submissions must preserve explicit Step Type intent for executable Tool and Skill steps, reject unresolved Preset or Activity-labeled steps at the executable submission boundary, and materialize Tool and Skill steps into distinct runtime plan node types without requiring preset provenance. The implementation will extend the existing task payload contract and runtime materializer, then update Create-page submission serialization so preset-applied steps are submitted as flattened discriminated executable steps. Verification will use focused Python unit tests for backend validation and runtime materialization plus Vitest coverage for Create-page submission payloads.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Template catalog accepts explicit `type: tool/skill`; task submission contract does not validate step discriminators. | Add task step discriminator validation and tests. | unit |
| FR-002 | partial | Runtime materializer maps multi-step entries by selected tool name and defaults non-Jira tools to `agent_runtime`. | Use explicit `step.type` to materialize Tool steps as typed tool nodes and Skill steps as agent-runtime/skill nodes. | unit |
| FR-003 | partial | Create page and template catalog have Step Type UI/model; submitted explicit steps omit `type` in several paths. | Serialize explicit step `type` for generated Tool and Skill submissions. | frontend unit |
| FR-004 | missing | Preset UI blocks preset submission, but backend contract does not reject `type: preset` if submitted directly. | Reject unresolved Preset steps in task payload validation. | unit |
| FR-005 | missing | No backend Step Type validation rejects `activity`. | Reject non-canonical Step Type values including `activity`. | unit |
| FR-006 | implemented_unverified | `source` and template provenance fields are preserved by permissive step models and expansion output. | Add regression coverage that provenance does not affect runtime mapping. | unit |
| FR-007 | partial | Template catalog rejects Tool/Skill conflicts; task submission contract does not. | Reject submitted executable steps that carry conflicting Tool and Skill sub-payloads. | unit |
| SCN-001 | partial | Tool template expansion exists; runtime plan mapping does not honor explicit submitted Tool Step Type. | Add materialization test and implementation. | unit |
| SCN-002 | partial | Skill steps materialize today through legacy `tool.type: skill`; explicit `type: skill` is not validated. | Add validation and materialization test. | unit |
| SCN-003 | missing | Backend direct submission can carry `type: preset`. | Add rejection test and validator. | unit |
| SCN-004 | implemented_unverified | `source` extra metadata survives Pydantic model validation by design. | Add runtime mapping test with provenance present. | unit |
| SCN-005 | missing | No direct Step Type rejection for `activity`. | Add rejection test and validator. | unit |
| DESIGN-REQ-008 | partial | Frontend blocks Preset steps; backend direct payload does not. | Enforce executable-only step types at submission boundary. | unit + frontend unit |
| DESIGN-REQ-011 | partial | Runtime plan nodes are generated but do not use submitted Step Type discriminator. | Map Tool and Skill by explicit type. | unit |
| DESIGN-REQ-012 | missing | Activity label is not explicitly rejected. | Reject Activity as a Step Type. | unit |
| DESIGN-REQ-016 | partial | API shape exists in source docs and UI types; submitted payload omits explicit type in key paths. | Preserve submitted discriminators and validate sub-payloads. | unit + frontend unit |
| DESIGN-REQ-019 | partial | Preset expansion flattens steps, but direct preset submission lacks backend guard. | Reject preset runtime nodes and keep provenance audit-only. | unit |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Create page behavior  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK runtime materializer, FastAPI task submission router, React/Vitest test harness  
**Storage**: No new persistent storage; uses existing task payloads and artifact-backed execution inputs  
**Unit Testing**: `./tools/test_unit.sh`; focused Python pytest and Vitest commands during iteration
**Integration Testing**: Hermetic `integration_ci` coverage is not required for this narrow payload-contract story; integration-boundary evidence comes from Create-page submitted payload tests plus worker runtime materialization tests  
**Target Platform**: MoonMind API service, Temporal worker runtime, Mission Control Create page  
**Project Type**: Web service plus frontend application  
**Performance Goals**: Validation remains synchronous and bounded to submitted step count; no additional network calls  
**Constraints**: Preserve MM-559 traceability; do not expose Temporal Activity as Step Type; do not require preset provenance for runtime correctness  
**Scale/Scope**: Existing task step limit of 50 submitted steps

## Constitution Check

- Orchestrate, Don't Recreate: PASS. The change preserves typed tool/skill orchestration and does not rebuild agent cognition.
- Thin Scaffolding, Thick Contracts: PASS. The work strengthens task payload and runtime plan contracts.
- Tests are the Anchor: PASS. Unit and frontend tests are required before implementation.
- Resilient by Default: PASS. Invalid runtime payloads fail fast before plan execution.
- Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain unchanged; implementation notes stay in this feature artifact.
- Compatibility Policy: PASS. Unsupported submitted Step Type values fail fast instead of hidden fallback behavior.

## Project Structure

### Documentation (this feature)

```text
specs/279-submit-discriminated-executable-payloads/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── executable-step-payload.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── tasks/task_contract.py
│   └── temporal/worker_runtime.py

frontend/
└── src/entrypoints/
    ├── task-create.tsx
    └── task-create.test.tsx

tests/
└── unit/workflows/
    ├── tasks/test_task_contract.py
    └── temporal/test_temporal_worker_runtime.py
```

**Structure Decision**: Update the existing task contract and runtime materializer where submitted task steps are validated and compiled. Update the existing Create page entrypoint and its Vitest tests for submitted payload shape.

## Complexity Tracking

No constitution violations.
