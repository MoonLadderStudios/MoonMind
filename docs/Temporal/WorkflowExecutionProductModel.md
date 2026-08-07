# Workflow Execution Product Model

**Status:** Normative  
**Owner:** MoonMind Platform  
**Last updated:** 2026-08-07  
**Audience:** operators, integrators, backend, dashboard, workflow authors  
**Source design:** `docs/Temporal/WorkflowLanguageHardSwitchPlan.md`

MoonMind work is represented as Temporal-backed Workflow Executions. MoonMind does not define a separate product entity named Task. The word Task is reserved for Temporal internals and explicitly qualified external systems. A logical Step may have one or more Step Executions; retries are low-level operations inside a Step Execution.

In informal UI copy, **Workflow** may be used as shorthand for **Workflow Execution** when context is clear. In APIs, schemas, docs, and operator contracts, use the exact term **Workflow Execution**.

Native chat is an interactive session surface bound to a Workflow Execution. It is not a second execution entity and does not become a Temporal workflow mutation merely because a user sends a message.

## Canonical model

| Term | Meaning |
|---|---|
| **Workflow Execution** | The top-level MoonMind product/runtime entity. It is a durable Temporal-backed execution identified by `workflowId`. |
| `workflowId` | The stable MoonMind product identity and route key. It is preserved across Continue-As-New and is the primary handle for links, lookups, and operator workflows. |
| `runId` | The current/latest Temporal run instance for the Workflow Execution. It is useful for debugging, artifacts, and history correlation, but is not the product route key. |
| `workflowType` | The root orchestration category, such as `MoonMind.UserWorkflow`, `MoonMind.AgentRun`, or `MoonMind.ManifestIngest`. |
| `entry` | A short, URL-safe workflow type slug used in payloads and routing. For example, `user_workflow` identifies `MoonMind.UserWorkflow`. |
| **Step** | A user-visible unit of work inside a Workflow Execution. A Step is not a Temporal Activity and not a Temporal Task. |
| **Step Execution** | One semantic execution of a logical Step, scoped to Workflow Execution, run, logical Step, and execution ordinal. |
| **native chat session** | The provider-maintained interactive session bound to an AgentRun or Step Execution and presented through the native Omnigent UI. |
| `chatBindingId` | An opaque MoonMind authorization handle that lets the browser open one native session through a workflow-bound bridge without authoring provider identity. It is not Workflow identity. |
| **workflow instruction** | A separately invoked, typed, artifact-backed command intended to change Temporal-managed workflow behavior. The deferred `SubmitChatInstruction` extension owns this term; ordinary native messages are not workflow instructions. |
| **linked continuation** | A new Workflow Execution created from authorized terminal source identity and evidence. It leaves the source execution and source provider session unchanged. |
| **artifacts** | Durable evidence, inputs, outputs, diagnostics, reports, logs, checkpoints, session journals, instruction records, and other large content stored outside workflow history and referenced by compact refs. |
| `externalRefs` | Qualified links to external systems such as Jira, GitHub, Codex, Omnigent, or another provider. External IDs are references, not MoonMind identity. |

## Identity rules

- Use `workflowId` as the stable product identity and route key.
- Use `runId` for the current/latest Temporal run instance.
- Do not route primary product pages by `runId`.
- Do not make Jira, GitHub, Codex, Omnigent, provider-session, chat-message, or chat-binding IDs part of MoonMind execution identity.
- Store provider identifiers in durable server-side bindings or qualified `externalRefs`.
- Expose only an opaque, caller-authorized `chatBindingId` to the ordinary native Chat browser surface.
- Possession of a `chatBindingId` is not authorization; every request through it is independently checked against the Workflow Execution and requested capability.

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
      "system": "omnigent",
      "type": "provider_session",
      "id": "server-side-safe-ref"
    }
  ]
}
```

## Step model

A Workflow Execution is composed from Steps. Each Step represents user-visible work in the plan or ledger. A Step may execute once, be skipped, fail, be superseded by an explicit plan revision, or be re-executed after recovery or another workflow-authorized reattempt.

A Step Execution is the semantic execution of a Step. Retries of Activities, provider calls, or other idempotent operations inside the same semantic execution do not create a new Step Execution. Re-executing a Step after recovery or another accepted workflow-level operation creates a new Step Execution with a new ordinal and new evidence artifacts.

An ordinary message sent to the native session does not create, replace, cancel, supersede, or reattempt a Step. The native session may change live workspace state within the authority already granted to that Step Execution; the Step ledger remains the authoritative workflow projection.

## Native Chat model

The target Workflow Detail Chat path is:

```text
Workflow Detail Chat
  -> native Omnigent application
  -> binding-scoped MoonMind bridge
  -> bound provider session
```

Rules:

- The native Omnigent application owns transcript, composer, queue, tool, approval, file, terminal, agent, task, and session-lifecycle presentation.
- MoonMind resolves one durable Workflow-to-session binding and exposes only a browser-safe binding projection.
- Every native UI, HTTP, SSE, WebSocket, resource, message, approval, terminal, and control request is authorized and capability-checked through MoonMind.
- Effective native capabilities are the intersection of upstream support, immutable Agent Profile snapshot, Provider Profile and launch policy, workflow/session state, and caller permission.
- MoonMind high-security policy scans text-bearing outbound messages before provider send and fails closed when enforcement is unavailable.
- The native transcript is authoritative for interactive session presentation; MoonMind artifacts remain authoritative durable Workflow evidence.
- Provider session and bridge identities are not product route keys.

Ordinary native messages do not:

- invoke `SubmitChatInstruction`,
- revise the Workflow plan,
- supersede future Steps,
- create a Step Execution reattempt,
- rerun or resume the Workflow,
- create linked continuation work.

## Explicit workflow-instruction model

`docs/Workflows/ChatInstructionIntervention.md` reserves a future, separately labeled **Steer Workflow** capability.

If that extension is promoted:

- the user must invoke it separately from the native composer,
- the command is artifact-backed,
- `MoonMind.UserWorkflow` validates stale run, Step, plan, and policy state through `SubmitChatInstruction`,
- acceptance by Temporal remains distinct from provider-session delivery,
- plan changes create new immutable artifacts and preserve superseded Step evidence,
- closed Workflow Executions remain immutable.

Until promotion, no ordinary Workflow Chat path depends on that extension.

## Terminal continuation

A terminal Workflow Execution and its provider session are immutable.

When authorized, **Continue in a new workflow** creates a linked Workflow Execution using pinned source `workflowId`, source `runId`, source Step identity, and authorized source artifact refs such as final session snapshot and finish summary.

Linked continuation is not:

- a native chat message,
- failed-Step recovery,
- an in-place update of the terminal source,
- evidence that `SubmitChatInstruction` is enabled.

## Artifact posture

Large or sensitive content remains outside Temporal history and compact API projections.

Examples include:

- raw and normalized session event journals,
- transcript bodies and provider payloads,
- changed files and diffs,
- snapshots and capture manifests,
- diagnostics and audit evidence,
- optional explicit workflow-instruction text.

Workflow history, Search Attributes, Memo, Step rows, and binding projections carry only bounded safe metadata and refs.

## Allowed uses of Task

Task terminology is valid only when it names a Temporal/internal concept or an explicitly qualified external system:

- Temporal Task
- Temporal Workflow Task
- Temporal Activity Task
- Temporal Task Queue
- Jira task
- Codex provider task

Do not use an unqualified Task label for MoonMind-owned product work.

## Workflow type direction

`MoonMind.UserWorkflow` is the user-submitted, Step-ledger-owning Workflow Execution type. Existing references to historic naming identify the live implementation or durable history boundary, not a separate product entity named Task.

`MoonMind.AgentRun` remains the durable lifecycle wrapper for one true managed or external agent execution. `MoonMind.ManifestIngest`, `MoonMind.AgentSession`, `MoonMind.ManagedSessionReconcile`, `MoonMind.ProviderProfileManager`, `MoonMind.OAuthSession`, and `MoonMind.MergeAutomation` retain specialized workflow semantics.

A future `MoonMind.ChatThread` workflow should be added only if chat threads become durable orchestration objects that own multi-execution lifecycle, not merely because the UI embeds a native session.
