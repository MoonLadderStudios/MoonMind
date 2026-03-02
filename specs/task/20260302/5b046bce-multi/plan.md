# Implementation Plan: Manifest Queue Alignment and Hardening

**Branch**: `[028-manifest-queue]` | **Date**: March 2, 2026 | **Spec**: `specs/028-manifest-queue/spec.md`  
**Input**: Feature specification from `/specs/028-manifest-queue/spec.md`

## Summary

This feature aligns `specs/028-manifest-queue` with the current MoonMind runtime and hardening strategy while preserving existing manifest queue normalization behavior. The implementation strategy is to keep request-action validation fail-fast at the API schema boundary (`plan|run` only), keep queue normalization and metadata compatibility unchanged, and ensure validation coverage is executed via `./tools/test_unit.sh`. Per feature scope guard, this work remains in runtime mode (production code + tests), not docs-only mode.

## Technical Context

**Language/Version**: Python 3.11 target runtime (project supports `>=3.10,<3.14`)  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async, Celery, PyYAML, pytest  
**Storage**: PostgreSQL for API/queue state; RabbitMQ broker for queue dispatch; queue payload metadata in JSONB  
**Testing**: `./tools/test_unit.sh` (required wrapper around pytest)  
**Target Platform**: Linux containerized MoonMind API + worker services (Docker Compose/WSL workflows)  
**Project Type**: Multi-service backend (API service + queue/workflow modules + tests)  
**Performance Goals**: Reject unsupported manifest run actions during request parsing; preserve existing queue submit path latency and metadata behavior  
**Constraints**: Fail-fast for unsupported runtime values; no compatibility transforms that change manifest normalization semantics; deliverables must include runtime code + tests for this feature scope; no secrets in payload/docs/logs  
**Scale/Scope**: Focused hardening of `POST /api/manifests/{name}/runs` action validation and artifact alignment for existing manifest queue phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. One-Click Deployment**: PASS. No deployment-path changes; existing compose-based startup remains unchanged.
- **II. Runtime Configurability**: PASS. No hardcoded runtime overrides introduced; action validation is explicit request-contract behavior.
- **III. Modular Architecture**: PASS. Validation stays in `api_service/api/schemas.py` and queue normalization remains in `moonmind/workflows/agent_queue/manifest_contract.py`.
- **IV. Avoid Vendor Lock-In**: PASS. No provider-specific coupling added.
- **V. Self-Healing by Default**: PASS. No retry/state behavior regressions introduced.
- **VI. Continuous Improvement**: PASS. Existing structured outcomes/tests remain intact and are updated for regression coverage.
- **VII. Spec-Driven Development**: PASS. `spec.md`, `plan.md`, `tasks.md`, and design artifacts are kept synchronized with runtime reality.
- **VIII. Skills First-Class**: PASS. Planning artifacts remain compatible with current skill-driven workflow execution.

**Gate Status (Pre-Design)**: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/028-manifest-queue/
├── plan.md                              # This file (speckit-plan output)
├── research.md                          # Phase 0 decisions
├── data-model.md                        # Phase 1 entity/validation model
├── quickstart.md                        # Phase 1 validation steps
├── contracts/
│   ├── manifests-api.md                 # API contract for registry/runs
│   └── requirements-traceability.md     # DOC-REQ to FR mapping + validation
├── checklists/requirements.md
├── spec.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/
│   ├── schemas.py                       # ManifestRunRequest validation contract
│   └── routers/manifests.py             # /api/manifests/{name}/runs endpoint
└── services/manifests_service.py        # Registry-backed queue submission

moonmind/workflows/agent_queue/
├── manifest_contract.py                 # Normalization + hash/capability derivation
├── service.py                           # Queue create/validation flow
├── repositories.py                      # Queue persistence/claim logic
└── job_types.py                         # manifest job type registration

tests/
├── unit/api/test_manifest_run_request_schema.py
├── unit/api/routers/test_manifests.py
└── unit/workflows/agent_queue/test_manifest_contract.py
```

**Structure Decision**: Keep API-request validation in Pydantic schema models and preserve queue normalization in the existing agent queue contract layer. This avoids semantic drift while enforcing fail-fast behavior at the earliest boundary.

## Phase 0: Research Outcomes

1. Confirmed action hardening should occur in `ManifestRunRequest` schema so invalid inputs fail before `ManifestsService.submit_manifest_run`.
2. Confirmed `normalize_manifest_job_payload()` already enforces manifest compatibility requirements (`manifestHash`, `manifestVersion`, capability derivation, secret hygiene) and should not be changed in this scope.
3. Confirmed this feature is runtime-mode scoped (runtime code + tests), so documentation alignment must track existing/implemented runtime behavior instead of replacing runtime work.
4. Confirmed test entrypoint requirement: use `./tools/test_unit.sh` rather than direct `pytest`.

## Phase 1: Design Outputs

- `research.md`: decisions and rejected alternatives for action validation boundary, artifact alignment strategy, and compatibility preservation.
- `data-model.md`: request/response/payload entities and validation semantics.
- `contracts/manifests-api.md`: concrete endpoint behavior, including schema-level 422 and queue-level validation 422 semantics.
- `contracts/requirements-traceability.md`: full `DOC-REQ-*` mapping to FRs, implementation surfaces, and validation strategy.
- `quickstart.md`: deterministic verification steps for valid/default/invalid action paths and queue metadata safety checks.

## Post-Design Constitution Re-check

- Re-check status for Principles I-VIII remains **PASS**.
- No principle violations require complexity exceptions.
- Spec-driven alignment requirement (Principle VII) is satisfied by synchronized updates across `spec.md`, `plan.md`, design artifacts, and `tasks.md`.

**Gate Status (Post-Design)**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
