# Omnigent Bridge Rollout

This temporary working note tracks rollout sequencing for the Omnigent Bridge
design in `docs/Omnigent/OmnigentBridge.md`. The canonical doc owns the durable
desired-state module boundaries and contracts; this note owns the ordered,
disposable delivery plan and may be deleted or archived once the bridge lands.

## Phase 1 — Bridge config, schemas, and store

Add bridge code to the existing `moonmind/omnigent/` package (not a new
`moonmind/omnigent_bridge/` package), alongside the current
`moonmind/omnigent/store.py` (`OmnigentRunStore`) and
`moonmind/omnigent/execute.py`:

```text
moonmind/omnigent/bridge_config.py
moonmind/omnigent/bridge_store.py
moonmind/schemas/omnigent_bridge_models.py
api_service/db/models.py: OmnigentBridgeSession / OmnigentBridgeSessionEvent
api_service/migrations/versions/*_omnigent_bridge_sessions.py
```

`bridge_store.py` supersedes the existing `omnigent_external_runs` mapping in
`moonmind/omnigent/store.py`: migrate that mapping into
`omnigent_bridge_sessions` and remove the superseded store in the same change
rather than aliasing or wrapping it. `omnigent_bridge_sessions` is the single
canonical session/idempotency store after this phase.

## Phase 2 — Proxy mode

Implement:

```text
api_service/api/routers/omnigent_bridge.py
moonmind/omnigent/bridge_proxy.py
moonmind/omnigent/bridge_events.py
moonmind/omnigent/bridge_artifacts.py
```

Behavior:

```text
MoonMind receives Omnigent-shaped requests.
MoonMind persists bridge state.
MoonMind forwards to stock Omnigent Server.
MoonMind captures streams/resources/artifacts.
Stock Omnigent Server talks to unchanged host.
```

## Phase 3 — Workflow Chat projection

Add API and UI support for bridge-session events:

```text
GET /api/omnigent/bridge-sessions/{bridge_session_id}/events
GET /api/omnigent/bridge-sessions/{bridge_session_id}/stream
```

Update Workflow Chat to prefer bridge session events.

## Phase 4 — Direct Codex compatibility producer

Update direct Codex managed sessions to emit bridge-compatible session events
before and during launch. This phase fixes the class of failures where direct
Codex launch can end before a managed runtime observability record is created.

## Phase 5 — Embedded compatibility mode

Implement direct host-facing compatibility only after proxy mode passes
conformance and live smoke tests.

```text
MoonMind API becomes the Omnigent-compatible server surface.
Unchanged host points directly at MoonMind bridge URL.
```
