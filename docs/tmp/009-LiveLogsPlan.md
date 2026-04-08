# Live Logs Session-Aware Implementation Plan

Status: Proposed
Owners: MoonMind Platform
Last updated: 2026-04-08
Related:
- `docs/ManagedAgents/LiveLogs.md`
- `docs/ManagedAgents/CodexManagedSessionPlane.md`
- `docs/tmp/009-LiveLogsPlan.md`

## 1. Objective

Evolve the current functional Live Logs stack into the desired final state:

- still artifact-first
- still MoonMind-owned
- still spool/SSE backed for live follow
- but no longer only a merged stdout/stderr/system tail
- instead, a continuity-aware observability timeline for Codex managed sessions

This plan starts from the codebase as it exists today and avoids replacing working pieces unless the new session-plane contract requires it.

## 2. Honest current baseline

The current project is not starting from zero.

Already in place:

- durable `stdout`, `stderr`, and diagnostics artifact production
- run-level observability summary and artifact-backed log retrieval APIs
- shared append-only spool transport for live chunks
- SSE endpoint for active runs
- Mission Control Live Logs panel with summary -> merged-tail -> optional SSE lifecycle
- run-global sequence numbering across `stdout` / `stderr` / `system`
- session continuity projection and control APIs for Codex-style session artifacts

Still missing for the desired final state:

- a canonical session-aware observability event model
- session-plane lifecycle events in the same run-global timeline as stdout/stderr/system
- explicit reset-boundary rows in the Live Logs timeline
- structured historical event retrieval for the timeline
- frontend rendering beyond line-centric `stdout` / `stderr` / `system`
- `react-virtuoso` and `anser` adoption for the long-term viewer baseline
- hardening/performance work for large logs and long-lived sessions

## 3. Delta from current state

The Codex Managed Session Plane adds four requirements that the shipped line-centric Live Logs stack does not yet model as first-class timeline facts:

- bounded session identity: `session_id`, `session_epoch`, `container_id`, `thread_id`, and `active_turn_id`
- control vocabulary: `start_session`, `resume_session`, `send_turn`, `steer_turn`, `interrupt_turn`, `clear_session`, `cancel_session`, and `terminate_session`
- reset-boundary semantics: `clear_session` must produce a visible epoch/thread boundary instead of hiding the change inside plain text
- durable continuity artifacts: summary, checkpoint, control-event, and reset-boundary artifacts remain the authoritative drill-down evidence

## 4. Guiding migration rules

1. Keep the current transport boundary.
   - Do not replace the spool/SSE architecture unless it blocks the new contract.
   - Extend it to carry richer events.

2. Preserve artifact-first semantics.
   - Every new live-visible fact must also be reconstructable from artifacts or bounded workflow metadata.

3. Do not merge logging and control.
   - Session controls remain separate.
   - Live Logs only reflects those controls as passive observability rows.

4. Prefer additive, backward-compatible changes first.
   - Enrich payloads before replacing endpoints.
   - Dual-read and dual-write where helpful.

5. Keep historical runs usable.
   - Runs without session-aware history must continue to degrade through merged artifacts and continuity drill-down.

## 5. Rollout flag

The new timeline contract is gated behind `liveLogsSessionTimelineEnabled`.

Rollout scopes:

- `off`
- `internal`
- `codex_managed`
- `all_managed`

`logStreamingEnabled` remains the transport toggle for existing spool/SSE behavior. The new flag controls session-aware timeline semantics independently from live transport availability.

## 6. Desired end state

By the end of this plan:

- `/observability-summary` returns both live-stream status and the latest session snapshot when present
- Live streaming still uses `/logs/stream`, but events can include session metadata and event kinds
- the UI prefers a structured event-history endpoint for initial load when available
- Live Logs renders a unified timeline of `stdout`, `stderr`, `system`, and `session`
- `clear_session` appears as a visible epoch-boundary banner, not just a text line or a separate side panel artifact
- Session Continuity remains as drill-down evidence, but the main operator experience is the unified timeline

---

## Phase 0 — Re-baseline the implementation plan

### Goal

Reset the project plan so it reflects what is already shipped versus what is newly required by the Codex Managed Session Plane.

### Tasks

- [x] Replace the old line-centric `docs/tmp/009-LiveLogsPlan.md` with this session-aware migration plan.
- [x] Mark the original line-centric Live Logs phases as baseline-complete for the shipped artifact/spool/SSE architecture.
- [x] Document the delta from current state:
  - bounded session identity
  - control vocabulary
  - reset-boundary semantics
  - durable continuity artifacts
- [x] Introduce one feature flag for the new timeline contract:
  - `liveLogsSessionTimelineEnabled`
- [x] Define explicit rollout scopes:
  - `off`
  - `internal`
  - `codex_managed`
  - `all_managed`

### Exit criteria

- [x] The current spool/SSE/artifact stack is treated as the baseline, not a prototype to be replaced.
- [x] The remaining work is clearly framed as a session-aware upgrade rather than "build Live Logs from scratch."

---

## Phase 1 — Define and persist the canonical observability event model

### Goal

Introduce a stable MoonMind event model that can represent both existing log chunks and new session-plane lifecycle facts.

### Tasks

- [x] Define a canonical browser/backend contract: `RunObservabilityEvent`
- [x] Keep current live-log payloads readable during migration while the canonical event contract becomes the source of truth.
- [x] Persist structured historical event reconstruction via an artifact-backed JSONL journal.
- [x] Extend the managed-run persistence model with session snapshot and historical event refs:
  - `session_id`
  - `session_epoch`
  - `container_id`
  - `thread_id`
  - `active_turn_id`
  - `observability_events_ref`
- [x] Keep sequence numbering run-global across all streams, including `session` rows.

### Exit criteria

- [x] A single event model can represent current stdout/stderr/system chunks and future session lifecycle rows.
- [x] No frontend work depends on provider-native event payloads.
- [x] The backend can persist enough structured history to reconstruct the timeline after the run ends.

### Notes

This phase is the contract anchor. Everything after this becomes simpler once the browser sees one unified event shape.

---

## Phase 2 — Make the Codex Managed Session Plane a first-class observability producer

### Goal

Have the session plane publish passive lifecycle events into the same run-global observability stream already used by Live Logs.

### Tasks

- Add an observability sink/adapter at the session-plane boundary.
- For each stable session-plane action, emit a normalized `session` event:
  - `start_session`
  - `resume_session`
  - `send_turn`
  - `steer_turn`
  - `interrupt_turn`
  - `clear_session`
  - `cancel_session`
  - `terminate_session`
- Emit session events for major session lifecycle transitions:
  - session started
  - session resumed
  - turn started
  - turn completed
  - turn interrupted
  - approval requested
  - approval resolved
  - summary published
  - checkpoint published
- For `clear_session`, emit both:
  - a passive control event row, and
  - a dedicated `session_reset_boundary` row carrying the new epoch/thread info
- Ensure each emitted session event includes the latest known session snapshot fields when available.
- Update the managed session store/workflow adapter so the latest session snapshot is mirrored onto the run record or other summary source.
- Sanity-check that session-plane publishing failures do not break runtime control or artifact persistence.

### Exit criteria

- Session-plane lifecycle facts show up in the same sequence namespace as stdout/stderr/system.
- `clear_session` produces an explicit boundary event with epoch/thread changes.
- Live observability no longer depends on the separate continuity panel for the operator to notice a reset.

### Risk to watch

Avoid turning the session-plane adapter into a mirror of raw Codex provider events. The emitted contract should stay MoonMind-normalized.

---

## Phase 3 — Add structured historical event retrieval while preserving current APIs

### Goal

Give the UI a durable structured history source for the timeline, without breaking the existing merged-tail and SSE behavior.

### Tasks

- Add a historical retrieval endpoint:
  - `GET /api/task-runs/{id}/observability/events`
- Support:
  - `since=`
  - `limit=`
  - optional stream filtering
  - optional kind filtering
- Keep `/logs/stream` as the live endpoint, but enrich its payload to use the same event schema.
- Keep `/logs/merged` as a human-readable fallback/download surface.
- Decide and document fallback order for historical load:
  1. structured events endpoint
  2. merged artifact-backed tail
  3. continuity artifact drill-down if necessary for missing session facts
- Enrich `/observability-summary` to include latest session snapshot fields when present.
- Ensure the summary endpoint remains truthful for ended runs and non-stream-capable runs.

### Exit criteria

- The UI can render a session-aware historical timeline without depending on live transport.
- `/logs/stream` and `/logs/merged` remain compatible with current consumers.
- Summary payloads expose enough session snapshot data for compact header rendering.

---

## Phase 4 — Upgrade the frontend from line viewer to observability timeline

### Goal

Preserve the current panel lifecycle while replacing the underlying row model and rendering logic.

### Tasks

- Replace the current `LogLine` model with a richer timeline row model.
- Keep the current lifecycle:
  - fetch summary
  - fetch historical content
  - attach live SSE only when panel is open, visible, and active
- Change initial load order to:
  1. summary
  2. structured history if available
  3. fallback to merged text if not
- Render distinct row types:
  - stdout line
  - stderr line
  - system annotation
  - session lifecycle row
  - reset boundary banner
  - summary/checkpoint publication marker
  - approval row
- Add a compact header snapshot for session context:
  - session ID
  - epoch
  - container ID
  - thread ID
  - active turn ID
  - live status
- Keep Session Continuity as a drill-down area, but reduce duplication with the main timeline.
- Decide whether to:
  - keep a separate "Session Continuity" panel, or
  - collapse it into "Artifacts / Session drill-down" once timeline integration lands
- Finish the viewer hardening work already open in the previous plan:
  - adopt `react-virtuoso`
  - adopt `anser`
- Add stream/session filters if cheap enough in the first pass.

### Exit criteria

- The Live Logs panel renders a unified timeline of `stdout`, `stderr`, `system`, and `session`.
- Reset boundaries are visually obvious.
- The current passive-observation UX remains intact.
- Large logs no longer depend on naive DOM growth.

---

## Phase 5 — Unify continuity artifacts with timeline semantics

### Goal

Make the continuity artifact system and Live Logs feel like one observability model instead of two parallel ones.

### Tasks

- Add explicit links from timeline rows to continuity artifacts where relevant.
- Ensure the session projection remains the source for artifact drill-down, not for the main operator timeline.
- Add helper APIs or response shaping if the UI needs artifact refs attached directly to timeline events.
- Decide whether the current artifact session projection polling should remain separate or be folded into summary/history refresh paths.
- Ensure artifact groups still cover:
  - summary
  - checkpoint
  - control
  - reset boundary
- Update copy and panel labels so operators understand that:
  - timeline = what happened
  - continuity artifacts = durable evidence / drill-down

### Exit criteria

- Operators can move from a timeline event to the related artifact without context switching.
- Continuity artifacts remain available for audit/postmortem even if the main timeline is enough for most usage.
- The UI no longer feels split between logs and session continuity as separate worlds.

---

## Phase 6 — Compatibility, migration, and cleanup

### Goal

Roll out the new timeline without breaking existing active runs, historical runs, or non-session-managed runs.

### Tasks

- Support a compatibility mode where `/logs/stream` may still emit old minimal events while the new UI can read both shapes.
- Support a compatibility mode where the UI can synthesize timeline rows from merged text if structured history is absent.
- Keep `/logs/merged` and existing stream-name endpoints stable during rollout.
- Avoid backfilling old runs unless needed; degrade them gracefully.
- Gate the new timeline rendering behind `liveLogsSessionTimelineEnabled` until dev/internal validation is complete.
- Roll out in stages:
  1. dev/internal only
  2. Codex-managed runs
  3. all managed runs that emit session events
- Remove obsolete assumptions once the new path is stable:
  - plain `LogLine`-only UI logic
  - ad hoc merged-text parsing as the primary model
  - duplication between separate continuity banners and the main timeline

### Exit criteria

- Existing runs remain observable during deployment transitions.
- The frontend and backend can be deployed independently for a short window without catastrophic breakage.
- Historical runs still work, even if they render a degraded timeline.

---

## Phase 7 — Hardening, performance, and operational rollout

### Goal

Make the session-aware timeline robust enough to be the default managed-run observability experience.

### Tasks

- Add backend tests for:
  - session event publication
  - reset boundary publication
  - dual live + historical reconstruction
  - missing partial continuity artifacts
  - ended-run reloads
- Add frontend tests for:
  - boundary rendering
  - session snapshot header
  - fallback from structured history to merged text
  - reconnect after visibility change
  - mixed stdout/stderr/system/session ordering
- Load test:
  - large merged histories
  - long-running live streams
  - runs with many boundary/system/session rows
- Instrument the observability system itself:
  - event journal write failures
  - SSE disconnect rates
  - journal read latency
  - summary endpoint latency
  - timeline render performance on large datasets
- Validate auth and ownership checks for all new structured history surfaces.
- Define rollback behavior:
  - disable structured history endpoint usage in UI
  - fall back to current merged-tail behavior
- Remove the feature flag only after internal Codex-managed runs look stable.

### Exit criteria

- The UI remains responsive for large timelines.
- Operators can reliably observe active and completed Codex-managed runs.
- Reset boundaries and session lifecycle are consistently visible in both live and historical views.
- The new timeline path is safe to enable by default.

---

## 7. Smallest safe PR sequence

1. **Contract PR**
   - add `RunObservabilityEvent`
   - update `docs/tmp/009-LiveLogsPlan.md`
   - keep current payload readers working

2. **Summary PR**
   - enrich `/observability-summary` with session snapshot fields

3. **Session-plane producer PR**
   - emit `session` rows for start/resume/turn/clear events
   - emit explicit reset-boundary rows

4. **Structured-history PR**
   - persist/read observability events
   - add `/observability/events`

5. **Frontend model PR**
   - replace `LogLine` with timeline rows
   - still render mostly like today

6. **Frontend UX PR**
   - boundary banners
   - session snapshot header
   - continuity artifact links

7. **Viewer-hardening PR**
   - `react-virtuoso`
   - `anser`

8. **Cleanup PR**
   - remove old-only assumptions
   - simplify continuity-panel duplication

## 8. Definition of done

This migration is done when all of the following are true:

- Live Logs still works for today’s active and completed runs.
- Codex-managed runs surface session identity in summary and timeline.
- `clear_session` produces an explicit epoch-boundary row in both live and historical views.
- The main operator experience is a unified timeline, not separate mental models for logs and continuity.
- Session continuity artifacts remain available as durable drill-down evidence.
- The viewer is virtualized and ANSI-aware.
- Large-log and long-session behavior has been tested and is operationally acceptable.
