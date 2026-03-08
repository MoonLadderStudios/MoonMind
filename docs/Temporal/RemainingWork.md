# Temporal Transition Plan

Based on the provided documentation, MoonMind currently has a **"Temporal-shaped staging implementation"** but has not yet fully transitioned to a real Temporal-backed execution system.

While substantial foundational work is complete—including Docker Compose services, UI plumbing, worker topologies, activity handlers, and an execution API—the local database currently acts as the authority rather than Temporal itself.

Here is a deep dive into the major work remaining to achieve a fully Temporal-based workflow execution system:

## 1. Core Runtime and Workflow Definitions

Currently, workers can start but default to idling (e.g., `sleep infinity`), and state transitions are managed by an application-level state machine. The following must be built:

* **Real Temporal Worker Runtime:** Implement a long-running worker process that actively connects to the Temporal Server, registers workflows/activities, and polls configured task queues instead of idling.
* **`MoonMind.Run` Workflow:** Create the actual Temporal workflow definition to own the standard task execution lifecycle (initializing, planning, executing, awaiting_external, finalizing) and handle terminal outcomes.
* **`MoonMind.ManifestIngest` Workflow:** Implement a dedicated Temporal workflow for manifest ingest that can orchestrate child runs, emit durable statuses, and compile manifests.

## 2. Shifting the Source of Truth to Temporal

Currently, MoonMind generates execution IDs and manages state via local DB records. This architecture must be inverted so Temporal acts as the absolute authority:

* **Temporal Client Layer:** Introduce a client layer to talk directly to the Temporal Server for starts, lists, signals, and cancellations. It must retrieve real Temporal `workflowId` and `runId` values rather than generating local IDs first.
* **Temporal-Authoritative Execution Service:** Refactor the `TemporalExecutionService` to act as an adapter over Temporal. Operations like list, describe, update, and cancel must query or mutate real Temporal workflows.
* **Projection Sync:** Convert local execution tables into read-only projections and compatibility caches. These local records must be downstream mirrors constructed idempotently from Temporal state, memo fields, and search attributes.

## 3. Handling Side Effects, Actions, and Integrations

The system must safely handle large payloads, operator actions, and external integrations without breaking workflow durability:

* **Move Actions to Updates/Signals:** Mission Control UI actions (Pause, Resume, Approve, Rerun, Cancel, etc.) must be mapped to real Temporal update and signal handlers with workflow-side validation.
* **Artifact Wiring:** Activity handlers must be invoked by real workflows. To keep workflow histories compact, large outputs (like instructions, test outputs, or plan outputs) must be stored in the artifact backend, passing only reference links into the workflow history.
* **Integration Polling & Callbacks:** Integration monitoring must be moved into the workflow layer using durable waiting states, activities, and timers, rather than relying on local record mutations.

## 4. Mission Control (UI) and Production Routing

The frontend is scaffolded but needs to be wired to authoritative backend data:

* **List and Detail Authority:** APIs must provide fully authoritative Temporal data (status, state, waiting reasons, artifact refs) to the Mission Control dashboard without relying on local DB approximations.
* **Enable UI Actions & Submission:** Once the backend is stable, the `TEMPORAL_DASHBOARD_ACTIONS_ENABLED` and `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` feature flags must be turned on to allow users to create and act on Temporal-backed tasks natively.
* **Production Routing Policy:** Define deterministic, maintainable backend routing policies to resolve whether a task runs on the legacy queue, orchestrator, or Temporal.

## 5. Testing and Developer Experience

To ensure stability and usability for engineering teams:

* **Local Bring-up Path:** Document a standardized, reproducible local development path with properly configured Docker Compose defaults.
* **End-to-End Acceptance Test:** Write a comprehensive E2E test that proves the real user path—creating a task, letting an activity execute, validating artifact linkage, fetching details, and performing an operator action.

**Release Gate:** The switchover will only be considered complete when real workers poll queues by default, a task can be fully created and operated on via Mission Control using Temporal, artifacts link properly, and the E2E acceptance test passes.