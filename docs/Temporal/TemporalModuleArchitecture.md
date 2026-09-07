# Temporal Module Architecture

**Document Class:** Module Architecture View
**Status:** Normative entrypoint
**Owner:** MoonMind Platform
**Last updated:** 2026-09-07
**Audience:** backend, workflow authors, operators, new readers
**Source issue:** MoonLadderStudios/MoonMind#3961

MoonMind uses Temporal as its durable orchestration substrate for
Temporal-managed execution. Workflows orchestrate deterministically and carry
no side effects; Activities perform all side effects and are bounded or
heartbeat-aware. This document is the short entrypoint: it states the runtime
model once and routes each major contract to its single owning document. It
does not duplicate catalog tables, payload shapes, or policy matrices owned
elsewhere. Ownership disputes resolve per
`docs/DocumentationArchitecture.md` §7 (claim-type authority, not file age).

## Runtime model

One durable execution identity (`workflowId`, `mm:<uuid>` for ordinary
user/service executions) owns orchestration state. One concrete attempt is a
`runId`. `MoonMind.UserWorkflow` plans work, owns the Step ledger, starts
child agent runs, and integrates results. True agent execution runs as a
`MoonMind.AgentRun` child workflow, never as a bare Activity. Large payloads
travel as artifact refs; Temporal Visibility carries only bounded query
fields (`mm_state`, Search Attributes); Memo carries bounded debug fields.
Updates are acknowledged mutations; Signals are compact async ingress.
Cancellation, Continue-As-New, replay, and projection repair follow the
owners below.

## Contract owners

| Concern | Canonical owner |
|---|---|
| Execution and lifecycle model, invariants, operating planes | [`TemporalArchitecture.md`](./TemporalArchitecture.md) |
| Platform deployment, namespaces, retention, shards, fleet strategy | [`TemporalPlatformFoundation.md`](./TemporalPlatformFoundation.md) |
| Workflow type catalog, naming/identity rules, lifecycle, cancellation, history policy | [`WorkflowTypeCatalogAndLifecycle.md`](./WorkflowTypeCatalogAndLifecycle.md) |
| Activity catalog, worker fleets, Task Queue routing | [`ActivityCatalogAndWorkerTopology.md`](./ActivityCatalogAndWorkerTopology.md) |
| Managed, external, and profile-bound Omnigent execution lanes | [`ManagedAndExternalAgentExecutionModel.md`](./ManagedAndExternalAgentExecutionModel.md) |
| Product vocabulary (Workflow Execution, Step Execution, no product Task) | [`WorkflowExecutionProductModel.md`](./WorkflowExecutionProductModel.md) |
| Update/Signal contracts per workflow | [`TemporalSignalsSystem.md`](./TemporalSignalsSystem.md) |
| Scheduling (one-time delay, Temporal Schedules, reschedulable timers) | [`TemporalScheduling.md`](./TemporalScheduling.md) |
| Dashboard scheduling UX over the canonical scheduling contracts | [`WorkflowSchedulingGuide.md`](./WorkflowSchedulingGuide.md) (UI use only; §4 defers to `TemporalScheduling.md`) |
| Control plane: admission pause, drain, confirmations | [`WorkerPauseSystem.md`](./WorkerPauseSystem.md) |
| Source of truth, projections, recovery, Continue-As-New | [`SourceOfTruthAndProjectionModel.md`](./SourceOfTruthAndProjectionModel.md) |
| Run history and new-run (rerun/recovery) semantics | [`WorkflowRunHistoryAndNewRunSemantics.md`](./WorkflowRunHistoryAndNewRunSemantics.md) |
| Artifacts and checkpoints | [`WorkflowArtifactSystemDesign.md`](./WorkflowArtifactSystemDesign.md) |
| Checkpoint-backed Resume promotion state | [`CheckpointResumePromotion.md`](./CheckpointResumePromotion.md) |
| Step ledger and progress | [`StepLedgerAndProgressModel.md`](./StepLedgerAndProgressModel.md) |
| Type safety and wire/replay contracts | [`TemporalTypeSafety.md`](./TemporalTypeSafety.md) |
| Visibility and UI query model | [`VisibilityAndUiQueryModel.md`](./VisibilityAndUiQueryModel.md) |
| Status domains and conversion boundaries | [`StatusDomainMatrix.md`](./StatusDomainMatrix.md) |
| Error taxonomy and retry posture | [`ErrorTaxonomy.md`](./ErrorTaxonomy.md) |
| Production routing policy | [`RoutingPolicy.md`](./RoutingPolicy.md) |
| Operational recovery | [`ops-runbook.md`](./ops-runbook.md) |
| Integrations and monitoring | [`IntegrationsMonitoringDesign.md`](./IntegrationsMonitoringDesign.md) |
| Research evidence for signal gaps (non-canonical) | [`TemporalSignalsResearch.md`](./TemporalSignalsResearch.md) (evidence only; contracts live in `TemporalSignalsSystem.md`) |
| Historical agent-execution narrative | [`TemporalAgentExecution.md`](./TemporalAgentExecution.md) (narrative only; catalogs live in `ActivityCatalogAndWorkerTopology.md`) |
| Hard-switch terminology plan | [`WorkflowLanguageHardSwitchPlan.md`](./WorkflowLanguageHardSwitchPlan.md) (plan; settled vocabulary lives in `WorkflowExecutionProductModel.md`) |
| Deferred chat-steering reservation | [`ChatInstructionTemporalContract.md`](./ChatInstructionTemporalContract.md) |

Full old-path/heading → canonical-owner disposition is pinned in
[`ContractOwnership.md`](./ContractOwnership.md).
