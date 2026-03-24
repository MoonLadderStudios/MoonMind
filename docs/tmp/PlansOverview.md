# Plans overview (non-`docs/tmp` sources)

This file indexes **migration plans, implementation plans, phased rollouts, and similar roadmaps** documented under `docs/`, **excluding** `docs/tmp/`. Entries note where a plan is a **dedicated** artifact versus a **section** inside a larger document.

Scope: product/architecture migrations, implementation sequencing, rollout phases, and explicit roadmaps. Omitted: pure reference docs that only mention “migration” in passing without a plan-shaped section (unless the section is materially about sequencing or posture).

---

## Dedicated plan artifacts

| Document | Summary |
| -------- | ------- |
| [`docs/Tasks/TaskDependenciesPlan.md`](../Tasks/TaskDependenciesPlan.md) | Phased implementation plan for task dependencies (backend → workflow → API → UI), with status markers. |

---

## API and execution compatibility (Temporal migration posture)

| Document | Summary |
| -------- | ------- |
| [`docs/Api/ExecutionsApiContract.md`](../Api/ExecutionsApiContract.md) | Migration posture for `/api/executions` and compatibility during the task-oriented / Temporal migration (incl. §16 compatibility contract). |
| [`docs/CodexMcpToolsAdapter.md`](../CodexMcpToolsAdapter.md) | Notes legacy Codex paths as compatibility during migration. |
| [`docs/Temporal/TaskExecutionCompatibilityModel.md`](../Temporal/TaskExecutionCompatibilityModel.md) | Bridge contract for task-shaped surfaces and Temporal; compatibility maturity (sequencing in `remaining-work`). |
| [`docs/Temporal/SourceOfTruthAndProjectionModel.md`](../Temporal/SourceOfTruthAndProjectionModel.md) | Migration stance for projections vs Temporal; migration-phase exception matrix. |
| [`docs/Temporal/VisibilityAndUiQueryModel.md`](../Temporal/VisibilityAndUiQueryModel.md) | Projection rules; target contract vs implementation gaps (tracker). |
| [`docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`](../Temporal/WorkflowTypeCatalogAndLifecycle.md) | Temporal-native lifecycle contract; task vs workflow terminology at compatibility boundaries. |
| [`docs/Temporal/RoutingPolicy.md`](../Temporal/RoutingPolicy.md) | § Migration & Rollout. |
| [`docs/Temporal/WorkflowSchedulingGuide.md`](../Temporal/WorkflowSchedulingGuide.md) | Migration compatibility expectations for scheduling (dual substrate). |
| [`docs/Temporal/TemporalArchitecture.md`](../Temporal/TemporalArchitecture.md) | §7 substrate evolution; UI contract (rollout detail in `remaining-work` / `SingleSubstrateMigration`). |
| [`docs/Temporal/TemporalPlatformFoundation.md`](../Temporal/TemporalPlatformFoundation.md) | Pre-rollout gates, upgrades, shard-count and cluster migration implications. |

---

## Temporal features and operations

| Document | Summary |
| -------- | ------- |
| [`docs/Temporal/TemporalScheduling.md`](../Temporal/TemporalScheduling.md) | §10 Implementation Plan — points to the detailed phased plan in `docs/tmp/TemporalSchedulingPlan.md` (file outside this index’s source scope). |
| [`docs/Temporal/LiveTaskManagement.md`](../Temporal/LiveTaskManagement.md) | §11 Rollout Plan (live logs, terminal, artifacts). |
| [`docs/Temporal/WorkerPauseSystem.md`](../Temporal/WorkerPauseSystem.md) | §9 Rollout Plan (Temporal alignment vs advanced suspend). |
| [`docs/Temporal/JulesProposalDelivery.md`](../Temporal/JulesProposalDelivery.md) | §7 Rollout Plan for Jules proposal delivery via Temporal. |
| [`docs/Temporal/IntegrationsMonitoringDesign.md`](../Temporal/IntegrationsMonitoringDesign.md) | §9.4 migration note; §15 implementation order. |
| [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md) | §13 implementation sequence for the activity catalog; rollout/migration notes for new activity types. |
| [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md) | Phased implementation strategy (contracts through observability); migration note for fleet hosting. |
| [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../Temporal/WorkflowArtifactSystemDesign.md) | §16 deliverables checklist (implementation-oriented). |
| [`docs/Temporal/RunHistoryAndRerunSemantics.md`](../Temporal/RunHistoryAndRerunSemantics.md) | Rerun and Continue-As-New semantics; related backlog in tracker. |

---

## Mission Control UI and frontend

| Document | Summary |
| -------- | ------- |
| [`docs/UI/MissionControlArchitecture.md`](../UI/MissionControlArchitecture.md) | Phased rollout (Temporal read → actions → submit → scheduling → compatibility); implementation checklist. |
| [`docs/UI/MissionControlStyleGuide.md`](../UI/MissionControlStyleGuide.md) | §11 Migration Status — design-system phases (toolchain through polish). |
| [`docs/UI/TypeScriptSystem.md`](../UI/TypeScriptSystem.md) | §15 Migration Plan (Phase 0–3); migration rules and constraints elsewhere in the doc. |
| [`docs/UI/SkillsTabDesign.md`](../UI/SkillsTabDesign.md) | API/backend/frontend implementation sections and implementation checklist. |

---

## Tasks, skills, presets, and plans

| Document | Summary |
| -------- | ------- |
| [`docs/Tasks/TaskDependencies.md`](../Tasks/TaskDependencies.md) | §8 Implementation Plan — delegates to `docs/Tasks/TaskDependenciesPlan.md`; includes schema/migration requirements for dependency state. |
| [`docs/Tasks/TaskPresetsSystem.md`](../Tasks/TaskPresetsSystem.md) | §10 Migration Path — phased preset → plan compilation and API/table renames. |
| [`docs/Tasks/SkillAndPlanEvolution.md`](../Tasks/SkillAndPlanEvolution.md) | Recommendations & migration strategy; implementation roadmap. |
| [`docs/Tasks/SkillAndPlanContracts.md`](../Tasks/SkillAndPlanContracts.md) | §14 implementation checklist (minimum to ship). |
| [`docs/Tasks/ImageSystem.md`](../Tasks/ImageSystem.md) | § Rollout & Migration Notes — phased vision/artifact path. |

---

## Managed agents

| Document | Summary |
| -------- | ------- |
| [`docs/ManagedAgents/SecretStore.md`](../ManagedAgents/SecretStore.md) | §9 Implementation Plan (Vault → workflow refs → observability → remove bridge); initial rollout non-goals. |
| [`docs/ManagedAgents/TmateArchitecture.md`](../ManagedAgents/TmateArchitecture.md) | §16 MVP implementation sequence (four phases). |
| [`docs/ManagedAgents/CursorCli.md`](../ManagedAgents/CursorCli.md) | §13 Implementation Plan (binary through testing). |
| [`docs/ManagedAgents/DockerOutOfDocker.md`](../ManagedAgents/DockerOutOfDocker.md) | DOOD “plan” for generic container runner (desired state). |
| [`docs/ManagedAgents/ManagedAgentsAuthentication.md`](../ManagedAgents/ManagedAgentsAuthentication.md) | §8 Migration Path — phases for profiles, registry, multi-volume, queuing, dedicated fleet. |
| [`docs/ManagedAgents/WorkerVectorEmbedding.md`](../ManagedAgents/WorkerVectorEmbedding.md) | Minimal implementation plan. |
| [`docs/ManagedAgents/SkillGithubPrResolver.md`](../ManagedAgents/SkillGithubPrResolver.md) | §13 implementation checklist. |
| [`docs/ManagedAgents/ManagedAgentsGit.md`](../ManagedAgents/ManagedAgentsGit.md) | Fast-path implementation; forward compatibility with secret-reference migration. |

---

## External agents

| Document | Summary |
| -------- | ------- |
| [`docs/ExternalAgents/ExternalAgentIntegrationSystem.md`](../ExternalAgents/ExternalAgentIntegrationSystem.md) | §5 Proposed Universal External Adapter Plan; §7 Practical Implementation Sequence (phases A–…). |
| [`docs/tmp/JulesTemporalIntegrationReport.md`](JulesTemporalIntegrationReport.md) | Prioritized implementation tasks; event contract migration & versioning. |
| [`docs/ExternalAgents/JulesTemporalExternalEventContract.md`](../ExternalAgents/JulesTemporalExternalEventContract.md) | References Phase C migration items (e.g. adapter wiring). |
| [`docs/ExternalAgents/OpenClawAgentAdapter.md`](../ExternalAgents/OpenClawAgentAdapter.md) | Merge-ready sequence and implementation checklist. |
| [`docs/ExternalAgents/AddingExternalProvider.md`](../ExternalAgents/AddingExternalProvider.md) | Step-by-step integration procedure for a new provider (implementation guide). |
| [`docs/ExternalAgents/JulesClientAdapter.md`](../ExternalAgents/JulesClientAdapter.md) | Relationship to the universal external adapter plan (context for rollout standardization). |

---

## RAG and manifests

| Document | Summary |
| -------- | ------- |
| [`docs/Rag/ManifestIngestDesign.md`](../Rag/ManifestIngestDesign.md) | §19 delivery scope — baseline vs remaining pipeline/UI (detail in tracker). |
| [`docs/Rag/LlamaIndexManifestSystem.md`](../Rag/LlamaIndexManifestSystem.md) | Roadmap & versioning. |
| [`docs/Rag/WorkflowRag.md`](../Rag/WorkflowRag.md) | Implementation checklist. |

---

## Memory

| Document | Summary |
| -------- | ------- |
| [`docs/Memory/MemoryResearch.md`](../Memory/MemoryResearch.md) | Evaluation / experimental plans; effort estimates involving schema migration and API work (research doc with planning character). |

---

## Database migration tooling (procedural)

| Document | Summary |
| -------- | ------- |
| [`docs/ExternalAgents/AlembicMigrationGeneration.md`](../ExternalAgents/AlembicMigrationGeneration.md) | How to **generate** Alembic migrations (not a feature migration roadmap, but the canonical migration-script workflow). |

---

## Declarative rewrites (2026-03-24)

These entries were converted from migration / phased-implementation framing to **steady-state** technical docs after confirming behavior in the repository:

- [`docs/ExternalAgents/ExternalAgentIntegrationSystem.md`](../ExternalAgents/ExternalAgentIntegrationSystem.md) — universal adapter base and provider boundaries are implemented; phased §7 removed.
- [`docs/ExternalAgents/OpenClawAgentAdapter.md`](../ExternalAgents/OpenClawAgentAdapter.md) — streaming-gateway path, settings, and tests exist; implementation checklist removed.
- [`docs/ManagedAgents/SkillGithubPrResolver.md`](../ManagedAgents/SkillGithubPrResolver.md) — skill tree and unit tests exist; implementation checklist replaced with verification pointers.

**Not rewritten** (incomplete, open research, operational procedure, or product-wide migration still in flight): all other rows in this index — notably task dependencies phases 2–4, Cursor CLI integration checklists, Mission Control / Temporal migration posture docs, `WorkflowRag` and manifest delivery unfinished items, `ManagedAndExternalAgentExecutionModel` Phase 7, `AlembicMigrationGeneration.md` (procedural), and `MemoryResearch.md` (research).

---

## Maintenance

When adding or renaming plan-shaped sections in `docs/`, update this index so `docs/tmp/` remains a useful aggregate without duplicating full plans.

**Remaining-work trackers:** For each indexed document that still has open migration or implementation content, a companion file lives under [`docs/tmp/remaining-work/`](remaining-work/README.md) (see `README.md` there for the full table). Update or remove a tracker when the source doc’s backlog is cleared.
