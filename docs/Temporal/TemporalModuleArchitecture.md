# Temporal Module Architecture

**Document Class:** Module Architecture View
**Status:** Normative module entrypoint
**Owner:** MoonMind Platform
**Source:** MoonLadderStudios/MoonMind#3961 (parent #3929; coordinates with #3959 generated catalogs, #3960 factual corrections, #3943 workflow decomposition, #3944 patch retirement)

MoonMind execution is Temporal-native: durable Workflow Executions orchestrate,
Activities perform side effects, Task Queues route work, and artifacts carry
large data. This short entrypoint names the canonical owner for each Temporal
contract. It defines no new semantics; when this page and an owner disagree,
the owner wins.

## Runtime model in one paragraph

A client submits work through the API control plane; the Temporal server
persists Workflow Executions and history; workflow fleets orchestrate without
side effects; Activity fleets (LLM, sandbox, agent runtime, artifacts,
integrations) do bounded or heartbeat-aware work; Visibility indexes bounded
query fields; Schedules own recurring starts; Updates mutate, Signals notify,
Queries read live state, and Continue-As-New bounds history. Product vocabulary
maps to Workflow Execution identity. Details live in
[TemporalArchitecture.md](TemporalArchitecture.md), which remains the internal
architecture hub.

## Contract owners

| Concern | Canonical owner |
| --- | --- |
| Runtime model, architecture rules, replay-safe evolution | [TemporalArchitecture.md](TemporalArchitecture.md) |
| Workflow execution lifecycle, Updates/Signals, timeouts, history management | [WorkflowTypeCatalogAndLifecycle.md](WorkflowTypeCatalogAndLifecycle.md) |
| Workflow type inventory (exact type, module/class owner, projection role) | [WorkflowTypeCatalogGenerated.md](WorkflowTypeCatalogGenerated.md) (generated from `moonmind/workflows/temporal/workflow_registry.py` via `python tools/generate_temporal_catalog.py`; MoonLadderStudios/MoonMind#3959) |
| Activity catalog, worker fleets, task queues, determinism boundary | [ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md) |
| Execution lanes (managed, external, hybrid) and canonical contracts | [ManagedAndExternalAgentExecutionModel.md](ManagedAndExternalAgentExecutionModel.md) |
| Control, pause, drain, quiesce, resume confirmations | [WorkerPauseSystem.md](WorkerPauseSystem.md) |
| Source of truth, projections, reconciliation, projection repair | [SourceOfTruthAndProjectionModel.md](SourceOfTruthAndProjectionModel.md) |
| Artifacts and large-payload discipline | [WorkflowArtifactSystemDesign.md](WorkflowArtifactSystemDesign.md) |
| Step ledger, progress, checkpoints | [StepLedgerAndProgressModel.md](StepLedgerAndProgressModel.md), [CheckpointResumePromotion.md](CheckpointResumePromotion.md) |
| Replay, versioning, run history, new-run semantics | [WorkflowRunHistoryAndNewRunSemantics.md](WorkflowRunHistoryAndNewRunSemantics.md) and [TemporalArchitecture.md](TemporalArchitecture.md) §18 |
| Operational recovery, deployment, runbook | [ops-runbook.md](ops-runbook.md) |
| Scheduling (Schedules vs timers vs worker polling) | [WorkflowSchedulingGuide.md](WorkflowSchedulingGuide.md), [TemporalScheduling.md](TemporalScheduling.md) |
| Signals/Updates/Queries reference | [TemporalSignalsSystem.md](TemporalSignalsSystem.md) |
| Visibility and UI query model | [VisibilityAndUiQueryModel.md](VisibilityAndUiQueryModel.md) |
| Errors, retries, cancellation | [ErrorTaxonomy.md](ErrorTaxonomy.md) |
| Type safety across the workflow boundary | [TemporalTypeSafety.md](TemporalTypeSafety.md) |
| Production routing | [RoutingPolicy.md](RoutingPolicy.md) |
| Status domains | [StatusDomainMatrix.md](StatusDomainMatrix.md) |
| Product vocabulary over Workflow Executions | [WorkflowExecutionProductModel.md](WorkflowExecutionProductModel.md) |
| Platform deployment foundation (draft, implementation-oriented) | [TemporalPlatformFoundation.md](TemporalPlatformFoundation.md) |
| Agent execution design (active design) | [TemporalAgentExecution.md](TemporalAgentExecution.md) |

## Catalog authority rule

There is exactly one workflow-type inventory:
[WorkflowTypeCatalogGenerated.md](WorkflowTypeCatalogGenerated.md). There is
exactly one Activity catalog and worker-topology surface:
[ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md).
Other documents link to these references instead of copying type tables. A
handwritten list that duplicates them is a bug, not a second source.

## Guarantee preservation

Replay, pause, projection repair, publication, and cleanup guarantees keep the
owners named above. The per-file successor and guarantee mapping for this
consolidation lives in
[../tmp/temporal-consolidation-ownership-map-3961.md](../tmp/temporal-consolidation-ownership-map-3961.md):
every removed unique requirement names a successor heading or an explicit
obsolete disposition, and no consolidation step silently substitutes
credentials, provider profiles, runtime values, source authority, or
less-constrained execution paths.

## Related module contracts

- Execution boundary: [ManagedAndExternalAgentExecutionModel.md](ManagedAndExternalAgentExecutionModel.md)
- Runtime boundaries: `docs/ManagedAgents/`, `docs/Observability/LiveLogs.md`
- System architecture: `docs/MoonMindArchitecture.md`
- Documentation rules: `docs/DocumentationArchitecture.md`
