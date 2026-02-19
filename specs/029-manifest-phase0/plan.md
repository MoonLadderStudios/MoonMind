# Implementation Plan: Manifest Queue Phase 0

**Branch**: `[029-manifest-phase0]` | **Date**: February 19, 2026 | **Spec**: `specs/029-manifest-phase0/spec.md`
**Input**: Feature specification from `/specs/029-manifest-phase0/spec.md`

## Summary

Phase 0 wires manifest ingestion jobs into the existing Agent Queue flow so manifests can be queued, validated, and audited as first-class jobs. Delivery covers the new `manifest` queue type (per `docs/ManifestTaskSystem.md` §6.1), a dedicated manifest contract that parses YAML, enforces name/option invariants, and derives `requiredCapabilities` (§6.2–§6.5), persistence of manifest hashes/versions in queue payloads (§6.6), registry CRUD + run submission endpoints (§7), and sanitized queue payload responses that expose only hashes/capabilities rather than raw manifests (FR-009). Runtime scope also includes targeted unit tests (`./tools/test_unit.sh`) covering the manifest contract, capability derivation, registry flows, and queue/job filtering guardrails (FR-010).

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, SQLAlchemy (async), Alembic, Pydantic v2, PyYAML, Celery task runtime, pytest  
**Storage**: PostgreSQL (`agent_jobs`, `manifest` tables) plus object storage for queue artifacts; manifest YAML content stored in `manifest.content` and hashed server-side  
**Testing**: Project-standard `./tools/test_unit.sh` wrapper (pytest backend)  
**Target Platform**: MoonMind API service + Celery workers running in Linux containers/WSL with RabbitMQ/PostgreSQL backends  
**Project Type**: Backend services (queue workflow + REST API) with accompanying runtime contract/tests  
**Performance Goals**: Manifest job creation path remains <400 ms at API layer; queue listing must filter `type=manifest` without loading YAML blobs into responses or telemetry  
**Constraints**: No raw secrets in manifests or payloads; manifest jobs must only be claimable by workers advertising `manifest` capability; registry + queue changes must stay backward compatible with existing task job types; runtime intent guard requires production code + automated tests  
**Scale/Scope**: Dozens of manifests and concurrent ingestion jobs; scope limited to queue plumbing + registry endpoints (execution engine lands in later phases)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` only contains placeholder headings with no enforceable principles, so there are no additional project-level constraints to evaluate.
- Default MoonMind guardrails (security + runtime intent) therefore govern this plan.

**Gate Status**: PASS WITH NOTE — proceed while keeping the lack of a ratified constitution on the backlog.

## Project Structure

### Documentation (this feature)

```text
specs/029-manifest-phase0/
├── plan.md                          # This file (speckit-plan output)
├── research.md                      # Phase 0 findings
├── data-model.md                    # Phase 1 entity + contract details
├── quickstart.md                    # Phase 1 guided verification
├── contracts/
│   ├── manifest-phase0.openapi.yaml  # Registry + queue contract additions
│   └── requirements-traceability.md  # DOC-REQ ↔ FR matrix
├── checklists/requirements.md       # Existing spec checklist
└── tasks.md                         # Generated later by speckit-tasks
```

### Source Code (repository root)

```text
docs/
└── ManifestTaskSystem.md                        # Source contract driving FRs

moonmind/
├── workflows/agent_queue/
│   ├── job_types.py                             # SUPPORTED_QUEUE_JOB_TYPES
│   ├── manifest_contract.py                     # Manifest normalization + caps
│   ├── service.py                               # Job creation + validation path
│   └── repositories.py                          # Claim filtering + capability gating
└── manifest/                                    # Legacy manifest runner (context)

api_service/
├── api/routers/
│   ├── agent_queue.py                           # Queue producer/worker APIs
│   └── manifests.py                             # Registry CRUD + run submission
├── services/manifests_service.py                # Registry orchestration
├── schemas/agent_queue_models.py                # Job serialization (to sanitize)
└── db/models.py                                 # `manifest` table definition

tests/
├── unit/workflows/agent_queue/test_manifest_contract.py    # Contract coverage
├── unit/api/routers/test_manifests.py                      # Registry endpoint tests
└── unit/services/test_manifest_sync_service.py             # Existing manifest service tests
```

**Structure Decision**: Continue leveraging the established FastAPI → service → repository layering so manifest queue logic lives under `moonmind/workflows/agent_queue` while REST exposure stays inside `api_service/api/routers`. Sanitized queue payloads will be implemented by extending `moonmind/schemas/agent_queue_models.py` (and related helper utilities) so manifests do not leak raw YAML when jobs are listed or fetched.

## Phase 0: Research Plan

1. **Manifest contract + YAML parsing** — confirm the required validation rules from `docs/ManifestTaskSystem.md` (§6.2–§6.3, §6.6) and ensure `normalize_manifest_job_payload` derives `manifestHash`, `manifestVersion`, options guards, and capability lists (`manifest`, embeddings provider, vector store, adapters).
2. **Capability enforcement + worker policy** — verify how `AgentQueueRepository._is_job_claim_eligible` uses `requiredCapabilities` and define the manifest worker capability set so jobs cannot be claimed by codex/gemini workers; document required worker token settings.
3. **Registry data model + hashing** — align `ManifestRecord` fields with registry requirements (§7.1) and document how upserted YAML flows through normalization so content hashes/versions stay in sync with queue payloads.
4. **Queue payload sanitization** — design a response-shaping helper that strips inline YAML before returning queue jobs via `/api/queue` while still surfacing `manifestHash`, `manifestVersion`, and `requiredCapabilities` for auditability (FR-009).
5. **Validation & test matrix** — outline automated coverage for manifest contract edge cases (name mismatch, unsupported adapters/options), registry CRUD, and queue filtering so `./tools/test_unit.sh` enforces the runtime intent guard (FR-010).

## Phase 1: Design Outputs

- `research.md` — captures decisions/alternatives for manifest normalization, capability gating, registry hashing, sanitization, and test strategy.
- `data-model.md` — documents `ManifestQueueJobPayload`, `ManifestRecord`, capability derivation rules, and validation constraints referenced by API/worker code.
- `contracts/manifest-phase0.openapi.yaml` — OpenAPI snippets for manifest queue submissions, registry CRUD, and registry-backed run creation (including sanitized payload schema).
- `contracts/requirements-traceability.md` — DOC-REQ-001…005 mapped to FR-001…010 with planned implementation surfaces and validation strategies.
- `quickstart.md` — step-by-step guide covering registry upsert, inline job submission, manifest-only job filtering, and executing `./tools/test_unit.sh`.

## Post-Design Constitution Re-check

- Design artifacts do not introduce new governance rules; the constitution remains a placeholder file with no enforceable directives.
- Runtime/testing guardrails from AGENTS instructions stay satisfied (code + tests required, no raw secrets).

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
