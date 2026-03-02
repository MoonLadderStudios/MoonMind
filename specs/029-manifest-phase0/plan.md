# Implementation Plan: Manifest Queue Phase 0 Alignment

**Branch**: `029-manifest-phase0` | **Date**: March 2, 2026 | **Spec**: `specs/029-manifest-phase0/spec.md`  
**Input**: Feature specification from `/specs/029-manifest-phase0/spec.md`

## Summary

Close out Manifest Phase 0 alignment by hardening validation ergonomics on existing runtime paths: manifest queue submissions (`POST /api/queue/jobs`) and manifest registry upserts (`PUT /api/manifests/{name}`) must return actionable manifest-contract error messages with stable manifest-specific codes, while preserving existing non-manifest queue validation semantics. No new manifest execution behavior is added in this scope.

## Technical Context

**Language/Version**: Python 3.11 (FastAPI API service + shared workflow modules)  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy, MoonMind agent queue service + manifest contract modules  
**Storage**: PostgreSQL queue/manifest persistence (schema unchanged for this feature)  
**Testing**: `./tools/test_unit.sh` (required by DOC-REQ-005)  
**Target Platform**: Linux/Docker MoonMind API service runtime  
**Project Type**: Multi-service backend (API + queue workflow modules)  
**Performance Goals**: Preserve existing queue/registry response latency characteristics while adding clearer 422 messages  
**Constraints**: No raw secret leakage in surfaced validation errors; non-manifest queue validation code/path must remain backward compatible; runtime-scope guard requires production code + tests (not docs-only)  
**Scale/Scope**: Narrow Phase 0 hardening item affecting manifest submission validation in API routers and corresponding unit tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Phase 0 Gate

- **I. One-Click Deployment with Smart Defaults**: **PASS**. No deployment surface or required configuration changes.
- **II. Powerful Runtime Configurability**: **PASS**. Existing runtime configuration behavior is preserved; only response mapping changes.
- **III. Modular and Extensible Architecture**: **PASS**. Changes are isolated to API router validation handling and existing contract interfaces.
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: **PASS**. No provider-specific integrations introduced.
- **V. Self-Healing by Default**: **PASS**. Fail-fast validation remains deterministic and retry-safe with improved diagnostics.
- **VI. Facilitate Continuous Improvement**: **PASS**. Actionable error messages improve operator feedback loops and debugging.
- **VII. Spec-Driven Development Is the Source of Truth**: **PASS**. Spec, plan, tasks, and traceability artifacts are aligned to DOC-REQ and FR IDs.
- **VIII. Skills Are First-Class and Easy to Add**: **PASS**. No skill registration/runtime contract regressions.

### Post-Phase 1 Re-Check

- Research and design artifacts (`research.md`, `data-model.md`, `contracts/*`, `quickstart.md`) remain within the same bounded runtime scope.
- All constitution principles remain **PASS**; no violations require complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/029-manifest-phase0/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── manifest-phase0.openapi.yaml
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── agent_queue.py      # Manifest queue 422 mapping (`invalid_manifest_job`)
│   └── manifests.py        # Registry upsert 422 mapping (`invalid_manifest`)
└── services/
    └── manifests_service.py # Existing registry orchestration (no semantic expansion)

moonmind/workflows/agent_queue/
└── manifest_contract.py    # Contract error source for actionable messages

tests/unit/api/routers/
├── test_agent_queue.py
└── test_manifests.py
```

**Structure Decision**: Keep implementation constrained to existing API router boundaries and current manifest contract/service integration points; no new modules or storage layers are required.

## Phase 0 Research Summary

`research.md` confirms the runtime baseline is already delivered (manifest job type, contract normalization, registry APIs, payload sanitization). Remaining gap: manifest submission failures were not consistently actionable. Chosen approach: propagate manifest-contract validation text for manifest-specific endpoints while preserving generic queue behavior for non-manifest types.

## Phase 1 Design Outputs

- `data-model.md`: documents validation response entities and unchanged manifest payload/registry entities.
- `contracts/manifest-phase0.openapi.yaml`: captures queue + registry API shapes and 422 response contracts.
- `contracts/requirements-traceability.md`: maps every `DOC-REQ-*` to FRs, implementation surfaces, implementation task IDs, validation strategy, and validation task IDs.
- `quickstart.md`: provides deterministic manual validation flow plus required `./tools/test_unit.sh` execution.

## Implementation Strategy

1. Update `POST /api/queue/jobs` manifest validation error mapping to return `invalid_manifest_job` with manifest contract message text.
2. Update `PUT /api/manifests/{name}` upsert validation mapping to return `invalid_manifest` with manifest contract message text.
3. Add/maintain unit coverage verifying manifest-specific mappings and non-manifest regression safety.
4. Run `./tools/test_unit.sh` as required validation gate.

## Runtime vs Docs Mode Alignment

- **Selected orchestration mode**: `runtime`.
- **Required deliverables**: production runtime code changes in API validation paths plus automated tests.
- **Non-compliant outcome**: docs/spec-only updates without runtime code and test evidence.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime-mode scope coverage is explicit in `tasks.md` with production runtime implementation tasks (`T003`, `T004`, `T007`, `T008`, `T011`, `T012`) and validation tasks (`T005`, `T006`, `T009`, `T010`, `T015`).
- `DOC-REQ-*` coverage is deterministic in `contracts/requirements-traceability.md`: each source requirement now has mapped implementation task IDs and validation task IDs.
- Cross-artifact consistency is enforced by aligning spec runtime guard language, plan runtime constraints, and task execution coverage without contradictory scope language.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls to `tasks.md` so runtime/test gating remains auditable if tasks are regenerated.
- Clarified in this plan that requirements traceability includes task-level implementation and validation mappings, not only surface descriptions.

### Residual risks

- Manifest contract message text may evolve over time; tests validate actionable behavior and codes, but wording-level assertions should avoid brittle coupling.
- Because this feature intentionally preserves existing baseline behavior for non-manifest paths, regressions outside targeted routers still depend on broader unit suite coverage.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| None | N/A | N/A |
