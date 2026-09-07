# Temporal Contract Ownership (MoonLadderStudios/MoonMind#3961)

**Document Class:** Module ownership map

This is the durable target-state ownership map for `docs/Temporal/`. Every
Temporal contract has exactly one surviving owner. Duplicated restatements
were replaced with pointers to the owner; no owner text was deleted. The
short reader entrypoint is
[`TemporalModuleArchitecture.md`](./TemporalModuleArchitecture.md).

Precedence follows `docs/DocumentationArchitecture.md` §7: conflicts resolve
by claim-type authority (contract shape → Module Contract Specification,
dependency direction → System Architecture View), not by file age.

## Owner register

| File | Role | Status | Unique normative content (stays) | Defers to |
|---|---|---|---|---|
| `TemporalModuleArchitecture.md` | Module Architecture View entrypoint | Normative entrypoint | Runtime model summary + owner routing table | — (routes only) |
| `ContractOwnership.md` | Ownership map (this file) | Target-state record | Old-path/heading → owner dispositions | — |
| `TemporalArchitecture.md` | Architecture hub: invariants, operating model | Normative architecture hub | §§1–6 invariants and operating planes; §8 identifier model | Workflow catalog → `WorkflowTypeCatalogAndLifecycle.md` §4; worker/activity/visibility detail → `ActivityCatalogAndWorkerTopology.md`, `TemporalTypeSafety.md`, `VisibilityAndUiQueryModel.md` |
| `TemporalPlatformFoundation.md` | Platform deployment foundation | Draft (implementation-oriented) | Deployment profile, namespaces/retention, shards, Task Queue routing policy, security/observability foundation | Core workflow catalog → `WorkflowTypeCatalogAndLifecycle.md` §4; visibility detail → `VisibilityAndUiQueryModel.md`; scheduling → `TemporalScheduling.md` |
| `WorkflowTypeCatalogAndLifecycle.md` | Workflow type catalog + lifecycle contract | Normative (Temporal application layer) | Workflow Type names, IDs, domain state, cancellation, timeouts, retry posture, history rules | Update/Signal shapes → `TemporalSignalsSystem.md` §6; run-history detail → `WorkflowRunHistoryAndNewRunSemantics.md` |
| `ActivityCatalogAndWorkerTopology.md` | Activity catalog + worker fleet topology | Implemented in core runtime | Canonical Activity Types, fleets, Task Queue routing, operational execution rules | — |
| `ManagedAndExternalAgentExecutionModel.md` | Managed/external/Omnigent execution lanes | Current | Lane model, capability snapshot, workspace/artifact authority, retry/replay/reconciliation | Workflow catalog entries → `WorkflowTypeCatalogAndLifecycle.md` §4 |
| `WorkflowExecutionProductModel.md` | Product vocabulary contract | Normative | Workflow Execution identity, Step model, Task-word ban, chat/instruction models | Workflow type rows → `WorkflowTypeCatalogAndLifecycle.md` §4 |
| `TemporalSignalsSystem.md` | Canonical Update/Signal contracts | Design Draft | Per-workflow signal/update shapes, async-vs-acknowledged rules, compactness/retry safety | — |
| `TemporalSignalsResearch.md` | Signal gap research evidence | Research report (non-canonical) | Inventory, assessment, migration evidence | All contracts → `TemporalSignalsSystem.md` §6 |
| `TemporalScheduling.md` | Canonical scheduling semantics | Draft | start_delay, Temporal Schedules, reschedulable timers, mechanism matrix, DST guarantees | Publication scope → Workflow Publishing owner |
| `WorkflowSchedulingGuide.md` | Dashboard scheduling UX | Active | Submit-form UX, schedule panel fields, list/detail routes, legacy compatibility | §4 canonical mechanisms → `TemporalScheduling.md` §§4–6, 11 |
| `TemporalAgentExecution.md` | Historical agent-execution narrative | Active design | End-to-end submission flow, payload/ToolInvocation shapes, status narrative | Activity catalog → `ActivityCatalogAndWorkerTopology.md`; worker topology → `ActivityCatalogAndWorkerTopology.md` |
| `WorkerPauseSystem.md` | Admission pause, drain, confirmations | Normative contract | Admission state, audit, drain vs shutdown boundary | — (recovery execution → `ops-runbook.md`) |
| `SourceOfTruthAndProjectionModel.md` | Source of truth, projections, recovery, Continue-As-New | Normative steady-state contract | Truth/projection split, recovery and Continue-As-New semantics (required by #3961) | — |
| `WorkflowRunHistoryAndNewRunSemantics.md` | Run history and new-run semantics | Desired State | Run identity, `RequestRerun`, failed-step recovery vs new run | History mechanics → `SourceOfTruthAndProjectionModel.md` |
| `WorkflowArtifactSystemDesign.md` | Artifacts and checkpoints | Draft | Artifact lifecycle, execution projections, support persistence | Checkpoint Resume admission → `CheckpointResumePromotion.md` |
| `CheckpointResumePromotion.md` | Checkpoint-backed Resume promotion | Promoted `codex_cli` capability | Supported boundary/phase pairs, frozen admitted descriptor | — |
| `StepLedgerAndProgressModel.md` | Step ledger and progress | Normative | Planned-step source, live ledger, progress model | — |
| `TemporalTypeSafety.md` | Wire/replay type-safety discipline | Desired state / normative target | Payload validation, serialization, replay-history contract rules | — |
| `VisibilityAndUiQueryModel.md` | Visibility and UI query model | Active design guidance | Search Attribute budget, query model, provider-neutrality rule | Status values → `StatusDomainMatrix.md` |
| `StatusDomainMatrix.md` | Cross-domain status matrix | Normative | Domain owners, value sets, conversion boundaries | — |
| `ErrorTaxonomy.md` | Error taxonomy and retry posture | Core policy (partially enforced) | Failure categories, retry mapping, UI-facing classes | — |
| `RoutingPolicy.md` | Production routing policy | Operational policy | queue/system/temporal routing, runtime-picker exclusion | — |
| `ops-runbook.md` | Operational recovery runbook | Operational | Recovery procedures, triage, cleanup execution | Contract semantics → owning contracts above |
| `IntegrationsMonitoringDesign.md` | External integrations monitoring | Draft (Temporal-first) | External operation state, provider activity contract, callback/polling paths | `ExternalEvent` shape → `TemporalSignalsSystem.md` §6.1 |
| `ChatInstructionTemporalContract.md` | Deferred chat-steering reservation | Deferred optional extension | Future steering-action reservation (not ordinary chat) | Chat product contract → `docs/UI/WorkflowChatPanel.md` |
| `WorkflowLanguageHardSwitchPlan.md` | Terminology hard-switch plan | Proposed plan | Task-word removal decision, cutover sequencing | Settled vocabulary → `WorkflowExecutionProductModel.md` |

## Removed-duplicate dispositions

- `TemporalAgentExecution.md` §§3.3–3.4 (Activity catalog, worker fleet
  tables) → superseded by `ActivityCatalogAndWorkerTopology.md`; replaced
  with owner pointers. Unique §§1–3.2, 4–6 retained.
- `WorkflowTypeCatalogAndLifecycle.md` §6 (Update/Signal contracts) →
  superseded by `TemporalSignalsSystem.md` §6; replaced with owner pointers.
  Requirement IDs, payload names, and retry/cancellation rules stay with the
  owner. Unique §§1–5, 7–10 retained.
- `TemporalArchitecture.md` §7 (workflow catalog table) → superseded by
  `WorkflowTypeCatalogAndLifecycle.md` §4; replaced with an owner pointer.
  Unique §§1–6, 8–11 retained.
- `TemporalPlatformFoundation.md` §5 (core workflow catalog) → superseded by
  `WorkflowTypeCatalogAndLifecycle.md` §4; replaced with an owner pointer.
  Unique deployment/platform content retained.
- `WorkflowSchedulingGuide.md` §4 (canonical scheduling mechanisms) →
  superseded by `TemporalScheduling.md` §§4–6, 11; replaced with an owner
  pointer. Unique dashboard UX (§§5–7) retained.
- `TemporalSignalsResearch.md` desired-state contract sections → evidence
  only; authority note points to `TemporalSignalsSystem.md` §6. No research
  content deleted.
- `TemporalArchitecture.md` §§9–11 worker/activity/visibility detail →
  summary retained, detail pointers to `ActivityCatalogAndWorkerTopology.md`,
  `TemporalTypeSafety.md`, `VisibilityAndUiQueryModel.md`.
