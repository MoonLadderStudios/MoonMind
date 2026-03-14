# Legacy Docs Review

This document tracks the status of all documentation found in the `docs` directory in relation to the new Temporal-based execution model defined in `docs/ManagedAgents/ManagedAgentsAuthentication.md` and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`. Documents that are fully aligned or unrelated to task execution are omitted from this list.

## Documents to Delete

The following documents refer to obsolete systems (such as the legacy REST `api/queue` endpoints, Celery workers, and `SpecKit` processes).

| Document | Status | Judgment |
|----------|--------|----------|
| `docs/CodexCliWorkers.md` | Completely irrelevant | Delete |
| `docs/DockerOutOfDocker.md` | Completely irrelevant | Delete |
| `docs/GeminiCliWorkers.md` | Completely irrelevant | Delete |
| `docs/SpecKitAutomation.md` | Completely irrelevant | Delete |
| `docs/SpecKitAutomationInstructions.md` | Completely irrelevant | Delete |
| `docs/UnifiedCliSingleQueueArchitecture.md` | Completely irrelevant | Delete |
| `docs/WorkerSelfHealSystem.md` | Completely irrelevant | Delete (Superseded by Temporal native retries) |

## Documents to Update

The following documents contain valuable architectural concepts or workflows but reference legacy payload structures or execution pathways that need to be aligned with Temporal Activities and Workflows.

| Document | Status | Judgment |
|----------|--------|----------|
| `docs/LiveTaskHandoff.md` | Partially out-of-date | Update (Align with Temporal pause/unpause signals) |
| `docs/ManifestTaskSystem.md` | Partially out-of-date | Update (Refactor to Temporal terms) |
| `docs/OrchestratorArchitecture.md` | Partially out-of-date | Update (Update orchestration references) |
| `docs/OrchestratorTaskRuntime.md` | Partially out-of-date | Update (Update runtime execution references) |
| `docs/SecretStore.md` | Partially out-of-date | Update (Refactor legacy queue payload to Temporal payload format) |
| `docs/SkillGithubPrResolver.md` | Partially out-of-date | Update (Refactor legacy payload structures to Temporal formats) |
| `docs/WorkerGitAuth.md` | Partially out-of-date | Update (Update payload formats and worker capability mentions) |
| `docs/WorkerPauseSystem.md` | Partially out-of-date | Update (Rewrite to use Temporal Activity pauses instead of REST API claim blocking) |
| `docs/WorkerRag.md` | Partially out-of-date | Update (Refactor references from legacy Codex workers to Temporal Activities) |
| `docs/WorkerVectorEmbedding.md` | Partially out-of-date | Update (Refactor references from FastAPI queue endpoints to Temporal Activities) |
| `docs/ops-runbook.md` | Partially out-of-date | Update (Remove Celery and REST queue recovery instructions; add Temporal runbook instructions) |
| `docs/Tasks/TasksJira.md` | Partially out-of-date | Update / Finish Implementation |
| `docs/Tasks/TasksStepSystem.md` | Partially out-of-date | Update (Align step execution with Temporal workflows) |
