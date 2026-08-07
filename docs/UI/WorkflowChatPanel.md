# Workflow Chat Panel

**Document Class:** Canonical declarative  
**Status:** Accepted target  
**Owner:** MoonMind Dashboard / Platform  
**Last updated:** 2026-08-07  
**Audience:** dashboard, backend, Omnigent integration, workflow authors

**Authority:** This document owns the Workflow Detail **Chat** product surface. Omnigent owns the native session UI and interaction model. MoonMind owns workflow/session binding, workflow context, authorization, durable evidence, and linked continuation.

**Implementation tracking:** Rollout tasks belong under `docs/tmp/`, issues, or pull requests.

## 1. Purpose

The Workflow Detail Chat surface presents the **native Omnigent chat experience** for the Omnigent session bound to a MoonMind Workflow Execution.

MoonMind does not build a second chat application that imitates Omnigent. The Workflow Detail page provides a thin workflow shell around the native Omnigent UI and retains MoonMind-specific workflow and evidence advantages outside that UI.

Related documents:

- `docs/Omnigent/OmnigentBridge.md`
- `docs/Omnigent/NormalCodexProductPathReconciliation.md`
- `docs/UI/WorkflowDetailsPage.md`
- `docs/Workflows/ChatInstructionIntervention.md`
- `docs/Api/ChatInstructionsApiContract.md`
- `docs/Temporal/ChatInstructionTemporalContract.md`

The three Chat Instruction documents define a deferred, explicit workflow-steering extension. They do not define the ordinary Workflow Detail Chat send path.

## 2. Product decision

The primary Workflow Detail Chat experience is native Omnigent UI embedded inside MoonMind.

The native Omnigent application remains responsible for:

- transcript rendering,
- the composer,
- optimistic and queued messages,
- steering while a turn is active,
- attachments and workspace mentions,
- Markdown, code, image, and tool rendering,
- reasoning and process-trace presentation,
- approvals and elicitations,
- files, terminals, agents, tasks, and browser workspace surfaces,
- model, effort, goal, and session controls when supported,
- session history, liveness, wake, reconnect, and interruption behavior.

MoonMind remains responsible for:

- resolving the authoritative Workflow-to-Omnigent session binding,
- authorizing access to that binding,
- showing bounded workflow context around the native UI,
- capturing durable event, resource, artifact, terminal, cleanup, and lease evidence,
- exposing immutable captured evidence through MoonMind artifacts,
- offering a linked **Continue in a new workflow** action after terminal work.

The existing MoonMind event-to-chat projection is a compatibility and diagnostic surface. It is not the target primary chat product.

## 3. Route and layout

The primary route is:

```text
/workflows/{workflowId}/chat
```

The route renders:

```text
Workflow context bar
Native Omnigent chat application
```

The workflow context bar is intentionally small. It may show:

- workflow title,
- workflow status,
- current or source Step,
- runtime label,
- **Back to Overview**,
- **View captured evidence**,
- **Continue in a new workflow** when terminal continuation is available.

MoonMind must not add a second composer, transcript toolbar, approval tray, file rail, or session-control panel around the native UI.

In embedded mode, Omnigent may hide only application-global chrome that MoonMind already supplies, such as the global conversation sidebar. Session-specific chat header controls and the native workspace rail remain available.

On mobile, the native chat application fills the Workflow Detail content region and uses its own responsive behavior.

## 4. Native application integration

The preferred deployment exposes the stock Omnigent web application through the MoonMind origin, for example:

```text
/omnigent-ui/
```

The server-generated session URL may use a route such as:

```text
/omnigent-ui/c/{providerSessionRef}?embedded=1
```

`embedded=1` is a presentational mode only. It must not create a second session protocol, transcript model, composer implementation, or fork of the native chat application.

The first implementation should use a same-origin embedded application boundary, such as an iframe behind a same-origin reverse proxy. This keeps the native application independently deployable and avoids copying Omnigent React components into the MoonMind frontend. A shared-package or direct microfrontend integration may be considered later only if the embedded boundary proves insufficient.

The integration must provide:

- authenticated same-origin access,
- a server-generated and authorization-checked `chatUrl`,
- frame and content-security policy compatible with the configured deployment,
- bounded loading, unavailable, disconnected, and terminal states,
- an **Open in Omnigent** escape hatch when the embedded surface cannot be used.

The browser must not author or substitute `providerSessionRef`, host IDs, runner IDs, profile refs, or workspace paths.

## 5. Workflow chat binding

The Workflow Detail API should expose one authoritative binding for the session that the Chat route should open.

Representative projection:

```ts
type WorkflowChatBinding = {
  workflowId: string;
  runId: string;
  logicalStepId?: string;
  stepExecutionId?: string;
  bridgeSessionId: string;
  providerSessionRef: string;
  chatUrl: string;
  state: 'starting' | 'available' | 'ended' | 'unavailable';
  unavailableReason?: string;
};
```

Binding rules:

1. The backend resolves the binding from durable Workflow, Step Execution, AgentRun, and Omnigent Bridge state.
2. The active chat-capable session is preferred while work is running.
3. For terminal workflows, the last authoritative chat-capable session may be returned read-only.
4. The browser does not infer a session by scanning logs, events, or provider metadata.
5. A stale or unauthorized binding fails closed and does not fall back to an arbitrary provider session.
6. `chatUrl` is generated by the server and is the only browser navigation target required by the UI.

## 6. Message semantics

For an active native Omnigent session:

```text
User message -> native Omnigent composer -> native Omnigent session event API
```

Ordinary messages do not pass through Temporal and do not call:

```http
POST /api/executions/{workflowId}/chat-instructions
```

MoonMind does not classify ordinary session messages as workflow mutations. A message sent in native chat does not implicitly:

- cancel or reattempt a Step,
- revise the workflow plan,
- supersede future Steps,
- rerun or resume the Workflow,
- create a new Workflow Execution.

Workflow-level operations remain explicit MoonMind actions such as **Edit Workflow**, **Rerun**, **Resume**, **Remediate**, **Cancel**, and **Continue in a new workflow**.

If a future product adds workflow-level chat steering, it must be a separately labeled action governed by `docs/Workflows/ChatInstructionIntervention.md`. It must not replace or intercept the native composer.

## 7. Terminal behavior

When the bound Omnigent session is terminal:

- the native transcript remains available when authorized,
- the native composer is read-only or absent according to native session behavior,
- MoonMind shows terminal Workflow and session context,
- MoonMind may show **Continue in a new workflow**.

**Continue in a new workflow** creates a linked Workflow Execution. It pins the source workflow and run and carries authorized source evidence refs needed for continuation, such as the final session snapshot, finish summary, relevant artifacts, and source Step identity.

The source Workflow Execution and source Omnigent session remain immutable.

Terminal continuation is an explicit MoonMind workflow action. It is not a message sent through the native composer and is not automatically routed through `SubmitChatInstruction`.

## 8. Durable evidence

The embedded native UI presents live session state. MoonMind artifacts remain the durable workflow evidence boundary.

The Omnigent Bridge continues to capture and publish authorized evidence such as:

- raw and normalized event journals,
- changed files and diffs,
- session files,
- initial and final snapshots,
- diagnostics,
- terminal outcome,
- capture manifests,
- cleanup and lease-release evidence.

The workflow context bar may link to **View captured evidence**. MoonMind should not duplicate all evidence inside the native transcript.

The product distinction is:

```text
Native Omnigent UI = live interactive session state
MoonMind artifacts = immutable workflow evidence
```

## 9. Compatibility and diagnostics

The existing MoonMind `ChatSessionView`, raw timeline, bridge event projection, resource evidence panel, and administrative controls remain useful for:

- bridge diagnostics,
- support evidence,
- legacy managed-session compatibility,
- cases where the native UI cannot be reached,
- raw event and artifact inspection.

They should move under **Debug**, **Diagnostics**, or a clearly labeled compatibility fallback. They must not present a second ordinary composer once native chat is available.

When the native UI is unavailable, the Chat route should show:

1. the stable reason,
2. **Retry**,
3. **Open in Omnigent** when possible,
4. a read-only compatibility transcript when available.

It must not silently switch to a behaviorally different custom chat implementation.

For runtimes without a native Omnigent session, the route may show a read-only compatibility transcript or a clear `Chat unavailable for this runtime` state.

## 10. Explicit non-goals

The Workflow Detail frontend does not build or maintain its own versions of:

- the Omnigent composer,
- queued-message editing or reordering,
- rich message and Markdown rendering,
- tool, reasoning, or approval cards,
- file, terminal, agent, task, or browser rails,
- model, effort, plan, or goal controls,
- session reconnect and wake flows,
- reply, quote, history, or transcript navigation systems.

The primary Chat route also does not require implementation of:

- `SubmitChatInstruction`,
- chat-driven plan revision,
- chat-driven Step cancellation and reattempt,
- chat-driven future-Step supersession,
- heuristic routing between session chat and workflow steering.

## 11. Rollout

### Phase 1: native handoff

- Resolve and expose `WorkflowChatBinding`.
- Add **Open in Omnigent** for a valid binding.
- Validate authorization, deep linking, and session availability.

### Phase 2: native embed

- Add `/workflows/{workflowId}/chat`.
- Proxy the native Omnigent web application through the MoonMind origin.
- Add `embedded=1` presentation mode.
- Render the thin workflow context bar.

### Phase 3: demote duplicate UI

- Make native chat the primary Chat surface.
- Move MoonMind chat projection and session administration to Debug or Diagnostics.
- Remove the custom follow-up textarea from the primary path.

### Phase 4: retain MoonMind advantages

- Add **View captured evidence**.
- Add **Continue in a new workflow** for terminal work.
- Stop unless observed product usage justifies another workflow-specific addition.

Recommended rollout flags:

- `workflowNativeChatEnabled`
- `workflowNativeChatEmbedEnabled`

## 12. Acceptance criteria

The target is satisfied when:

1. Opening Workflow Chat lands in the native Omnigent conversation for the authoritative bound session.
2. The native transcript, composer, queue, approvals, workspace rail, and lifecycle controls behave as they do in Omnigent.
3. MoonMind adds only a bounded workflow context bar and does not duplicate native interaction components.
4. Ordinary messages travel through the native Omnigent session path, not Temporal chat instructions.
5. MoonMind continues to publish durable workflow evidence independently of the native UI.
6. Terminal work can be inspected read-only and continued through an explicit linked-workflow action.
7. The existing MoonMind projection remains available for diagnostics without competing with the native primary experience.
