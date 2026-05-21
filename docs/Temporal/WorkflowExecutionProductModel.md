# Workflow Execution Product Model

**Status:** Normative
**Owner:** MoonMind Platform
**Last updated:** 2026-05-21
**Audience:** operators, integrators, backend, dashboard, workflow authors
**Source design:** `docs/Temporal/WorkflowLanguageHardSwitchPlan.md`

MoonMind work is represented as Temporal-backed Workflow Executions. MoonMind does not define a separate product entity named Task. The word Task is reserved for Temporal internals and explicitly qualified external systems. A logical Step may have one or more Step Executions; retries are low-level operations inside a Step Execution.

In informal UI copy, **Workflow** may be used as shorthand for **Workflow Execution** when the context is clear. In APIs, schemas, docs, and operator contracts, use the exact term **Workflow Execution**.

## Canonical Model

| Term | Meaning |
| --- | --- |
| **Workflow Execution** | The top-level MoonMind product/runtime entity. It is a durable Temporal-backed execution identified by `workflowId`. |
| `workflowId` | The stable MoonMind product identity and route key. It is preserved across Continue-As-New and is the primary handle for links, lookups, and operator workflows. |
| `runId` | The current/latest Temporal run instance for the Workflow Execution. It is useful for debugging, artifacts, and history correlation, but it is not the product route key. |
| `workflowType` | The root orchestration category, such as `MoonMind.UserWorkflow`, `MoonMind.AgentRun`, or `MoonMind.ManifestIngest`. |
| `entry` | A short, URL-safe workflow type slug used in payloads and routing. For example, `user_workflow` identifies `MoonMind.UserWorkflow`. |
| **Step** | A user-visible unit of work inside a Workflow Execution. A Step is not a Temporal Activity and not a Temporal Task. |
| **Step Execution** | One semantic execution of a logical Step, scoped to the Workflow Execution, latest run, logical step, and execution ordinal. |
| **artifacts** | Durable evidence, inputs, outputs, diagnostics, reports, logs, checkpoints, and other large content stored outside workflow history and referenced by compact artifact refs. |
| `externalRefs` | Qualified links to external systems such as Jira, GitHub, Codex, or another provider. External IDs are references, not MoonMind identity. |

## Identity Rules

- Use `workflowId` as the stable product identity and route key.
- Use `runId` for the current/latest Temporal run instance.
- Do not route primary product pages by `runId`.
- Do not make Jira, GitHub, Codex, or provider IDs part of MoonMind identity.
- Store external provider identifiers in `externalRefs`.

Example:

```json
{
  "workflowId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
  "runId": "temporal-run-id",
  "workflowType": "MoonMind.UserWorkflow",
  "entry": "user_workflow",
  "externalRefs": [
    {
      "system": "jira",
      "type": "issue",
      "id": "MM-725"
    },
    {
      "system": "codex",
      "type": "provider_task",
      "id": "codex-provider-task-id"
    }
  ]
}
```

## Step Model

A Workflow Execution is composed from Steps. Each Step represents user-visible work in the plan or ledger. A Step may execute once, be skipped, fail, or be re-executed after recovery.

A Step Execution is the semantic execution of a Step. Retries of Activities, provider calls, or other idempotent operations inside the same semantic execution do not create a new Step Execution. Re-executing a Step after recovery creates a new Step Execution with a new ordinal and new evidence artifacts.

## Allowed Uses Of Task

Task terminology is valid only when it names a Temporal/internal concept or an explicitly qualified external system:

- Temporal Task
- Temporal Workflow Task
- Temporal Activity Task
- Temporal Task Queue
- Jira task
- Codex provider task

Do not use an unqualified Task label for MoonMind-owned product work.

## Workflow Type Direction

`MoonMind.UserWorkflow` is the user-submitted, Step-ledger-owning Workflow Execution type. Existing references to `MoonMind.Run` identify the current live workflow implementation or historic naming, not a separate product entity named Task.

`MoonMind.AgentRun` remains the durable lifecycle wrapper for one true managed or external agent execution. `MoonMind.ManifestIngest`, `MoonMind.AgentSession`, `MoonMind.ManagedSessionReconcile`, `MoonMind.ProviderProfileManager`, `MoonMind.OAuthSession`, and `MoonMind.MergeAutomation` keep their specialized workflow semantics.
