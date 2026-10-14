# Omnigent Bridge

Document Class: System / Feature Design View  
Owners: MoonMind Platform

## Related docs

- [`docs/Omnigent/README.md`](./README.md) — module entrypoint and contract owners
- [`docs/Omnigent/ContractOwnership.md`](./ContractOwnership.md) — per-file ownership map
- [`docs/UI/WorkflowChatPanel.md`](../UI/WorkflowChatPanel.md)
- [`docs/Omnigent/AgentProfiles.md`](./AgentProfiles.md)
- [`docs/Omnigent/CodexCreateToHostContract.md`](./CodexCreateToHostContract.md)
- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](../ManagedAgents/CodexCliManagedSessions.md)
- [`docs/Observability/LiveLogs.md`](../Observability/LiveLogs.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../Temporal/WorkflowArtifactSystemDesign.md)
- [`docs/Temporal/VisibilityAndUiQueryModel.md`](../Temporal/VisibilityAndUiQueryModel.md)
- [`docs/ExternalAgents/AddingExternalProvider.md`](../ExternalAgents/AddingExternalProvider.md)

---

## 1. Purpose and authority

The **MoonMind Omnigent Bridge** is a MoonMind API capability that exposes Omnigent-shaped session, event, stream, resource, and control communication while MoonMind stays the durable orchestration, authorization, policy, and artifact authority. It works with an unchanged Omnigent host.

```text
MoonMind Workflow / UI -> Omnigent Bridge -> stock Omnigent Server -> unchanged host / runner -> harness
```

MoonMind owns Temporal orchestration, Workflow/Step Execution/AgentRun identity, the durable Workflow-to-provider session binding, authorization of every request, capability and approval policy, outbound security scans, artifact refs, and audit evidence. The native Omnigent web application owns the Workflow Detail Chat interaction model (transcript, composer, queue, tool views, approvals, files, terminals, agents, tasks); it is a presentation client behind the bridge, not a control plane or credential boundary. The host owns live execution, harness launch, runtime events, and resource reporting.

MoonMind treats the bundled Omnigent UI as trusted first-party code, so same-origin presentation is intentional; server-side binding authorization and untrusted-artifact protections still apply, and security changes are weighed equally with resilience and simplicity.

---

## 2. Design principles

- **Omnigent names at the boundary.** The bridge uses Omnigent nouns and operations (`session`, `event`, `stream`, `host`, `runner`, `resource`, `snapshot`, `interrupt`, `stop_session`) rather than a parallel MoonMind protocol.
- **MoonMind artifact authority.** Provider-native ids, URLs, host paths, and file ids may appear in redacted diagnostics or server-side mappings, never in place of MoonMind artifact refs in workflow evidence or `AgentRunResult.outputRefs`.
- **Unchanged host.** A stock Omnigent host works with no custom image or patch; deployment configuration supplies URLs, host auth, endpoint refs, and routing.
- **Native UI, central authority.** MoonMind serves the native application instead of reproducing it, but every native request crosses a binding-scoped boundary. Client-side hiding is only an affordance; the bridge independently authenticates, authorizes, capability-checks, scans, audits, rewrites, and forwards each request.

## 3. Topologies

**Proxy mode** is the only selectable `hostProtocolMode` (`upstream_omnigent_server_proxy`), because stock Omnigent Server already owns the host/runner tunnel. The bridge persists bindings, idempotency state, and event and artifact refs, and calls stock session/event/resource/control APIs; the host speaks its native protocol, which the bridge treats as a versioned compatibility profile.

**Embedded mode** (`embedded_omnigent_compatible_server`) is retired and admits no new work. Selecting it fails fast naming the proxy alternative, and embedded host routes answer `410 Gone` with `omnigent_embedded_transport_retired` (WebSocket denials use the same code as the close reason). The value remains decodable so retained sessions keep their recorded mode: session-scoped controls dispatch by each row's recorded mode, so retained embedded sessions keep their cleanup owner. See [`EmbeddedHostAuthCompatibility.md`](EmbeddedHostAuthCompatibility.md). The Workflow Chat `embedded=1` presentation option is unrelated.

**Direct Codex compatibility** routes the bridge event model through the direct Codex managed-session adapter. It is a compatibility and historical-read path that emits bridge-shaped diagnostic evidence, not a second primary Chat renderer; see [`CodexSupportAndCutover.md`](./CodexSupportAndCutover.md).

---

## 4. Protocol surfaces

### 4.1 Provider/service session API facade

MoonMind services and compatibility clients use Omnigent-shaped routes. Every session-scoped operation resolves the durable binding and authorizes the operation before touching upstream state.

#### `omnigent.server.v1` compatibility matrix

| Operation | Facade route | Policy |
|---|---|---|
| Agents / hosts | `GET /api/agents`, `GET /api/hosts` | Bounded catalog and readiness; no credential or caller-selected host authority |
| Create/reuse | `POST /v1/sessions` | Workflow-owned idempotency, profile/policy resolution, first-message reconciliation |
| Snapshot / items | `GET /v1/sessions/{id}[/items]` | Bound sessions only; strict cursor pagination; identity virtualization |
| Attach | `POST /v1/sessions/{id}/attach` | Existing owned binding; conflicts fail closed |
| Message/interrupt/stop | `POST /v1/sessions/{id}/events` | Capability, expected state, audit, outbound scan |
| Delete | `DELETE /v1/sessions/{id}` | Terminal cleanup capability and lease ownership |
| Stream | `GET /v1/sessions/{id}/stream` | Authorization on every connect and reconnect |
| Resolve elicitation | `POST /v1/sessions/{id}/elicitations/{eid}/resolve` | Approval authority, expected state, idempotency, audit |
| Files and diffs | `GET .../resources/environments/default/{changes,filesystem[/{path}],diff/{path}}`, `.../resources/files*` | Bounded, path-safe, traversal rejection, read capability |

These are transport results; raw provider paths and ids never become authoritative workflow evidence.

### 4.2 Workflow-bound native Chat facade

The browser never names a provider session. It receives an opaque `chatBindingId` and uses `GET /omnigent-ui/workflow-chat/{chatBindingId}` and `/api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}`. For every document, HTTP, SSE, WebSocket, resource, message, approval, terminal, and control request, including reconnects, the facade authenticates the caller, loads the binding, authorizes the operation against the Workflow Execution, verifies that any session reference maps to the bound session, rejects caller-supplied endpoint/session/host/runner/workspace/profile/credential identity, recomputes capabilities (§11), validates expected state, scans and audits mutations, strips MoonMind credentials, and forwards only to the server-resolved target. The full-page **Open in Omnigent** view uses the same facade, so it cannot bypass MoonMind authority.

The native HTTP surface admits only fields reviewed against the pinned server and runner entrypoints. Unknown fields, duplicate keys, and unreviewed media types are rejected before any mutation or upstream I/O; uploads contain exactly one `file` part; terminal creation cannot inject bootstrap flags, runtime specs, workspace roots, or sandbox overrides. A route without a verified upstream counterpart is not served.

#### Versioned native-UI compatibility map

Route and transport coverage is one versioned map pinned to `omnigent.server.v1` (`moonmind/omnigent/native_ui_compat.py`). A route is either **served** (single-sourced from the facade allowlist, including the reviewed global session-update, terminal-attach, and dictation WebSockets) or **compatibility_review_required** (authenticated and binding-validated, then failed closed with a compatibility diagnostic). An earlier HTTP bootstrap never authorizes a later WebSocket, and a terminal or revoked binding cannot open a new live transport. Terminal input/resize/close and browser-pane control require capabilities the facade does not grant; terminal viewing uses a separate read capability. Read-only terminal attachments are enforced in the MoonMind relay, which closes the attachment on any browser data frame; the upstream `read_only=true` flag is defense in depth.

#### Browser transport API preservation

An adapter that rebases native browser requests preserves the browser API the pinned application uses, not just the URL. An adapted `WebSocket` keeps constructor arguments, subprotocols, instance/prototype behavior, events, and the static constants `CONNECTING = 0`, `OPEN = 1`, `CLOSING = 2`, `CLOSED = 3`; an adapted `EventSource` keeps its options, behavior, and constants. Copying an instance prototype alone is not constructor compatibility. Verification runs the served adapter with its real native consumer: null or non-open sockets cannot send and authorized open sockets send normally, so a failed guard cannot be hidden by swallowing exceptions. Rebasing is idempotent and binding-scoped across fetch, XHR, SSE, and WebSocket.

#### Scoped bootstrap and compiled assets

The reviewed surface covers the compiled application's actual startup requests and response shapes, each with an explicit scoped operation or unsupported disposition in the compatibility map; a route is never admitted just because a newer UI requests it. Boot metadata and global-looking catalogs are reduced to binding-local projections so they never enumerate unrelated harnesses, sessions, or hosts, and a missing required transcript route is never disguised as an empty success. Assets (HTML references, JS-imported images, CSS URLs, preloads, lazy chunks) resolve through the scoped asset surface; because a base tag does not scope references inside compiled JS or CSS, the bridge uses supported asset-base behavior or a bounded reviewed mapping rather than a root proxy or source rewriting.

#### Readiness and denial evidence

Readiness and failure behavior is described in [Workflow Chat Panel sections 4.1 and 11.1](../UI/WorkflowChatPanel.md#41-application-readiness-and-failure-containment). Serving authorization, compatibility, document delivery, rendered readiness, and transport health are separate facts, none of which grants later authority. Authorized bindings get stable reason codes distinguishing capability denial, stale authority, unsupported operations, and asset failure; unknown bindings get non-enumerating responses. The bridge never repairs a 403 by trusting browser flags or substituting a raw session id, and a denied optional feature does not block an authorized transcript. Qualification ties the build, UI and server artifacts, facade contract, and support identity to executable browser evidence (§20.5).

---

## 5. Components

| Component | Responsibility |
|---|---|
| Session API Facade | Omnigent-shaped service routes |
| Workflow Chat Binding / Policy Facade | Opaque bindings, native UI serving, identity virtualization, per-request authority |
| Host Protocol Proxy | Forwarding to stock Omnigent Server |
| Bridge Session Store | Bindings, profile and launch refs, first-message idempotency, event/snapshot/diagnostic refs |
| Event Normalizer | MoonMind-safe event records plus redacted raw JSONL |
| Resource Harvester / Artifact Publisher | Copies resources into artifacts and returns only MoonMind refs |
| Diagnostic Chat Projection | Read-only debug transcript from normalized events, falling back to legacy managed-run logs |
| Direct Codex Compatibility Producer | Bridge-shaped events from the direct Codex path |

## 6. Configuration

The bridge reads a `moonmind.omnigent_bridge.v1` document from `OMNIGENT_BRIDGE_CONFIG_PATH` (parsed by `moonmind/omnigent/bridge_config.py`) before registering routes; an unreadable or invalid document fails fast. It declares authority (Temporal and artifacts to MoonMind, live execution to the host), the `omnigent.server.v1` profile and proxy mode, public route mounts, the Workflow Chat mounts with `exposeProviderSessionId: false`, `authorizeEveryRequest`, credential stripping and server-side injection, `immutable_policy_intersection` capabilities and inherited high-security scanning, session defaults (managed host, capture of stream, snapshots, files, capability-probed diffs, child sessions), the first-message state machine, and journal and observability feeds. Environment configuration cannot disable request authorization, credential separation, or required high-security scanning for an enabled native Chat surface.

---

## 7. Durable data model

**`omnigent_bridge_sessions`** (ORM in `api_service/db/models.py`) is keyed by `bridge_session_id` with unique `idempotency_key` and optional unique `chat_binding_id`. It records MoonMind workflow, run, AgentRun, and Step Execution ids; profile, launch, and policy snapshot refs; Omnigent endpoint, session, host, runner, and agent identity; host type, workspace, and coarse status (`declared`, `creating`, `active`, `completed`, `failed`, `canceled`, `timed_out`); first-message state, digest, marker, and provider item ids; and event, snapshot, manifest, diagnostic, external-state, and terminal refs. `chat_binding_id` is an opaque lookup key, not a bearer capability. The table replaced the former `omnigent_external_runs` mapping; canonical session authority lives in `omnigent_sessions` and the control-plane repositories, while bridge rows keep attempt identity and evidence.

**`omnigent_bridge_session_events`** rows are a bounded index (sequence, direction, type, status, preview, artifact ref); full redacted bodies live in `runtime.omnigent.sse.raw.jsonl` and `runtime.omnigent.sse.normalized.jsonl`.

**Mutation audit.** Every message, control, approval, terminal, or workspace mutation records the actor, operation, idempotency key, workflow/run/step and session ids, expected epoch/turn/elicitation, snapshot refs, normalized outcome, upstream correlation, and audit artifact ref. A provider event without this evidence can be displayed but never becomes authoritative approval or side-effect evidence.

---

## 8. Session creation

A create request carries `agent_id`, title, `moonmind.*` labels (workflow, AgentRun, Step Execution, idempotency key), `host_type`, workspace (for example `https://github.com/org/repo#main`), optional model override and reasoning effort, and terminal launch args. The bridge validates principal and workflow authority, resolves immutable profile, policy, and launch snapshots, creates or reuses the row by idempotency key, resolves endpoint and agent server-side, forwards to Omnigent Server, persists provider session identity before the first message, allocates `chat_binding_id` only after the durable binding exists, and emits `session.created`.

For `host_type = managed`, caller-authored `host_id` and local absolute paths are rejected. For `host_type = external`, an authorized existing host binding and a valid external-host locator are required, and browser-authored absolute paths are still rejected on Chat routes.

---

## 9. Event posting contract

### 9.1 First message

The first message is a `type: message` event whose metadata carries `moonmindFirstMessageDigest` and `moonmindIdempotencyKey`, and whose text includes a `MoonMind-Omnigent-Run:` marker block with correlation id, idempotency key, and digest. Its state moves `not_prepared -> prepared -> posting -> posted -> terminal`: the digest is computed and `prepared` persisted before any POST, `posting` immediately before forwarding, and `posted` after confirmed success. A retry that sees `posted` skips the POST; one that sees `posting` reconciles snapshots, pending inputs, item ids, stream events, and the marker. When absence cannot be proven the bridge fails closed rather than reposting, and a digest conflict under one key fails fast.

Message dispatch has a 150-second transport budget because a native message can synchronously ensure the runner terminal before acceptance; it covers a connect-time bootstrap and one recovery attempt. The `omnigent.submit_turn` Activity allows 180 seconds per attempt and 600 across retries; other events use 60 seconds. The budget assumes the OpenCode host image's warm plugin npm cache ([`OpenCodeHost.md`](./OpenCodeHost.md)), so a cold `@opencode-ai/plugin` download is treated as a host image defect.

### 9.2 Native follow-up messages

Every native composer message passes through the scoped facade, which validates authority and expected state, assigns or checks an idempotency key, and applies `MOONMIND_HIGH_SECURITY_MODE` to extracted text (messages, slash-command arguments, approval text, textual attachments). A blocked scan stops the upstream request and reports only redacted category and location. In high-security mode an unparsable schema, unavailable scanner, or uninspectable required payload fails closed; the bridge never forwards first and diagnoses later.

### 9.3 Marked-turn terminal authority

A native runner acknowledges the injection itself: `running`, `response.in_progress`, and `response.completed` can arrive within a second while the harness has not begun the turn. A terminal SSE frame is therefore only a candidate, and the ordered session snapshot decides:

1. **Boundary and progress.** The marked user item (or the item-id frontier captured before dispatch) fixes where the turn begins. Progress is a `function_call`, `function_call_output`, or assistant `message` after it.
2. **Quiescence.** A complete turn (no unmatched tool call) must stay unchanged for 60s after final assistant text, or 300s after a tool result, and the snapshot must project an inactive turn; neither elapsed time nor a generic `response.completed` overrides an active projection. For `codex-native` snapshots, the mirrored `turn_diff` call is evidence-only rather than tool work, and status `running` (mirrored from Codex's own turn lifecycle) counts as active. If a live completion event is followed by 300s of unchanged, fully matched tool output while still active, the bridge emits `OMNIGENT_SAME_SESSION_CONTINUATION_REQUIRED`; a profile-bound repository run then claims a continuation turn and keeps its session, host, credential, and workspace authority.
3. **Turn start.** One watchdog per attempt, anchored at acceptance (or reattach) and evaluated on every observation (snapshots, heartbeats, idle ticks, stream close), bounds the wait at 300s while nothing follows the boundary and no live response remains. A response id already named by a terminal event is stale and cannot defer it, and retries rebuild that set from the durable raw journal. Expiry fails with `OMNIGENT_CURRENT_TURN_NOT_STARTED` (`integration_error`, `retry_step_execution`): the provider dropped the turn and there is no work to preserve. The execution boundary finalizes it (snapshot captured, row marked `failed`, typed result returned) so every path releases host and credential authority through normal cleanup, and the retry is a new Step Execution attempt with a fresh idempotency key.
4. **Host loss.** A Docker engine restart stops on-demand hosts without restarting them, and Omnigent then projects `runner_online`, `host_online`, and `host_resumable` as false while the turn stays frozen. After 180s of such observations with no final assistant text, the attempt fails with `OMNIGENT_SESSION_HOST_LOST` and `retry_step_execution`. A successor is allowed only once the lost host is confirmed stopped; otherwise the attempt reports `successorBlocked` with `do_not_retry` and the lease stays fenced for the janitor. The successor runs on a fresh host under the same workflow and logical step, restoring the verified worktree archive when one exists or recording `restartLimitation: no_verified_workspace_checkpoint`, within the remaining execution ceiling; cancellation starts no successor. Any observation without a lost host restarts the grace.
5. **Stalls.** An active turn whose ordered-work signature does not change cannot extend its execution budget through heartbeats and records `OMNIGENT_CURRENT_TURN_PROGRESS_STALLED`. A profile-bound continuation owner whose policy permits interruption may make one bounded interrupt/probe, and only a confirmed inactive projection admits same-session continuation. Otherwise the ambiguous-session cleanup fence holds and unresolved shapes report `OMNIGENT_CURRENT_TURN_TERMINAL_AMBIGUOUS`.
6. **Native rejection.** A post-dispatch `session.status: failed` with an explicit error ends the turn when the marked message is still the latest user item and no response is active; the redacted error is preserved. Omnigent's `codex_reauth_required` maps to `reauthenticate`, which blocks automatic retries because provider authorization is an operator decision. A harness that never starts reports `response.failed` marked `undelivered` with the message left in `pending_inputs`, so the attempt fails immediately with Omnigent's code (for example `agent_startup_pending`). Because `codex login status` only checks that tokens exist, a never-started profile-bound Codex attempt confirms its binding and credential generation are unchanged, then inspects the stored token in a network-less read-only container; only a provider-issued refreshed token with no lifetime (`exp <= iat`) converts the failure to `codex_reauth_required`.

Heartbeats expose turn progress, activity, wait and quiet durations, budgets, and `markedTurnObservationSource`, so operators can tell an injection echo, slow turn, and dropped turn apart. Event index fields replace NUL with U+FFFD for PostgreSQL, while the raw journal keeps the original and identities are hashed from it.

---

## 10. Stream and event normalization

The normalized journal records `postDispatchRawEventIndex` provenance for events after dispatch, committed before terminal reads, so on reattachment a journaled native failure is evaluated before reopening the stream and keeps its original code. Journals without this provenance use snapshot-based reconciliation.

**Retention.** Raw and normalized journals default to seven-day retention and are written in bounded chunks (at most 256 events or about 4 MiB), so per-event work does not grow with turn length. Each chunk version commits before the index rows that locate it, so a crash can leave an unindexed tail but never an index row without evidence. Every event commits before the loop waits on the provider or acts on a turn-ending or approval status; up to 32 queued events may share a commit. Longer chunk versions commit before shorter ones are reclaimed, and Activity retry restores chunk history and continues in a new chunk. The terminal capture bundle publishes complete journals, and terminal index rows are repointed in the same transaction. Deployment transitions compact sealed chunk history into the full JSONL prefix older readers understand without terminalizing the turn ([Docker Compose updates](../Steps/DockerComposeUpdateSystem.md#106-recreate-services)). Canonical refs, non-terminal chunk history, pins, and live use claims are protected from reclamation, and failed deletions are reconciled by the hourly lifecycle sweep.

**Shape.** Normalized events (`moonmind.omnigent_bridge.event.v1`) carry sequence, timestamp, bridge and Omnigent session ids, MoonMind workflow and AgentRun ids, direction, type (for example `response.delta`), normalized status, data, artifact refs, and `metadata.moonmind` (diagnostic visibility and source).

**Classes.** Recognized classes include `session.created`, `session.started`, `session.codex_approval_mode`, `session.item.*`, `session.input.*`, `response.delta`, `response.output`, `response.completed`, `response.failed`, `response.elicitation_request`, `session.child.*`, `resource.changed_file`, `resource.session_file`, `host.heartbeat`, and `host.capabilities`. Unsupported types are captured in diagnostics; execution-critical drift fails closed and optional resource drift may degrade. Credential redaction preserves event names and targets path-shaped values, so the `.codex` in `session.codex_approval_mode` is not treated as a credential path; that observation grants no authority.

---

## 11. Control and capability contract

| Intent | Control | Required authority |
|---|---|---|
| Send message | `type=message` | `sendMessage`, expected state, outbound scan, audit |
| Interrupt / stop | `type=interrupt`, `type=stop_session` | `interruptTurn` / `stopSession`, expected turn or session, audit |
| Resolve elicitation | native resolve route | Approval authority, expected elicitation, audit |
| Request harvest | bridge-local `harvest_session` | Artifact authority; no provider mutation |
| Clear/reset | native or new-session action | Explicit policy and session-boundary evidence |
| Terminal / workspace | native terminal and file routes | Separate create/input or read/write capability, path containment, audit |
| Model/effort/goal | native configuration route | Only when profile and launch policy permit per-session change |

Effective capabilities are upstream capability ∩ immutable Agent Profile snapshot ∩ Provider Profile and launch policy ∩ Workflow/Step/session state ∩ caller permission. The native client receives the filtered set for UX, but the server recomputes it on every request, because upstream support never grants MoonMind permission.

## 12. Resource harvesting and artifacts

The harvester copies changed files (`output.omnigent.changed_files*`), workspace files (`output.omnigent.workspace_files*`), diffs (`output.omnigent.workspace_diffs/<path>.diff`), session files (`output.omnigent.session_files*`), and child sessions (`runtime.omnigent.child_sessions*`) into artifacts. Diff capture is capability-probed and publishes `output.omnigent.patch_unavailable.json` rather than failing when unsupported. Live resource browsing in Chat is bound and non-authoritative; artifacts are the durable evidence.

## 13. Artifact outputs

Each terminal session produces the raw and normalized journals, initial and final snapshots, `output.omnigent.capture_manifest.json`, `runtime.omnigent.diagnostics.json`, `checkpoint.omnigent.external_state.json`, and `runtime.omnigent.control_audit.jsonl`. Artifact references keep their content across attempts even when a new admission reuses a filename. Generic-host runs also publish `generic-host-cleanup.json` with fenced cleanup results and, captured before the host container is removed, a redacted output tail (last 2000 lines, final 64 KiB) as `generic-host-logs.txt`, because the runner and harness inside that container are the only source for why a provider dropped or rejected a turn. Capture failures are recorded as `hostLogsCaptureError` and never block cleanup. Janitor recovery of a stale binding publishes no evidence.

---

## 14. AgentRun integration

### 14.1 Request shape

An AgentRun uses `agentKind: external`, `agentId: omnigent`, with `parameters.omnigent` naming `endpointRef`, session host type and workspace, and capture flags. A direct managed runtime that emits bridge events declares that under `parameters.communication`; its approval and control events are rejected without full audit evidence. `communication.comparisonMode = dual_write` compares independently persisted event evidence for parity without creating a second primary timeline.

### 14.2 Terminal result

```json
{
  "outputRefs": ["art_final_snapshot", "art_normalized_stream", "art_capture_manifest"],
  "summary": "Implemented the change and opened PR ...",
  "diagnosticsRef": "art_diagnostics",
  "failureClass": null,
  "metadata": {"providerName": "omnigent", "normalizedStatus": "completed", "captureManifestRef": "art_capture_manifest", "externalStateRef": "art_external_state"}
}
```

Provider-native refs never appear in top-level `outputRefs`.

---

## 15. Native Workflow Chat integration

Product behavior is described in [`WorkflowChatPanel.md`](../UI/WorkflowChatPanel.md). The backend resolves the session from Workflow, run, Step Execution, AgentRun, and bridge state and returns a browser-safe `WorkflowChatBinding`: `chatBindingId`, workflow and run ids, optional logical step and Step Execution ids, `chatUrl`, `apiBase`, `state` (`starting`, `available`, `ended`, `unavailable`), `readOnly`, `capabilities`, and `unavailableReason`. Provider, bridge, endpoint, host, runner, credential, and profile identities stay server-side. The native application is the primary renderer, and ordinary messages use the scoped facade rather than Temporal or `SubmitChatInstruction`. The bridge-event routes `GET /api/omnigent/bridge-sessions/{bridge_session_id}/events` and `/stream` are read-only diagnostic APIs, not a second composer.

For terminal work the transcript is read-only. `GET /api/executions/{workflowId}/captured-evidence` returns MoonMind artifact refs, never provider paths. `POST /api/executions/{workflowId}/continue` is admitted only from a terminal source with a stable `idempotencyKey`; it pins the source run and selected evidence under `relationshipType = linked_continuation`, resolves fresh authority through the ordinary create path, and never mutates the source. `GET .../continuations?direction=outbound|inbound` lists the relationship from either side.

### 15.1 Serving the native UI through MoonMind-scoped routes

MoonMind serves the native application at `GET /omnigent-ui/workflow-chat/{chatBindingId}[?embedded=1]`, reverse-proxying stock assets and injecting a bootstrap, so the browser never reaches the upstream server. The host boundary presents the application's expected `/c/{chatBindingId}` route and rebases HTTP, SSE, and WebSocket traffic onto the facade, then restores the reloadable scoped URL. Boot probes get bounded local projections, the session catalog contains only the bound session, and after provider cleanup reads use the captured final snapshot. Embedded and full-page views share binding, facade, credentials, and policy.

`window.__MOONMIND_OMNIGENT_CHAT__` carries only the binding id, scoped API and WebSocket bases, presentation mode, read-only state, filtered capabilities with disabled reasons, labels, and a compatibility version, never a provider session id, upstream URL, host id, credential, profile, or workspace authority.

The document CSP uses `default-src 'none'`, same-origin sources for scripts, styles, images, fonts, and media, `connect-src 'self'`, `object-src 'none'`, `worker-src 'none'`, and a nonce or exact hash for the inline bootstrap rather than `unsafe-inline`, `unsafe-eval`, wildcards, or external origins. CSP complements URL mapping because `connect-src` neither scopes same-origin paths nor governs asset loading, and it blocks absolute external URLs that rebasing leaves. Embedded documents allow `frame-ancestors 'self'`; full-page refuses framing. Documents are `no-store` with `Vary: Cookie` so one caller's bootstrap cannot leak to another; hashed assets are privately cacheable.

`OMNIGENT_NATIVE_UI_ENABLED` defaults to enabled, but serving requires the deployed `OMNIGENT_IMAGE_REF` digest to resolve against protected qualification evidence (§20.5) binding it to the Omnigent source, native bundle, MoonMind build, and facade profile with passing integrity and freshness checks. A digest is never translated unconditionally into `PINNED_OMNIGENT_COMMIT`, and an explicit `OMNIGENT_NATIVE_UI_VERSION` only constrains the validated version. Mutable tags, missing or stale evidence, and mismatches fail closed with `omnigent_native_chat_unavailable`. Readiness reports serving state under `compatibilityDiagnostics.nativeUi`.

---

## 16. Security and authentication

- Every request is authenticated and authorized against its Workflow, AgentRun, bridge session, and operation, on every connect and reconnect.
- `chatBindingId` is a lookup key, not a bearer capability; any session id in a request must map exactly to the bound session.
- Capabilities are recomputed server-side, and no control exceeds MoonMind policy even when upstream supports it.
- Mutations retain full audit evidence (§7). In high-security mode, text-bearing sends are scanned and fail closed when enforcement is unavailable.
- Omnigent endpoint credentials never enter browser or Temporal payloads. MoonMind cookies, tokens, CSRF tokens, and internal headers are stripped before forwarding; only allowlisted headers and server-injected credentials go upstream.
- Raw provider events are redacted before persistence.

Authentication is layered: the MoonMind user is authorized by MoonMind, the scoped facade uses a server-side Omnigent credential, and the host uses its host/runner auth profile. No configuration turns a direct upstream browser URL into an authority bypass.

---

## 17. Error classification

| Failure | Class / posture | Notes |
|---|---|---|
| Binding absent or stale | typed unavailable/retry | Never selects another session. |
| Caller unauthorized; session or endpoint substitution | `403`/`409` with audit | Fails closed before upstream without revealing other sessions. |
| Capability or policy denial; scan blocked or unavailable | typed rejection | Redacted findings; no provider send. |
| Native boot or render failure; required transcript denied | visible unavailable state | Never an empty transcript; optional denials stay feature-local. |
| Upstream unavailable, unreachable host, upstream auth failure | `integration_error` (auth non-retryable until fixed) | Requests that never connected are re-sent for up to 120s; answered requests are never replayed. |
| Invalid create payload; first-message digest mismatch | `user_error` | |
| Ambiguous `posting`; stream disconnect | `integration_error` | Reconcile instead of reposting. |
| Turn never started | `OMNIGENT_CURRENT_TURN_NOT_STARTED` | `retry_step_execution` (§9.3). |
| Harness never started | Omnigent's code, e.g. `agent_startup_pending` | Ended Codex login becomes `reauthenticate`. |
| Host lost for 180s | `OMNIGENT_SESSION_HOST_LOST` | Retry on a fresh host within the remaining ceiling. |
| Stalled or ambiguous turn | `OMNIGENT_CURRENT_TURN_PROGRESS_STALLED`, `..._TERMINAL_AMBIGUOUS` | Cleanup deferred while work may be live. |
| Harness failure; timeout | `execution_error`; `system_error` | `timed_out` stays distinct. |
| Optional harvest failure | result plus diagnostics | Unless policy requires full evidence. |
| Required artifact persistence failure | `system_error` | |

---

## 18. Module boundaries

Bridge code lives in `moonmind/omnigent/` rather than a parallel package: `bridge_config.py` (configuration), `bridge_store.py` (binding and idempotency store), `bridge_proxy.py` with `api_service/api/routers/omnigent_bridge.py` (facades), `bridge_events.py` (normalizer), `bridge_artifacts.py` (harvest and publication), and `native_ui_compat.py` (compatibility map). Schemas are in `moonmind/schemas/omnigent_bridge_models.py`. When a native binding exists, the native application replaces the custom MoonMind transcript and composer as the primary Chat; the MoonMind projection serves diagnostics, history, and compatibility fallback.

## 19. Non-goals

The bridge does not fork the Omnigent host, recreate the native Chat UI, expose the upstream server to bypass authorization, turn provider ids or states into Workflow ids or states, make provider resources authoritative over artifacts, let native controls override policy, skip outbound scanning, or blindly repost first messages.

---

## 20. Testing strategy

### 20.1 Unit and policy tests

Unit tests cover configuration, authorization and substitution rejection, capability intersection, audit validation, credential separation, high-security scanning, first-message reconciliation, normalization, and the served adapter's constants, sends, and rebasing.

### 20.2 Fake Omnigent server tests

These cover bootstrap, message and stream flow, queued turns, approvals, resources, terminals, reconnect, substitution attempts, policy denials, scan blocks, first-message retries, child sessions, and terminal evidence.

### 20.3 Browser journey tests

Browser journeys show that `/workflows/{workflowId}/chat` opens the bound native session through the scoped facade; hidden controls are also rejected by direct API calls; full-page view keeps the boundary; altered bindings cannot reach other sessions; terminal Chat is read-only with no second composer; the real adapter handles socket states and reconnects; required assets load before readiness; failures recover visibly while optional denials stay nonfatal; and stale readiness signals or replacement bindings cannot revive frames or duplicate mutations.

### 20.4 Stock host smoke tests

Smoke tests against a real unchanged host verify registration, capabilities, session creation, harness launch, messaging, stream capture, snapshots, harvest, credential separation, no duplicate first message, and cleanup and audit evidence.

### 20.5 Native Workflow Chat rollout gate

Native Workflow Chat support is qualified by a protected browser-to-stock-host matrix with one journey per claimed support combination; unclaimed combinations appear as `unsupported` with a code-owned reason. Each journey creates a live conversation through `/api/executions` and Temporal and covers scoped transports, resources, authority and security denials (including cross-user and cross-workflow), terminal evidence, linked continuation, and release-last cleanup against an unchanged digest-pinned host. Evidence is bound to the exact support-combination identity, and the recorded `supportCombinationKey` must recompute from it, so evidence from one credential authority or host image cannot qualify another.

Every source record carries an observation timestamp, image digests, and one server-owned correlation envelope: `workflowId`, `chatBindingId`, `bridgeSessionId`, `providerSessionId`, and `browserTraceId`. All records in a row share the envelope, and every record's request ids resolve to the row's browser trace. The records (`browserTrace`, `bindingSnapshot`, `nativeConversation`, `nativeControls`, `facadeRequests`, `resourceInventory`, `mutationReceipts`, `denialAudit`, `executionCreation`, `capabilitySnapshot`, `scanAudit`, `credentialBoundary`, `terminalSnapshot`, `capturedEvidence`, `continuationReceipt`, `replaySnapshot`, `cleanupReceipt`) and their required facts are defined and validated in `moonmind/omnigent/workflow_chat_acceptance.py`. Each `facadeRequests` record correlates to the bound `providerSessionId`.

The resulting `moonmind.omnigent.workflow-chat-acceptance/v2` artifact is bound to a source commit, compatibility profile, image and bundle digests, scenario and route-inventory versions, and a freshness window, and holds SHA-256 bindings for case evidence, source records, conformance reports, the operator timeline, and secret-scan results for every raw channel. A bare pass flag, repository-only test, unresolved ref, mutable tag, stale report, self-asserted scan, or omitted combination does not qualify. The builder validates against `--expected-commit`, the provider gate requires the same commit through `MOONMIND_OMNIGENT_SOURCE_COMMIT`, and `test_live_native_workflow_chat_release_matrix` is the release gate.
