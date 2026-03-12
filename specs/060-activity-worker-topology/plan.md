# Implementation Plan: Activity Catalog and Worker Topology

**Branch**: `060-activity-worker-topology` | **Date**: 2026-03-06 | **Spec**: `specs/060-activity-worker-topology/spec.md`  
**Input**: Feature specification from `/specs/060-activity-worker-topology/spec.md`

## Summary

Maintain the canonical activity catalog/topology implementation and apply the 2026-03-12 runtime alignment delta from `docs/Temporal/WorkflowArtifactSystemDesign.md` and `docs/Temporal/TemporalAgentExecution.md`. The required delta focuses on execution-stage correctness: artifact reads must route on the artifact fleet, execution-stage node dispatch must be catalog-routed (`resolve_skill` when registry metadata is available), and workflow progress must remain visible through memo updates while honoring plan failure/edge semantics.

## Technical Context

**Language/Version**: Python 3.11, Docker Compose YAML, shell bootstrap scripts  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, existing MoonMind Temporal runtime modules (`moonmind/workflows/temporal`), skill registry/plan contracts, Jules adapter, `SecretRedactor`  
**Storage**: PostgreSQL for Temporal execution and artifact metadata, MinIO/S3-compatible artifact blob storage, Temporal persistence/visibility storage  
**Testing**: `./tools/test_unit.sh`, existing Temporal unit suites in `tests/unit/workflows/temporal/`, contract coverage in `tests/contract/`, compose-backed Temporal integration suites in `tests/integration/temporal/`  
**Target Platform**: Linux Docker Compose deployment with Temporal server, MoonMind API, and dedicated Temporal worker fleet services  
**Project Type**: Backend runtime, worker topology, and infrastructure-compose feature  
**Performance Goals**: 100% canonical v1 activity routing correctness; artifact-backed payloads keep workflow history reference-sized; long-running sandbox operations maintain heartbeats and cancellation responsiveness under retry/failure injection  
**Constraints**: Runtime implementation mode is mandatory; queue semantics remain routing-only; v1 keeps one shared `mm.activity.llm` queue; activities must not mutate Search Attributes or Memo directly; local object storage remains private even when `AUTH_PROVIDER=disabled`  
**Scale/Scope**: Extend current `activity_catalog.py`, `activity_runtime.py`, artifact services, skill dispatch, and compose/runtime wiring into the full canonical system across workflow, artifacts, LLM, sandbox, and integrations fleets with production code and validation tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. Worker fleets are planned as Docker Compose services with documented env defaults and no new hidden prerequisites.
- **II. Avoid Vendor Lock-In**: PASS. Activity contracts remain provider-neutral; provider-specific behavior stays behind adapters (`TemporalArtifactStore`, integration adapters, LLM provider selection inside worker logic).
- **III. Own Your Data**: PASS. Large inputs, outputs, logs, and previews remain MoonMind-managed artifacts rather than provider-owned opaque payloads.
- **IV. Skills Are First-Class and Easy to Add**: PASS. `mm.skill.execute` stays the low-ceremony default while explicit bindings remain registry-declared exceptions.
- **V. The Bittersweet Lesson**: PASS. Stable activity names are the durable contracts; worker implementations and provider adapters remain replaceable.
- **VI. Powerful Runtime Configurability**: PASS. Queue names, concurrency, backend selection, provider routing, and fleet enablement remain configuration-driven and observable.
- **VII. Modular and Extensible Architecture**: PASS. The feature extends existing temporal, skills, adapter, and compose boundaries instead of introducing a parallel runtime model.
- **VIII. Self-Healing by Default**: PASS. Idempotency keys, bounded retries, heartbeats, and artifact-backed diagnostics keep retries and operator recovery safe.
- **IX. Facilitate Continuous Improvement**: PASS. Structured summaries, metrics, and diagnostics artifacts are explicit design outputs for operational feedback loops.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-001` through `DOC-REQ-024` remain mapped to implementation surfaces and validation strategy in this plan package.

### Post-Design Re-Check

- PASS. Phase 1 artifacts preserve runtime-authoritative completion: production code and automated validation are mandatory.
- PASS. Queue routing, capability binding, and visibility ownership stay explicit contract surfaces rather than implicit worker behavior.
- PASS. No constitution violations require a Complexity Tracking exception.

## Project Structure

### Documentation (this feature)

```text
specs/060-activity-worker-topology/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-activity-worker-contract.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/Temporal/ActivityCatalogAndWorkerTopology.md
docs/Temporal/WorkflowArtifactSystemDesign.md
docs/Temporal/TemporalAgentExecution.md

docker-compose.yaml
services/temporal/
└── scripts/
    └── start-worker.sh                              # planned fleet bootstrap helper

moonmind/
├── config/settings.py
├── utils/logging.py
├── workflows/
│   ├── adapters/
│   │   └── jules_client.py
│   ├── skills/
│   │   ├── skill_dispatcher.py
│   │   ├── skill_plan_contracts.py
│   │   └── skill_registry.py
│   └── temporal/
│       ├── __init__.py
│       ├── activity_catalog.py
│       ├── activity_runtime.py
│       ├── artifacts.py
│       ├── service.py
│       ├── telemetry.py                            # planned
│       └── workers.py                              # planned

api_service/
├── api/routers/
│   └── temporal_artifacts.py
└── db/models.py

tests/
├── contract/
│   └── test_temporal_activity_topology.py          # planned queue, envelope, and activity-contract assertions
├── integration/
│   └── temporal/
│       ├── test_activity_worker_topology.py        # planned
│       ├── test_temporal_artifact_local_dev.py
│       └── test_temporal_artifact_lifecycle.py
└── unit/
    ├── specs/
    │   └── test_doc_req_traceability.py
    └── workflows/temporal/
        ├── test_activity_catalog.py
        ├── test_activity_runtime.py
        └── test_temporal_workers.py                # planned
```

**Structure Decision**: Keep the existing `moonmind/workflows/temporal/` package as the single runtime authority for activity catalog, bindings, and worker execution. Add dedicated worker bootstrap and telemetry helpers there, wire fleet-specific services in `docker-compose.yaml`, and expand the current unit/integration suites instead of introducing a second runtime path.

## Phase 0 - Research Summary

Research outcomes in `specs/060-activity-worker-topology/research.md` establish:

1. Current `activity_catalog.py`, `activity_runtime.py`, and artifact services are the seed implementation and must be extended, not bypassed.
2. v1 queue topology remains fixed to `mm.workflow` plus four activity queues, with one shared LLM queue for all providers.
3. Capability-based routing is resolved per activity invocation from the catalog and pinned skill registry snapshot, never from workflow type.
4. Shared business payloads stay artifact-reference-first and derive execution metadata from Temporal runtime context.
5. Explicit skill-to-activity bindings require a declared operational reason and fail closed when that reason is absent.
6. Dedicated workflow/artifacts/llm/sandbox/integrations worker fleets should be materialized as separate compose services with least-privilege env and concurrency controls.
7. Sandbox completion requires more than `run_command`: checkout, patch, and test operations need idempotent workspace handling, heartbeats, and redacted artifact-backed diagnostics.
8. Integration execution should remain callback-first with bounded polling fallback, and provider credentials stay isolated to the integrations fleet.
9. Structured logs, metrics, and diagnostics artifacts should reuse existing redaction and StatsD-style patterns rather than inventing a separate telemetry stack.
10. Runtime mode remains the controlling orchestration mode; docs-only completion is invalid for this feature.

## Phase 1 - Design Outputs

- **Research**: `research.md` records the routing, fleet, payload, sandbox, integration, observability, and runtime-mode decisions needed to remove ambiguity before implementation.
- **Data Model**: `data-model.md` defines activity contracts, invocation envelopes, capability bindings, fleet profiles, sandbox workspaces, integration tracking, and observability summaries.
- **Runtime Contract**: `contracts/temporal-activity-worker-contract.md` captures the canonical queue set, activity families, binding rules, worker privileges, and timeout/retry/heartbeat invariants.
- **Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to FRs, planned implementation surfaces, and validation strategy.
- **Traceability Gate**: `tests/unit/specs/test_doc_req_traceability.py` keeps `DOC-REQ-*` mappings from drifting away from the spec package as runtime work lands.
- **Execution Guide**: `quickstart.md` defines runtime-mode implementation and validation flow, including compose startup expectations and repository-standard test commands.

## Phase 1.5 - March 2026 Runtime Alignment Delta

- Route `artifact.read` in `MoonMind.Run` through the catalog-defined artifact queue (`mm.activity.artifacts`) to enforce the artifact activity boundary.
- Remove execution-stage queue constant duplication by deriving activity task queue/timeout/retry behavior from `TemporalActivityCatalog` route metadata.
- Parse plan artifacts via validated plan contracts, then honor plan edges and failure policy when dispatching nodes.
- Resolve node routing with `resolve_skill` when pinned registry snapshot data is available, and fail clearly if a referenced skill is absent from the snapshot.
- Update workflow memo summary on each node boundary so dashboard/operator views show active execution progress.
- Extend workflow unit coverage to prove artifact queue routing and registry-aware node routing behavior.

## Implementation Strategy

### 1. Complete the canonical activity catalog

- Expand `moonmind/workflows/temporal/activity_catalog.py` from the current seed list into the full v1-required catalog, including the missing sandbox family members and the documented lifecycle operations.
- Keep stable dotted activity names as the contract surface and reject unsupported aliases or capability mismatches instead of silently remapping them.
- Encode family-level timeout, retry, and heartbeat defaults so worker options are catalog-driven rather than ad hoc.

### 2. Normalize shared envelopes and context-derived metadata

- Standardize side-effecting activity inputs around `correlation_id`, `idempotency_key`, `input_refs`, and compact `parameters`, with large payloads stored as artifacts.
- Keep `workflow_id`, `run_id`, `activity_id`, and `attempt` sourced from activity runtime context for logging and tracing, not duplicated into business payloads by default.
- Ensure activity results return compact summaries, artifact references, and diagnostics refs, leaving visibility/memo updates to workflow code.

### 3. Materialize dedicated worker fleet runtime

- Add a Temporal worker bootstrap module (`moonmind/workflows/temporal/workers.py`) that can register workflow workers and per-fleet activity workers from the same catalog/binding source.
- Add compose service definitions for workflow, artifacts, llm, sandbox, and integrations fleets with fleet-specific env, secret scope, queue assignment, and concurrency controls.
- Keep the worker topology capability-driven: any future provider-specific isolation extends the fleet composition without changing the v1 public activity names.

### 4. Harden artifact and planning families

- Reuse the existing artifact repository/service/activity helpers as the canonical implementation for `artifact.create`, `artifact.write_complete`, `artifact.read`, `artifact.list_for_execution`, `artifact.compute_preview`, `artifact.link`, `artifact.pin`, `artifact.unpin`, and `artifact.lifecycle_sweep`.
- Keep plan generation and validation artifact-backed, with `plan.validate` remaining the authoritative deep-validation gate before execution.
- Schedule `artifact.lifecycle_sweep` through Temporal-native scheduling surfaces rather than background queue-specific cleanup logic.

### 5. Enforce hybrid skill binding rules

- Preserve `mm.skill.execute` as the default registry-dispatched path.
- Extend skill definition validation so curated explicit bindings require one of the allowed operational reasons: stronger isolation, specialized credentials, or clearer routing.
- Route default dispatcher executions by declared capability (`llm`, `sandbox`, `artifacts`, `integration:<provider>`) while keeping provider selection inside the LLM worker for v1.

### 6. Expand sandbox execution to the full canonical family

- Add `sandbox.checkout_repo`, `sandbox.apply_patch`, and `sandbox.run_tests` alongside the existing `sandbox.run_command` helper.
- Introduce idempotent workspace references keyed by repo input and idempotency key so retries do not create duplicate workspaces or reapply destructive steps unexpectedly.
- Keep long-running sandbox work heartbeat-enabled, cancellation-aware, resource-limited, and redacted before log artifact persistence.

### 7. Finalize integration fleet behavior

- Keep `integration.jules.start/status/fetch_result` behind the existing Jules adapter and add explicit idempotent external-start bookkeeping.
- Support callback-first completion paths with bounded polling fallback so workflows do not rely on monolithic long-running integration activities.
- Enforce that provider credentials and webhook verification secrets are available only to the integrations fleet.

### 8. Add observability and security guardrails

- Emit structured activity summaries with workflow/run/activity/correlation identifiers and artifact-backed log references for large output.
- Add fleet-level metrics hooks for queue backlog, latency, retries, sandbox duration/resource usage, and provider usage where available.
- Keep MinIO/object storage private on the internal network, redact secrets from logs/artifacts, and ensure sandbox workers never receive provider-only credentials.

### 9. Validation strategy

- Extend unit tests for catalog invariants, capability routing, explicit-binding rejection, timeout/retry defaults, worker registration, and visibility-ownership boundaries.
- Add contract coverage for the runtime contract surface so queue sets, activity names, and envelope rules stay aligned to the source document, including the sandbox/integration request-result envelopes exercised by User Story 2.
- Add Temporal integration coverage for per-family routing, multi-fleet compose startup, heartbeat/cancellation behavior, bounded retry paths, and artifact-backed diagnostics; the first compose-backed routing proof belongs to User Story 1, with Phase 6 rerunning it as a regression gate.
- Keep the feature-specific DOC-REQ traceability unit gate in the acceptance path so catalog/topology changes cannot land without updated mappings.
- Run repository-standard unit acceptance through `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- Completion for this feature requires production runtime code changes plus automated validation tests.
- Documentation and spec artifacts support implementation but do not satisfy the delivery gate on their own.
- Downstream `tasks.md` generation must preserve this runtime-mode scope and keep validation work explicit.

## Remediation Gates

- Every `DOC-REQ-*` row must remain mapped to FRs, planned implementation surfaces, and validation strategy in `contracts/requirements-traceability.md`.
- The v1 queue topology must remain `mm.workflow`, `mm.activity.artifacts`, `mm.activity.llm`, `mm.activity.sandbox`, and `mm.activity.integrations`, with provider-specific LLM queues deferred.
- `MoonMind.Run` execution stage must route `artifact.read` on the artifact queue and must not hardcode non-artifact queues for artifact operations.
- Activities must not upsert Search Attributes or Memo fields directly; workflow code remains the only visibility owner.
- Capability routing must stay per activity invocation and must reject unsupported or unjustified explicit bindings instead of silently falling back.
- Execution-stage skill dispatch must derive queue/timeouts from activity catalog routing (`resolve_skill` when registry metadata is available).
- Each user story's independent test must appear in that story's task group rather than being deferred only to later hardening or final-polish phases.
- Planning is invalid if it permits docs-only completion for this feature.

## Risks & Mitigations

- **Risk: partial existing runtime creates false confidence that the canonical system is already complete.**
  - **Mitigation**: treat current catalog/runtime helpers as seed coverage only and add contract/integration tests for every missing family behavior and fleet bootstrap path.
- **Risk: worker topology drift between compose services and catalog metadata.**
  - **Mitigation**: derive worker registration and validation from the same catalog source and add compose-level topology assertions.
- **Risk: sandbox retry semantics can duplicate destructive side effects.**
  - **Mitigation**: require idempotency-keyed workspace lifecycle, bounded retries, and explicit destructive-operation policies.
- **Risk: secret leakage through logs or preview artifacts.**
  - **Mitigation**: reuse `SecretRedactor`, restricted artifact preview generation, and fleet-specific secret scoping.
- **Risk: capability or explicit-binding regressions bypass least-privilege intent.**
  - **Mitigation**: validate skill registry bindings strictly and add negative tests for unjustified explicit routes and cross-fleet leakage.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
