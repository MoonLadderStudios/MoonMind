# Remaining migration / implementation work (by source doc)

Each file in this directory tracks **open migration or implementation work** described in the corresponding document under `docs/`.

**Alignment with idiomatic Temporal:** Trackers should describe *remaining* work without endorsing **non-Temporal primary orchestration**, **permanent** dual queue/Temporal dispatch, or **side-channel** lifecycle control (e.g. cancel/process control outside `WorkflowHandle.cancel` / workflow code). Prefer pointing to:

- [`docs/tmp/SingleSubstrateMigration.md`](../SingleSubstrateMigration.md) — single execution substrate; retire queue/system paths.
- [`docs/tmp/TemporalSchedulingPlan.md`](../TemporalSchedulingPlan.md) and [`docs/tmp/TemporalSchedulingImprovements.md`](../TemporalSchedulingImprovements.md) — **Temporal Schedules** (and related APIs) for recurring cadence, not a long-lived bespoke DB scheduler as the end state.
- [`docs/tmp/TemporalWorkflowExecutionImprovements.md`](../TemporalWorkflowExecutionImprovements.md) — signals/updates/queries, `ActivityCancellationType`, worker versioning, schedule reconciliation.
- [`docs/tmp/CancellationAnalysis.md`](../CancellationAnalysis.md) — cancellation via Temporal primitives (heartbeats, `TRY_CANCEL`, etc.).

**No tracker** (steady-state rewrites already done): `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`, `docs/ExternalAgents/OpenClawAgentAdapter.md`, `docs/ManagedAgents/SkillGithubPrResolver.md` — see `docs/tmp/PlansOverview.md` §Declarative rewrites.

**Index**

| Source document | Tracker |
|-----------------|---------|
| `docs/Tasks/TaskDependenciesPlan.md` | [`plans-TaskDependenciesPlan.md`](plans-TaskDependenciesPlan.md) |
| `docs/Api/ExecutionsApiContract.md` | [`Api-ExecutionsApiContract.md`](Api-ExecutionsApiContract.md) |
| `docs/CodexMcpToolsAdapter.md` | [`CodexMcpToolsAdapter.md`](CodexMcpToolsAdapter.md) |
| `docs/Temporal/TaskExecutionCompatibilityModel.md` | [`Temporal-TaskExecutionCompatibilityModel.md`](Temporal-TaskExecutionCompatibilityModel.md) |
| `docs/Temporal/SourceOfTruthAndProjectionModel.md` | [`Temporal-SourceOfTruthAndProjectionModel.md`](Temporal-SourceOfTruthAndProjectionModel.md) |
| `docs/Temporal/VisibilityAndUiQueryModel.md` | [`Temporal-VisibilityAndUiQueryModel.md`](Temporal-VisibilityAndUiQueryModel.md) |
| `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` | [`Temporal-WorkflowTypeCatalogAndLifecycle.md`](Temporal-WorkflowTypeCatalogAndLifecycle.md) |
| `docs/Temporal/RoutingPolicy.md` | [`Temporal-RoutingPolicy.md`](Temporal-RoutingPolicy.md) |
| `docs/Temporal/WorkflowSchedulingGuide.md` | [`Temporal-WorkflowSchedulingGuide.md`](Temporal-WorkflowSchedulingGuide.md) |
| `docs/Temporal/TemporalArchitecture.md` | [`Temporal-TemporalArchitecture.md`](Temporal-TemporalArchitecture.md) |
| `docs/Temporal/TemporalPlatformFoundation.md` | [`Temporal-TemporalPlatformFoundation.md`](Temporal-TemporalPlatformFoundation.md) |
| `docs/Temporal/TemporalScheduling.md` | [`Temporal-TemporalScheduling.md`](Temporal-TemporalScheduling.md) |
| `docs/Temporal/LiveTaskManagement.md` | [`Temporal-LiveTaskManagement.md`](Temporal-LiveTaskManagement.md) |
| `docs/Temporal/WorkerPauseSystem.md` | [`Temporal-WorkerPauseSystem.md`](Temporal-WorkerPauseSystem.md) |
| `docs/Temporal/JulesProposalDelivery.md` | [`Temporal-JulesProposalDelivery.md`](Temporal-JulesProposalDelivery.md) |
| `docs/Temporal/IntegrationsMonitoringDesign.md` | [`Temporal-IntegrationsMonitoringDesign.md`](Temporal-IntegrationsMonitoringDesign.md) |
| `docs/Temporal/ActivityCatalogAndWorkerTopology.md` | [`Temporal-ActivityCatalogAndWorkerTopology.md`](Temporal-ActivityCatalogAndWorkerTopology.md) |
| `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` | [`Temporal-ManagedAndExternalAgentExecutionModel.md`](Temporal-ManagedAndExternalAgentExecutionModel.md) |
| `docs/Temporal/WorkflowArtifactSystemDesign.md` | [`Temporal-WorkflowArtifactSystemDesign.md`](Temporal-WorkflowArtifactSystemDesign.md) |
| `docs/Temporal/RunHistoryAndRerunSemantics.md` | [`Temporal-RunHistoryAndRerunSemantics.md`](Temporal-RunHistoryAndRerunSemantics.md) |
| `docs/UI/MissionControlArchitecture.md` | [`UI-MissionControlArchitecture.md`](UI-MissionControlArchitecture.md) |
| `docs/UI/MissionControlStyleGuide.md` | [`UI-MissionControlStyleGuide.md`](UI-MissionControlStyleGuide.md) |
| `docs/UI/TypeScriptSystem.md` | [`UI-TypeScriptSystem.md`](UI-TypeScriptSystem.md) |
| `docs/UI/SkillsTabDesign.md` | [`UI-SkillsTabDesign.md`](UI-SkillsTabDesign.md) |
| `docs/Tasks/TaskDependencies.md` | [`Tasks-TaskDependencies.md`](Tasks-TaskDependencies.md) |
| `docs/Tasks/TaskPresetsSystem.md` | [`Tasks-TaskPresetsSystem.md`](Tasks-TaskPresetsSystem.md) |
| `docs/Tasks/SkillAndPlanEvolution.md` | [`Tasks-SkillAndPlanEvolution.md`](Tasks-SkillAndPlanEvolution.md) |
| `docs/Tasks/SkillAndPlanContracts.md` | [`Tasks-SkillAndPlanContracts.md`](Tasks-SkillAndPlanContracts.md) |
| `docs/Tasks/ImageSystem.md` | [`Tasks-ImageSystem.md`](Tasks-ImageSystem.md) |
| `docs/ManagedAgents/SecretStore.md` | [`ManagedAgents-SecretStore.md`](ManagedAgents-SecretStore.md) |
| `docs/ManagedAgents/UniversalTmateOAuth.md` | [`ManagedAgents-UniversalTmateOAuth.md`](ManagedAgents-UniversalTmateOAuth.md) |
| `docs/ManagedAgents/CursorCli.md` | [`ManagedAgents-CursorCli.md`](ManagedAgents-CursorCli.md) |
| `docs/ManagedAgents/DockerOutOfDocker.md` | [`ManagedAgents-DockerOutOfDocker.md`](ManagedAgents-DockerOutOfDocker.md) |
| `docs/ManagedAgents/ManagedAgentsAuthentication.md` | [`ManagedAgents-ManagedAgentsAuthentication.md`](ManagedAgents-ManagedAgentsAuthentication.md) |
| `docs/ManagedAgents/WorkerVectorEmbedding.md` | [`ManagedAgents-WorkerVectorEmbedding.md`](ManagedAgents-WorkerVectorEmbedding.md) |
| `docs/ManagedAgents/ManagedAgentsGit.md` | [`ManagedAgents-ManagedAgentsGit.md`](ManagedAgents-ManagedAgentsGit.md) |
| `docs/tmp/JulesTemporalIntegrationReport.md` | [`ExternalAgents-JulesTemporalIntegrationReport.md`](ExternalAgents-JulesTemporalIntegrationReport.md) |
| `docs/ExternalAgents/JulesTemporalExternalEventContract.md` | [`ExternalAgents-JulesTemporalExternalEventContract.md`](ExternalAgents-JulesTemporalExternalEventContract.md) |
| `docs/ExternalAgents/AddingExternalProvider.md` | [`ExternalAgents-AddingExternalProvider.md`](ExternalAgents-AddingExternalProvider.md) |
| `docs/ExternalAgents/JulesClientAdapter.md` | [`ExternalAgents-JulesClientAdapter.md`](ExternalAgents-JulesClientAdapter.md) |
| `docs/Rag/ManifestIngestDesign.md` | [`Rag-ManifestIngestDesign.md`](Rag-ManifestIngestDesign.md) |
| `docs/Rag/LlamaIndexManifestSystem.md` | [`Rag-LlamaIndexManifestSystem.md`](Rag-LlamaIndexManifestSystem.md) |
| `docs/Rag/WorkflowRag.md` | [`Rag-WorkflowRag.md`](Rag-WorkflowRag.md) |
| `docs/Memory/MemoryResearch.md` | [`Memory-MemoryResearch.md`](Memory-MemoryResearch.md) |
| `docs/MIGRATION_GENERATION.md` | [`MIGRATION_GENERATION.md`](MIGRATION_GENERATION.md) |

**Maintenance:** When a source doc’s open work is finished, update or delete its tracker and adjust `docs/tmp/PlansOverview.md` if needed.
