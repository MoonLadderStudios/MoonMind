# Omnigent Bridge Rollout

This temporary working note tracks delivery sequencing for the Omnigent Bridge and native Workflow Chat designs in:

- `docs/Omnigent/OmnigentBridge.md`
- `docs/UI/WorkflowChatPanel.md`

The canonical documents own durable desired state. This note owns ordered, disposable implementation work and may be deleted or archived once the target is operational.

## Phase 1 — Bridge config, schemas, and store

Use the existing `moonmind/omnigent/` package and canonical bridge session store:

```text
moonmind/omnigent/bridge_config.py
moonmind/omnigent/bridge_store.py
moonmind/schemas/omnigent_bridge_models.py
api_service/db/models.py: OmnigentBridgeSession / OmnigentBridgeSessionEvent
api_service/migrations/versions/*_omnigent_bridge_sessions.py
```

`bridge_store.py` supersedes the former `omnigent_external_runs` mapping. Migrate and remove the superseded store in the same cohesive implementation rather than adding an alias or parallel table.

## Phase 2 — Authorized proxy mode

Implement the stock-server proxy path in:

```text
api_service/api/routers/omnigent_bridge.py
moonmind/omnigent/bridge_proxy.py
moonmind/omnigent/bridge_events.py
moonmind/omnigent/bridge_artifacts.py
```

The proxy must establish the security substrate needed by native Workflow Chat before embedding the UI:

- durable Workflow-to-provider session bindings,
- per-request authorization for HTTP, SSE, WebSocket, resources, messages, approvals, terminals, and controls,
- session-id and upstream-target non-substitution,
- immutable Agent Profile and effective-launch capability enforcement,
- actor/idempotency/expected-state/outcome/audit evidence for mutations,
- `MOONMIND_HIGH_SECURITY_MODE` outbound scans before text-bearing sends,
- MoonMind/upstream credential separation and header allowlisting.

## Phase 3 — Native Workflow Chat

> **Status (MoonLadderStudios/MoonMind#3638 — Serve the native Omnigent web app):**
> The native UI serving surface is implemented. MoonMind serves the stock native
> Omnigent app through the binding-scoped route
> `GET /omnigent-ui/workflow-chat/{chatBindingId}[?embedded=1]`
> (`api_service/api/routers/omnigent_native_ui.py`), reverse-proxying stock UI
> assets, injecting a browser-safe bootstrap
> (`moonmind/omnigent/native_ui.py`, `window.__MOONMIND_OMNIGENT_CHAT__`),
> scoping asset URLs, applying the embedded vs full-page security-header policy,
> and gating on a compatible native UI/server version
> (`OMNIGENT_NATIVE_UI_ENABLED` / `OMNIGENT_NATIVE_UI_VERSION`). The frontend
> embeds it via an iframe and adds **Open in Omnigent**
> (`frontend/src/entrypoints/WorkflowChatNative.tsx`); readiness reports the
> native-UI serving state under `compatibilityDiagnostics.nativeUi`. Builds on
> #3633 (opaque `chat_binding_id`) and #3634 (binding-scoped HTTP/SSE facade).

### 3.1 Browser-safe binding

Add the authoritative binding projection and scoped routes:

```text
GET /api/executions/{workflowId}/chat-binding
GET /omnigent-ui/workflow-chat/{chatBindingId}
*   /api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}
```

Expose an opaque `chatBindingId`, server-generated `chatUrl`, read-only state, and filtered capability manifest. Keep provider session, bridge session, endpoint, host, runner, profile snapshot, and credential identity server-side.

### 3.2 Native handoff

Add **Open in Omnigent** through the MoonMind-scoped surface and validate:

- correct binding resolution,
- caller authorization,
- filtered capability behavior,
- message scan enforcement,
- deep linking and session availability,
- full-page navigation without an upstream-auth bypass.

### 3.3 Embedded native application

Embed the native Omnigent web application under `/workflows/{workflowId}/chat` with `embedded=1` presentation behavior. Retain only the thin MoonMind workflow context bar.

Do not copy Omnigent transcript, composer, queue, approval, tool, file, terminal, agent, task, model, effort, or reconnect components into the MoonMind frontend.

### 3.4 Demote duplicate MoonMind UI

Move the current event projection, raw timeline, resource evidence, and administrative session controls under Debug or Diagnostics. Keep them read-only for compatibility when native Chat is available. Remove the custom follow-up composer from the primary path.

### 3.5 MoonMind-specific advantages

Add:

- **View captured evidence**,
- terminal read-only behavior,
- **Continue in a new workflow** using pinned source identity and authorized evidence.

Recommended temporary rollout flags:

```text
workflowNativeChatEnabled
workflowNativeChatEmbedEnabled
```

Remove the flags or make the canonical path unconditional after the rollout and fallback window is complete.

## Phase 4 — Direct Codex compatibility producer

Keep the direct Codex producer only for the explicit migration and historical evidence window. It emits bridge-compatible events for diagnostics and compatibility projection; it does not become a second primary Chat renderer.

Retire it only when the conformance, production coverage, retention, and Temporal-history gates in the canonical cutover documents are satisfied.

## Phase 5 — Embedded host compatibility mode

Implement direct host-facing compatibility only after authorized proxy mode passes conformance and live smoke tests.

```text
MoonMind API becomes the Omnigent-compatible server surface.
Unchanged host points directly at the MoonMind bridge URL.
```

Embedded host compatibility does not weaken the native Workflow Chat request-authorization, capability, outbound-scan, audit, or credential-separation requirements established in proxy mode.
