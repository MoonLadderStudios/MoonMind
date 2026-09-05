# Workflow Chat Panel

**Document Class:** Canonical declarative  
**Status:** Accepted target  
**Owner:** MoonMind Dashboard / Platform  
**Last updated:** 2026-09-05  
**Audience:** dashboard, backend, Omnigent integration, workflow authors

**Authority:** This document owns the Workflow Detail **Chat** product surface. Omnigent owns the native session presentation and interaction model. MoonMind owns the workflow/session binding, request authorization, effective capabilities, security policy, durable evidence, and linked continuation.

**Implementation tracking:** Rollout tasks belong under `docs/tmp/`, issues, or pull requests.

## 1. Purpose

The Workflow Detail Chat surface presents the **native Omnigent chat experience** for the Omnigent session bound to a MoonMind Workflow Execution.

MoonMind does not build a second chat application that imitates Omnigent. The Workflow Detail page provides a thin workflow shell around the native Omnigent UI, while every provider-facing request continues through a MoonMind-authorized bridge boundary.

Related documents:

- `docs/Omnigent/OmnigentBridge.md`
- `docs/Omnigent/AgentProfiles.md`
- `docs/Omnigent/NormalCodexProductPathReconciliation.md`
- `docs/Security/SecretsSystem.md`
- `docs/UI/WorkflowDetailsPage.md`
- `docs/Workflows/WorkflowRunsApi.md`
- `docs/Workflows/ChatInstructionIntervention.md`
- `docs/Api/ChatInstructionsApiContract.md`
- `docs/Temporal/ChatInstructionTemporalContract.md`

The three Chat Instruction documents define a deferred, explicit workflow-steering extension. They do not define the ordinary Workflow Detail Chat send path.

## 2. Product and authority boundary

The primary Workflow Detail Chat experience uses the native Omnigent application for:

- transcript rendering,
- the composer,
- optimistic and queued messages,
- steering while a turn is active,
- attachments and workspace mentions,
- Markdown, code, image, reasoning, tool, approval, and status presentation,
- files, terminals, agents, tasks, and browser workspace surfaces,
- session history, liveness, wake, reconnect, and interruption affordances.

MoonMind remains authoritative for:

- resolving the Workflow-to-Omnigent session binding,
- authenticating and authorizing every UI, HTTP, SSE, and WebSocket request,
- deriving the effective control and resource capabilities,
- enforcing immutable Agent Profile, Provider Profile, launch-policy, workflow-state, and caller-permission constraints,
- applying high-security outbound scans before provider sends,
- recording actor, idempotency, expected-state, outcome, and audit evidence for mutations,
- capturing durable event, resource, artifact, terminal, cleanup, and lease evidence,
- offering a linked **Continue in a new workflow** action after terminal work.

The native application is therefore the presentation client, not a second control plane. Hiding or disabling a native control is an affordance only; the MoonMind bridge must reject any request that exceeds effective authority.

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

In embedded mode, Omnigent may hide application-global chrome that MoonMind already supplies, such as the global conversation sidebar. Session-specific controls and the native workspace rail remain visible only when the effective capability projection permits them.

On mobile, the native chat application fills the Workflow Detail content region and uses its own responsive behavior.

## 4. Native application integration

The preferred deployment exposes the native Omnigent web application through the MoonMind origin, for example:

```text
/omnigent-ui/workflow-chat/{chatBindingId}?embedded=1
```

`chatBindingId` is an opaque MoonMind binding identifier. The browser does not author an upstream endpoint, provider session id, host id, runner id, profile ref, workspace path, or provider credential.

`embedded=1` is a presentational mode only. It must not create a second session protocol, transcript model, composer implementation, or fork of Omnigent behavior.

A same-origin embedded application boundary may use an iframe or equivalent microfrontend isolation. A full-page **Open in Omnigent** action, when offered, must open the same MoonMind-scoped surface rather than bypassing MoonMind authorization by navigating directly to the upstream server.

The integration must provide:

- authenticated same-origin access,
- a server-generated `chatUrl` based on an authorized opaque binding,
- frame and content-security policy compatible with the deployment,
- a binding-scoped API base used by the native application,
- bounded loading, unavailable, disconnected, and terminal states,
- an authorized full-page escape hatch when embedding is unavailable.

### 4.1 Application readiness and failure containment

Binding resolution, document loading, native application readiness, and session liveness are distinct observations. An `available` binding or a successful HTML response authorizes an attempt to open chat. Neither proves that the native application rendered successfully. An iframe `load` event, a nonempty root element, and an open transport are also insufficient readiness evidence.

The native application is ready when the conversation for the current authorized binding has rendered its initial transcript or a validated empty-transcript state. Read-only presentation may be ready without mutation authority. A failed or denied transcript read must not be represented as an empty successful conversation. Live transport availability is reported separately, so a readable conversation with a disconnected stream is not advertised as fully live.

The embedding boundary provides a bounded application-ready or fatal-failure signal to the workflow shell. Prefer a supported upstream lifecycle hook and keep host-specific signaling in the thin integration boundary, not a second renderer. When using `postMessage`, the receiver validates the message schema, exact origin, iframe window, current binding, and current mount generation. A signal from a replaced frame or earlier retry cannot mark the current application ready or failed. The sender uses the exact target origin. Signals contain only bounded presentation state and safe reason codes, never provider identity, transcript content, credentials, or new permission grants.

Startup has a finite deadline. Missing required assets, failed essential reads, startup exceptions, or an application that never becomes ready produce the visible unavailable behavior in section 11. A fatal application failure after readiness produces the same visible recovery surface rather than leaving a blank frame. Ordinary transient stream loss instead uses the disconnected state and the native reconnect behavior. Presentation timers, listeners, and frame-specific state are disposed on unmount, binding replacement, or retry.

These are local presentation states, not new persisted Workflow or provider-session lifecycle states. Loading, retrying, or opening the full-page presentation must not create a session, replay a message, change credentials, or restart the workflow. The full-page action is a same-authority presentation alternative, not a workaround for shared bootstrap, capability, or transport defects.

## 5. Workflow chat binding

The Workflow Detail API exposes one authoritative binding for the session that the Chat route opens.

Representative browser-safe projection:

```ts
type WorkflowChatBinding = {
  chatBindingId: string;
  workflowId: string;
  runId: string;
  logicalStepId?: string;
  stepExecutionId?: string;
  chatUrl: string;
  apiBase: string;
  state: 'starting' | 'available' | 'ended' | 'unavailable';
  readOnly: boolean;
  capabilities: {
    viewTranscript: boolean;
    sendMessage: boolean;
    interruptTurn: boolean;
    resolveElicitation: boolean;
    readResources: boolean;
    createTerminal: boolean;
    writeTerminal: boolean;
    mutateWorkspace: boolean;
    changeModel: boolean;
    changeEffort: boolean;
    changeGoal: boolean;
  };
  unavailableReason?: string;
};
```

Provider session, bridge session, host, runner, endpoint, credential, and immutable profile-snapshot identifiers remain server-side unless a separately authorized diagnostic surface exposes bounded safe refs.

Binding rules:

1. The backend resolves the binding from durable Workflow, Step Execution, AgentRun, and Omnigent Bridge state.
2. The active chat-capable session is preferred while work is running.
3. For terminal workflows, the last authoritative chat-capable session may be returned read-only.
4. The browser does not infer a session by scanning logs, events, URLs, or provider metadata.
5. A stale, missing, or unauthorized binding fails closed and never falls back to an arbitrary provider session.
6. The browser uses only the server-generated `chatUrl` and binding-scoped API base.
7. Possession of a binding id or URL is not authorization; every subsequent request is independently checked.

## 6. Per-request proxy authorization

Every native application request that crosses the MoonMind origin—HTML/bootstrap, session snapshot, history, message, stream, resource, terminal, approval, control, reconnect, and WebSocket traffic—must pass through the binding-scoped MoonMind bridge boundary.

For every request and reconnect, the bridge must:

1. authenticate the MoonMind caller,
2. resolve `chatBindingId` from durable state,
3. authorize the caller against the bound Workflow Execution and requested operation,
4. verify that any route, payload, or query session reference maps to the one bound provider session,
5. reject caller-supplied upstream endpoints, alternate session ids, host ids, runner ids, workspace roots, and provider identities,
6. recompute effective capabilities from the immutable Agent Profile snapshot, Provider Profile and launch policy, workflow/session state, and caller role,
7. validate expected workflow, run, Step Execution, bridge session, provider session, session epoch, and active turn where the operation can race,
8. apply required security scans and policy checks before forwarding,
9. record durable mutation audit evidence,
10. forward only to the server-resolved upstream target.

The proxy strips MoonMind cookies, bearer tokens, CSRF tokens, internal authorization headers, and other MoonMind credentials before forwarding upstream. It injects only server-side Omnigent credentials and an allowlisted set of transport headers. No deployment option may turn browser-supplied upstream authorization into provider authority.

SSE and WebSocket upgrades receive the same authorization and binding validation before connection, and reconnects repeat the check rather than relying on the authorization that opened an earlier stream.

## 7. Native control policy

The native UI receives a filtered capability projection so it can retain the Omnigent interaction model without displaying controls that MoonMind policy forbids.

The effective capability set is the intersection of:

```text
upstream session capabilities
∩ immutable Omnigent Agent Profile snapshot
∩ Provider Profile and effective launch policy
∩ Workflow and Step state
∩ caller permission
```

In particular:

- pinned model and effort values cannot be changed from native controls,
- approval and elicitation decisions require the MoonMind approval capability for the caller and request,
- terminal creation/input, workspace mutation, browser actions, clear/reset, stop, cancel, cleanup, and resource access remain separately capability-gated,
- upstream support for an operation does not grant MoonMind authority to use it,
- unsupported or denied controls are hidden or disabled in the native client and rejected server-side if invoked directly.

Every mutating session or control request carries or receives a MoonMind idempotency key and records the actor, operation, expected identities/state, normalized outcome, upstream correlation, timestamp, and durable audit reference. Approval and control events are not authoritative unless this evidence is retained.

## 8. Message semantics and outbound security

For an active native Omnigent session:

```text
User message
  -> native Omnigent composer
  -> binding-scoped MoonMind bridge
  -> authorized native Omnigent session event
```

Ordinary messages do not pass through Temporal and do not call:

```http
POST /api/executions/{workflowId}/chat-instructions
```

When high-security mode is enabled, the bridge must run the canonical MoonMind outbound-text scan over every text-bearing native message or command payload before forwarding it. This includes message text, supported slash-command arguments, approval response text, and textual attachment content that MoonMind forwards to the provider.

A blocked scan prevents the provider request and returns only redacted finding category and location data. If high-security mode is enabled and MoonMind cannot parse the native event safely, cannot inspect a required textual payload, or cannot run the configured scanner, the send fails closed with a stable error; it must not bypass the scan or forward first and diagnose later.

When high-security mode is disabled, the scan contract allows the unchanged caller payload. The proxy must never silently rewrite user content.

MoonMind does not classify ordinary session messages as workflow mutations. A native message does not implicitly:

- cancel or reattempt a Step,
- revise the workflow plan,
- supersede future Steps,
- rerun or resume the Workflow,
- create a new Workflow Execution.

Workflow-level operations remain explicit MoonMind actions such as **Edit Workflow**, **Rerun**, **Resume**, **Remediate**, **Cancel**, and **Continue in a new workflow**.

A future workflow-level chat-steering product must use a separately labeled action governed by `docs/Workflows/ChatInstructionIntervention.md`. It must not replace or intercept the native composer.

## 9. Terminal behavior

When the bound Omnigent session is terminal:

- the native transcript remains available when authorized,
- the native composer is read-only or absent,
- mutating native controls fail closed,
- MoonMind shows terminal Workflow and session context,
- MoonMind may show **Continue in a new workflow**.

**Continue in a new workflow** creates a linked Workflow Execution. It pins the source workflow and run and carries authorized source evidence refs needed for continuation, such as the final session snapshot, finish summary, relevant artifacts, and source Step identity.

The source Workflow Execution and source Omnigent session remain immutable.

Terminal continuation is an explicit MoonMind workflow action. It is not a message sent through the native composer and is not automatically routed through `SubmitChatInstruction`.

## 10. Durable evidence

The embedded native UI presents live session state. MoonMind artifacts remain the durable workflow evidence boundary.

The Omnigent Bridge continues to capture and publish authorized evidence such as:

- raw and normalized event journals,
- changed files and diffs,
- session files,
- initial and final snapshots,
- diagnostics,
- terminal outcome,
- capture manifests,
- mutation and approval audit refs,
- cleanup and lease-release evidence.

The workflow context bar may link to **View captured evidence**. MoonMind should not duplicate all evidence inside the native transcript.

The product distinction is:

```text
Native Omnigent UI = live interactive session presentation
MoonMind bridge = request and policy authority
MoonMind artifacts = immutable workflow evidence
```

## 11. Compatibility and diagnostics

The existing MoonMind `ChatSessionView`, raw timeline, bridge event projection, resource evidence panel, and administrative diagnostics remain useful for:

- bridge diagnostics,
- support evidence,
- legacy managed-session compatibility,
- cases where the native UI cannot be reached,
- raw event and artifact inspection.

They belong under **Debug**, **Diagnostics**, or a clearly labeled read-only compatibility fallback. They must not present a second ordinary composer once native chat is available.

When the native UI is unavailable, the Chat route shows the stable reason, a retry action, the authorized full-page native surface when available, and a read-only compatibility transcript when available. It must not silently switch to a behaviorally different custom chat implementation.

For runtimes without a native Omnigent session, the route may show a read-only compatibility transcript or a clear `Chat unavailable for this runtime` state.

### 11.1 Essential failures, optional features, and recovery

The unavailable surface applies both before and after the native document loads. It identifies the failed stage with a stable, redacted reason, offers retry only when meaningful, and preserves authorized Workflow-level evidence and terminal continuation actions outside the failed application. It must not require the user to open the browser console to discover that chat failed.

Required application assets and authorized initial transcript reads are essential to conversation readiness. A denied essential read produces an unavailable or access-denied state, not a success-shaped empty response. A live stream that cannot connect produces a disconnected or unavailable-live state while already-authorized historical content may remain readable. A denied optional terminal, browser, subagent, or resource operation disables that feature with an explanation and must not crash an otherwise readable conversation. Essential versus optional behavior follows the reviewed UI contract and effective capabilities, not a blanket rule that every 403 or 404 is harmless.

Diagnostics distinguish binding access, missing or stale immutable authority, capability denial, unsupported routes, asset incompatibility, and application failure only to the extent that verified, safely disclosed evidence supports that distinction. Unknown or unauthorized bindings retain non-enumerating responses. An HTTP status alone is not a root-cause diagnosis. Missing authority remains denied until repaired at its authoritative producer. Neither browser flags nor a successful document load may grant transcript or mutation access.

Retries are bounded and reuse the authorized binding. They recheck current access and do not replay provider mutations. Automatic retries stop for persistent denial or incompatibility instead of creating an endless reload loop. A read-only fallback still enforces its own authorization and cannot recover revoked content from another binding or stale user cache. Failure details, readiness signals, and support artifacts exclude credentials, raw provider identities, private message bodies, and unrestricted upstream URLs.

## 12. Explicit non-goals

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

## 13. Acceptance criteria

The target is satisfied when:

1. Opening Workflow Chat lands in the native Omnigent conversation for the authoritative bound session.
2. The native transcript, composer, queue, approvals, workspace rail, and lifecycle affordances retain Omnigent behavior within the effective MoonMind capability set.
3. MoonMind adds only bounded workflow context and does not duplicate native interaction components.
4. Every UI, HTTP, SSE, WebSocket, resource, and control request is authorized against the durable binding; substituting another session or upstream target fails closed.
5. MoonMind credentials never reach the upstream Omnigent server, and upstream credentials never reach the browser.
6. Native controls cannot override immutable profile, billing, approval, workflow-state, or caller-permission policy.
7. High-security mode scans outbound native message payloads before send and fails closed when enforcement is unavailable.
8. Ordinary messages use the native session path rather than Temporal chat instructions.
9. MoonMind continues to publish durable workflow evidence independently of the native UI.
10. Terminal work can be inspected read-only and continued through an explicit linked-workflow action.
11. The existing MoonMind projection remains available for diagnostics without competing with the native primary experience.
12. Embedded and full-page readiness requires the current bound transcript or a validated empty state to render, independently of document loading and live transport availability.
13. Startup timeout, required-asset failure, essential-read denial, and a fatal native crash after loading produce a visible bounded recovery surface rather than an indefinite blank panel.
14. Optional denied features do not crash authorized transcript rendering, and retries never widen authority, recreate a provider session, or duplicate a send.
15. Stale or forged frame signals cannot change the current mount's presentation state, and listeners and timers are cleaned up on replacement or unmount.
16. Executable browser regressions cover the served adapter and compiled native application in both presentations. Passing source-string assertions, a version pin, or an iframe load alone is not rendering or deployed-support evidence.
