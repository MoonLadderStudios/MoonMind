# Temporal Consolidation Ownership Map (MoonLadderStudios/MoonMind#3961)

Temporary handoff for the #3961 consolidation. Parent: #3929. Coordinates with
#3959 (generated catalogs), #3960 (factual corrections), #3943 (workflow
decomposition), #3944 (patch retirement); completed scaffolding deletes under
#3963. This map is the required old-path/heading → canonical-owner/heading
record produced **before deletion**. This change deletes no unique requirement:
dispositions below are `retain as owner`, `narrative defers to reference`, or
`obsolete stub replaced by pointer`. Any future deletion must cite a row here
and name its successor.

New module entrypoint: `docs/Temporal/TemporalModuleArchitecture.md`
(canonical name per `docs/DocumentationArchitecture.md` §3.2 Module
Architecture View). `docs/Temporal/TemporalArchitecture.md` remains the
internal architecture hub; the entrypoint links it, it does not replace it.

## Guarantee owners (REQ-05)

These guarantees survive consolidation with the owners named here. Nothing
below is deleted or rewritten by this change.

| Guarantee | Surviving owner |
| --- | --- |
| Recovery and continue-as-new semantics | `docs/Temporal/SourceOfTruthAndProjectionModel.md` (recovery/continue-as-new authority) + `docs/Temporal/TemporalArchitecture.md` §§12, 18 |
| Replay-safe evolution and versioning | `docs/Temporal/TemporalArchitecture.md` §18 + `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md` (normative) |
| Pause / control / resume confirmations | `docs/Temporal/WorkerPauseSystem.md` (normative contract) |
| Projection repair | `docs/Temporal/SourceOfTruthAndProjectionModel.md` + `docs/Temporal/TemporalArchitecture.md` §12.1 |
| Publication guarantees | `docs/Temporal/WorkflowTypeCatalogGenerated.md` (`MoonMind.PublicationRecoveryV1` registration) + projection owners above |
| Cleanup / resource-release ordering | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` (§3.7 Continue-As-New awareness, fleet ownership) + `docs/Temporal/TemporalArchitecture.md` |
| Retry / cancellation behavior | `docs/Temporal/ErrorTaxonomy.md` (core policy) + `docs/Temporal/ActivityCatalogAndWorkerTopology.md` + lifecycle doc |
| Requirement IDs, public payload names, authority rules, retained-history obligations | Preserved verbatim in their owning docs; this change copies none of them |

## Per-file map (REQ-02)

Format: file → disposition; unique normative sections; consumers;
duplicate passages removed or linked; design status; tracking issue.

| File | Disposition → successor | Unique normative sections (stay) | Consumers | Duplicate passages handled | Design status | Tracking |
| --- | --- | --- | --- | --- | --- | --- |
| `TemporalModuleArchitecture.md` (new) | New entrypoint, defines no new semantics | Entrypoint table, catalog authority rule | New readers, agents, README/AGENTS links | None (links only) | Normative entrypoint | #3961 |
| `TemporalArchitecture.md` | Retain as internal architecture hub | §§4–6 operating model, invariants, §7 catalog pointer, §8 identifier model, §§12/18 projection-repair/replay | Entrypoint, platform docs, operators | §7 already points at generated ref (kept) | Normative hub | #3961 |
| `WorkflowTypeCatalogAndLifecycle.md` | Retain as lifecycle owner | Lifecycle, domain state, Update/Signal contracts, invariants, timeouts, retry posture, history management, Search Attribute/Memo minimums | Generated ref (lifecycle links), UI, backend | §3.1 already defers enumeration to generated ref (kept) | Normative (Temporal application layer) | #3961 |
| `WorkflowTypeCatalogGenerated.md` | Retain as sole workflow-type inventory | Generated registration table, queue/handler routing | Lifecycle doc, architecture hub, entrypoint | Authoritative; all other lists point here | Generated (#3959) | #3959 |
| `ActivityCatalogAndWorkerTopology.md` | Retain as sole Activity/worker-topology surface | Determinism boundary, queues, fleets, naming, contract model, continue-as-new awareness | Entrypoint, agent execution docs | Authoritative; agent-execution copies point here | Normative | #3961 |
| `ManagedAndExternalAgentExecutionModel.md` | Retain as execution-lane owner | Lanes, canonical contracts (`AgentExecutionRequest`, `AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`), workspace/artifact authority | AGENTS.md runtime boundary, adapters | None removed | Current | #3961 |
| `SourceOfTruthAndProjectionModel.md` | Retain as source-of-truth/projection authority | Source layering, projection components, steady-state matrix, write-path, recovery/continue-as-new | API, projections, entrypoint | None removed | Normative steady-state contract | #3961 |
| `WorkerPauseSystem.md` | Retain as pause/control authority | Admission state, durable confirmation, enumeration, recovery/observer behavior | Operators, entrypoint | None removed | Normative contract | #3961 |
| `WorkflowArtifactSystemDesign.md` | Retain as artifact authority | Artifact goals, lifecycle, refs | Steps, UI, entrypoint | None removed | Draft | #3961 |
| `StepLedgerAndProgressModel.md` | Retain as step-ledger owner | Step identity, status, attempts, checks, refs, artifact semantics | Dashboard, steps | None removed | Normative | #3961 |
| `CheckpointResumePromotion.md` | Retain as checkpoint-resume owner | Supported contract, promotion procedure, pause conditions, rollback/drain | Operators | None removed | Active | #3961 |
| `WorkflowRunHistoryAndNewRunSemantics.md` | Retain as replay/history owner | Run history, new-run semantics | Versioning, entrypoint | None removed | Normative | #3961 |
| `TemporalSignalsSystem.md` | Retain as signal/update/query reference | Signal contracts, principles | Lifecycle doc, entrypoint | None removed | Design draft | #3961 |
| `TemporalSignalsResearch.md` | Retain as research report | Signal usage inventory, assessment | Signals system design | Research, not contract; not linked as authority | Research | — |
| `VisibilityAndUiQueryModel.md` | Retain as visibility/query owner | Query boundaries, Search Attribute rules, registered attributes | UI, dashboard | None removed | Active design guidance | #3961 |
| `TemporalScheduling.md` | Retain (draft) | Deferred execution design | Scheduling guide | Overlaps guide in purpose; both retained, guide is canonical how-to | Draft | #3961 |
| `WorkflowSchedulingGuide.md` | Retain as canonical scheduling how-to | Scheduling patterns, Temporal-managed scheduling | Operators, entrypoint | None removed | Active | #3961 |
| `ops-runbook.md` | Retain as operational recovery owner | Workflow operations, recovery/safeguards, deployment canary | Operators, on-call | None removed | Active | #3961 |
| `ErrorTaxonomy.md` | Retain as error/retry policy owner | Error classes, retryable/fatal rules | Activities, entrypoint | None removed | Core policy (partially enforced) | #3961 |
| `TemporalTypeSafety.md` | Retain as type-safety owner | Canonical modeling rules | Workflow boundary | None removed | Desired state / normative target | #3961 |
| `RoutingPolicy.md` | Retain as routing owner | Workflow classes, feature flags, runtime picker | Control plane | None removed | Active | #3961 |
| `StatusDomainMatrix.md` | Retain as status-domain owner | Domain matrix, audit actions | UI, projections | None removed | Normative | #3961 |
| `WorkflowExecutionProductModel.md` | Retain as product-vocabulary owner | Canonical model, identity/step/chat rules | Product APIs, UI | None removed | Normative | #3961 |
| `TemporalPlatformFoundation.md` | Retain as deployment-foundation draft; §5 catalog table → pointer to generated ref | Deployment model, persistence/visibility, namespaces/retention, queues, fleet strategy, shard constraint | Deployments, entrypoint | §5 hand-written catalog table (18 lines) replaced by pointer to `WorkflowTypeCatalogGenerated.md` | Draft (implementation-oriented) | #3961 |
| `TemporalAgentExecution.md` | Retain as agent-execution design; §§3.3–3.4 tables → pointers to Activity catalog + generated ref | End-to-end execution flow, payload shapes, implementation status | Backend, infra | §§3.3–3.4 hand-written Activity/fleet tables (29 lines) replaced by pointers; flow/status prose kept | Active design | #3961 |
| `WorkflowLanguageHardSwitchPlan.md` | Retain (still-active imperative plan under its own owner) | Hard-switch decision, glossary | Migration owners | Not moved; stays owned here until its plan lands | Proposed plan | Own plan |
| `IntegrationsMonitoringDesign.md` | Retain (draft design) | State alignment, monitoring principles | Integrations | None removed | Draft | Own design |
| `ChatInstructionTemporalContract.md` | Retain (deferred optional extension) | Reserved primitive, promotion gate | Future chat work | None removed | Deferred | Own contract |

No file is deleted by this change, so every unique requirement keeps its
current owner. Requirement IDs, public payload names, authority rules,
retry/cancellation behavior, resource-release ordering, and retained-history
obligations are untouched.

## Duplication and navigation measurement (REQ-06)

Exact file count is not a merge gate. Measured on this change:

- Before: 27 `docs/Temporal/*.md` files, no module entrypoint. A new reader
  reached contracts from `README.md` (one deep section link) and `AGENTS.md`
  (one execution-model link) only; every other contract required knowing its
  filename (navigation depth unbounded, effectively ≥2–3 hops via search).
- Competing catalog prose before: `TemporalPlatformFoundation.md` §5
  hand-written workflow table (9 table rows covering 7 types, missing
  registered operator/excluded types) vs `WorkflowTypeCatalogGenerated.md`
  (308 lines, full registry); `TemporalAgentExecution.md` §§3.3–3.4
  hand-written Activity/fleet tables (21 table rows, stale queue names) vs
  `ActivityCatalogAndWorkerTopology.md` (1018 lines, authoritative) — 30
  copied catalog table rows plus 2 stale intro lines across two files.
- After: 28 files (27 + new 77-line entrypoint). Every major contract is
  reachable in 1 hop from `TemporalModuleArchitecture.md`. Copied catalog
  definitions removed: 32 lines deleted, 31 owner-pointer lines added
  (net −32 copied-definition lines; no second handwritten type list remains).
- Navigation depth after: entrypoint → owner = 1 hop for execution/lifecycle,
  workflow catalog, Activity catalog, control/pause, source-of-truth,
  artifacts/checkpoints, replay/versioning, and operational recovery.

## Link/anchor verification (REQ-04)

- `python3 tools/check_documentation_links.py --scope all --format json` →
  `finding_count` 0 (run against the consolidation candidate).
- `python3 tools/check_documentation_architecture.py` → no blocking/advisory
  findings on touched docs.
- `python3 tools/generate_temporal_catalog.py --check --ref docs/Temporal/WorkflowTypeCatalogGenerated.md` → passes (no registry drift).
- `tests/unit/docs/test_temporal_consolidation_3961.py` → passes (entrypoint,
  full-file coverage, guarantee survival, owner-pointer, link/anchor checks).
