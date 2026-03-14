# Legacy Docs Review

This document tracks the status of all documentation found in the `docs` directory in relation to the new Temporal-based execution model defined in `docs/ManagedAgents/ManagedAgentsAuthentication.md` and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`. Documents that are fully aligned or unrelated to task execution are omitted from this list.

## Documents to Delete

The following documents refer to obsolete systems (such as the legacy REST `api/queue` endpoints, Celery workers, and `SpecKit` processes).

| Document | Status | Judgment |
|----------|--------|----------|
| `docs/CodexCliWorkers.md` | Completely irrelevant | Delete |
| `docs/GeminiCliWorkers.md` | Completely irrelevant | Delete |
| `docs/SpecKitAutomation.md` | Completely irrelevant | Delete |
| `docs/SpecKitAutomationInstructions.md` | Completely irrelevant | Delete |
| `docs/UnifiedCliSingleQueueArchitecture.md` | Completely irrelevant | Delete |
| `docs/WorkerSelfHealSystem.md` | Completely irrelevant | Delete (Superseded by Temporal native retries) |

## Documents to Update

The following documents contain valuable architectural concepts or workflows but reference legacy payload structures or execution pathways that need to be aligned with Temporal Activities and Workflows.

| Document | Status | Judgment |
|----------|--------|----------|
| `docs/LiveTaskHandoff.md` | Partially out-of-date | Updated (Moved to `docs/Temporal/LiveTaskHandoff.md` and refactored) |
| `docs/ManifestTaskSystem.md` | Partially out-of-date | Updated (Moved to `docs/Tasks/ManifestTaskSystem.md` and refactored) |
| `docs/OrchestratorArchitecture.md` | Partially out-of-date | Updated (Moved to `docs/Temporal/OrchestratorArchitecture.md` and refactored) |
| `docs/OrchestratorTaskRuntime.md` | Partially out-of-date | Updated (Moved to `docs/Temporal/OrchestratorTaskRuntime.md` and refactored) |
| `docs/SecretStore.md` | Partially out-of-date | Updated (Moved to `docs/ManagedAgents/SecretStore.md` and refactored) |
| `docs/DockerOutOfDocker.md` | Partially out-of-date | Updated (Moved to `docs/ManagedAgents/DockerOutOfDocker.md` and refactored) |
| `docs/SkillGithubPrResolver.md` | Partially out-of-date | Updated (Moved to `docs/ManagedAgents/SkillGithubPrResolver.md` and refactored) |
| `docs/WorkerGitAuth.md` | Partially out-of-date | Updated (Moved to `docs/ManagedAgents/WorkerGitAuth.md` and refactored) |
| `docs/WorkerPauseSystem.md` | Partially out-of-date | Updated (Moved to `docs/Temporal/WorkerPauseSystem.md` and refactored) |
| `docs/WorkerRag.md` | Partially out-of-date | Updated (Moved to `docs/ManagedAgents/WorkerRag.md` and refactored) |
| `docs/WorkerVectorEmbedding.md` | Partially out-of-date | Updated (Moved to `docs/ManagedAgents/WorkerVectorEmbedding.md` and refactored) |
| `docs/ops-runbook.md` | Partially out-of-date | Updated (Moved to `docs/Temporal/ops-runbook.md` and refactored) |
| `docs/Tasks/TasksJira.md` | Partially out-of-date | Updated (Finished Temporal Activity Implementation Details) |
| `docs/Tasks/TasksStepSystem.md` | Partially out-of-date | Updated (Aligned step execution with Temporal workflows) |
